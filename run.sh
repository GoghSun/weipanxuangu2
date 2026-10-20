#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python -m weipan "$@"
elif [[ -d /tmp/weipan-deps/akshare ]]; then
  export PYTHONPATH="/tmp/weipan-deps${PYTHONPATH:+:$PYTHONPATH}"
  exec python3 -m weipan "$@"
else
  exec python3 -m weipan "$@"
fi
