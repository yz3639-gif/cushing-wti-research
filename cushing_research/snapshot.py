"""Fail-closed verification of the source files required by an offline snapshot.

Hashes establish consistency of this local snapshot, not immutability of the
publisher's files since their original release.  Required files come from the
processed audits and source links; enumerating the surviving cache is insufficient.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pandas as pd


def _read_json(path: Path, root: Path, purpose: str) -> dict:
    if not path.is_file():
        raise ValueError(f"{purpose} missing: {path.relative_to(root)}")
    try:
        value = json.loads(path.read_text())
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"Invalid {purpose}: {path.relative_to(root)}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Invalid {purpose}: {path.relative_to(root)} must be an object")
    return value


def _sha(value, location: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"Required SHA256 missing or invalid: {location}")
    return value


def _relative_path(root: Path, value, prefix: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError(f"Source path must be relative to snapshot: {value!r}")
    path = root / value
    if not path.resolve().is_relative_to((root / prefix).resolve()):
        raise ValueError(f"Source path is outside {prefix}: {value}")
    return path


def _walk_source_references(value, key=""):
    if isinstance(value, dict):
        if "raw_file" in value or ("url" in value and "sha256" in value and key != "archive_index"):
            yield value
        for child_key, child in value.items():
            yield from _walk_source_references(child, child_key)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_source_references(child)


def verify_snapshot(root: Path) -> dict:
    """Require every used source, its sidecar and all processed checksums offline.

    A public research build requires both inventory and public futures inputs.
    Optional cross-check files become required whenever an audit cites them.
    Actual-contract/Bloomberg files are separately checked by market validation.
    """
    root = Path(root).resolve()
    audits, tables, processed_checks = {}, {}, []

    def verify_processed(filename: str, checksum, label: str):
        path = _relative_path(root, filename, "data/processed")
        expected = _sha(checksum, label)
        if not path.is_file():
            raise ValueError(f"Processed source missing: {filename}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Processed source SHA256 mismatch: {filename}")
        processed_checks.append(filename)
        return path

    for filename, auditname in (
        ("inventory.csv", "inventory_audit.json"),
        ("public_futures.csv", "public_futures_audit.json"),
    ):
        audit_path = root / "data/processed" / auditname
        audit = _read_json(audit_path, root, "Processed audit")
        path = verify_processed(f"data/processed/{filename}", audit.get("processed_sha256"), f"{auditname}.processed_sha256")
        try:
            table = pd.read_csv(path)
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"Invalid processed table: {filename}") from exc
        if table.empty:
            raise ValueError(f"Processed source is empty: {filename}")
        if audit.get("rows") != len(table):
            raise ValueError(f"Processed audit row count mismatch: {filename}")
        audits[filename] = audit
        tables[filename] = table

    inventory_audit = audits["inventory.csv"]
    sources = inventory_audit.get("source_hashes")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Required source manifest missing: inventory_audit.json.source_hashes")
    archive = inventory_audit.get("archive_index")
    if not isinstance(archive, dict) or not archive:
        raise ValueError("Required source manifest missing: inventory_audit.json.archive_index")
    archive = {**archive, "raw_file": archive.get("raw_file", "data/raw/eia_archive_index.html")}
    futures_sources = audits["public_futures.csv"].get("sources")
    if not isinstance(futures_sources, list) or not futures_sources:
        raise ValueError("Required source manifest missing: public_futures_audit.json.sources")

    required = {}

    def register(source):
        if not isinstance(source, dict):
            raise ValueError("Invalid source manifest record")
        raw = _relative_path(root, source.get("raw_file"), "data/raw")
        name = str(raw.relative_to(root))
        checksum = _sha(source.get("sha256"), name)
        if not isinstance(source.get("url"), str) or not source["url"]:
            raise ValueError(f"Required source URL missing: {name}")
        if name in required:
            previous = required[name]
            if checksum != previous["sha256"] or source["url"] != previous["url"]:
                raise ValueError(f"Conflicting source manifest records: {name}")
        required[name] = source

    # Explicitly register every core row, so deleting raw_file does not make a
    # malformed core source disappear from the recursive optional-source scan.
    for source in [archive, *sources, *futures_sources]:
        register(source)
    # These reports require Table 4 evidence for the documented mixed-precision
    # reconciliation. Deleting its manifest field must not make it optional.
    support_ids={'2020_03_18','2020_04_15'} & set(tables['inventory.csv'].get('report_id',[]))
    supporting=inventory_audit.get('supporting_sources',[])
    if support_ids:
        if not isinstance(supporting,list):
            raise ValueError('Required supporting-source manifest missing')
        by_id={s.get('report_id'):s for s in supporting if isinstance(s,dict)}
        if not support_ids <= set(by_id):
            raise ValueError('Required supporting-source manifest missing: '+', '.join(sorted(support_ids-set(by_id))))
        for report_id in sorted(support_ids):
            source=by_id[report_id]
            if source.get('passed') is not True:
                raise ValueError(f'Supporting-source validation not passed: {report_id}')
            register(source)
    for audit in audits.values():
        for source in _walk_source_references(audit):
            register(source)

    columns = {"report_id", "source_url", "sha256"}
    inventory = tables["inventory.csv"]
    if not columns.issubset(inventory.columns):
        raise ValueError(f"Inventory source-link columns missing: {sorted(columns - set(inventory.columns))}")
    by_report = {}
    for source in sources:
        report = source.get("report_id")
        if not isinstance(report, str) or not report or report in by_report:
            raise ValueError("Inventory source manifest requires unique nonempty report_id values")
        by_report[report] = source
    if inventory.report_id.isna().any() or inventory.report_id.duplicated().any():
        raise ValueError("Inventory source links require unique nonempty report_id values")
    if set(by_report) != set(inventory.report_id):
        raise ValueError("Inventory source manifest report IDs differ from processed rows")
    for row in inventory.itertuples():
        source = by_report[row.report_id]
        if row.sha256 != source["sha256"] or row.source_url != source["url"]:
            raise ValueError(f"Inventory source link mismatch: {row.report_id}")
    ranks = [source.get("rank") for source in futures_sources]
    expected_ranks = {column for column in tables["public_futures.csv"].columns if re.fullmatch(r"F[1-4]", column)}
    if not expected_ranks or len(set(ranks)) != len(ranks) or set(ranks) != expected_ranks:
        raise ValueError("Public futures source ranks differ from processed columns")

    crosscheck = inventory_audit.get("latest_vintage_crosscheck")
    if isinstance(crosscheck, dict) and (crosscheck.get("status") == "complete" or "processed_file" in crosscheck or "processed_sha256" in crosscheck):
        verify_processed(crosscheck.get("processed_file"), crosscheck.get("processed_sha256"),
                         "inventory_audit.json.latest_vintage_crosscheck.processed_sha256")

    checked = set()

    def verify_raw(name, expected=None):
        raw = _relative_path(root, name, "data/raw")
        if not raw.is_file():
            raise ValueError(f"Cached source missing: {name}")
        sidecar = raw.with_name(raw.name + ".source.json")
        metadata = _read_json(sidecar, root, "Cached source sidecar")
        checksum = _sha(metadata.get("sha256"), str(sidecar.relative_to(root)))
        if expected is not None:
            for field in ("sha256", "url"):
                if metadata.get(field) != expected[field]:
                    raise ValueError(f"Cached source sidecar {field} mismatch with manifest: {name}")
        if hashlib.sha256(raw.read_bytes()).hexdigest() != checksum:
            raise ValueError(f"Cached source SHA256 mismatch: {name}")
        checked.add(name)

    for name, source in sorted(required.items()):
        verify_raw(name, source)
    # Also check additional present cache entries. These cannot replace the
    # required-source pass above, which detects raw+sidecar deletion together.
    for sidecar in sorted((root / "data/raw").rglob("*.source.json")):
        name = str(sidecar.with_name(sidecar.name.removesuffix(".source.json")).relative_to(root))
        if name not in checked:
            verify_raw(name)
    return {
        "status": "complete", "mode": "offline_sha256",
        "expected_raw_files": len(required), "verified_raw_files": len(checked),
        "verified_processed_files": len(processed_checks), "processed_files": processed_checks,
        "inventory_source_links": len(inventory), "public_futures_source_ranks": sorted(expected_ranks),
        "required_source_rule": "Processed audits determine expected sources; each raw file and sidecar must agree with its audit checksum and URL",
        "limitation": "Local snapshot consistency does not prove publisher files were immutable since first release",
    }
