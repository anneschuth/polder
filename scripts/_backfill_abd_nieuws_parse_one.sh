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
#
# Pre-filter: HTML zonder benoemings/ontslag-markers wordt overgeslagen zonder
# claude-aanroep. Voor de bulk-backfill bespaart dat ~30-50% van de calls.
#
# Env-vars:
#   POLDER_CLAUDE_MODEL  default claude-haiku-4-5.
#
# Exit-codes:
#   0   succes (ook bij skip of validate-fail; failures gaan naar log)
#   99  rate-limit gedetecteerd; bovenliggend script kan de batch afbreken.

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

CLAUDE_MODEL="${POLDER_CLAUDE_MODEL:-claude-haiku-4-5}"

# Pre-filter: strip HTML naar plain text en check op markers. Geen marker ->
# overslaan zonder LLM-call.
PRE_FILTER_PATTERNS="wordt benoemd|is benoemd|wordt per|start als|neemt afscheid|afdelingshoofd|directeur|secretaris-generaal|directeur-generaal|inspecteur-generaal|minister|staatssecretaris|kwartiermaker"

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
  echo "skip-pre-filter $BASENAME" >&2
  exit 0
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
TMP_ERR="$(mktemp -t parse_abdn_err.XXXXXX)"
trap 'rm -f "$TMP_OUT" "$TMP_ERR"' EXIT

if ! (cd "$REPO_ROOT" && printf '%s' "$PROMPT" | timeout 180 "$CLAUDE_BIN" \
        --print \
        --model "$CLAUDE_MODEL" \
        --permission-mode bypassPermissions \
        --allowedTools "Read" \
        --output-format text \
        >"$TMP_OUT" 2>"$TMP_ERR"); then
  cat "$TMP_ERR" >>"$FAILURES_LOG"
  # Rate-limit gedetecteerd? Stuur signaal omhoog.
  if grep -qiE "hit your (usage |rate )?limit|rate[ -]limit|usage limit reached|429" "$TMP_OUT" "$TMP_ERR" 2>/dev/null; then
    echo "$(date -u +%FT%TZ) RATE_LIMIT $HTML_PATH" >>"$FAILURES_LOG"
    exit 99
  fi
  echo "$(date -u +%FT%TZ) CLAUDE_FAIL $HTML_PATH" >>"$FAILURES_LOG"
  exit 0
fi

# Ook bij rc=0: scan output op rate-limit-tekst (de claude-CLI exit soms 0
# maar plakt de rate-limit-melding in de body).
if grep -qiE "hit your (usage |rate )?limit|rate[ -]limit|usage limit reached|429" "$TMP_OUT" 2>/dev/null; then
  echo "$(date -u +%FT%TZ) RATE_LIMIT $HTML_PATH" >>"$FAILURES_LOG"
  exit 99
fi

JSON="$(uv run --project "$REPO_ROOT" python "$SCRIPT_DIR/_backfill_abd_nieuws_validate.py" \
          "$TMP_OUT" "$HTML_PATH" "$OUTFILE" "$BASENAME" 2>>"$FAILURES_LOG")" || {
  echo "$(date -u +%FT%TZ) VALIDATE_FAIL $HTML_PATH" >>"$FAILURES_LOG"
  exit 0
}

if [ -n "${JSON}" ]; then
  echo "ok $BASENAME -> $OUTFILE: $JSON" >&2
fi
