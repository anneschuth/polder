#!/usr/bin/env bash
set -euo pipefail
HTML="$1"
STAGING_DIR="$2"
IN_PLACE="$3"
LOG="$4"
RESULT="$(uv run --project "/Users/anneschuth/polder" python "/Users/anneschuth/polder/scripts/_reparse_abd_nieuws_one.py" "$HTML" "$STAGING_DIR" "$IN_PLACE" 2>>"$LOG" || echo '{"status":"fail","reason":"wrapper"}')"
echo "$RESULT" >>"$LOG"
echo "$RESULT"
