"""Explicit local imports and opt-in, bounded read-only vendor retrieval.

Imports never turn settlements into BBOs, infer contract legs, or fill missing
quotes. Vendor normalization requires an independently resolved definition map;
raw downloads are not automatically eligible snapshots or real-data evidence.
"""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import queue
import re
import time
import threading
from typing import Any, Callable, Iterator, Mapping

from .models import Contract, MarketSnapshot, Position, Quote, Settings, utc

MAX_IMPORT_BYTES = 20_000_000
MAX_SNAPSHOTS = 2000
PRODUCT_KINDS = {"CL": "future", "LCE": "vanilla", "LC": "vanilla", "B7A": "cso", "7A": "cso"}
CONTRACT_FIELDS = {"contract_id", "product", "kind", "underlyings", "expiry", "multiplier", "tick_size", "currency", "exercise", "source"}


class ImportValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ImportBundle:
    snapshots: tuple[MarketSnapshot, ...]
    portfolio: tuple[Position, ...] = ()
    settings: Settings | None = None
    warnings: tuple[str, ...] = ()
    source_sha256: str = ""


def _decode(data: bytes | str) -> str:
    if isinstance(data, bytes):
        if len(data) > MAX_IMPORT_BYTES:
            raise ImportValidationError("Import exceeds the 20 MB limit")
        return data.decode("utf-8-sig")
    if len(data.encode("utf8")) > MAX_IMPORT_BYTES:
        raise ImportValidationError("Import exceeds the 20 MB limit")
    return data.lstrip("\ufeff")


def _require_fields(data: Mapping, fields: set[str], label: str) -> None:
    missing = fields-set(data)
    if missing:
        raise ImportValidationError(f"{label} missing metadata: {', '.join(sorted(missing))}")


def validate_snapshot(snapshot: MarketSnapshot, *, now: datetime | None = None) -> MarketSnapshot:
    """Validate semantics in addition to dataclass structural validation.

    Source strings/full identifiers are declarations. Only a checked raw-source
    manifest can verify that supplied observed data actually came from a venue.
    """
    now = utc(now or datetime.now(timezone.utc))
    event = utc(snapshot.as_of)
    if event > now:
        raise ImportValidationError("Snapshot as_of is in the future")
    if snapshot.mode not in {"observed_eod", "replay", "live", "engineering_fixture"}:
        raise ImportValidationError("Unsupported snapshot mode")
    if not snapshot.source.strip() or not snapshot.contracts:
        raise ImportValidationError("Snapshot needs contracts and a source")
    if snapshot.sequence < 0 or isinstance(snapshot.sequence, bool) or not isinstance(snapshot.sequence, int):
        raise ImportValidationError("Snapshot sequence must be a nonnegative integer")
    if snapshot.received_at and utc(snapshot.received_at) < event:
        raise ImportValidationError("received_at precedes event time")
    if snapshot.received_at and utc(snapshot.received_at) > now:
        raise ImportValidationError("received_at is in the future")
    cmap = snapshot.contract_map
    for c in snapshot.contracts:
        if c.product not in PRODUCT_KINDS or c.kind != PRODUCT_KINDS[c.product]:
            raise ImportValidationError(f"Unsupported/mismatched product {c.product}; LO American options are not LCE")
        if not c.source.strip() or c.currency != "USD" or not all(math.isfinite(v) and v > 0 for v in (c.multiplier, c.tick_size)):
            raise ImportValidationError("Contract source, USD currency and finite units are required")
        if re.fullmatch(r"(?:CL|LCE|B7A)(?:[123]|[._](?:[cvn]?[._]?)?\d+)?", c.contract_id, re.I) or c.contract_id.endswith((".FUT", ".OPT")):
            raise ImportValidationError("Continuous/rank/parent symbols are not exact contract IDs")
        if c.kind == "future" and c.underlyings:
            raise ImportValidationError("Futures cannot declare option underlying legs")
        if len(set(c.underlyings)) != len(c.underlyings):
            raise ImportValidationError("Spread legs must be different exact futures")
        for leg in c.underlyings:
            if leg not in cmap or cmap[leg].kind != "future" or cmap[leg].product != "CL":
                raise ImportValidationError("Every option leg needs an exact CL definition in the same snapshot")
            if utc(c.expiry) > utc(cmap[leg].expiry):
                raise ImportValidationError("Option expiry follows its underlying future expiry")
    for q in snapshot.quotes:
        if not q.source.strip() or q.kind not in {"bbo", "settlement", "trade"}:
            raise ImportValidationError("Quote needs source and explicit bbo/settlement/trade kind")
        if q.kind == "settlement" and (q.bid is not None or q.ask is not None):
            raise ImportValidationError("Settlement is not an observed bid/ask")
        if q.mid is None and q.bid is None and q.ask is None:
            raise ImportValidationError("Quote has no observed price")
        if cmap[q.contract_id].kind != "future" and any(x is not None and x < 0 for x in (q.bid, q.ask, q.mark)):
            raise ImportValidationError("Option premium cannot be negative")
    return snapshot


