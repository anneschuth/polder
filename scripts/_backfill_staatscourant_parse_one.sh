#!/usr/bin/env bash
# _backfill_staatscourant_parse_one.sh - parse één Staatscourant-XML via claude -p.
#
# Aangeroepen door backfill_staatscourant.sh via xargs -P.
#
# Args:
#   $1  pad naar de KB-XML (binnen _cache/staatscourant/<year>/<month>/<id>.xml)
#   $2  staging-dir (bv. data/_staging)
#   $3  failures-log
#
# Output: append-merge naar data/_staging/staatscourant-<YYYY-MM>.json (JSON-array).
# Substring-check wordt na de claude-call uitgevoerd: evidence_snippet MOET
# letterlijk in de XML staan, anders wordt het proposal afgewezen.
#
# Pre-filter: KB-titel zonder benoemings/ontslag-marker -> overslaan.
#
# Env-vars:
#   POLDER_CLAUDE_MODEL  default claude-haiku-4-5.
#
# Exit-codes:
#   0   succes (ook bij skip)
#   99  rate-limit gedetecteerd; bovenliggend script kan de batch afbreken.

set -euo pipefail

XML_PATH="$1"
STAGING_DIR="$2"
FAILURES_LOG="$3"

if [ ! -f "$XML_PATH" ]; then
  echo "$(date -u +%FT%TZ) MISSING $XML_PATH" >>"$FAILURES_LOG"
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Year/month uit het pad halen (.../<year>/<month>/<id>.xml).
MONTH_DIR="$(dirname "$XML_PATH")"
YEAR_DIR="$(dirname "$MONTH_DIR")"
YEAR="$(basename "$YEAR_DIR")"
MONTH="$(basename "$MONTH_DIR")"
BASENAME="$(basename "$XML_PATH" .xml)"
OUTFILE="$STAGING_DIR/staatscourant-${YEAR}-${MONTH}.json"

mkdir -p "$STAGING_DIR"

CLAUDE_BIN="${CLAUDE_BIN:-claude}"
if ! command -v "$CLAUDE_BIN" >/dev/null 2>&1; then
  if [ -x "/Users/anneschuth/.local/bin/claude" ]; then
    CLAUDE_BIN="/Users/anneschuth/.local/bin/claude"
  else
    echo "$(date -u +%FT%TZ) NO_CLAUDE_BIN $XML_PATH" >>"$FAILURES_LOG"
    exit 0
  fi
fi

CLAUDE_MODEL="${POLDER_CLAUDE_MODEL:-claude-haiku-4-5}"

# Pre-filter op titel.
PRE_FILTER_PATTERNS="benoeming|ontslag|verlenging|secretaris-generaal|directeur-generaal|inspecteur-generaal|minister|staatssecretaris"

TITLE="$(python3 - "$XML_PATH" <<'PY'
import re
import sys

with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
    content = f.read()

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
  echo "skip-pre-filter $BASENAME" >&2
  exit 0
fi

# Bouw een prompt die expliciet om JSON-array vraagt voor één KB en de skill
# laadt. We pipen de prompt via stdin om quoting-issues te vermijden. De skill
# leest het XML-bestand zelf via Read.
PROMPT="$(cat <<PROMPT_EOF
Pas de skill .claude/skills/parse-staatscourant/SKILL.md toe.

Stappen:
1. Lees ${XML_PATH} met de Read-tool. Dit is een Staatscourant-publicatie (KB).
2. Identificeer benoeming(en) of ontslag(en) van: Secretaris-Generaal, plv SG,
   Directeur-Generaal, plv DG, Inspecteur-Generaal, minister, of staatssecretaris.
3. Als het document GEEN benoeming/ontslag bevat van zo een functie (bv. mandaatbesluit,
   regeling, benoeming voor commissie), retourneer dan een lege array.
4. Anders: bouw een JSON-array van proposals zoals beschreven in de skill.
   Elke proposal bevat person_name, organization_id, post_id, role, start_date,
   end_date (null bij benoeming), decision_reference, staatscourant_url,
   confidence, confidence_reasoning, evidence_snippet.
5. evidence_snippet MOET een letterlijke substring zijn van de XML-inhoud.
6. Mark needs_review=true bij confidence kleiner dan 0.98.

Output ALLEEN de JSON-array. Geen markdown, geen uitleg.
PROMPT_EOF
)"

TMP_OUT="$(mktemp -t parse_stcrt.XXXXXX)"
TMP_ERR="$(mktemp -t parse_stcrt_err.XXXXXX)"
trap 'rm -f "$TMP_OUT" "$TMP_ERR"' EXIT

if ! (cd "$REPO_ROOT" && printf '%s' "$PROMPT" | timeout 120 "$CLAUDE_BIN" \
        --print \
        --model "$CLAUDE_MODEL" \
        --permission-mode bypassPermissions \
        --allowedTools "Read" \
        --output-format text \
        >"$TMP_OUT" 2>"$TMP_ERR"); then
  cat "$TMP_ERR" >>"$FAILURES_LOG"
  if grep -qiE "hit your (usage |rate )?limit|rate[ -]limit|usage limit reached|429" "$TMP_OUT" "$TMP_ERR" 2>/dev/null; then
    echo "$(date -u +%FT%TZ) RATE_LIMIT $XML_PATH" >>"$FAILURES_LOG"
    exit 99
  fi
  echo "$(date -u +%FT%TZ) CLAUDE_FAIL $XML_PATH" >>"$FAILURES_LOG"
  exit 0
fi

if grep -qiE "hit your (usage |rate )?limit|rate[ -]limit|usage limit reached|429" "$TMP_OUT" 2>/dev/null; then
  echo "$(date -u +%FT%TZ) RATE_LIMIT $XML_PATH" >>"$FAILURES_LOG"
  exit 99
fi

# Pak de eerste JSON-array uit de output (Sonnet kan soms tekst eromheen zetten).
JSON="$(uv run --project "$REPO_ROOT" python "$SCRIPT_DIR/_backfill_staatscourant_validate.py" \
          "$TMP_OUT" "$XML_PATH" "$OUTFILE" "$BASENAME" 2>>"$FAILURES_LOG")" || {
  echo "$(date -u +%FT%TZ) VALIDATE_FAIL $XML_PATH" >>"$FAILURES_LOG"
  exit 0
}

# JSON komt al gemerget. We loggen alleen het resultaat.
if [ -n "${JSON}" ]; then
  echo "ok $BASENAME -> $OUTFILE: $JSON" >&2
fi
