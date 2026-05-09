#!/usr/bin/env bash
# backfill_staatscourant.sh - Backfill van benoemings/ontslag-KB's in de Staatscourant.
#
# Twee fases:
#   1. Enumerate KB's via KOOP SRU en download de full XML in _cache/staatscourant/.
#   2. Parse iedere XML met de parse-staatscourant skill via `claude -p`.
#      Schrijft Membership-proposals naar data/_staging/staatscourant-<YYYY-HH>.json.
#
# Default: alle benoemings-KB's vanaf 2009-01-01 in 6-maands-batches.
# Pas met --since/--until aan voor smaller windows.
#
# Gebruik:
#   ./scripts/backfill_staatscourant.sh                                  # alles 2009-heden
#   ./scripts/backfill_staatscourant.sh --since 2024-01-01               # vanaf 2024
#   ./scripts/backfill_staatscourant.sh --since 2024-01-01 --until 2026-05-09
#   ./scripts/backfill_staatscourant.sh --parallel 8                     # 8 parallelle parsers
#   ./scripts/backfill_staatscourant.sh --phase 1                        # alleen download
#   ./scripts/backfill_staatscourant.sh --phase 2                        # alleen parse
#   ./scripts/backfill_staatscourant.sh --max-claude-calls 100           # cap voor sanity-run
#
# Conformiteit met Polder-regels:
#   - LLM schrijft alleen naar data/_staging/, nooit direct naar data/personen/.
#   - Substring-check op evidence_snippet wordt na elke parse uitgevoerd.
#   - Cost-cap optie voorkomt ongelimiteerde Sonnet-calls.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Defaults
SINCE="2009-01-01"
UNTIL="$(date -u +%Y-%m-%d)"
PARALLEL=5
PHASE="both"
MAX_CLAUDE_CALLS=1000
QUERY_TERMS='Secretaris-Generaal Directeur-Generaal Inspecteur-Generaal minister staatssecretaris'
CACHE_DIR="$REPO_ROOT/_cache/staatscourant"
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
    --query-terms)       QUERY_TERMS="$2"; shift 2;;
    -h|--help)           print_help; exit 0;;
    *) echo "Onbekende optie: $1" >&2; exit 2;;
  esac
done

mkdir -p "$CACHE_DIR" "$STAGING_DIR"
: > "$LOG_FAILURES" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Fase 1: enumerate + download full KB-XML
# ---------------------------------------------------------------------------

phase1_download() {
  local since="$1" until="$2"
  echo "==> Fase 1: download KBs ${since} tot ${until}" >&2
  CACHE_DIR="$CACHE_DIR" SINCE="$since" UNTIL="$until" QUERY_TERMS="$QUERY_TERMS" \
    uv run --project "$REPO_ROOT" python "$SCRIPT_DIR/_backfill_staatscourant_download.py"
}

# ---------------------------------------------------------------------------
# Fase 2: parse via parse-staatscourant skill (claude -p, parallel)
# ---------------------------------------------------------------------------

phase2_parse() {
  local since="$1" until="$2"
  echo "==> Fase 2: parse KBs ${since} tot ${until}, parallel=${PARALLEL}, cap=${MAX_CLAUDE_CALLS}" >&2

  # Bouw lijst van alle gedownloade XML's binnen het [since, until] venster.
  # Path: _cache/staatscourant/<jaar>/<maand>/<id>.xml
  # We filteren op pad-prefix zodat we niet naar de bestand-modified hoeven te kijken.
  local xml_list
  xml_list="$(uv run --project "$REPO_ROOT" python "$SCRIPT_DIR/_backfill_staatscourant_list.py" \
                "$CACHE_DIR" "$since" "$until" "$STAGING_DIR" "$MAX_CLAUDE_CALLS")"

  local total
  total="$(printf '%s\n' "$xml_list" | grep -c . || true)"
  if [ "$total" -eq 0 ]; then
    echo "    geen XML's te parsen voor ${since}..${until}" >&2
    return 0
  fi
  echo "    te parsen: ${total} XML's" >&2

  # Parallel via xargs -P. Geef pad door aan parser-helper.
  printf '%s\n' "$xml_list" \
    | xargs -P "$PARALLEL" -I {} bash "$SCRIPT_DIR/_backfill_staatscourant_parse_one.sh" "{}" "$STAGING_DIR" "$LOG_FAILURES" \
    || true
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

case "$PHASE" in
  1)    phase1_download "$SINCE" "$UNTIL";;
  2)    phase2_parse "$SINCE" "$UNTIL";;
  both) phase1_download "$SINCE" "$UNTIL"; phase2_parse "$SINCE" "$UNTIL";;
  *) echo "Onbekende --phase: $PHASE (kies 1, 2 of both)" >&2; exit 2;;
esac

echo "==> Klaar. Failures: $(wc -l <"$LOG_FAILURES" 2>/dev/null || echo 0)" >&2
