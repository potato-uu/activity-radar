#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
ROOT="${SCRIPT_DIR:h}"
cd "$ROOT"

if ! git pull --ff-only; then
  echo "[push_local] git pull --ff-only failed; continuing with local data" >&2
fi

export PYTHONPATH="$ROOT/src"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/.pw-browsers"
exec "$ROOT/.venv/bin/python" -m activity_radar.cli push --auto --send
