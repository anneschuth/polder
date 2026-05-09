#!/usr/bin/env bash
# apply_staging_local.sh - lokale runner voor `polder apply-staging`.
#
# Gebruik:
#   ./scripts/apply_staging_local.sh <input.resolved.json|map> [--apply]
#
# Default: dry-run analyse. Met --apply als tweede argument schrijft de runner
# de YAML's daadwerkelijk naar data/ en valideert daarna de tree.
#
# Voor full-batch over alle resolved bestanden in data/_staging/:
#   ./scripts/apply_staging_local.sh data/_staging/ --apply

set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: $0 <input.resolved.json|map> [--apply]" >&2
  exit 2
fi

INPUT="$1"
MODE="${2:-}"

if [ ! -e "$INPUT" ]; then
  echo "apply_staging_local.sh: input niet gevonden: $INPUT" >&2
  exit 1
fi

if [ "$MODE" = "--apply" ]; then
  exec uv run polder apply-staging "$INPUT" --apply
else
  exec uv run polder apply-staging "$INPUT"
fi
