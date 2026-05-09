#!/usr/bin/env bash
# parse_staatscourant_local.sh - lokale runner voor de parse-staatscourant skill.
#
# Gebruik:
#   ./scripts/parse_staatscourant_local.sh <kb.xml> [output.json]
#
# Default output: data/_staging/staatscourant-<YYYY-MM-DD>.json
#
# Roept de parse-staatscourant skill aan op een KB/XML-bestand uit de KOOP-feed
# en schrijft Membership-proposals als JSON naar het staging-pad. Volgens de
# Polder-regels schrijft een LLM nooit direct naar data/personen of
# data/organisaties; alleen naar data/_staging/.
#
# Pre-filter: Voor de claude-aanroep parsen we de XML-titel (officiele-titel)
# en checken of een benoemings/ontslag-marker voorkomt. Geen marker -> lege
# JSON-array, geen LLM-call. Bespaart calls op mandaatbesluiten en regelingen.
#
# Env-vars:
#   POLDER_CLAUDE_MODEL  zie run_skill.sh. Default claude-haiku-4-5.

set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: $0 <kb.xml> [output.json]" >&2
  exit 2
fi

XML_PATH="$1"
OUTPUT="${2:-}"

if [ ! -f "$XML_PATH" ]; then
  echo "parse_staatscourant_local.sh: XML-bestand niet gevonden: $XML_PATH" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -z "$OUTPUT" ]; then
  TODAY="$(date -u +%Y-%m-%d)"
  BASE="$(basename "$XML_PATH" .xml)"
  OUTPUT="$REPO_ROOT/data/_staging/staatscourant-${BASE}-${TODAY}.json"
fi

mkdir -p "$(dirname "$OUTPUT")"

# Pre-filter op KB-titel. Markers (lowercase) overeenkomstig wat de
# parse-staatscourant skill als relevant beschouwt.
PRE_FILTER_PATTERNS="benoeming|ontslag|verlenging|secretaris-generaal|directeur-generaal|inspecteur-generaal|minister|staatssecretaris"

TITLE="$(python3 - "$XML_PATH" <<'PY'
import re
import sys

with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
    content = f.read()

# Pak de eerste tag die plausibel de KB-titel bevat. KOOP-XML gebruikt diverse
# varianten (officiele-titel, citeertitel, intitule, titel). We pakken de
# eerste match en strippen tags.
m = re.search(
    r"<(officiele-titel|citeertitel|intitule|titel|onderwerp)[^>]*>(.*?)</\1>",
    content,
    re.IGNORECASE | re.DOTALL,
)
title = m.group(2) if m else content[:2000]
title = re.sub(r"<[^>]+>", " ", title)
sys.stdout.write(title.lower())
PY
)"

if ! printf '%s' "$TITLE" | grep -qiE "$PRE_FILTER_PATTERNS"; then
  echo "[]" >"$OUTPUT"
  echo "parse_staatscourant_local.sh: pre-filter skip (geen-benoeming-titel), $OUTPUT" >&2
  exit 0
fi

if bash "$SCRIPT_DIR/run_skill.sh" parse-staatscourant "$XML_PATH" "$OUTPUT"; then
  echo "parse_staatscourant_local.sh: proposals geschreven naar $OUTPUT" >&2
else
  rc=$?
  if [ "$rc" -eq 99 ]; then
    echo "parse_staatscourant_local.sh: rate-limit, abort signaal (exit 99)" >&2
    exit 99
  fi
  exit "$rc"
fi
