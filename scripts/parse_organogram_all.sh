#!/usr/bin/env bash
# parse_organogram_all.sh - draai de parse-organogram skill op alle PDFs in
# _cache/abd-organogrammen/<ministerie>/ in serie.
#
# Vereist: ABD-fetcher heeft eerst gedraaid (`polder fetch abd --all`) zodat de
# PDFs in _cache/abd-organogrammen/<min-slug>/assets/ staan.
#
# Gebruik:
#   ./scripts/parse_organogram_all.sh [--cache _cache/abd-organogrammen]
#                                     [--out data/_staging]
#                                     [--parallel 1]
#
# Output: per ministerie een data/_staging/organogram-<slug>-<YYYY-MM-DD>.json.
# Idempotent: ministeries waarvoor de output-file van vandaag al bestaat
# worden overgeslagen tenzij --force wordt meegegeven.

set -euo pipefail

CACHE_DIR="_cache/abd-organogrammen"
OUT_DIR="data/_staging"
PARALLEL=1
FORCE=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --cache) CACHE_DIR="$2"; shift 2;;
    --out) OUT_DIR="$2"; shift 2;;
    --parallel) PARALLEL="$2"; shift 2;;
    --force) FORCE=1; shift;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \?//'
      exit 0;;
    *) echo "Onbekende optie: $1" >&2; exit 2;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TODAY="$(date -u +%Y-%m-%d)"

if [ ! -d "$REPO_ROOT/$CACHE_DIR" ]; then
  echo "Cache-dir bestaat niet: $REPO_ROOT/$CACHE_DIR" >&2
  echo "Run eerst: polder fetch abd --all" >&2
  exit 1
fi

mkdir -p "$REPO_ROOT/$OUT_DIR"

# Verzamel ministerie-PDF paren.
TMP_LIST="$(mktemp -t polder-organogram-list.XXXXXX)"
trap 'rm -f "$TMP_LIST"' EXIT

while IFS= read -r pdf; do
  rel="${pdf#$REPO_ROOT/$CACHE_DIR/}"
  min_slug="${rel%%/*}"
  out_file="$REPO_ROOT/$OUT_DIR/organogram-${min_slug}-${TODAY}.json"
  if [ "$FORCE" = 0 ] && [ -f "$out_file" ]; then
    echo "skip $min_slug (output bestaat: $out_file)" >&2
    continue
  fi
  printf '%s\t%s\t%s\n' "$pdf" "$min_slug" "$out_file" >> "$TMP_LIST"
done < <(find "$REPO_ROOT/$CACHE_DIR" -name "*.pdf" | sort)

n=$(wc -l < "$TMP_LIST" | tr -d ' ')
if [ "$n" = 0 ]; then
  echo "Geen nieuwe PDFs om te verwerken." >&2
  exit 0
fi

echo "Te verwerken: $n PDFs" >&2

run_one() {
  pdf="$1"; min_slug="$2"; out_file="$3"
  echo "=== $min_slug ===" >&2
  bash "$SCRIPT_DIR/parse_organogram_local.sh" "$pdf" "$min_slug" "$out_file" || {
    echo "FAIL: $min_slug ($pdf)" >&2
    return 0  # door met de rest
  }
}

export -f run_one
export SCRIPT_DIR

if [ "$PARALLEL" -gt 1 ]; then
  # GNU xargs heeft -P, BSD xargs ook (macOS).
  awk -F'\t' '{print $1 "\t" $2 "\t" $3}' "$TMP_LIST" | \
    xargs -P "$PARALLEL" -L 1 bash -c 'run_one "$@"' _
else
  while IFS=$'\t' read -r pdf min_slug out_file; do
    run_one "$pdf" "$min_slug" "$out_file"
  done < "$TMP_LIST"
fi

echo "Klaar. Output in $REPO_ROOT/$OUT_DIR/organogram-*.json" >&2
