#!/usr/bin/env bash
# parse_organogram_local.sh - lokale runner voor de parse-organogram skill.
#
# Gebruik:
#   ./scripts/parse_organogram_local.sh <organogram.pdf> <ministerie-slug> [output.json]
#
# Default output: data/_staging/organogram-<ministerie>-<YYYY-MM-DD>.json
#
# Roept de parse-organogram skill aan op een PDF uit de ABD-cache. PDF wordt
# als pad doorgegeven aan claude -p; de skill leest het bestand zelf via de
# Read-tool.
#
# Default model: claude-opus-4-7. Een organogram is een visueel-zwaar PDF en
# Haiku haalt de hierarchische relaties niet betrouwbaar terug. Caller kan via
# POLDER_CLAUDE_MODEL overrulen, maar we gokken hier zelf op Opus omdat de
# foutkosten van een verkeerde organogramparse hoger zijn dan de extra LLM-cost.

set -euo pipefail

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "usage: $0 <organogram.pdf> <ministerie-slug> [output.json]" >&2
  exit 2
fi

PDF_PATH="$1"
MIN_SLUG="$2"
OUTPUT="${3:-}"

if [ ! -f "$PDF_PATH" ]; then
  echo "parse_organogram_local.sh: PDF-bestand niet gevonden: $PDF_PATH" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -z "$OUTPUT" ]; then
  TODAY="$(date -u +%Y-%m-%d)"
  OUTPUT="$REPO_ROOT/data/_staging/organogram-${MIN_SLUG}-${TODAY}.json"
fi

mkdir -p "$(dirname "$OUTPUT")"

# Resolve PDF naar absoluut pad zodat claude -p het kan vinden ongeacht cwd.
PDF_ABS="$(cd "$(dirname "$PDF_PATH")" && pwd)/$(basename "$PDF_PATH")"

# Voor parse-organogram willen we ook de ministerie-slug meegeven. We bouwen
# een wrapper-prompt die het pad EN de slug bevat.
TMP_INPUT="$(mktemp -t polder-parse-organogram.XXXXXX)"
trap 'rm -f "$TMP_INPUT"' EXIT

cat >"$TMP_INPUT" <<EOF
ministerie_slug: ${MIN_SLUG}
pdf_pad: ${PDF_ABS}

Lees de PDF op pdf_pad met de Read-tool. Verwerk volgens de parse-organogram
skill. Voeg de ministerie_slug toe aan elke proposal in het output-record waar
relevant. Output uitsluitend JSON naar stdout.
EOF

# Override model alleen als caller geen expliciete keuze heeft gemaakt.
if [ -z "${POLDER_CLAUDE_MODEL:-}" ]; then
  export POLDER_CLAUDE_MODEL="claude-opus-4-7"
fi

if bash "$SCRIPT_DIR/run_skill.sh" parse-organogram "$TMP_INPUT" "$OUTPUT"; then
  echo "parse_organogram_local.sh: proposals geschreven naar $OUTPUT (model=$POLDER_CLAUDE_MODEL)" >&2
else
  rc=$?
  if [ "$rc" -eq 99 ]; then
    echo "parse_organogram_local.sh: rate-limit, abort signaal (exit 99)" >&2
    exit 99
  fi
  exit "$rc"
fi
