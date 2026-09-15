#!/usr/bin/env python3
"""Execute the final research notebook for QA without Jupyter dependencies.

Run from the project directory, after the final build/report:
    .venv/bin/python scripts/run_notebook.py

Execute saved notebook cells in order without a running notebook server. It does not rebuild results.
"""
from __future__ import annotations

import ast
import base64
import contextlib
from datetime import datetime, timezone
import hashlib
import importlib.machinery
import io
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
import types


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json_atomic(path: Path, payload: dict) -> None:
    temporary = path.with_name(path.name + '.qa-writing')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n')
    temporary.replace(path)


class OutputCapture:
    """Preserve the order of stream text and rich display output."""

    def __init__(self):
        self.outputs: list[dict] = []
        self.figure_count = 0

    def stream(self, name: str, text: str) -> None:
        if not text:
            return
        if self.outputs and self.outputs[-1].get('output_type') == 'stream' and self.outputs[-1].get('name') == name:
            self.outputs[-1]['text'] += text
        else:
            self.outputs.append({'output_type': 'stream', 'name': name, 'text': text})

    def display_bundle(self, data: dict, metadata: dict | None = None, execution_count: int | None = None) -> None:
        entry = {'output_type': 'display_data' if execution_count is None else 'execute_result',
                 'data': data, 'metadata': metadata or {}}
        if execution_count is not None:
            entry['execution_count'] = execution_count
        self.outputs.append(entry)

    def figure(self, figure) -> None:
        buffer = io.BytesIO()
        figure.savefig(buffer, format='png', dpi=150, bbox_inches='tight', facecolor='white')
        self.display_bundle({'image/png': base64.b64encode(buffer.getvalue()).decode('ascii'),
                             'text/plain': f'Matplotlib figure ({figure.get_size_inches()[0]:.1f} × {figure.get_size_inches()[1]:.1f} inches)'},
                            {'needs_background': 'light'})
        self.figure_count += 1

    def display(self, *objects, raw=False, metadata=None, **kwargs) -> None:
        for value in objects:
            if value is None:
                continue
            if raw:
                self.display_bundle(dict(value), metadata)
                continue
            if hasattr(value, 'savefig') and hasattr(value, 'get_size_inches'):
                self.figure(value)
                continue
            if hasattr(value, 'to_html') and hasattr(value, 'to_string'):
                # DataFrame values are escaped; source URLs remain readable text.
                plain = value.to_string(index=False)
                html = value.to_html(index=False, escape=True, border=0)
                self.display_bundle({'text/plain': plain, 'text/html': html}, metadata)
                continue
            data = {'text/plain': repr(value)}
            representation = getattr(value, '_repr_html_', None)
            if callable(representation):
                html = representation()
                if isinstance(html, str):
                    data['text/html'] = html
            self.display_bundle(data, metadata)