def _snapshot(data: Mapping[str, Any]) -> MarketSnapshot:
    _require_fields(data, {"contracts", "quotes", "as_of", "mode", "source", "sequence"}, "snapshot")
    for contract in data["contracts"]:
        _require_fields(contract, CONTRACT_FIELDS, "contract")
        if not isinstance(contract["underlyings"], (list, tuple)):
            raise ImportValidationError("underlyings must be an ordered array of exact CL IDs")
        if contract["kind"] != "future":
            _require_fields(contract, {"strike", "right"}, "option")
    for quote in data["quotes"]:
        _require_fields(quote, {"contract_id", "as_of", "kind", "source"}, "quote")
    return validate_snapshot(MarketSnapshot.from_dict(data))


def parse_json(data: bytes | str | dict | list) -> ImportBundle:
    try:
        raw_bytes = data if isinstance(data, bytes) else data.encode() if isinstance(data, str) else None
        if isinstance(data, (str, bytes)):
            payload = json.loads(_decode(data), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f"Invalid JSON number {x}")))
        else:
            payload = data
        encoded = json.dumps(payload, sort_keys=True, allow_nan=False).encode()
        if len(encoded) > MAX_IMPORT_BYTES:
            raise ImportValidationError("Import exceeds the 20 MB limit")
        meta = payload if isinstance(payload, dict) else {}
        rows = payload if isinstance(payload, list) else payload.get("snapshots", [payload.get("snapshot", payload)])
        if not rows or len(rows) > MAX_SNAPSHOTS:
            raise ImportValidationError("Expected 1–2000 full snapshots")
        snapshots = tuple(_snapshot(row) for row in rows)
        for previous, current in zip(snapshots, snapshots[1:]):
            if current.sequence <= previous.sequence or utc(current.as_of) < utc(previous.as_of):
                raise ImportValidationError("Replay sequence/time must increase; snapshots cannot be silently reordered")
            if (current.mode == "engineering_fixture") != (previous.mode == "engineering_fixture"):
                raise ImportValidationError("Observed and engineering snapshots cannot share a replay")
        positions = tuple(Position(**row) for row in meta.get("portfolio", []))
        if len({p.contract_id for p in positions}) != len(positions):
            raise ImportValidationError("Duplicate portfolio contracts")
        if any(p.contract_id not in snapshots[0].contract_map for p in positions):
            raise ImportValidationError("Portfolio references an undefined contract")
        settings = Settings.from_dict(meta["settings"]) if meta.get("settings") is not None else None
        if settings and settings.target_id and settings.target_id not in snapshots[0].contract_map:
            raise ImportValidationError("Settings target is undefined")
        warnings = []
        if any(s.mode == "engineering_fixture" for s in snapshots):
            warnings.append("SYNTHETIC ENGINEERING FIXTURE — not observed WTI prices or real-data calibration.")
        else:
            warnings.append("Observed source labels are declared by the importer; verify against raw files and source manifest.")
        if any(q.kind == "settlement" for s in snapshots for q in s.quotes):
            warnings.append("Settlement marks cannot establish executable bid/ask spreads or intraday timing.")
        if any(len(s.quotes) < len(s.contracts) for s in snapshots):
            warnings.append("One or more full snapshots contain quote gaps; missing quotes are not carried forward.")
        return ImportBundle(snapshots, positions, settings, tuple(warnings), hashlib.sha256(raw_bytes if raw_bytes is not None else encoded).hexdigest())
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ImportValidationError):
            raise
        raise ImportValidationError(str(exc)) from exc


