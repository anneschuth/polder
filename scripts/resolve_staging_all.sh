#!/usr/bin/env bash
# resolve_staging_all.sh - run resolve-staging-proposals skill parallel op alle
# staging-files in data/_staging/ die nog geen .resolved.json companion hebben.
#
# Gebruik:
#   bash scripts/resolve_staging_all.sh [--parallel 5] [--source abd-nieuws|staatscourant|organogram|all]
#
# Default: --parallel 5, --source all.
# Idempotent: bestaande .resolved.json files worden overgeslagen.

set -euo pipefail

PARALLEL=5
SOURCE="all"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --parallel) PARALLEL="$2"; shift 2;;
    --source) SOURCE="$2"; shift 2;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \?//'
      exit 0;;
    *) echo "Onbekende optie: $1" >&2; exit 2;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

# Bouw glob-pattern op basis van source.
case "$SOURCE" in
  all)            PATTERN="data/_staging/*.json";;
  abd-nieuws)     PATTERN="data/_staging/abd-nieuws-*.json";;
  staatscourant)  PATTERN="data/_staging/staatscourant-*.json";;
  organogram)     PATTERN="data/_staging/organogram-*.json";;
  *) echo "Onbekende source: $SOURCE" >&2; exit 2;;
esac

# Verzamel files die nog geen .resolved.json hebben en niet zelf .resolved zijn.
TMP_LIST="$(mktemp -t polder-resolve-list.XXXXXX)"
trap 'rm -f "$TMP_LIST"' EXIT

for f in $PATTERN; do
  case "$f" in
    *.resolved.json) continue;;
  esac
  base="${f%.json}"
  resolved="${base}.resolved.json"
  if [ -f "$resolved" ]; then continue; fi
  echo "$f" >> "$TMP_LIST"
done

n=$(wc -l < "$TMP_LIST" | tr -d ' ')
if [ "$n" = 0 ]; then
  echo "Geen onresolved staging-files gevonden voor source=$SOURCE." >&2
  exit 0
fi

echo "Te resolven: $n staging-files (parallel=$PARALLEL)" >&2

run_one() {
  staging="$1"
  echo "  resolving $staging" >&2
  if ! uv run polder skill resolve-staging "$staging" >/dev/null 2>>"_logs/resolve-staging.log"; then
    echo "  FAIL: $staging" >&2
    return 0
  fi
}

export -f run_one

mkdir -p _logs

xargs -P "$PARALLEL" -L 1 bash -c 'run_one "$@"' _ < "$TMP_LIST"

echo "Klaar. Resolved files in data/_staging/*.resolved.json" >&2
