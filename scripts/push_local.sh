#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
ROOT="${SCRIPT_DIR:h}"
cd "$ROOT"

git checkout -- logs data site 2>/dev/null || git checkout -- data site 2>/dev/null || true
export RADAR_GIT_PULL_FAILED=0
if ! git pull --ff-only; then
  export RADAR_GIT_PULL_FAILED=1
  echo "[push_local] git pull --ff-only failed; continuing with local data" >&2
fi

export PYTHONPATH="$ROOT/src"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/.pw-browsers"
exec "$ROOT/.venv/bin/python" -m activity_radar.cli push --auto --send
