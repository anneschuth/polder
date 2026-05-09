#!/usr/bin/env bash
# _backfill_abd_nieuws_parse_one.sh - parse één ABD-nieuws-HTML via claude --print.
#
# Aangeroepen door backfill_abd_nieuws.sh via xargs -P.
#
# Args:
#   $1  pad naar de HTML (binnen _cache/abd-nieuws/<slug>-<YYYY-MM-DD>.html)
#   $2  staging-dir (bv. data/_staging)
#   $3  failures-log
#
# Output: append-merge naar data/_staging/abd-nieuws-<YYYY-MM>.json (JSON-array).
# Substring-check wordt na de claude-call uitgevoerd: evidence_snippet MOET
# letterlijk in de HTML staan, anders wordt het proposal afgewezen.

set -euo pipefail

HTML_PATH="$1"
STAGING_DIR="$2"
FAILURES_LOG="$3"

if [ ! -f "$HTML_PATH" ]; then
  echo "$(date -u +%FT%TZ) MISSING $HTML_PATH" >>"$FAILURES_LOG"
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

BASENAME="$(basename "$HTML_PATH" .html)"
# Datum-suffix uit de bestandsnaam (-YYYY-MM-DD aan het einde).
if [[ "$BASENAME" =~ -([0-9]{4})-([0-9]{2})-([0-9]{2})$ ]]; then
  YEAR="${BASH_REMATCH[1]}"
  MONTH="${BASH_REMATCH[2]}"
else
  echo "$(date -u +%FT%TZ) BAD_NAME $HTML_PATH" >>"$FAILURES_LOG"
  exit 0
fi
OUTFILE="$STAGING_DIR/abd-nieuws-${YEAR}-${MONTH}.json"

mkdir -p "$STAGING_DIR"

CLAUDE_BIN="${CLAUDE_BIN:-claude}"
if ! command -v "$CLAUDE_BIN" >/dev/null 2>&1; then
  if [ -x "/Users/anneschuth/.local/bin/claude" ]; then
    CLAUDE_BIN="/Users/anneschuth/.local/bin/claude"
  else
    echo "$(date -u +%FT%TZ) NO_CLAUDE_BIN $HTML_PATH" >>"$FAILURES_LOG"
    exit 0
  fi
fi

PROMPT="$(cat <<PROMPT_EOF
Pas de skill .claude/skills/parse-abd-nieuws/SKILL.md toe.

Stappen:
1. Lees ${HTML_PATH} met de Read-tool. Dit is een ABD-nieuwsbericht.
2. Identificeer benoeming(en), ontslag(en), verlenging(en) of aankondiging(en)
   in het bericht. Negeer pure persberichten die geen concrete persoon-functie-
   koppeling bevatten (jaarverslagen, ABD-blad-aankondigingen, etc.).
3. Als het bericht GEEN benoeming/ontslag/verlenging bevat, retourneer een
   lege JSON-array: [].
4. Anders: bouw een JSON-array van proposals zoals beschreven in de skill.
   Elke proposal bevat tenminste:
     person_name, organization_id, post_id, role, start_date,
     end_date (null bij benoeming), decision_reference, staatscourant_url,
     abd_nieuws_url (verplicht), event_type, confidence, confidence_reasoning,
     evidence_snippet.
5. evidence_snippet MOET een letterlijke substring zijn van de HTML-inhoud.
6. Confidence-cap 0.85 als er GEEN staatscourant_url in het bericht staat.

Output ALLEEN de JSON-array op stdout. Geen markdown-fences, geen uitleg,
geen tool-output, geen begroeting. Begin met '[' en eindig met ']'.
PROMPT_EOF
)"

TMP_OUT="$(mktemp -t parse_abdn.XXXXXX)"
trap 'rm -f "$TMP_OUT"' EXIT

if ! (cd "$REPO_ROOT" && printf '%s' "$PROMPT" | timeout 180 "$CLAUDE_BIN" \
        --print \
        --model claude-sonnet-4-6 \
        --permission-mode bypassPermissions \
        --allowedTools "Read" \
        --output-format text \
        >"$TMP_OUT" 2>>"$FAILURES_LOG"); then
  echo "$(date -u +%FT%TZ) CLAUDE_FAIL $HTML_PATH" >>"$FAILURES_LOG"
  exit 0
fi

JSON="$(uv run --project "$REPO_ROOT" python "$SCRIPT_DIR/_backfill_abd_nieuws_validate.py" \
          "$TMP_OUT" "$HTML_PATH" "$OUTFILE" "$BASENAME" 2>>"$FAILURES_LOG")" || {
  echo "$(date -u +%FT%TZ) VALIDATE_FAIL $HTML_PATH" >>"$FAILURES_LOG"
  exit 0
}

if [ -n "${JSON}" ]; then
  echo "ok $BASENAME -> $OUTFILE: $JSON" >&2
fi
