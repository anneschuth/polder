#!/usr/bin/env bash
# daily_update_local.sh - lokale variant van .github/workflows/daily-update.yml.
#
# Stappen:
#   1. Run alle deterministische fetchers (fail-soft: continue bij failure).
#   2. polder-validate (hard fail).
#   3. polder-diff -> diff.json + proposals.json.
#   4. Bepaal PR-label (auto-merge of needs-review) en schrijf naar
#      dist/pr-label.txt.
#   5. Roep review_pr_diff_local.sh aan -> dist/pr-body.md.
#   6. Print samenvatting (aantal records, label, paden).
#
# Doet GEEN git-commits, GEEN PR. Anne reviewt zelf en commit handmatig.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

mkdir -p dist

echo "=== daily_update_local.sh ==="
echo "repo: $REPO_ROOT"
echo "datum: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

run_fetcher() {
  # $1 = label, rest = command
  local label="$1"
  shift
  echo "[fetch] $label"
  if "$@"; then
    echo "[fetch] $label: ok"
  else
    echo "[fetch] $label: gefaald (continue)" >&2
  fi
  echo
}

run_fetcher "ROO"           uv run polder-fetch-roo
run_fetcher "Logius COR"    uv run python -m polder.fetchers.logius_cor
run_fetcher "Wikidata orgs" uv run polder-fetch-wikidata --orgs
run_fetcher "TK OData"      uv run polder-fetch-tk-odata
run_fetcher "EK scrape"     uv run polder-fetch-ek-scrape
run_fetcher "AR RWT"        uv run polder-fetch-ar-rwt

echo "[validate] polder-validate"
uv run polder-validate
echo

echo "[diff] polder-diff"
set +e
uv run polder-diff \
  --cache _cache \
  --data data \
  --out diff.json \
  --proposals proposals.json
DIFF_EXIT=$?
set -e
if [ "$DIFF_EXIT" -ne 0 ]; then
  echo "[diff] polder-diff exit=$DIFF_EXIT (continue)" >&2
fi
echo

# Label-bepaling (zelfde logica als daily-update.yml).
echo "[label] bepaal PR-label"
LABEL="$(uv run python - <<'PY'
import json
from pathlib import Path

diff_path = Path("diff.json")
if not diff_path.exists():
    print("needs-review")
    raise SystemExit(0)

try:
    diffs = json.loads(diff_path.read_text(encoding="utf-8") or "[]")
except json.JSONDecodeError:
    diffs = []


def confidence_ok(entry: dict) -> bool:
    after = entry.get("after") or {}
    for source in after.get("sources", []) or []:
        conf = source.get("confidence")
        if conf is not None and conf < 0.95:
            return False
    return True


has_high_stakes = any(d.get("high_stakes") for d in diffs)
all_confident = all(confidence_ok(d) for d in diffs)
print("auto-merge" if all_confident and not has_high_stakes else "needs-review")
PY
)"

echo "$LABEL" >dist/pr-label.txt
echo "[label] $LABEL -> dist/pr-label.txt"
echo

# Aantal gewijzigde records.
RECORD_COUNT=0
if [ -f diff.json ]; then
  RECORD_COUNT="$(uv run python -c 'import json,sys; print(len(json.loads(open("diff.json").read() or "[]")))' 2>/dev/null || echo 0)"
fi

# Genereer PR-body via review-pr-diff skill.
PR_BODY="dist/pr-body.md"
if [ -f diff.json ]; then
  echo "[review] genereer PR-body via claude -p (skill: review-pr-diff)"
  bash "$SCRIPT_DIR/review_pr_diff_local.sh" diff.json "$PR_BODY"
else
  echo "[review] geen diff.json, sla PR-body over"
fi
echo

echo "=== samenvatting ==="
echo "records gewijzigd: $RECORD_COUNT"
echo "label:             $LABEL"
echo "pr-body:           $PR_BODY"
echo "diff.json:         $REPO_ROOT/diff.json"
if [ -f proposals.json ]; then
  echo "proposals.json:    $REPO_ROOT/proposals.json"
fi
echo
echo "Geen commit gemaakt. Review en commit handmatig."