def load_json(path: str | Path) -> ImportBundle:
    return parse_json(Path(path).read_bytes())


def parse_csv(data: bytes | str) -> ImportBundle:
    """Flattened full snapshots, one definition/quote per row.

    Header includes snapshot_as_of, mode, snapshot_source, sequence, rate,
    received_at, all Contract fields and quote_as_of, quote_kind, quote_source,
    bid,ask,bid_size,ask_size,mark,flags. underlyings/flags are JSON arrays.
    Empty quote_kind means an explicit quote gap. Portfolio/settings use JSON.
    """
    text = _decode(data)
    reader = csv.DictReader(io.StringIO(text))
    required = (CONTRACT_FIELDS | {"snapshot_as_of", "mode", "snapshot_source", "sequence", "quote_as_of", "quote_kind", "quote_source"})
    _require_fields({x: None for x in reader.fieldnames or []}, required, "CSV")
    groups: dict[int, dict] = {}
    for index, row in enumerate(reader, start=2):
        try:
            seq = int(row["sequence"])
            feed_text = (row.get("feed_alive") or "true").strip().lower()
            if feed_text not in {"true", "false"}:
                raise ImportValidationError("feed_alive must be true or false")
            shared = {"as_of": row["snapshot_as_of"], "mode": row["mode"], "source": row["snapshot_source"], "sequence": seq,
                      "rate": float(row.get("rate") or 0), "received_at": row.get("received_at") or None,
                      "feed_alive": feed_text == "true"}
            if seq in groups:
                if any(groups[seq][k] != v for k, v in shared.items()):
                    raise ImportValidationError("Inconsistent shared snapshot metadata")
            else:
                groups[seq] = {**shared, "contracts": [], "quotes": []}
            contract = {k: row[k] for k in CONTRACT_FIELDS}
            contract["underlyings"] = json.loads(contract["underlyings"])
            for k in ("multiplier", "tick_size"):
                contract[k] = float(contract[k])
            contract.update(strike=float(row["strike"]) if row.get("strike") else None, right=row.get("right") or None)
            groups[seq]["contracts"].append(contract)
            if row["quote_kind"]:
                quote = {"contract_id": row["contract_id"], "as_of": row["quote_as_of"], "kind": row["quote_kind"], "source": row["quote_source"], "flags": json.loads(row.get("flags") or "[]")}
                quote.update({k: float(row[k]) if row.get(k) else None for k in ("bid", "ask", "bid_size", "ask_size", "mark")})
                groups[seq]["quotes"].append(quote)
        except (ValueError, TypeError, KeyError) as exc:
            raise ImportValidationError(f"CSV row {index}: {exc}") from exc
    result = parse_json({"snapshots": list(groups.values())})
    return replace(result, source_sha256=hashlib.sha256(text.encode()).hexdigest())


def load_csv(path: str | Path) -> ImportBundle:
    return parse_csv(Path(path).read_bytes())


def replay(bundle: ImportBundle) -> Iterator[MarketSnapshot]:
    """Yield complete immutable states; no merge with prior snapshot quotes."""
    yield from bundle.snapshots


def engineering_fixture() -> ImportBundle:
    return load_json(Path(__file__).parent / "examples" / "engineering_replay.json")


@dataclass(frozen=True)
class DatabentoRequest:
    symbols: tuple[str, ...]
    schema: str
    start: str
    end: str
    dataset: str = "GLBX.MDP3"
    stype_in: str = "raw_symbol"
    max_records: int = 100000
    max_seconds: float = 30.0

    def __post_init__(self):
        start, end = utc(self.start), utc(self.end)
        if self.dataset != "GLBX.MDP3" or self.schema not in {"definition", "mbp-1", "bbo-1s", "bbo-1m", "statistics"}:
            raise ValueError("Only explicit CME definitions/BBO/statistics requests are supported")
        if not 0 < (end-start).total_seconds() <= 86400 or end > datetime.now(timezone.utc):
            raise ValueError("Request must cover at most one completed day")
        if not 1 <= len(self.symbols) <= 20 or not 1 <= self.max_records <= 100000 or not 0 < self.max_seconds <= 60:
            raise ValueError("Request exceeds symbol/record/time cap")
        if self.stype_in not in {"raw_symbol", "instrument_id", "parent"}:
            raise ValueError("Explicit raw, instrument_id or parent symbology required")

    def query(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k not in {"max_records", "max_seconds"}} | {"limit": self.max_records}


