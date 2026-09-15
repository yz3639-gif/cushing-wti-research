"""Build a versioned local release ZIP from the public repository content."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
subprocess.run([sys.executable, str(ROOT / 'scripts/check_release.py')], cwd=ROOT, check=True)
directories = ['cushing_research', 'config', 'data', 'docs', 'outputs', 'tests', 'scripts', '.github']
files = [ROOT / name for name in ['README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
         'CITATION.cff', 'Makefile', 'requirements.lock.txt', 'pyproject.toml', 'setup.sh',
         'Run Research.command', '.gitignore', '.gitattributes', 'research_walkthrough.ipynb']]
for folder in directories:
    files += [p for p in (ROOT / folder).rglob('*') if p.is_file()
              and '__pycache__' not in p.parts and p.suffix not in {'.pyc', '.pyo'} and p.name != '.DS_Store']
files = sorted(set(p for p in files if p.is_file()))
result = json.loads((ROOT / 'outputs/results.json').read_text())
destination = ROOT / 'dist/cushing-wti-research-v1.0.0.zip'
destination.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for path in files:
        archive.write(path, 'cushing-wti-research/' + str(path.relative_to(ROOT)))
with zipfile.ZipFile(destination) as archive:
    assert archive.testzip() is None
manifest = {'version': '1.0.0', 'run_id': result['run_id'], 'status': result['status'],
            'archive': destination.name, 'files': len(files), 'bytes': destination.stat().st_size,
            'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}
(ROOT / 'dist/release_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps(manifest, indent=2))
