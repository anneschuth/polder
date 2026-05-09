#!/usr/bin/env bash
# review_pr_diff_local.sh - lokale runner voor de review-pr-diff skill.
#
# Gebruik:
#   ./scripts/review_pr_diff_local.sh <diff.json> [output.md]
#
# Default output: dist/pr-body.md
#
# Spiegelt de claude-review job uit .github/workflows/daily-update.yml maar
# zonder PR-comment posten. Het resultaat is een markdown-bestand dat je zelf
# kunt plakken in een PR-body of inspecteren.

set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: $0 <diff.json> [output.md]" >&2
  exit 2
fi

DIFF_PATH="$1"
OUTPUT="${2:-dist/pr-body.md}"

if [ ! -f "$DIFF_PATH" ]; then
  echo "review_pr_diff_local.sh: diff-bestand niet gevonden: $DIFF_PATH" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Maak een tijdelijk prompt-bestand met diff-inhoud plus eventueel proposals.
TMP_INPUT="$(mktemp -t polder-review-pr-diff.XXXXXX)"
trap 'rm -f "$TMP_INPUT"' EXIT

{
  echo "## diff.json"
  echo
  cat "$DIFF_PATH"
  if [ -f "$REPO_ROOT/proposals.json" ]; then
    echo
    echo "## proposals.json"
    echo
    cat "$REPO_ROOT/proposals.json"
  fi
  if [ -f "$REPO_ROOT/proposals_llm.json" ]; then
    echo
    echo "## proposals_llm.json"
    echo
    cat "$REPO_ROOT/proposals_llm.json"
  fi
} >"$TMP_INPUT"

mkdir -p "$(dirname "$OUTPUT")"

bash "$SCRIPT_DIR/run_skill.sh" review-pr-diff "$TMP_INPUT" "$OUTPUT"

echo "review_pr_diff_local.sh: PR-body geschreven naar $OUTPUT" >&2