class CapturedStream(io.TextIOBase):
    def __init__(self, capture: OutputCapture, name: str):
        super().__init__()
        self.capture = capture
        self.name = name

    @property
    def encoding(self):
        return 'utf-8'

    def writable(self):
        return True

    def write(self, text):
        self.capture.stream(self.name, str(text))
        return len(text)

    def flush(self):
        return None


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    if Path.cwd().resolve() != project_root:
        raise RuntimeError(f'Run this QA helper from the project root: {project_root}')
    notebook_path = project_root / 'research_walkthrough.ipynb'
    result_path = project_root / 'outputs/results.json'
    manifest_path = project_root / 'outputs/run_manifest.json'
    if not notebook_path.is_file() or not result_path.is_file() or not manifest_path.is_file():
        raise RuntimeError('Notebook or final results/manifest missing. Finish build/report first.')

    # This interpreter must import the current project even though the script lives
    # in scripts/. No dependency installation or filesystem package changes occur.
    sys.path.insert(0, str(project_root))
    from cushing_research.pipeline import make_manifest
    from cushing_research.snapshot import verify_snapshot

    result = json.loads(result_path.read_text())
    saved_manifest = json.loads(manifest_path.read_text())
    if result.get('run_id') != saved_manifest.get('run_id') or make_manifest(project_root, result['config'])['run_id'] != result.get('run_id'):
        raise RuntimeError('Results are stale or their manifest differs. Run build --offline and report --offline first.')
    snapshot_audit = verify_snapshot(project_root)
    input_hashes = {str(path.relative_to(project_root)): sha256(path) for path in (result_path, manifest_path)}

    notebook = json.loads(notebook_path.read_text())
    if notebook.get('nbformat') != 4 or not isinstance(notebook.get('cells'), list):
        raise ValueError('Expected a valid nbformat 4 notebook JSON structure.')
    for index, cell in enumerate(notebook['cells']):
        if cell.get('cell_type') == 'code':
            source = ''.join(cell['source']) if isinstance(cell['source'], list) else cell['source']
            ast.parse(source, filename=f'{notebook_path.name}:cell-{index}')
            cell['execution_count'] = None
            cell['outputs'] = []

    started = datetime.now(timezone.utc)
    started_clock = time.monotonic()
    backup = project_root / 'tmp' / ('research_walkthrough.before_qa_' + started.strftime('%Y%m%dT%H%M%SZ') + '.ipynb')
    shutil.copy2(notebook_path, backup)
    matplotlib_cache = project_root / 'tmp/matplotlib-notebook-qa'
    matplotlib_cache.mkdir(parents=True, exist_ok=True)
    os.environ['MPLCONFIGDIR'] = str(matplotlib_cache)
    os.environ['MPLBACKEND'] = 'Agg'

    current_capture: OutputCapture | None = None

    def rich_display(*objects, **kwargs):
        if current_capture is None:
            raise RuntimeError('Display called outside an executing code cell')
        return current_capture.display(*objects, **kwargs)

    # The notebook imports IPython.display when available. A process-local adapter
    # supplies just that hook; Matplotlib sees get_ipython() == None and stays Agg.
    # No installed package is created, changed or required.
    ipython = types.ModuleType('IPython')
    ipython.__spec__ = importlib.machinery.ModuleSpec('IPython', loader=None, is_package=True)
    ipython.__path__ = []
    ipython.__version__ = '0.0.0-qa-adapter'
    ipython.version_info = (0, 0, 0)
    ipython.get_ipython = lambda: None
    display_module = types.ModuleType('IPython.display')
    display_module.__spec__ = importlib.machinery.ModuleSpec('IPython.display', loader=None)
    display_module.display = rich_display
    ipython.display = display_module
    sys.modules['IPython'] = ipython
    sys.modules['IPython.display'] = display_module

    import matplotlib
    matplotlib.use('Agg', force=True)
    import matplotlib.pyplot as plt

    def flush_figures(*args, **kwargs):
        if current_capture is None:
            raise RuntimeError('Plot display called outside an executing code cell')
        for number in list(plt.get_fignums()):
            figure = plt.figure(number)
            current_capture.figure(figure)
            plt.close(figure)

    plt.show = flush_figures
    namespace = {'__name__': '__main__', 'display': rich_display}
    executed = 0
    failed_cell = None
    cells_audit = []
    failure_message = None

    for index, cell in enumerate(notebook['cells']):
        if cell.get('cell_type') != 'code':
            continue
        executed += 1
        cell['execution_count'] = executed
        current_capture = OutputCapture()
        cell_clock = time.monotonic()
        source = ''.join(cell['source']) if isinstance(cell['source'], list) else cell['source']
        filename = f'{notebook_path.name}:cell-{index}'
        try:
            with contextlib.redirect_stdout(CapturedStream(current_capture, 'stdout')), contextlib.redirect_stderr(CapturedStream(current_capture, 'stderr')):
                tree = ast.parse(source, filename=filename)
                # Match notebook last-expression display without re-executing it.
                last = tree.body[-1] if tree.body and isinstance(tree.body[-1], ast.Expr) else None
                statements = tree.body[:-1] if last is not None else tree.body
                module = ast.Module(body=statements, type_ignores=tree.type_ignores)
                exec(compile(module, filename, 'exec'), namespace)
                if last is not None:
                    value = eval(compile(ast.Expression(last.value), filename, 'eval'), namespace)
                    if value is not None:
                        current_capture.display_bundle({'text/plain': repr(value)}, execution_count=executed)
                flush_figures()
        except BaseException as error:
            failed_cell = index
            failure_message = f'{type(error).__name__}: {error}'
            current_capture.outputs.append({'output_type': 'error', 'ename': type(error).__name__,
                                            'evalue': str(error), 'traceback': ''.join(traceback.format_exception(error)).splitlines()})
            plt.close('all')
        cell['outputs'] = current_capture.outputs
        cells_audit.append({'cell_index': index, 'execution_count': executed,
                            'status': 'failed' if failed_cell == index else 'complete',
                            'outputs': len(current_capture.outputs), 'figures': current_capture.figure_count,
                            'elapsed_seconds': round(time.monotonic() - cell_clock, 3)})
        # Save completed cells and the first error; later cells remain unexecuted.
        write_json_atomic(notebook_path, notebook)
        print(f'Cell {index}: {cells_audit[-1]["status"]}, {len(current_capture.outputs)} outputs', flush=True)
        if failed_cell is not None:
            break

    unchanged_inputs = all(sha256(project_root / name) == checksum for name, checksum in input_hashes.items())
    if not unchanged_inputs and failure_message is None:
        failure_message = 'Results or manifest changed during notebook execution; these outputs cannot be accepted as one version.'
    status = 'complete' if failed_cell is None and unchanged_inputs else 'failed'
    audit = {'status': status, 'run_id': result['run_id'],
             'started_at_utc': started.isoformat(), 'finished_at_utc': datetime.now(timezone.utc).isoformat(),
             'elapsed_seconds': round(time.monotonic() - started_clock, 3), 'python_executable': sys.executable,
             'notebook': str(notebook_path), 'source_backup': str(backup),
             'executed_code_cells': executed, 'total_code_cells': sum(c.get('cell_type') == 'code' for c in notebook['cells']),
             'failed_cell_index': failed_cell, 'failure': failure_message,
             'source_snapshot': snapshot_audit, 'result_input_hashes': input_hashes,
             'result_inputs_unchanged': unchanged_inputs, 'notebook_sha256': sha256(notebook_path),
             'cells': cells_audit,
             'scope': 'QA sequential execution of the real-data notebook; no results rebuilt and no synthetic research evidence introduced'}
    audit_path = project_root / 'tmp/notebook_execution_audit.json'
    write_json_atomic(audit_path, audit)
    print(json.dumps({'status': status, 'code_cells': executed, 'figures': sum(c['figures'] for c in cells_audit),
                      'failure': failure_message, 'notebook': str(notebook_path), 'audit': str(audit_path)}, indent=2))
    return 0 if status == 'complete' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f'Notebook QA stopped: {type(error).__name__}: {error}', file=sys.stderr)
        raise SystemExit(1)
