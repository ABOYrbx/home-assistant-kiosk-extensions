#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: scripts/package.sh <extension-id> <version>" >&2
  exit 2
fi

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
python_bin="${PYTHON:-python3}"
if ! "$python_bin" -c 'import tomllib' >/dev/null 2>&1; then
  if command -v python3.12 >/dev/null 2>&1; then
    python_bin=python3.12
  else
    echo "error: Python 3.11 or newer is required" >&2
    exit 1
  fi
fi

exec "$python_bin" "$script_dir/package.py" "$1" "$2"
