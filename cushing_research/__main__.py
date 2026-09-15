from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .pipeline import load_config, run_build, save_json, make_manifest
from .market import validate_market
from .snapshot import verify_snapshot


def main():
    parser=argparse.ArgumentParser(description='Publication-aware Cushing and WTI research')
    parser.add_argument('command',choices=['fetch','validate','build','report'])
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--refresh',action='store_true',help='Explicitly refresh source data')
    parser.add_argument('--offline',action='store_true',help='Use existing snapshots only')
    parser.add_argument('--scope',choices=['public','full'],default='full',help='Validation scope; public checks source snapshots, full also requires actual-contract inputs')
    args=parser.parse_args(); root=args.root.resolve(); config=load_config(root)
    if args.offline and args.refresh: parser.error('--offline and --refresh are incompatible')
    if args.command=='fetch':
        if args.offline: parser.error('fetch requires network; build --offline uses snapshots')
        from .data import fetch_inventory, fetch_public_futures
        inventory,ia=fetch_inventory(root,config['data_cutoff'],refresh=args.refresh)
        futures,fa=fetch_public_futures(root,config['data_cutoff'],refresh=args.refresh)
        save_json(root/'data/processed/inventory_audit.json',ia)
        save_json(root/'data/processed/public_futures_audit.json',fa)
        print(json.dumps({'inventory_reports':len(inventory),'public_quote_days':len(futures)}))
    elif args.command=='validate':
        snapshot=verify_snapshot(root)
        _,audit=validate_market(root,config)
        save_json(root/'outputs/market_validation.json',audit)
        coverage={name:json.loads((root/'data/processed'/f'{name}_audit.json').read_text()).get('status')
                  for name in ['inventory','public_futures']}
        public_passed=all(status=='complete' for status in coverage.values())
        validation={'scope':args.scope,'public_snapshot':snapshot,'public_coverage':coverage,'market':audit,
                    'passed':public_passed and (args.scope=='public' or audit['passed'])}
        save_json(root/'outputs/validation.json',validation)
        print(json.dumps(validation,indent=2))
        return 0 if validation['passed'] else 2
    elif args.command=='build':
        result=run_build(root,config)
        print(json.dumps({'status':result['status'],'run_id':result['run_id'],
                          'coverage':result['coverage'],'blockers':result['blockers']},indent=2))
    else:
        cache=root/'tmp/matplotlib'
        cache.mkdir(parents=True,exist_ok=True)
        os.environ.setdefault('MPLCONFIGDIR',str(cache))
        from .report import render_report
        path=root/'outputs/results.json'
        if not path.exists(): parser.error('Run build first')
        verify_snapshot(root)
        result=json.loads(path.read_text())
        if make_manifest(root,result['config'])['run_id'] != result.get('run_id'):
            parser.error('Source, configuration or dependencies changed after build. Run build --offline before report.')
        paths=render_report(result,root/'outputs')
        print(json.dumps({k:str(v) for k,v in paths.items()},indent=2))
    return 0


if __name__=='__main__':
    try: sys.exit(main())
    except (ValueError,FileNotFoundError) as exc:
        print(f'Research stopped: {exc}',file=sys.stderr);sys.exit(2)
