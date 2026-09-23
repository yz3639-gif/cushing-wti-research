"""Public synthetic demonstration; no user-file uploads or provider secrets."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from options_lab.app import main

main(public_demo=True)
