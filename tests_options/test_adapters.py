from dataclasses import asdict
from datetime import datetime, timezone, timedelta
import csv
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from options_lab.adapters import (
    DatabentoAdapter, DatabentoRequest, ImportValidationError,
    engineering_fixture, parse_csv, parse_json, replay, DatabentoSnapshotBuilder,
)


def payload():
    return json.loads((Path(__file__).parents[1]/'options_lab/examples/engineering_replay.json').read_text())


def test_fixture_stays_explicit_and_replay_returns_whole_states():
    bundle = engineering_fixture()
    assert len(bundle.snapshots) == 3
    assert all(s.mode == 'engineering_fixture' for s in replay(bundle))
    assert 'SYNTHETIC' in bundle.warnings[0]
    assert bundle.portfolio[0].contract_id == bundle.settings.target_id


@pytest.mark.parametrize('mutation,match', [
    (lambda p: p['snapshots'][0]['contracts'][0].pop('multiplier'), 'missing metadata'),
    (lambda p: p['snapshots'][0].update(as_of='2026-01-01T00:00:00'), 'Timezone'),
    (lambda p: p['snapshots'][0]['quotes'][0].update(as_of='2099-01-01T00:00:00+00:00'), 'Future quote'),
    (lambda p: p['snapshots'][0]['contracts'][2].update(underlyings=['CL_UNKNOWN']), 'exact CL definition'),
    (lambda p: p['snapshots'][0]['contracts'][2].update(product='LO'), 'Unsupported/mismatched'),
    (lambda p: p['snapshots'][1].update(sequence=0), 'sequence/time'),
    (lambda p: p['snapshots'][0]['quotes'][0].update(kind='settlement'), 'Settlement is not'),
])
def test_rejects_metadata_and_temporal_failures(mutation, match):
    value = payload(); mutation(value)
    with pytest.raises(ImportValidationError, match=match):
        parse_json(value)


def test_no_quote_carry_forward_in_replay():
    value=payload(); value['snapshots'][1]['quotes'].pop()
    bundle=parse_json(value)
    assert len(bundle.snapshots[1].quotes)==len(bundle.snapshots[0].quotes)-1
    assert any('not carried forward' in warning for warning in bundle.warnings)


def test_rank_symbol_is_not_exact_contract():
    value=payload(); value.pop('portfolio'); value.pop('settings')
    value['snapshots']=value['snapshots'][:1]
    value['snapshots'][0]['contracts']=value['snapshots'][0]['contracts'][:1]
    value['snapshots'][0]['quotes']=value['snapshots'][0]['quotes'][:1]
    value['snapshots'][0]['contracts'][0]['contract_id']='CL.c.0'
    value['snapshots'][0]['quotes'][0]['contract_id']='CL.c.0'
    with pytest.raises(ImportValidationError, match='Continuous/rank'):
        parse_json(value)


def test_csv_full_state_round_trip():
    snapshot=engineering_fixture().snapshots[0]
    rows=[]
    for contract in snapshot.contracts:
        q=snapshot.quote_map[contract.contract_id]
        row=asdict(contract)
        row['underlyings']=json.dumps(row['underlyings'])
        row.update(snapshot_as_of=snapshot.as_of, mode=snapshot.mode,
                   snapshot_source=snapshot.source, sequence=snapshot.sequence,
                   rate=snapshot.rate,received_at=snapshot.received_at,
                   quote_as_of=q.as_of,quote_kind=q.kind,quote_source=q.source,
                   bid=q.bid,ask=q.ask,bid_size=q.bid_size,ask_size=q.ask_size,mark=q.mark,
                   flags=json.dumps(q.flags))
        rows.append(row)
    stream=io.StringIO(); writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
    writer.writeheader();writer.writerows(rows)
    loaded=parse_csv(stream.getvalue())
    assert loaded.snapshots[0] == snapshot


def test_vendor_no_authorization_never_checks_credentials_or_network(tmp_path):
    adapter=DatabentoAdapter(client_factory=lambda **_: pytest.fail('network/client created'))
    request=DatabentoRequest(('CLM6',),'definition','2026-01-01T00:00:00Z','2026-01-02T00:00:00Z')
    with pytest.raises(PermissionError,match='authorization'):
        adapter.download(request,tmp_path)


