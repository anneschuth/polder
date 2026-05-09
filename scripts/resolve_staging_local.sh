#!/usr/bin/env bash
# resolve_staging_local.sh - lokale runner voor de resolve-staging-proposals skill.
#
# Gebruik:
#   ./scripts/resolve_staging_local.sh <staging.json> [output.json]
#
# Default output: <staging-zonder-extensie>.resolved.json in dezelfde map.
#
# Roept de resolve-staging-proposals skill aan op een staging-bestand uit
# data/_staging/ en schrijft een verrijkte JSON-array naar het output-pad.
# De skill leest data/organisaties/, data/posten/ en data/personen/, maar
# schrijft alleen naar data/_staging/ conform de Polder-regels.

set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: $0 <staging.json> [output.json]" >&2
  exit 2
fi

INPUT="$1"
OUTPUT="${2:-}"

if [ ! -f "$INPUT" ]; then
  echo "resolve_staging_local.sh: staging-bestand niet gevonden: $INPUT" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -z "$OUTPUT" ]; then
  INPUT_DIR="$(cd "$(dirname "$INPUT")" && pwd)"
  BASE="$(basename "$INPUT" .json)"
  OUTPUT="$INPUT_DIR/${BASE}.resolved.json"
fi

mkdir -p "$(dirname "$OUTPUT")"

bash "$SCRIPT_DIR/run_skill.sh" resolve-staging-proposals "$INPUT" "$OUTPUT"

echo "resolve_staging_local.sh: resolved-output geschreven naar $OUTPUT" >&2