class DatabentoAdapter:
    """Metadata estimates are separate from explicitly authorized paid retrieval.

    Never accesses credentials except DATABENTO_API_KEY; never creates accounts,
    subscriptions, purchases or licensing agreements. No automatic fallback fetch.
    A cost estimate is not a guaranteed invoice ceiling; use vendor account limits.
    """
    def __init__(self, *, client_factory: Callable | None = None):
        self._factory = client_factory

    def _client(self, timeout: float):
        key = os.environ.get("DATABENTO_API_KEY")
        if not key or not key.strip():
            raise RuntimeError("DATABENTO_API_KEY is not configured; no network request made")
        if self._factory:
            return self._factory(key=key, timeout=timeout)
        try:
            import databento as db
        except ImportError as exc:
            raise RuntimeError("Optional databento SDK is not installed") from exc
        # Official SDK Historical() accepts key/gateway only. Its HTTP endpoint
        # objects expose TIMEOUT, used for connect/read timeouts in the transport.
        client = db.Historical(key=key)
        client.metadata.TIMEOUT = timeout
        client.timeseries.TIMEOUT = timeout
        return client

    def estimate(self, request: DatabentoRequest) -> float:
        estimate = float(self._client(request.max_seconds).metadata.get_cost(**request.query()))
        if not math.isfinite(estimate) or estimate < 0:
            raise RuntimeError("Vendor returned an invalid cost estimate")
        return estimate

    def download(self, request: DatabentoRequest, output_dir: str | Path, *, authorized: bool = False, max_estimated_cost_usd: float = 0.0) -> dict:
        if not authorized:
            raise PermissionError("Explicit authorization required for a potentially billable data request")
        if not math.isfinite(max_estimated_cost_usd) or max_estimated_cost_usd < 0:
            raise ValueError("A finite nonnegative estimate cap is required")
        output = Path(output_dir).resolve()
        private_root = (Path(__file__).resolve().parents[1] / "options_lab_runs" / "private").resolve()
        if not output.is_relative_to(private_root):
            raise ValueError("Vendor raw data must stay inside options_lab_runs/private/")
        token = hashlib.sha256(json.dumps(request.query(), sort_keys=True).encode()).hexdigest()[:16]
        path = output / f"databento_{token}.dbn"
        if path.exists():
            raise FileExistsError("Refusing to overwrite an existing raw data artifact")
        started = time.monotonic()
        client = self._client(request.max_seconds)
        cost = float(client.metadata.get_cost(**request.query()))
        if not math.isfinite(cost) or cost < 0 or cost > max_estimated_cost_usd:
            raise PermissionError("Vendor estimate exceeds the authorized estimate cap")
        if time.monotonic()-started >= request.max_seconds:
            raise TimeoutError("Cost estimation exhausted request time budget")
        # The second request uses only the remaining transport timeout budget.
        client = self._client(request.max_seconds - (time.monotonic()-started))
        data = client.timeseries.get_range(**request.query())
        output.mkdir(parents=True, exist_ok=True)
        data.to_file(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = {"request": request.query(), "estimated_cost_usd": cost, "estimate_cap_usd": max_estimated_cost_usd,
                    "path": str(path), "sha256": digest, "bytes": path.stat().st_size,
                    "elapsed_seconds": time.monotonic()-started,
                    "normalization_status": "pending_definitions_and_completeness_validation",
                    "warning": "Record limit may truncate results; no automatic continuation. Estimate is not an invoice ceiling. SDK timeout limits connect/read waits, not total streaming wall time."}
        path.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2))
        return manifest

    def live_records(self, *, symbols: tuple[str, ...], subscription_confirmed: bool = False,
                     max_seconds: float = 30.0, max_records: int = 10000,
                     coalesce_seconds: float = 0.0) -> Iterator[Any]:
        """Bounded raw read-only live stream; existing entitlements required.

        This transport does not establish live-demo completion. Resolve exact
        definitions and pass records through DatabentoSnapshotBuilder first.
        """
        if not subscription_confirmed:
            raise PermissionError("Confirm existing live subscription/entitlement before opening a stream")
        if not 1 <= len(symbols) <= 20 or not 0 < max_seconds <= 3600 or not 1 <= max_records <= 2_000_000 or not 0 <= coalesce_seconds <= 5:
            raise ValueError("Live request exceeds explicit symbol/time/record caps")
        key = os.environ.get("DATABENTO_API_KEY")
        if not key or not key.strip():
            raise RuntimeError("DATABENTO_API_KEY is not configured")
        import databento as db
        client = db.Live(key=key)
        received: queue.Queue = queue.Queue(maxsize=min(max_records,100000))
        latest = {}; lock = threading.Lock(); stop = threading.Event()
        raw_count = 0
        def callback(record):
            nonlocal raw_count
            raw_count += 1
            if raw_count > max_records:
                stop.set()
                client.stop()
                return
            if coalesce_seconds and hasattr(record, 'levels'):
                with lock:
                    latest[int(record.instrument_id)] = record
                return
            try:
                received.put_nowait(record)
            except queue.Full:
                stop.set()
                client.stop()
        client.add_callback(callback)
        client.subscribe(dataset="GLBX.MDP3", schema="mbp-1", stype_in="raw_symbol", symbols=list(symbols))
        deadline = time.monotonic()+max_seconds
        try:
            client.start()
            if coalesce_seconds:
                while time.monotonic() < deadline and not stop.is_set():
                    stop.wait(min(coalesce_seconds, max(0.0, deadline-time.monotonic())))
                    with lock:
                        batch = list(latest.values()); latest.clear()
                    while not received.empty():
                        yield received.get_nowait()
                    for record in sorted(batch,key=lambda record:int(record.ts_event)):
                        yield record
                return
            for _ in range(max_records):
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    break
                try:
                    yield received.get(timeout=remaining)
                except queue.Empty:
                    break
        finally:
            client.terminate()


