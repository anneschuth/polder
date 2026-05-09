#!/usr/bin/env python
"""Helper voor backfill_staatscourant.sh, fase 2.

Valideert de claude-output van een single-KB parse-call en mergeert geldige
proposals atomisch in de maand-staging-file.

Args:
    sys.argv[1]  pad naar de claude stdout (text-output, hopelijk JSON-array)
    sys.argv[2]  pad naar de bron-XML (voor substring-check)
    sys.argv[3]  pad naar de doel-staging-file (data/_staging/staatscourant-YYYY-MM.json)
    sys.argv[4]  identifier van het KB (basename zonder .xml)

Print op stdout een 1-regel-summary: "n_valid=<x> n_rejected=<y>".
Print op stderr details bij rejecties.
Exit non-zero alleen bij echte fouten (geen JSON, geen array).
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

# evidence_snippet moet als letterlijke substring in de XML voorkomen.
# We doen text-vergelijking op zowel raw bytes als op gestripte XML-tekst,
# zodat snippets die binnen een <al>...</al> staan ook valideren ondanks
# whitespace-verschillen. Default: strenge raw-substring check.
ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'"}


def decode_entities(s: str) -> str:
    out = s
    for k, v in ENTITIES.items():
        out = out.replace(k, v)
    return out


def extract_json_array(text: str) -> list[Any]:
    """Probeer een JSON-array uit `text` te halen.

    Strategieën, in volgorde:
    1. ``json.loads(text)`` op de hele tekst.
    2. Pak het eerste ``[ ... ]`` blok en parse dat.
    """
    text = text.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    # Eerste array-blok zoeken via braces-counting.
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


def substring_in_xml(snippet: str, xml_text: str) -> bool:
    """Strict-on-best-effort substring check.

    1. Raw substring in xml_text.
    2. After entity-decoding xml_text.
    3. Normalize witte regels en tabs naar enkele spatie aan beide kanten.
    """
    if not snippet:
        return False
    if snippet in xml_text:
        return True
    decoded = decode_entities(xml_text)
    if snippet in decoded:
        return True
    # Whitespace-normalize beide kanten.
    norm_snip = re.sub(r"\s+", " ", snippet).strip()
    norm_xml = re.sub(r"\s+", " ", decoded).strip()
    return norm_snip in norm_xml


def merge_into_staging(staging_path: Path, new_items: list[dict]) -> int:
    """Voeg new_items toe aan staging_path. Idempotent op (decision_reference, person_name).

    Atomic write: temp + rename. Returnt aantal toegevoegd.
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
        (item.get("decision_reference"), item.get("person_name"), item.get("post_id"))
        for item in existing
        if isinstance(item, dict)
    }

    added = 0
    for item in new_items:
        if not isinstance(item, dict):
            continue
        key = (item.get("decision_reference"), item.get("person_name"), item.get("post_id"))
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


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(
            "usage: _backfill_staatscourant_validate.py <claude-out> <xml-path> <staging-path> <identifier>",
            file=sys.stderr,
        )
        return 2

    claude_out = Path(argv[1])
    xml_path = Path(argv[2])
    staging_path = Path(argv[3])
    identifier = argv[4]

    text = claude_out.read_text(encoding="utf-8", errors="replace")
    proposals = extract_json_array(text)

    if not isinstance(proposals, list):
        print(f"validate {identifier}: geen JSON-array (type={type(proposals)})", file=sys.stderr)
        print("n_valid=0 n_rejected=0", end="")
        return 0

    if not proposals:
        # Geen relevante benoeming, dat is OK.
        print("n_valid=0 n_rejected=0", end="")
        return 0

    xml_text = xml_path.read_text(encoding="utf-8", errors="replace")

    valid: list[dict] = []
    rejected = 0
    for prop in proposals:
        if not isinstance(prop, dict):
            rejected += 1
            continue
        snippet = prop.get("evidence_snippet")
        if not isinstance(snippet, str) or not snippet.strip():
            rejected += 1
            print(f"reject {identifier}: missing evidence_snippet", file=sys.stderr)
            continue
        if not substring_in_xml(snippet, xml_text):
            rejected += 1
            print(
                f"reject {identifier}: evidence_snippet not found in XML "
                f"(snippet[:80]={snippet[:80]!r})",
                file=sys.stderr,
            )
            continue
        # Confidence-verlaging: < 0.98 -> needs-review.
        confidence = prop.get("confidence")
        if isinstance(confidence, (int, float)) and float(confidence) < 0.98:
            prop["needs_review"] = True
        # Voeg source_identifier toe voor dedup-tracking.
        prop.setdefault("source_identifier", identifier)
        valid.append(prop)

    added = merge_into_staging(staging_path, valid)
    print(f"n_valid={len(valid)} n_rejected={rejected} added={added}", end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