def test_vendor_cost_cap_prevents_fetch(monkeypatch):
    monkeypatch.setenv('DATABENTO_API_KEY','test-only-not-a-key')
    client=SimpleNamespace(metadata=SimpleNamespace(get_cost=lambda **_: 2.0),
                           timeseries=SimpleNamespace(get_range=lambda **_: pytest.fail('fetch called')))
    adapter=DatabentoAdapter(client_factory=lambda **_:client)
    request=DatabentoRequest(('CLM6',),'definition','2026-01-01T00:00:00Z','2026-01-02T00:00:00Z')
    output=Path(__file__).parents[1]/'options_lab_runs/private/test_cap_no_write'
    with pytest.raises(PermissionError,match='estimate'):
        adapter.download(request,output,authorized=True,max_estimated_cost_usd=.01)
    assert not output.exists()


def test_vendor_normalizer_requires_explicit_units_and_definitions():
    snap=engineering_fixture().snapshots[0]
    builder=DatabentoSnapshotBuilder(snap.contracts,{i:c.contract_id for i,c in enumerate(snap.contracts)},mode='replay')
    with pytest.raises(ImportValidationError,match='USD/bbl'):
        builder.update({'instrument_id':0,'as_of':snap.as_of,'bid':79,'ask':80},received_at=snap.received_at)
    result=builder.update({'instrument_id':0,'as_of':snap.as_of,'bid':79.,'ask':80.,'units':'USD/bbl'},received_at=snap.received_at)
    assert len(result.contracts)==20 and len(result.quotes)==1
    assert result.sequence==1


def test_native_mbp_price_scaling_and_missing_sentinel():
    from options_lab.adapters import normalized_mbp1
    level=SimpleNamespace(bid_px=79_250_000_000,ask_px=(1<<63)-1,bid_sz=4,ask_sz=0)
    record=SimpleNamespace(levels=[level],ts_event=1_700_000_000_000_000_000,instrument_id=42,flags=128)
    row=normalized_mbp1(record)
    assert row['bid']==79.25 and row['ask'] is None
    assert row['units']=='USD/bbl' and row['raw_flags']==128
    assert normalized_mbp1(SimpleNamespace()) is None


def test_live_capture_does_not_connect_without_explicit_entitlement(tmp_path):
    from options_lab.adapters import capture_live
    with pytest.raises(PermissionError,match='entitlement'):
        capture_live(tmp_path/'not-read.json',tmp_path/'not-created')
    assert not (tmp_path/'not-created').exists()


def test_official_historical_constructor_uses_supported_arguments(monkeypatch):
    import sys
    monkeypatch.setenv('DATABENTO_API_KEY','test-placeholder')
    instance=SimpleNamespace(metadata=SimpleNamespace(),timeseries=SimpleNamespace())
    def historical(*,key):
        assert key=='test-placeholder'
        return instance
    monkeypatch.setitem(sys.modules,'databento',SimpleNamespace(Historical=historical))
    assert DatabentoAdapter()._client(12.) is instance
    assert instance.metadata.TIMEOUT==12. and instance.timeseries.TIMEOUT==12.


def test_disconnect_requires_all_quotes_before_any_quote_proposal():
    from options_lab.engine import evaluate
    from options_lab.volatility import calibrate_market
    bundle=engineering_fixture(); original=bundle.snapshots[0]
    builder=DatabentoSnapshotBuilder(original.contracts,{i:c.contract_id for i,c in enumerate(original.contracts)},mode='replay')
    def update(index):
        c=original.contracts[index];q=original.quote_map[c.contract_id]
        return builder.update({'instrument_id':index,'as_of':q.as_of,'units':'USD/bbl','bid':q.bid,'ask':q.ask,'bid_size':q.bid_size,'ask_size':q.ask_size},received_at=original.received_at)
    for i in range(len(original.contracts)):
        full=update(i)
    assert full.feed_alive
    builder.begin_resync()
    for i in range(len(original.contracts)-1):
        partial=update(i)
    assert not partial.feed_alive and len(partial.quotes)==len(original.quotes)-1
    vol=calibrate_market(partial)
    result=evaluate(partial,vol,bundle.portfolio,bundle.settings)
    assert result['quotes'] and all(q['status']=='suppressed' for q in result['quotes'])
    recovered=update(len(original.contracts)-1)
    assert recovered.feed_alive
    assert all(q.as_of==original.quote_map[q.contract_id].as_of for q in recovered.quotes)