class DatabentoSnapshotBuilder:
    """Build full snapshots from resolved definitions and normalized quote rows.

    Caller resolves numeric instrument IDs through the definition schema first,
    including the B7A synthetic underlying's two exact CL legs. Native DBN fixed
    precision must be normalized to USD/bbl before calling update; units='USD/bbl'
    is mandatory. A symbol mapping message is not a contract definition.
    """
    def __init__(self, contracts: tuple[Contract, ...], instrument_map: Mapping[int, str], *, mode: str = "live", rate: float = 0.0):
        if mode not in {"live", "replay"}:
            raise ValueError("Builder mode must be live or replay")
        self.contracts, self.instrument_map, self.mode, self.rate = contracts, dict(instrument_map), mode, rate
        self.quotes: dict[str, Quote] = {}
        self.sequence = 0
        self.last_as_of: datetime | None = None
        if len(self.instrument_map) != len(contracts) or set(self.instrument_map.values()) != {c.contract_id for c in contracts}:
            raise ImportValidationError("Every contract must have a resolved provider instrument ID")

    def begin_resync(self) -> None:
        """Discard cached prices after a disconnect; all contracts must refresh."""
        self.quotes.clear()
        self.last_as_of = None

    def update(self, row: Mapping[str, Any], *, received_at: str, feed_alive: bool = True) -> MarketSnapshot:
        if not isinstance(feed_alive, bool):
            raise ImportValidationError("feed_alive must be a boolean")
        if row.get("units") != "USD/bbl":
            raise ImportValidationError("Explicit normalized USD/bbl prices required")
        instrument_id = int(row["instrument_id"])
        if instrument_id not in self.instrument_map:
            raise ImportValidationError("Unknown provider instrument ID; definitions must be resolved first")
        event = utc(row["as_of"])
        if self.last_as_of and event < self.last_as_of:
            raise ImportValidationError("Out-of-order update rejected; replay must be ordered upstream")
        contract_id = self.instrument_map[instrument_id]
        quote = Quote(contract_id=contract_id, as_of=row["as_of"], source="Databento GLBX.MDP3", kind=row.get("kind", "bbo"),
                      **{k: row.get(k) for k in ("bid", "ask", "bid_size", "ask_size", "mark")}, flags=tuple(row.get("flags", ())))
        new_quotes = dict(self.quotes)
        if quote.kind == "bbo" and all(value is None for value in (quote.bid, quote.ask, quote.mark)):
            # Native undefined-price sentinels mean this book is now unavailable.
            # Commit an explicit gap, so later updates of other contracts cannot
            # revive the previous book. A valid update of this contract restores it.
            new_quotes.pop(contract_id, None)
        else:
            new_quotes[contract_id] = quote
        fully_resynchronized = set(new_quotes) == {c.contract_id for c in self.contracts}
        snapshot = validate_snapshot(MarketSnapshot(self.contracts, tuple(new_quotes[k] for k in sorted(new_quotes)), row["as_of"],
                                                   self.mode, "Databento GLBX.MDP3", self.sequence+1, self.rate, feed_alive and fully_resynchronized, received_at))
        self.quotes, self.last_as_of, self.sequence = new_quotes, event, self.sequence+1
        return snapshot


