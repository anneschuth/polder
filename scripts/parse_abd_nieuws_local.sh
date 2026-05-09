#!/usr/bin/env bash
# parse_abd_nieuws_local.sh - lokale runner voor de parse-abd-nieuws skill.
#
# Gebruik:
#   ./scripts/parse_abd_nieuws_local.sh <artikel.html> [output.json]
#
# Default output: data/_staging/abd-nieuws-<YYYY-MM-DD>.json
#
# Roept de parse-abd-nieuws skill aan op een gedownload nieuwsbericht uit
# _cache/abd-nieuws/ en schrijft Membership-proposals als JSON naar het
# staging-pad. Volgens de Polder-regels schrijft een LLM nooit direct naar
# data/personen of data/organisaties; alleen naar data/_staging/.
#
# Pre-filter: Voor de claude-aanroep stript dit script de HTML naar plain text
# en checkt of een van de benoemings/ontslag-markers voorkomt. Zo ja, claude
# wordt aangeroepen. Zo nee, schrijven we een lege JSON-array `[]` en slaan de
# LLM-call over. Bespaart ~30-50% van de calls op typische ABD-nieuwsfeed.
#
# Env-vars:
#   POLDER_CLAUDE_MODEL  zie run_skill.sh. Default claude-haiku-4-5.

set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: $0 <artikel.html> [output.json]" >&2
  exit 2
fi

HTML_PATH="$1"
OUTPUT="${2:-}"

if [ ! -f "$HTML_PATH" ]; then
  echo "parse_abd_nieuws_local.sh: HTML-bestand niet gevonden: $HTML_PATH" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -z "$OUTPUT" ]; then
  TODAY="$(date -u +%Y-%m-%d)"
  BASE="$(basename "$HTML_PATH" .html)"
  OUTPUT="$REPO_ROOT/data/_staging/abd-nieuws-${BASE}-${TODAY}.json"
fi

mkdir -p "$(dirname "$OUTPUT")"

# Pre-filter markers (lowercase). Als geen enkele match, slaan we de LLM-call
# over. De skill zou anders ook `[]` retourneren maar tegen een prijs van
# enkele duizenden tokens per artikel.
PRE_FILTER_PATTERNS="wordt benoemd|is benoemd|wordt per|start als|neemt afscheid|afdelingshoofd|directeur|secretaris-generaal|directeur-generaal|inspecteur-generaal|minister|staatssecretaris|kwartiermaker"

# Strip HTML naar plain text via Python's stdlib HTMLParser. Geen externe deps,
# werkt met system-Python op zowel macOS als Linux.
TEXT="$(python3 - "$HTML_PATH" <<'PY'
import sys
from html.parser import HTMLParser

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip == 0:
            self.parts.append(data)

extractor = TextExtractor()
with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
    extractor.feed(f.read())
sys.stdout.write(" ".join(extractor.parts).lower())
PY
)"

if ! printf '%s' "$TEXT" | grep -qiE "$PRE_FILTER_PATTERNS"; then
  # Geen markers gevonden: schrijf lege array en sla claude-call over.
  echo "[]" >"$OUTPUT"
  echo "parse_abd_nieuws_local.sh: pre-filter skip (geen-benoeming-marker), $OUTPUT" >&2
  exit 0
fi

if bash "$SCRIPT_DIR/run_skill.sh" parse-abd-nieuws "$HTML_PATH" "$OUTPUT"; then
  echo "parse_abd_nieuws_local.sh: proposals geschreven naar $OUTPUT" >&2
else
  rc=$?
  if [ "$rc" -eq 99 ]; then
    echo "parse_abd_nieuws_local.sh: rate-limit, abort signaal (exit 99)" >&2
    exit 99
  fi
  exit "$rc"
fi
