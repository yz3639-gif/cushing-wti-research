"""Copy the verified offline report into the repository's static site."""
from pathlib import Path
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cushing_research.pipeline import make_manifest
from cushing_research.snapshot import verify_snapshot

result = json.loads((ROOT / 'outputs/results.json').read_text())
verify_snapshot(ROOT)
if make_manifest(ROOT, result['config'])['run_id'] != result['run_id']:
    raise ValueError('Stale research results; rebuild before publishing the site')
for source, target in [('report.html', 'index.html'), ('research_memo.pdf', 'research_memo.pdf')]:
    shutil.copy2(ROOT / 'outputs' / source, ROOT / 'docs' / target)
(ROOT / 'docs/.nojekyll').touch()
print('Static report prepared from result ' + result['run_id'])
