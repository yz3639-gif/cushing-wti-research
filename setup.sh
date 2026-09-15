#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
research_python="${CUSHING_PYTHON:-python3.12}"
if ! command -v "$research_python" >/dev/null 2>&1; then
  research_python="${CUSHING_PYTHON:-python3}"
fi
"$research_python" -c 'import sys; assert sys.version_info[:2] == (3,12), "Use Python 3.12 for the locked research environment"'
"$research_python" -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip check
printf '%s\n' 'Environment ready. Run: make reproduce'
