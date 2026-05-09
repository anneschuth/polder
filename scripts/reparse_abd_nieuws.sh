#!/usr/bin/env bash
# reparse_abd_nieuws.sh - draai parse-abd-nieuws skill (v0.4.0+) opnieuw op
# bestaande gedownloade HTML's. Vergelijk de nieuwe proposals met de bestaande
# staging-files; schrijf alleen verschillen.
#
# Doel: confidence-cijfers updaten naar v0.4.0-regels (floor 0.85, cap 0.94)
# zonder de volle backfill kosten te maken.
#
# Gebruik:
#   bash scripts/reparse_abd_nieuws.sh                       # volledige reparse
#   bash scripts/reparse_abd_nieuws.sh --parallel 8          # 8 parallelle calls
#   bash scripts/reparse_abd_nieuws.sh --limit 5             # sanity, alleen 5 HTMLs
#   bash scripts/reparse_abd_nieuws.sh --in-place            # overschrijf staging-files
#   bash scripts/reparse_abd_nieuws.sh --since 2026-01-01    # alleen recente HTMLs
#
# Default schrijfgedrag: per maand-staging-file `<staging>.v0.4.0.json` aanmaken
# met alleen de proposals waarvan confidence/confidence_reasoning veranderd is.
# Met --in-place wordt de bestaande staging-file daadwerkelijk overschreven
# (het oude bestand verhuist naar `<staging>.v0.3.0.bak`).
#
# Conformiteit met Polder-regels:
#   - LLM schrijft alleen naar data/_staging/.
#   - Substring-check op evidence_snippet bij elke proposal.
#   - Idempotent: ongewijzigde proposals worden niet herschreven.
#   - Logt naar stderr; structured log-regels naar _cache/abd-nieuws/reparse.log.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PARALLEL=5
LIMIT=0
IN_PLACE=0
SINCE=""
UNTIL=""
CACHE_DIR="$REPO_ROOT/_cache/abd-nieuws"
STAGING_DIR="$REPO_ROOT/data/_staging"
REPARSE_LOG="$CACHE_DIR/reparse.log"

print_help() {
  sed -n '2,30p' "$0"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --parallel)  PARALLEL="$2"; shift 2;;
    --limit)     LIMIT="$2"; shift 2;;
    --in-place)  IN_PLACE=1; shift;;
    --since)     SINCE="$2"; shift 2;;
    --until)     UNTIL="$2"; shift 2;;
    -h|--help)   print_help; exit 0;;
    *) echo "Onbekende optie: $1" >&2; exit 2;;
  esac
done

mkdir -p "$CACHE_DIR" "$STAGING_DIR"
: >"$REPARSE_LOG.tmp" && mv "$REPARSE_LOG.tmp" "$REPARSE_LOG" || true

if [ ! -d "$CACHE_DIR" ]; then
  echo "reparse_abd_nieuws.sh: cache-dir niet gevonden: $CACHE_DIR" >&2
  exit 1
fi

CLAUDE_BIN="${CLAUDE_BIN:-claude}"
if ! command -v "$CLAUDE_BIN" >/dev/null 2>&1; then
  if [ -x "/Users/anneschuth/.local/bin/claude" ]; then
    CLAUDE_BIN="/Users/anneschuth/.local/bin/claude"
  else
    echo "reparse_abd_nieuws.sh: claude CLI niet gevonden in PATH (CLAUDE_BIN=$CLAUDE_BIN)" >&2
    exit 1
  fi
fi
export CLAUDE_BIN

# ---------------------------------------------------------------------------
# Bouw lijst HTML-paden, gefilterd op since/until/limit.
# ---------------------------------------------------------------------------

FILTER_PY="$SCRIPT_DIR/_reparse_abd_nieuws_filter.py"
cat >"$FILTER_PY" <<'PYEOF'
"""Filter input-paden op since/until/limit en sorteer."""
from __future__ import annotations
import sys, re
from pathlib import Path

since = (sys.argv[1] if len(sys.argv) > 1 else "") or None
until = (sys.argv[2] if len(sys.argv) > 2 else "") or None
limit = int((sys.argv[3] if len(sys.argv) > 3 else "0") or 0)

date_re = re.compile(r"-(\d{4}-\d{2}-\d{2})$")
out: list[str] = []
for line in sys.stdin:
    p = line.strip()
    if not p:
        continue
    stem = Path(p).stem
    m = date_re.search(stem)
    if not m:
        continue
    d = m.group(1)
    if since and d < since:
        continue
    if until and d > until:
        continue
    out.append(p)

out.sort()
if limit > 0:
    out = out[:limit]
for p in out:
    print(p)
PYEOF

filter_list() {
  # stdin: pad-per-regel; stdout: gefilterde lijst.
  local since="$1" until="$2" limit="$3"
  uv run --project "$REPO_ROOT" python "$FILTER_PY" "$since" "$until" "$limit"
}

echo "==> reparse_abd_nieuws.sh: scan ${CACHE_DIR}" >&2