def normalized_mbp1(record: Any) -> dict | None:
    """Normalize native DBN MBP-1 fixed precision; non-price records are ignored.

    DBN's F_MAYBE_BAD_BOOK (4) and F_BAD_TS_RECV (8) are quality failures and
    propagate to Quote.flags, which suppresses calibration/quote eligibility.
    F_LAST (128) and the other structural flags are not quality failures. Raw
    bits remain in the capture log; this is not full exchange-status validation.
    """
    if not hasattr(record, 'levels') or not record.levels:
        return None
    level = record.levels[0]
    undefined = (1 << 63)-1
    def px(value):
        return None if value == undefined else float(value)/1_000_000_000.0
    raw_flags = int(getattr(record, 'flags', 0))
    quality_flags = tuple(name for bit, name in (
        (4, 'databento_maybe_bad_book'), (8, 'databento_bad_ts_recv')) if raw_flags & bit)
    event = datetime.fromtimestamp(int(record.ts_event)/1_000_000_000.0, timezone.utc).isoformat()
    return {'instrument_id': int(record.instrument_id), 'as_of': event, 'units': 'USD/bbl',
            'bid': px(level.bid_px), 'ask': px(level.ask_px),
            'bid_size': float(level.bid_sz), 'ask_size': float(level.ask_sz),
            'kind': 'bbo', 'flags': quality_flags, 'raw_flags': raw_flags}


