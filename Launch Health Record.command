#!/bin/bash
# Finder launcher: keep a compatible Python and all packages in this project.
set -euo pipefail
cd "$(dirname "$0")"

on_error() {
  echo
  echo "Setup stopped. The error is printed above."
  echo "Press Return to close this window."
  read -r || true
}
trap on_error ERR

export UV_PYTHON_INSTALL_DIR="$PWD/.runtime/python"
export UV_CACHE_DIR="$PWD/.runtime/cache"
export UV_NATIVE_TLS=true
UV_BIN="$PWD/.runtime/bootstrap/bin/uv"
APP_ENV="$PWD/.venv314"

if [ ! -x "$UV_BIN" ]; then
  echo "Installing the Python setup tool into this project…"
  python3 -m pip install --no-cache-dir --target "$PWD/.runtime/bootstrap" uv
fi

if [ ! -x "$APP_ENV/bin/python" ] || ! "$APP_ENV/bin/python" -c 'import sys; assert sys.version_info[:2] == (3, 14)' 2>/dev/null; then
  if [ -e "$APP_ENV" ]; then
    echo "The existing .venv314 is incomplete or uses another Python."
    echo "Move that folder aside, then open this launcher again."
    exit 1
  fi
  echo "Downloading Python 3.14 (your system Python will stay as it is)…"
  "$UV_BIN" python install 3.14 --no-bin
  "$UV_BIN" venv --python 3.14 "$APP_ENV"
fi

echo "Installing/checking the app's pinned dependencies…"
"$UV_BIN" pip install --python "$APP_ENV/bin/python" --require-hashes -r packaging/requirements-app.txt
"$UV_BIN" pip install --python "$APP_ENV/bin/python" --no-deps -e .

echo
echo "Opening Health Record at http://127.0.0.1:7777"
echo "Keep this window open. Press Control-C here to quit."
"$APP_ENV/bin/health-agent" app --no-tray
