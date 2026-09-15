#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_BIN=${PYTHON:-python3}

"$PYTHON_BIN" "$ROOT/scripts/status.py"
HAI_LIVE_TESTS=1 "$PYTHON_BIN" -m pytest "$ROOT/tests" -m live "$@"
