#!/usr/bin/env python
"""Helper voor backfill_abd_nieuws.sh, fase 2.

Valideert de claude-output van een single-article parse-call en mergeert
geldige proposals atomisch in de maand-staging-file.

Args:
    sys.argv[1]  pad naar de claude stdout (text-output, hopelijk JSON-array)
    sys.argv[2]  pad naar de bron-HTML (voor substring-check)
    sys.argv[3]  pad naar de doel-staging-file (data/_staging/abd-nieuws-YYYY-MM.json)
    sys.argv[4]  identifier van het artikel (basename zonder .html)

Print op stdout een 1-regel-summary: ``n_valid=<x> n_rejected=<y> added=<z>``.
Print op stderr details bij rejecties.
Exit non-zero alleen bij echte fouten.

Validatie:
- ``evidence_snippet`` MOET letterlijke substring zijn van de HTML (na entity-decode
  en whitespace-normalisatie).
- ``abd_nieuws_url`` MOET aanwezig zijn (skill-eis).
- Confidence-cap 0.85 als ``staatscourant_url`` null/ontbrekend is. We knijpen
  hogere confidences automatisch terug naar 0.85 in plaats van te rejecten,
  omdat de skill anders veel marginaal-bruikbare proposals laat vallen.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'", "&nbsp;": " "}


def decode_entities(s: str) -> str:
    out = s
    for k, v in ENTITIES.items():
        out = out.replace(k, v)
    return out


def extract_json_array(text: str) -> list[Any]:
    """Probeer een JSON-array uit ``text`` te halen."""
    text = text.strip()
    if not text:
        return []
    # Strip eventuele markdown-fences.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
        text = text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
    except json.JSONDecodeError:
        pass

    start = text.find("[")
    if start < 0:
        return []
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                blob = text[start : i + 1]
                try:
                    parsed = json.loads(blob)
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    return []
                return []
    return []


def substring_in_html(snippet: str, html_text: str) -> bool:
    """Strict-on-best-effort substring check.

    1. Raw substring.
    2. Na entity-decode.
    3. Whitespace-normalisatie aan beide kanten.
    """
    if not snippet:
        return False
    if snippet in html_text:
        return True
    decoded = decode_entities(html_text)
    if snippet in decoded:
        return True
    norm_snip = re.sub(r"\s+", " ", snippet).strip()
    norm_html = re.sub(r"\s+", " ", decoded).strip()
    return norm_snip in norm_html


def merge_into_staging(staging_path: Path, new_items: list[dict]) -> int:
    """Voeg ``new_items`` toe aan ``staging_path``. Idempotent.

    Dedup-key: ``(abd_nieuws_url, person_name, post_id)``. Atomic write via
    temp-file + ``os.replace``.
    """
    existing: list[dict] = []
    if staging_path.exists():
        try:
            existing = json.loads(staging_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except json.JSONDecodeError:
            existing = []

    seen_keys = {
        (item.get("abd_nieuws_url"), item.get("person_name"), item.get("post_id"))
        for item in existing
        if isinstance(item, dict)
    }

    added = 0
    for item in new_items:
        if not isinstance(item, dict):
            continue
        key = (item.get("abd_nieuws_url"), item.get("person_name"), item.get("post_id"))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        existing.append(item)
        added += 1

    if added == 0:
        return 0

    staging_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=staging_path.name + ".", dir=str(staging_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, staging_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return added


def write_empty_marker(staging_path: Path, identifier: str, abd_url: str | None) -> None:
    """Schrijf een marker-record voor artikelen zonder benoemingen.

    Zonder marker zou de fetcher dit artikel iedere run opnieuw door claude halen.
    De marker is GEEN proposal: ``event_type=overig``, ``person_name=null``,
    ``confidence=0.0``. Validators die personen mergen moeten dit overslaan.
    """
    marker = {
        "abd_nieuws_url": abd_url,
        "person_name": None,
        "post_id": None,
        "event_type": "geen_benoeming",
        "confidence": 0.0,
        "confidence_reasoning": "Bericht bevat geen benoemings/ontslag/verlenging-event.",
        "source_identifier": identifier,
        "needs_review": False,
    }
    merge_into_staging(staging_path, [marker])


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(
            "usage: _backfill_abd_nieuws_validate.py <claude-out> <html-path> <staging-path> <identifier>",
            file=sys.stderr,
        )
        return 2

    claude_out = Path(argv[1])
    html_path = Path(argv[2])
    staging_path = Path(argv[3])
    identifier = argv[4]

    text = claude_out.read_text(encoding="utf-8", errors="replace")
    proposals = extract_json_array(text)

    # Probeer canonical URL uit HTML te halen voor de marker.
    html_text = html_path.read_text(encoding="utf-8", errors="replace")
    abd_url: str | None = None
    m = re.search(
        r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', html_text
    ) or re.search(
        r'<meta[^>]+name="DCTERMS\.identifier"[^>]+content="([^"]+)"', html_text
    )
    if m:
        abd_url = m.group(1).strip()

    if not isinstance(proposals, list):
        print(f"validate {identifier}: geen JSON-array (type={type(proposals)})", file=sys.stderr)
        print("n_valid=0 n_rejected=0 added=0", end="")
        return 0

    if not proposals:
        # Markeren als verwerkt, anders re-runt fase 2 dit artikel altijd opnieuw.
        write_empty_marker(staging_path, identifier, abd_url)
        print("n_valid=0 n_rejected=0 added=0 marker=1", end="")
        return 0

    valid: list[dict] = []
    rejected = 0
    for prop in proposals:
        if not isinstance(prop, dict):
            rejected += 1
            continue
        # Verplichte velden volgens skill.
        if not prop.get("abd_nieuws_url"):
            # Vul in vanuit HTML als de LLM hem heeft laten vallen.
            if abd_url:
                prop["abd_nieuws_url"] = abd_url
            else:
                rejected += 1
                print(f"reject {identifier}: missing abd_nieuws_url", file=sys.stderr)
                continue
        snippet = prop.get("evidence_snippet")
        if not isinstance(snippet, str) or not snippet.strip():
            rejected += 1
            print(f"reject {identifier}: missing evidence_snippet", file=sys.stderr)
            continue
        if not substring_in_html(snippet, html_text):
            rejected += 1
            print(
                f"reject {identifier}: evidence_snippet not in HTML "
                f"(snippet[:80]={snippet[:80]!r})",
                file=sys.stderr,
            )
            continue
        # Confidence-cap 0.85 als geen staatscourant_url.
        stcrt = prop.get("staatscourant_url")
        confidence = prop.get("confidence")
        if isinstance(confidence, (int, float)):
            cf = float(confidence)
            if not (isinstance(stcrt, str) and stcrt.strip()):
                if cf > 0.85:
                    prop["confidence"] = 0.85
                    cur = prop.get("confidence_reasoning") or ""
                    extra = " [auto-capped to 0.85: geen staatscourant_url]"
                    if extra not in cur:
                        prop["confidence_reasoning"] = (cur + extra).strip()
                    cf = 0.85
            if cf < 0.98:
                prop["needs_review"] = True
        prop.setdefault("source_identifier", identifier)
        valid.append(prop)

    if not valid and rejected == 0:
        write_empty_marker(staging_path, identifier, abd_url)
        print("n_valid=0 n_rejected=0 added=0 marker=1", end="")
        return 0

    if not valid:
        # Alle proposals afgewezen. Schrijf GEEN marker zodat we kunnen retryen.
        print(f"n_valid=0 n_rejected={rejected} added=0", end="")
        return 0

    added = merge_into_staging(staging_path, valid)
    print(f"n_valid={len(valid)} n_rejected={rejected} added={added}", end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
