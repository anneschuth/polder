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

bash "$SCRIPT_DIR/run_skill.sh" parse-abd-nieuws "$HTML_PATH" "$OUTPUT"

echo "parse_abd_nieuws_local.sh: proposals geschreven naar $OUTPUT" >&2
