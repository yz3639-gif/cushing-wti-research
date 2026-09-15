"""Check source integrity, matching reports, and portable public artifacts."""
from pathlib import Path
import hashlib
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cushing_research.pipeline import make_manifest
from cushing_research.snapshot import verify_snapshot
from pypdf import PdfReader


def main():
    result = json.loads((ROOT / 'outputs/results.json').read_text())
    snapshot = verify_snapshot(ROOT)
    if make_manifest(ROOT, result['config'])['run_id'] != result['run_id']:
        raise ValueError('Results do not match the current source, inputs and environment')
    if result['status'] == 'public_evidence_only':
        assert result['research'] is None and not result['ledgers']
    html = (ROOT / 'outputs/report.html').read_text()
    payload = json.loads(html.split('<script id="report-data" type="application/json">')[1].split('</script>', 1)[0])
    assert payload['audit']['run_id'] == result['run_id']
    assert payload['mechanism'] == result['mechanism']
    assert payload['observations'] == result['public_observations']
    assert not re.search(r'<script\b[^>]*\bsrc\s*=', html, re.I)
    assert (ROOT / 'docs/index.html').read_bytes() == (ROOT / 'outputs/report.html').read_bytes()
    assert (ROOT / 'docs/research_memo.pdf').read_bytes() == (ROOT / 'outputs/research_memo.pdf').read_bytes()
    pdf = PdfReader(ROOT / 'outputs/research_memo.pdf')
    assert len(pdf.pages) == 4
    pdf_text = '\n'.join(page.extract_text() for page in pdf.pages)
    assert result['run_id'] in pdf_text
    for name in ['research_memo.md', 'interview_notes.md']:
        assert result['run_id'] in (ROOT / 'outputs' / name).read_text()
    notebook = json.loads((ROOT / 'research_walkthrough.ipynb').read_text())
    cells = [c for c in notebook['cells'] if c['cell_type'] == 'code']
    assert all(c['execution_count'] is not None for c in cells)
    assert not any(o.get('output_type') == 'error' for c in cells for o in c['outputs'])
    assert result['run_id'] in json.dumps(notebook)
    assert sorted(p.name for p in (ROOT / 'data/input').iterdir() if p.is_file()) == ['README.md'], 'Private market inputs must not enter a public release'
    expected = ['README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'CITATION.cff',
                'docs/DATA_SOURCES.md', 'docs/PORTFOLIO_GUIDE.md', 'docs/RELEASE_NOTES.md']
    assert all((ROOT / name).is_file() for name in expected)
    # Absolute machine paths have no place in distributable research outputs.
    path_pattern = re.compile(r'/(?:' + 'Users' + r'|home)/[^\s/]+/')
    for name in ['outputs/results.json', 'outputs/report.html', 'research_walkthrough.ipynb']:
        assert not path_pattern.search((ROOT / name).read_text()), name
    assert not path_pattern.search(pdf_text + str(pdf.metadata))
    print(json.dumps({'passed': True, 'run_id': result['run_id'], 'status': result['status'],
                      'verified_raw_sources': snapshot['verified_raw_files'], 'pdf_pages': 4,
                      'executed_notebook_cells': len(cells), 'site_matches_offline_report': True}, indent=2))


if __name__ == '__main__':
    main()