def capture_live(definitions_path: str | Path, output_dir: str | Path, *,
                 subscription_confirmed: bool = False, max_seconds: float = 30.0,
                 max_records: int = 1000, coalesce_seconds: float = 0.0) -> dict:
    """Explicit CLI bridge: resolved live updates -> full snapshot -> shared core.

    No stream starts without the entitlement flag and environment credential.
    Definitions JSON needs contracts, instrument_map (numeric ID -> raw symbol),
    portfolio, settings. Contract IDs must be actual vendor raw symbols because
    subscription symbology is raw_symbol. Saves licensed data privately only.
    """
    if not subscription_confirmed:
        raise PermissionError('Explicit existing-entitlement confirmation is required')
    from .controller import DeskController
    definitions = json.loads(Path(definitions_path).read_text())
    _require_fields(definitions, {'contracts', 'instrument_map', 'portfolio', 'settings'}, 'live definitions')
    for row in definitions['contracts']:
        _require_fields(row, CONTRACT_FIELDS, 'live contract')
    contracts = tuple(Contract.from_dict(row) for row in definitions['contracts'])
    if any('synthetic' in c.source.lower() or 'fixture' in c.contract_id.lower() for c in contracts):
        raise ImportValidationError('Synthetic fixture definitions cannot be sent to a real live feed')
    instrument_map = {int(k):v for k,v in definitions['instrument_map'].items()}
    builder = DatabentoSnapshotBuilder(contracts, instrument_map, rate=float(definitions.get('rate',0.0)))
    now = datetime.now(timezone.utc).isoformat()
    validate_snapshot(MarketSnapshot(contracts, (), now, 'live', 'Databento GLBX.MDP3; supplied resolved definitions'))
    portfolio = tuple(Position(**row) for row in definitions['portfolio'])
    settings = Settings.from_dict(definitions['settings'])
    output = Path(output_dir).resolve()
    private_root = (Path(__file__).resolve().parents[1]/'options_lab_runs/private').resolve()
    if not output.is_relative_to(private_root):
        raise ValueError('Live captures must stay in options_lab_runs/private/')
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('Choose a new empty live capture directory')
    output.mkdir(parents=True,exist_ok=True)
    snapshots = []; bundles = []; events = []; controller = None; byte_count = 0
    started = time.monotonic(); last_saved = 0.0
    source_hash = hashlib.sha256(Path(definitions_path).read_bytes()).hexdigest()
    records = DatabentoAdapter().live_records(symbols=tuple(c.contract_id for c in contracts),
        subscription_confirmed=True,max_seconds=max_seconds,max_records=max_records,coalesce_seconds=coalesce_seconds)
    try:
        for record in records:
            if type(record).__name__ == 'ErrorMsg':
                builder.begin_resync()
                controller = None
                events.append({'status':'gateway_error_resync_required','record_type':'ErrorMsg'})
                continue
            row = normalized_mbp1(record)
            if row is None:
                continue
            received = datetime.now(timezone.utc).isoformat()
            event = {'record':row,'local_received_at':received}
            try:
                snapshot=builder.update(row,received_at=received)
                # On coalesced input, every retained symbol updates the builder;
                # only one aggregate state per interval is persisted/evaluated.
                if coalesce_seconds and time.monotonic()-last_saved < coalesce_seconds:
                    continue
                last_saved = time.monotonic()
                snapshot_data=snapshot.to_dict()
                serialized=json.dumps(snapshot_data)
                if byte_count+len(serialized.encode()) > MAX_IMPORT_BYTES:
                    events.append({'status':'stopped','reason':'20 MB normalized snapshot cap'})
                    break
                snapshots.append(snapshot_data);byte_count+=len(serialized.encode())
                if controller is None:
                    controller=DeskController(snapshot,portfolio,settings)
                else:
                    controller.refresh(snapshot)
                bundles.append(controller.bundle)
                event.update(status='evaluated',snapshot_id=snapshot.snapshot_id)
            except (ValueError, KeyError) as exc:
                event.update(status='rejected_or_incomplete',reason=str(exc))
            events.append(event)
    finally:
        records.close()
    (output/'snapshots.json').write_text(json.dumps({'snapshots':snapshots,'portfolio':[asdict(p) for p in portfolio],'settings':asdict(settings)},indent=2))
    (output/'events.json').write_text(json.dumps(events,indent=2))
    (output/'bundles.json').write_text(json.dumps(bundles,indent=2,allow_nan=False))
    report={'status':'captured' if snapshots else 'no_accepted_snapshots',
            'live_demo_acceptance':'pending_independent_review', 'snapshots':len(snapshots),
            'evaluations':len(bundles),'definitions_sha256':source_hash,
            'max_seconds':max_seconds,'max_records':max_records,
            'elapsed_seconds':time.monotonic()-started,'coalesce_seconds':coalesce_seconds,
            'output_dir':str(output),
            'limitations':['Bad-book and bad-receive-timestamp bits suppress eligibility; other raw flags are retained. Full exchange-status validation remains pending.',
                           'A successful connection alone does not establish complete definitions, executable quotes, latency or reconnect acceptance.',
                           'Coalescing retains the latest event per instrument per interval; the capture is sampled, not a tick-complete raw audit.',
                           'Gateway errors clear the quote cache; recovery requires all resolved contracts. Automatic transport reconnect is not enabled.']}
    report['files']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}
    (output/'capture_manifest.json').write_text(json.dumps(report,indent=2))
    return report


def _main() -> None:
    import argparse
    parser=argparse.ArgumentParser(description='Explicit read-only live capture; never starts automatically.')
    parser.add_argument('--definitions',type=Path,required=True,help='Resolved real vendor definitions/map JSON')
    parser.add_argument('--output',type=Path,required=True,help='New options_lab_runs/private/ subdirectory')
    parser.add_argument('--confirm-existing-entitlement',action='store_true')
    parser.add_argument('--max-seconds',type=float,default=30.)
    parser.add_argument('--max-records',type=int,default=1000)
    parser.add_argument('--coalesce-seconds',type=float,default=0.0)
    args=parser.parse_args()
    print(json.dumps(capture_live(args.definitions,args.output,subscription_confirmed=args.confirm_existing_entitlement,
                                 max_seconds=args.max_seconds,max_records=args.max_records,coalesce_seconds=args.coalesce_seconds),indent=2))


if __name__ == '__main__':
    _main()
