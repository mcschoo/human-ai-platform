#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN=${PYTHON:-python3}
VENV="$ROOT/.venv"

"$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' || {
  echo "Python 3.10 or newer is required." >&2
  exit 1
}

if [ ! -x "$VENV/bin/python" ]; then
  echo "Creating local Python environment..."
  "$PYTHON_BIN" -m venv "$VENV"
fi

if ! "$VENV/bin/python" -c 'import httpx, openai, tqdm' 2>/dev/null; then
  "$VENV/bin/python" -m pip install -e "$ROOT"
fi

if [ "${1:-}" = "test" ]; then
  if ! "$VENV/bin/python" -c 'import fastapi, pytest' 2>/dev/null; then
    "$VENV/bin/python" -m pip install -e "$ROOT[test]"
    "$VENV/bin/python" -m pip install -e "$ROOT/services/control-plane[test]"
  fi
fi

exec "$VENV/bin/python" "$ROOT/scripts/client.py" "$@"
