#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
ROOT="${RADAR_ROOT_OVERRIDE:-${SCRIPT_DIR:h}}"
cd "$ROOT"

git checkout -- logs data site 2>/dev/null || git checkout -- data site 2>/dev/null || true
export RADAR_GIT_PULL_FAILED=0
export RADAR_GIT_PULL_ERROR=""
PULL_SUCCEEDED=1
if ! PULL_OUTPUT="$(git pull --ff-only 2>&1)"; then
  echo "$PULL_OUTPUT" >&2
  DIRECT_PULL_OUTPUT=""
  if DIRECT_PULL_OUTPUT="$(env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy -u NO_PROXY git pull --ff-only 2>&1)"; then
    echo "$DIRECT_PULL_OUTPUT"
  else
    echo "$DIRECT_PULL_OUTPUT" >&2
    export RADAR_GIT_PULL_FAILED=1
    PULL_SUCCEEDED=0
    PULL_ERROR="$(printf '%s\n%s' "$PULL_OUTPUT" "$DIRECT_PULL_OUTPUT" | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g')"
    export RADAR_GIT_PULL_ERROR="${PULL_ERROR[1,500]}"
    echo "[push_local] git pull failed with proxy and direct environments; continuing with local data" >&2
  fi
fi

export RADAR_DEPENDENCY_UPDATE_FAILED=0
if [[ "$PULL_SUCCEEDED" == "1" ]]; then
  PYPROJECT_HASH="$(shasum -a 256 "$ROOT/pyproject.toml" | awk '{print $1}')"
  HASH_MARKER="$ROOT/.venv/.pyproject.sha256"
  INSTALLED_HASH=""
  if [[ -f "$HASH_MARKER" ]]; then
    INSTALLED_HASH="$(<"$HASH_MARKER")"
  fi
  if [[ ! -x "$ROOT/.venv/bin/python" || "$PYPROJECT_HASH" != "$INSTALLED_HASH" ]]; then
    if [[ ! -x "$ROOT/.venv/bin/python" ]] && ! python3 -m venv "$ROOT/.venv"; then
      export RADAR_DEPENDENCY_UPDATE_FAILED=1
      echo "[push_local] dependency update failed: could not create .venv; continuing with old environment" >&2
    fi
    if [[ -x "$ROOT/.venv/bin/python" ]]; then
      if "$ROOT/.venv/bin/python" -m pip install -q -e .; then
        print -r -- "$PYPROJECT_HASH" > "$HASH_MARKER"
      else
        export RADAR_DEPENDENCY_UPDATE_FAILED=1
        echo "[push_local] dependency update failed: pip install failed; continuing with old environment" >&2
      fi
    fi
  fi
fi

export PYTHONPATH="$ROOT/src"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/.pw-browsers"
exec "$ROOT/.venv/bin/python" -m activity_radar.cli push --auto --send
