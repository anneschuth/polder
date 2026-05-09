#!/usr/bin/env bash
# backfill_abd_nieuws.sh - Backfill van ABD-nieuwsberichten.
#
# Twee fases:
#   1. Sitemap-walk + download artikel-HTML naar _cache/abd-nieuws/.
#   2. Parse iedere HTML met de parse-abd-nieuws skill via `claude --print`.
#      Schrijft Membership-proposals naar data/_staging/abd-nieuws-<YYYY-MM>.json.
#
# Default: alle nieuws-items vanaf 2018-01-01.
#
# Gebruik:
#   ./scripts/backfill_abd_nieuws.sh                                      # alles 2018-heden
#   ./scripts/backfill_abd_nieuws.sh --since 2024-01-01                   # vanaf 2024
#   ./scripts/backfill_abd_nieuws.sh --since 2024-01-01 --until 2026-05-09
#   ./scripts/backfill_abd_nieuws.sh --parallel 8                         # 8 parallelle parsers
#   ./scripts/backfill_abd_nieuws.sh --phase 1                            # alleen download
#   ./scripts/backfill_abd_nieuws.sh --phase 2                            # alleen parse
#   ./scripts/backfill_abd_nieuws.sh --max-claude-calls 50                # cap voor sanity-run
#
# Conformiteit met Polder-regels:
#   - LLM schrijft alleen naar data/_staging/, nooit direct naar data/personen/.
#   - Substring-check op evidence_snippet wordt na elke parse uitgevoerd.
#   - Cost-cap optie voorkomt ongelimiteerde Sonnet-calls.
#   - Idempotent en restart-veilig: zowel HTML-cache als staging-files worden
#     gecheckt voordat een bewerking herhaald wordt.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Defaults
SINCE="2018-01-01"
UNTIL="$(date -u +%Y-%m-%d)"
PARALLEL=5
PHASE="both"
MAX_CLAUDE_CALLS=200
CACHE_DIR="$REPO_ROOT/_cache/abd-nieuws"
STAGING_DIR="$REPO_ROOT/data/_staging"
LOG_FAILURES="$CACHE_DIR/failures.log"

print_help() {
  sed -n '2,30p' "$0"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --since)             SINCE="$2"; shift 2;;
    --until)             UNTIL="$2"; shift 2;;
    --parallel)          PARALLEL="$2"; shift 2;;
    --phase)             PHASE="$2"; shift 2;;
    --max-claude-calls)  MAX_CLAUDE_CALLS="$2"; shift 2;;
    -h|--help)           print_help; exit 0;;
    *) echo "Onbekende optie: $1" >&2; exit 2;;
  esac
done

mkdir -p "$CACHE_DIR" "$STAGING_DIR"
touch "$LOG_FAILURES"

# ---------------------------------------------------------------------------
# Fase 1: sitemap-walk + download artikel-HTML
# ---------------------------------------------------------------------------

phase1_download() {
  local since="$1"
  echo "==> Fase 1: download ABD-nieuws-HTMLs vanaf ${since}" >&2
  uv run --project "$REPO_ROOT" polder fetch abd-nieuws --deep --since "$since"
}

# ---------------------------------------------------------------------------
# Fase 2: parse via parse-abd-nieuws skill (claude --print, parallel)
# ---------------------------------------------------------------------------

phase2_parse() {
  local since="$1" until="$2"
  echo "==> Fase 2: parse ABD-nieuws ${since} tot ${until}, parallel=${PARALLEL}, cap=${MAX_CLAUDE_CALLS}" >&2

  local html_list
  html_list="$(uv run --project "$REPO_ROOT" python "$SCRIPT_DIR/_backfill_abd_nieuws_list.py" \
                "$CACHE_DIR" "$since" "$until" "$STAGING_DIR" "$MAX_CLAUDE_CALLS")"

  local total
  total="$(printf '%s\n' "$html_list" | grep -c . || true)"
  if [ "$total" -eq 0 ]; then
    echo "    geen HTMLs te parsen voor ${since}..${until}" >&2
    return 0
  fi
  echo "    te parsen: ${total} HTMLs" >&2

  printf '%s\n' "$html_list" \
    | xargs -P "$PARALLEL" -I {} bash "$SCRIPT_DIR/_backfill_abd_nieuws_parse_one.sh" "{}" "$STAGING_DIR" "$LOG_FAILURES" \
    || true
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

case "$PHASE" in
  1)    phase1_download "$SINCE";;
  2)    phase2_parse "$SINCE" "$UNTIL";;
  both) phase1_download "$SINCE"; phase2_parse "$SINCE" "$UNTIL";;
  *) echo "Onbekende --phase: $PHASE (kies 1, 2 of both)" >&2; exit 2;;
esac

echo "==> Klaar. Failures: $(wc -l <"$LOG_FAILURES" 2>/dev/null || echo 0)" >&2
