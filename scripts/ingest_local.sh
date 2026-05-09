#!/usr/bin/env bash
# ingest_local.sh - vol-automatische staging-pipeline voor polder.
#
# Draait `polder ingest --commit --push`: parse pending HTML/XML/PDF in
# _cache/, resolve, apply met threshold 0.85, validate, build, commit en push.
# Eén commando, geen tussenkomst nodig.
#
# Gebruik:
#   ./scripts/ingest_local.sh                   # alle bronnen, --commit --push
#   ./scripts/ingest_local.sh --dry-run         # plan tonen, niets doen
#   ./scripts/ingest_local.sh --source abd-nieuws --threshold 0.95
#
# Vereist: claude CLI in PATH plus uv. Stop bij elke fout (set -euo pipefail).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

# Default: alle bronnen, --commit --push. Override door extra args mee te geven.
if [ "$#" -eq 0 ]; then
  exec uv run polder ingest --commit --push
fi

exec uv run polder ingest "$@"
