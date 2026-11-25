#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH='' cd -- "$script_dir/.." && pwd)
python_bin="${PYTHON:-python3}"
if ! "$python_bin" -c 'import tomllib, jsonschema' >/dev/null 2>&1; then
  if command -v python3.12 >/dev/null 2>&1; then
    python_bin=python3.12
  else
    echo "error: Python 3.11+ and requirements-dev.txt are required" >&2
    exit 1
  fi
fi
if ! command -v minisign >/dev/null 2>&1; then
  echo "error: minisign is required" >&2
  exit 1
fi

"$python_bin" "$script_dir/validate.py" "$root"
minisign -V -p "$root/catalog.pub" -m "$root/index.json"
