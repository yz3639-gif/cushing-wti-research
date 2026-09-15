#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  printf '%s\n' 'Project environment is missing. Run: bash setup.sh'
  exit 1
fi
export MPLCONFIGDIR="$PWD/tmp/matplotlib"
make build report
if command -v open >/dev/null 2>&1; then
  open outputs/report.html
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open outputs/report.html
else
  printf '%s\n' 'Open outputs/report.html in a browser.'
fi
