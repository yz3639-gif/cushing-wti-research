#!/bin/zsh
set -eu
cd "$(dirname "$0")"
if [[ ! -x .venv-options/bin/python ]]; then
  print 'Run the isolated installation steps in options_lab/README.md first.'
  exit 1
fi
exec .venv-options/bin/python -m streamlit run options_lab/app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
