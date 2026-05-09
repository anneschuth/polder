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

bash "$SCRIPT_DIR/run_skill.sh" parse-staatscourant "$XML_PATH" "$OUTPUT"

echo "parse_staatscourant_local.sh: proposals geschreven naar $OUTPUT" >&2
