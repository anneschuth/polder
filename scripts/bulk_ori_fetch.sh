#!/usr/bin/env bash
# bulk_ori_fetch.sh - run de Open Raadsinformatie-fetcher voor alle gemeenten
# in data/organisaties/gemeenten/. De fetcher is idempotent: bestaande records
# worden gemerged, dus opnieuw draaien is veilig.
#
# Gebruik:
#   bash scripts/bulk_ori_fetch.sh                    # alle gemeenten, parallel=5
#   bash scripts/bulk_ori_fetch.sh --parallel 10      # 10 workers
#   bash scripts/bulk_ori_fetch.sh --limit 10         # eerste 10 gemeenten (sanity)
#   bash scripts/bulk_ori_fetch.sh --validate-every 50  # tussentijds polder validate
#
# De fetcher rate-limit zelf op 2 req/s per worker.
set -euo pipefail

PARALLEL=5
LIMIT=""
VALIDATE_EVERY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --parallel)
      PARALLEL="$2"
      shift 2
      ;;
    --limit)
      LIMIT="$2"
      shift 2
      ;;
    --validate-every)
      VALIDATE_EVERY="$2"
      shift 2
      ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *)
      echo "onbekende optie: $1" >&2
      exit 2
      ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

DATESTAMP="$(date +%Y%m%d-%H%M%S)"
LOG_DIR="$REPO_ROOT/_logs"
LOG_FILE="$LOG_DIR/bulk-ori-${DATESTAMP}.log"
PROGRESS_FILE="$LOG_DIR/bulk-ori-${DATESTAMP}.progress"
mkdir -p "$LOG_DIR"

GEM_DIR="data/organisaties/gemeenten"
if [[ ! -d "$GEM_DIR" ]]; then
  echo "Geen $GEM_DIR gevonden" >&2
  exit 1
fi

# Verzamel slugs (filename zonder .yaml). Bash 3.2-compatibel: geen mapfile.
SLUGS_FILE="$LOG_DIR/bulk-ori-${DATESTAMP}.slugs"
find "$GEM_DIR" -maxdepth 1 -name '*.yaml' -type f \
  | sed -E 's|.*/||; s/\.yaml$//' \
  | LC_ALL=C sort > "$SLUGS_FILE"

if [[ -n "$LIMIT" ]]; then
  head -n "$LIMIT" "$SLUGS_FILE" > "${SLUGS_FILE}.tmp" && mv "${SLUGS_FILE}.tmp" "$SLUGS_FILE"
fi

TOTAL=$(wc -l < "$SLUGS_FILE" | tr -d ' ')

echo "bulk-ori: $TOTAL gemeenten, parallel=$PARALLEL, log=$LOG_FILE" | tee -a "$LOG_FILE"
echo "start: $(date -Iseconds)" >> "$LOG_FILE"

# Worker: één gemeente. Schrijft één regel per gemeente naar progress-file.
# Telt records aangemaakt door het verschil in `current`+`historisch` te kijken
# is duur; in plaats daarvan parsen we de stderr-uitvoer van de fetcher.
process_one() {
  local slug="$1"
  local progress="$2"
  local log="$3"
  local out
  local rc
  local start
  start=$(date +%s)
  # De fetcher print naar stderr: "Wrote N current + M historisch persoon-records ..."
  # We loggen alle stderr naar log en pakken de samenvatting voor progress.
  if out=$(uv run python -m polder.fetchers.open_raadsinformatie \
        --gemeente "$slug" \
        --cache-dir _cache/ori \
        --out data/personen \
        --data-root data 2>&1); then
    rc=0
  else
    rc=$?
  fi
  local elapsed=$(( $(date +%s) - start ))
  # Pak laatste regel met "Wrote ... current + ... historisch".
  local summary
  summary=$(printf '%s\n' "$out" | grep -E 'Wrote [0-9]+ current' | tail -1 || true)
  if [[ -z "$summary" ]]; then
    summary="(geen records / fout)"
  fi
  {
    echo "===== $slug (rc=$rc, ${elapsed}s) ====="
    printf '%s\n' "$out"
  } >> "$log"
  printf '%s\trc=%d\t%ds\t%s\n' "$slug" "$rc" "$elapsed" "$summary" >> "$progress"
}

export -f process_one

# Run in parallel via xargs.
xargs -I {} -P "$PARALLEL" bash -c 'process_one "$@"' _ {} "$PROGRESS_FILE" "$LOG_FILE" < "$SLUGS_FILE"

# Optionele tussentijdse validatie - na alle workers klaar zijn, doen we die
# een keer aan het eind als VALIDATE_EVERY > 0.
if [[ "$VALIDATE_EVERY" -gt 0 ]]; then
  echo "validate na bulk-run:" | tee -a "$LOG_FILE"
  if uv run polder validate >> "$LOG_FILE" 2>&1; then
    echo "  ok" | tee -a "$LOG_FILE"
  else
    echo "  validate FAALDE; zie $LOG_FILE" | tee -a "$LOG_FILE"
  fi
fi

# Eindrapport.
echo "" | tee -a "$LOG_FILE"
echo "===== eindrapport =====" | tee -a "$LOG_FILE"
WITH_RECORDS=$(grep -E 'Wrote [1-9][0-9]* current|Wrote [0-9]+ current \+ [1-9]' "$PROGRESS_FILE" | wc -l | tr -d ' ')
NO_RECORDS=$(grep -E 'Wrote 0 current \+ 0 historisch' "$PROGRESS_FILE" | wc -l | tr -d ' ')
ERRORS=$(awk -F'\t' '$2 != "rc=0"' "$PROGRESS_FILE" | wc -l | tr -d ' ')

# Som totaal current + historisch over alle progress-regels.
TOTAL_C=$(grep -oE 'Wrote [0-9]+ current' "$PROGRESS_FILE" | awk '{s+=$2} END {print s+0}')
TOTAL_H=$(grep -oE '\+ [0-9]+ historisch' "$PROGRESS_FILE" | awk '{s+=$2} END {print s+0}')

CURRENT_NOW=$(find data/personen/current -maxdepth 1 -name '*.yaml' 2>/dev/null | wc -l | tr -d ' ')
HIST_NOW=$(find data/personen/historisch -maxdepth 1 -name '*.yaml' 2>/dev/null | wc -l | tr -d ' ')

{
  echo "totaal gemeenten verwerkt : $TOTAL"
  echo "  met records             : $WITH_RECORDS"
  echo "  zonder records (0/0)    : $NO_RECORDS"
  echo "  errors (rc != 0)        : $ERRORS"
  echo "som records (per-run)     : current=$TOTAL_C historisch=$TOTAL_H"
  echo "totale bestanden op disk  : current=$CURRENT_NOW historisch=$HIST_NOW"
  echo "log                       : $LOG_FILE"
  echo "progress                  : $PROGRESS_FILE"
} | tee -a "$LOG_FILE"

echo "klaar: $(date -Iseconds)" >> "$LOG_FILE"