HTML_LIST="$(find "$CACHE_DIR" -maxdepth 1 -name '*.html' -print | filter_list "$SINCE" "$UNTIL" "$LIMIT")"
TOTAL="$(printf '%s\n' "$HTML_LIST" | grep -c . || true)"
if [ "$TOTAL" -eq 0 ]; then
  echo "    geen HTMLs te verwerken" >&2
  exit 0
fi

echo "    te reparsen: ${TOTAL} HTMLs (parallel=${PARALLEL}, in_place=${IN_PLACE})" >&2

# ---------------------------------------------------------------------------
# Per-HTML helper: parse via claude, vergelijk met bestaand proposal,
# schrijf alleen als afwijkend. Wordt parallel via xargs aangeroepen.
# ---------------------------------------------------------------------------

REPARSE_ONE_PY="$SCRIPT_DIR/_reparse_abd_nieuws_one.py"
cat >"$REPARSE_ONE_PY" <<'PYEOF'
"""Helper voor reparse_abd_nieuws.sh.

Voor één HTML:
  1. Bepaal de bijbehorende staging-file uit de datum-suffix in de bestandsnaam.
  2. Roep `claude --print` aan met de parse-abd-nieuws skill.
  3. Vergelijk de nieuwe proposals met de bestaande proposals voor dit
     source_identifier in de staging-file.
  4. Schrijf naar `<staging>.v0.4.0.json` (default) of overschrijf de staging-file
     met `--in-place`. Het oude bestand verhuist naar `<staging>.v0.3.0.bak`.

Beschrijft veranderingen op stdout in JSON-Lines:
  {"identifier": ..., "status": "unchanged"|"changed"|"new"|"empty"|"fail",
   "old_confidences": [...], "new_confidences": [...], "delta": [...]}.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ENTITIES = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&quot;": '"', "&apos;": "'", "&nbsp;": " ",
}

PROMPT = """Pas de skill .claude/skills/parse-abd-nieuws/SKILL.md (v0.4.0) toe.

Stappen:
1. Lees {html_path} met de Read-tool.
2. Identificeer benoemingen, ontslagen, verlengingen of aankondigingen.
3. Bouw proposals zoals beschreven in de skill, met confidence volgens de
   "Confidence-bepaling"-sectie (floor 0.85 bij vier expliciete kernfeiten,
   cap 0.94 zonder staatscourant_url, "voorlopig"-boete -0.05 maar nooit
   onder de floor).
4. evidence_snippet MOET letterlijke substring zijn van de HTML.

