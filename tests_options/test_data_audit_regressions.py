"""Regressions for independently reproduced feed/import/history failures."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import csv
import io
import json
from types import SimpleNamespace

import pytest

from options_lab.adapters import (
    DatabentoSnapshotBuilder, ImportValidationError, engineering_fixture,
    normalized_mbp1, parse_csv, parse_json,
)
from options_lab.engine import evaluate
from options_lab.models import utc
from options_lab.research_validation import (
    HedgeMark, HistoryObservation, HistoryValidationError, evaluate_history,
)
from options_lab.volatility import calibrate_market


def normalized_record(bundle, index, *, flags=128, seconds=0, empty=False):
    snapshot = bundle.snapshots[0]
    quote = snapshot.quote_map[snapshot.contracts[index].contract_id]
    undefined = (1 << 63) - 1
    record = SimpleNamespace(
        levels=[SimpleNamespace(
            bid_px=undefined if empty else round(quote.bid * 1e9),
            ask_px=undefined if empty else round(quote.ask * 1e9),
            bid_sz=0 if empty else quote.bid_size,
            ask_sz=0 if empty else quote.ask_size)],
        ts_event=int((utc(quote.as_of) + timedelta(seconds=seconds)).timestamp() * 1e9),
        instrument_id=index, flags=flags)
    return normalized_mbp1(record)


def full_builder(bundle):
    source = bundle.snapshots[0]
    builder = DatabentoSnapshotBuilder(
        source.contracts, {i: c.contract_id for i, c in enumerate(source.contracts)},
        mode='replay')
    for i in range(len(source.contracts)):
        row = normalized_record(bundle, i)
        snapshot = builder.update(row, received_at=row['as_of'])
    return builder, snapshot


def target_index(bundle):
    return next(i for i, c in enumerate(bundle.snapshots[0].contracts)
                if c.contract_id == bundle.settings.target_id)


def quote_statuses(bundle, snapshot):
    result = evaluate(snapshot, calibrate_market(bundle.snapshots[0]),
                      bundle.portfolio, bundle.settings)
    return [q['status'] for q in result['quotes']
            if q['contract_id'] == bundle.settings.target_id]


@pytest.mark.parametrize('flag,name', [(4, 'maybe_bad_book'), (8, 'bad_ts_recv'), (12, None)])
def test_native_bad_flags_suppress_then_valid_update_recovers(flag, name):
    bundle = engineering_fixture()
    builder, _ = full_builder(bundle)
    row = normalized_record(bundle, target_index(bundle), flags=flag, seconds=1)
    assert row['flags'], 'Known Databento quality bits must reach the quote-quality gate'
    if name:
        assert any(name in value for value in row['flags'])
    assert row['raw_flags'] == flag
    bad = builder.update(row, received_at=row['as_of'])
    assert all(node.contract_id != bundle.settings.target_id
               for node in calibrate_market(bad).nodes)
    assert quote_statuses(bundle, bad) == ['suppressed']
    good = normalized_record(bundle, target_index(bundle), flags=128, seconds=2)
    recovered = builder.update(good, received_at=good['as_of'])
    assert not recovered.quote_map[bundle.settings.target_id].flags
    assert quote_statuses(bundle, recovered) == ['indicative']


def test_normal_last_flag_does_not_block_quotes():
    bundle = engineering_fixture()
    builder, snapshot = full_builder(bundle)
    assert normalized_record(bundle, target_index(bundle), flags=128)['flags'] == ()
    assert quote_statuses(bundle, snapshot) == ['indicative']


def test_empty_book_invalidates_cached_quote_and_recovers_only_after_target_update():
    bundle = engineering_fixture()
    builder, _ = full_builder(bundle)
    empty = normalized_record(bundle, target_index(bundle), seconds=1, empty=True)
    missing = builder.update(empty, received_at=empty['as_of'])
    assert bundle.settings.target_id not in missing.quote_map
    assert 'indicative' not in quote_statuses(bundle, missing)
    other = normalized_record(bundle, 0, seconds=2)
    later = builder.update(other, received_at=other['as_of'])
    assert bundle.settings.target_id not in later.quote_map
    assert 'indicative' not in quote_statuses(bundle, later)
    good = normalized_record(bundle, target_index(bundle), seconds=3)
    recovered = builder.update(good, received_at=good['as_of'])
    assert recovered.feed_alive
    assert quote_statuses(bundle, recovered) == ['indicative']


@pytest.mark.parametrize('invalid', ['false', 'true', 0, 1, None, [], {}])
def test_snapshot_rejects_nonboolean_connection_state(invalid):
    bundle = engineering_fixture()
    raw = bundle.snapshots[0].to_dict() | {'feed_alive': invalid}
    with pytest.raises(ImportValidationError, match='feed_alive.*bool'):
        parse_json(raw)
    with pytest.raises(ValueError, match='feed_alive.*bool'):
        replace(bundle.snapshots[0], feed_alive=invalid)
    builder, _ = full_builder(bundle)
    row = normalized_record(bundle, 0, seconds=1)
    with pytest.raises(ImportValidationError, match='feed_alive.*bool'):
        builder.update(row, received_at=row['as_of'], feed_alive=invalid)


def csv_snapshot(snapshot, feed_alive):
    rows = []
    for contract in snapshot.contracts:
        quote = snapshot.quote_map[contract.contract_id]
        row = asdict(contract)
        row['underlyings'] = json.dumps(row['underlyings'])
        row.update(snapshot_as_of=snapshot.as_of, mode=snapshot.mode,
                   snapshot_source=snapshot.source, sequence=snapshot.sequence,
                   rate=snapshot.rate, received_at=snapshot.received_at,
                   feed_alive=feed_alive, quote_as_of=quote.as_of,
                   quote_kind=quote.kind, quote_source=quote.source,
                   bid=quote.bid, ask=quote.ask, bid_size=quote.bid_size,
                   ask_size=quote.ask_size, mark=quote.mark, flags=json.dumps(quote.flags))
        rows.append(row)
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


@pytest.mark.parametrize('invalid', ['flase', '0', '1', 'yes'])
def test_csv_connection_state_rejects_typo_or_numeric_alias(invalid):
    with pytest.raises(ImportValidationError, match='feed_alive.*true.*false'):
        parse_csv(csv_snapshot(engineering_fixture().snapshots[0], invalid))


@pytest.mark.parametrize('text,value', [('true', True), ('false', False), (' TRUE ', True), (' FALSE ', False)])
def test_csv_connection_state_accepts_explicit_boolean(text, value):
    bundle = engineering_fixture()
    snapshot = parse_csv(csv_snapshot(bundle.snapshots[0], text)).snapshots[0]
    assert snapshot.feed_alive is value
    assert quote_statuses(bundle, snapshot) == (['indicative'] if value else ['suppressed'])


def observation():
    at = datetime(2025, 1, 1, 14, tzinfo=timezone.utc)
    return HistoryObservation('audit', at.date(), at, at, at + timedelta(hours=1),
        'snapshot', 'next-snapshot', 'synthetic numeric audit', 'a' * 64,
        'test_fixture', 'CSO-AUDIT', 1, 1000., 2., 3., 'audit-fees',
        (HedgeMark('CL-AUDIT', 'future', 1000., 79., 80., .01, 1.),
         HedgeMark('LCE-AUDIT', 'vanilla', 1000., 4., 4.5, .02, 1.)))


@pytest.mark.parametrize('field', ['target_mark', 'next_target_mark'])
def test_history_rejects_negative_target_option_premium(field):
    with pytest.raises(HistoryValidationError, match='option.*premium.*negative'):
        evaluate_history([replace(observation(), **{field: -.01})])


@pytest.mark.parametrize('field', ['mark', 'next_mark'])
def test_history_rejects_negative_vanilla_premium(field):
    sample = observation()
    bad = replace(sample.hedges[1], **{field: -.01})
    with pytest.raises(HistoryValidationError, match='option.*premium.*negative'):
        evaluate_history([replace(sample, hedges=(sample.hedges[0], bad))])


def test_history_allows_zero_option_premiums_and_negative_futures():
    sample = observation()
    valid = replace(sample, target_mark=0., next_target_mark=0., hedges=(
        replace(sample.hedges[0], mark=-2., next_mark=-1.),
        replace(sample.hedges[1], mark=0., next_mark=0.)))
    report = evaluate_history([valid])
    assert report['status'] == 'pending'
    assert not report['metrics']