Output ALLEEN de JSON-array op stdout. Geen markdown-fences, geen uitleg.
Begin met '[' en eindig met ']'."""


def decode_entities(s: str) -> str:
    out = s
    for k, v in ENTITIES.items():
        out = out.replace(k, v)
    return out


def extract_json_array(text: str) -> list:
    text = text.strip()
    if not text:
        return []
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


def call_claude(html_path: Path, repo_root: Path) -> list:
    claude_bin = os.environ.get("CLAUDE_BIN", "claude")
    prompt = PROMPT.format(html_path=str(html_path))
    try:
        proc = subprocess.run(
            [
                claude_bin,
                "--print",
                "--model", "claude-sonnet-4-6",
                "--permission-mode", "bypassPermissions",
                "--allowedTools", "Read",
                "--output-format", "text",
            ],
            input=prompt,
            text=True,
            capture_output=True,
            timeout=180,
            cwd=str(repo_root),
        )
    except subprocess.TimeoutExpired:
        return []
    if proc.returncode != 0:
        return []
    return extract_json_array(proc.stdout)


def load_staging(staging_path: Path) -> list:
    if not staging_path.exists():
        return []
    try:
        data = json.loads(staging_path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def proposal_key(prop: dict) -> tuple:
    return (
        prop.get("abd_nieuws_url"),
        prop.get("person_name"),
        prop.get("post_id"),
    )


def proposals_differ(old: dict, new: dict) -> bool:
    fields = ("confidence", "confidence_reasoning")
    for f in fields:
        if old.get(f) != new.get(f):
            return True
    return False


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: _reparse_abd_nieuws_one.py <html-path> <staging-dir> <in-place 0|1>",
              file=sys.stderr)
        return 2
    html_path = Path(argv[1])
    staging_dir = Path(argv[2])
    in_place = argv[3] == "1"
    repo_root = Path(__file__).resolve().parent.parent

    identifier = html_path.stem
    m = re.search(r"-(\d{4})-(\d{2})-(\d{2})$", identifier)
    if not m:
        print(json.dumps({"identifier": identifier, "status": "fail", "reason": "no-date"}))
        return 0
    year, month, _ = m.group(1), m.group(2), m.group(3)
    staging_path = staging_dir / f"abd-nieuws-{year}-{month}.json"

    html_text = html_path.read_text(encoding="utf-8", errors="replace")

    new_proposals = call_claude(html_path, repo_root)
    if not isinstance(new_proposals, list):
        print(json.dumps({"identifier": identifier, "status": "fail", "reason": "no-array"}))
        return 0

    valid_new: list[dict] = []
    for prop in new_proposals:
        if not isinstance(prop, dict):
            continue
        snippet = prop.get("evidence_snippet")
        if not isinstance(snippet, str) or not snippet.strip():
            continue
        if not substring_in_html(snippet, html_text):
            continue
        prop.setdefault("source_identifier", identifier)
        prop.setdefault("needs_review", float(prop.get("confidence", 0)) < 0.95)
        valid_new.append(prop)

    if not valid_new:
        print(json.dumps({"identifier": identifier, "status": "empty"}))
        return 0

    existing = load_staging(staging_path)
    existing_for_id = [p for p in existing if isinstance(p, dict)
                       and p.get("source_identifier") == identifier]

    old_by_key = {proposal_key(p): p for p in existing_for_id}
    delta: list[dict] = []
    for new in valid_new:
        old = old_by_key.get(proposal_key(new))
        if old is None:
            delta.append({
                "person_name": new.get("person_name"),
                "old_confidence": None,
                "new_confidence": new.get("confidence"),
            })
        elif proposals_differ(old, new):
            delta.append({
                "person_name": new.get("person_name"),
                "old_confidence": old.get("confidence"),
                "new_confidence": new.get("confidence"),
            })

    if not delta:
        print(json.dumps({
            "identifier": identifier,
            "status": "unchanged",
            "new_confidences": [p.get("confidence") for p in valid_new],
        }))
        return 0

    if in_place:
        # Backup oude staging eenmalig.
        backup_path = staging_path.with_suffix(".v0.3.0.bak")
        if staging_path.exists() and not backup_path.exists():
            shutil.copy2(staging_path, backup_path)

        # Vervang proposals voor dit identifier.
        kept = [p for p in existing if not (isinstance(p, dict)
                and p.get("source_identifier") == identifier)]
        merged = kept + valid_new
        write_atomic(
            staging_path,
            json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        )
        target = str(staging_path)
    else:
        # Schrijf naar v0.4.0-side-file. Append-merge.
        side_path = staging_path.with_suffix(".v0.4.0.json")
        side_existing = load_staging(side_path)
        kept = [p for p in side_existing if not (isinstance(p, dict)
                and p.get("source_identifier") == identifier)]
        merged = kept + valid_new
        write_atomic(
            side_path,
            json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        )
        target = str(side_path)

    print(json.dumps({
        "identifier": identifier,
        "status": "changed",
        "delta": delta,
        "target": target,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
PYEOF

# ---------------------------------------------------------------------------
# Per-HTML wrapper: roept python-helper aan, logt naar reparse.log.
# ---------------------------------------------------------------------------

REPARSE_WRAPPER="$SCRIPT_DIR/_reparse_abd_nieuws_one_wrap.sh"
cat >"$REPARSE_WRAPPER" <<WRAPEOF
#!/usr/bin/env bash
set -euo pipefail
HTML="\$1"
STAGING_DIR="\$2"
IN_PLACE="\$3"
LOG="\$4"
RESULT="\$(uv run --project "$REPO_ROOT" python "$REPARSE_ONE_PY" "\$HTML" "\$STAGING_DIR" "\$IN_PLACE" 2>>"\$LOG" || echo '{"status":"fail","reason":"wrapper"}')"
echo "\$RESULT" >>"\$LOG"
echo "\$RESULT"
WRAPEOF
chmod +x "$REPARSE_WRAPPER"

# ---------------------------------------------------------------------------
# Run parallel.
# ---------------------------------------------------------------------------

printf '%s\n' "$HTML_LIST" \
  | xargs -P "$PARALLEL" -I {} bash "$REPARSE_WRAPPER" "{}" "$STAGING_DIR" "$IN_PLACE" "$REPARSE_LOG" \
  | tee "$REPARSE_LOG.live" \
  || true

SUMMARY_PY="$SCRIPT_DIR/_reparse_abd_nieuws_summary.py"
cat >"$SUMMARY_PY" <<'PYEOF'
"""Vat de reparse-log samen op stderr."""
from __future__ import annotations
import json, sys
from collections import Counter
from pathlib import Path

log = Path(sys.argv[1])
if not log.exists():
    print("(geen log)", file=sys.stderr)
    raise SystemExit(0)

stats: Counter[str] = Counter()
deltas: list[dict] = []
for line in log.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or not line.startswith("{"):
        continue
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        continue
    stats[rec.get("status", "?")] += 1
    if rec.get("status") == "changed":
        deltas.extend(rec.get("delta", []))

print(f"    statussen: {dict(stats)}", file=sys.stderr)
if deltas:
    moved_up = sum(1 for d in deltas if (d.get("new_confidence") or 0) > (d.get("old_confidence") or 0))
    moved_dn = sum(1 for d in deltas if (d.get("new_confidence") or 0) < (d.get("old_confidence") or 0))
    print(f"    delta confidences: {len(deltas)} (omhoog {moved_up}, omlaag {moved_dn})", file=sys.stderr)
PYEOF

echo "==> reparse klaar." >&2
uv run --project "$REPO_ROOT" python "$SUMMARY_PY" "$REPARSE_LOG"
