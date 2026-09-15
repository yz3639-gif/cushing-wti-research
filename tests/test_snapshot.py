"""Destroyed local snapshots must never pass as complete offline evidence."""
import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from cushing_research.snapshot import verify_snapshot


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def make_snapshot(root):
    """A complete small invented source graph, solely for integrity tests."""
    def source(name, body, **extra):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        metadata = {"url": "https://example.invalid/" + path.name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        _write_json(path.with_name(path.name + ".source.json"), metadata)
        return {**metadata, "raw_file": name, **extra}

    archive = source("data/raw/eia_archive_index.html", "invented archive")
    inventory_sources = [source(f"data/raw/inventory/{i}.csv", f"invented report {i}", report_id=f"TEST-{i}") for i in range(2)]
    quote_sources = [source(f"data/raw/public_futures/{rank}.html", f"invented {rank} prices", rank=rank) for rank in ("F2", "F3")]
    inventory = pd.DataFrame([{"report_id": row["report_id"], "source_url": row["url"],
                               "sha256": row["sha256"], "cushing_mbbl": 10 + i}
                              for i, row in enumerate(inventory_sources)])
    quotes = pd.DataFrame({"trade_date": ["2020-04-20"], "F2": [-1.0], "F3": [0.0]})
    processed = root / "data/processed"
    processed.mkdir(parents=True)
    inventory.to_csv(processed / "inventory.csv", index=False)
    quotes.to_csv(processed / "public_futures.csv", index=False)
    inventory_audit = {"status": "complete", "rows": len(inventory), "source_hashes": inventory_sources,
                       "archive_index": {key: value for key, value in archive.items() if key != "raw_file"},
                       "processed_sha256": hashlib.sha256((processed / "inventory.csv").read_bytes()).hexdigest()}
    quote_audit = {"status": "complete", "rows": len(quotes), "sources": quote_sources,
                   "processed_sha256": hashlib.sha256((processed / "public_futures.csv").read_bytes()).hexdigest()}
    _write_json(processed / "inventory_audit.json", inventory_audit)
    _write_json(processed / "public_futures_audit.json", quote_audit)
    return {"inventory": inventory_audit, "futures": quote_audit, "raw": root / inventory_sources[0]["raw_file"]}


def test_complete_minimal_and_real_snapshots_are_verified(tmp_path):
    make_snapshot(tmp_path)
    result = verify_snapshot(tmp_path)
    assert result["status"] == "complete"
    assert result["expected_raw_files"] == result["verified_raw_files"] == 5
    assert result["verified_processed_files"] == 2
    assert result["inventory_source_links"] == 2
    real = verify_snapshot(Path(__file__).resolve().parents[1])
    assert real["inventory_source_links"] == 789
    assert real["expected_raw_files"] >= 799
    assert real["verified_raw_files"] >= real["expected_raw_files"]
    assert real["verified_processed_files"] == 3


@pytest.mark.parametrize("damage, message", [
    ("raw_and_sidecar", "Cached source missing"),
    ("all_raw", "Cached source missing"),
    ("sidecar", "Cached source sidecar missing"),
    ("raw_bytes", "Cached source SHA256 mismatch"),
    ("sidecar_sha", "Required SHA256"),
    ("sidecar_url", "sidecar url mismatch"),
    ("manifest_sha", "Required SHA256"),
    ("manifest_source", "report IDs differ"),
    ("manifest_path", "Source path must be relative"),
    ("processed_sha", "Required SHA256"),
    ("processed_value", "Processed source SHA256 mismatch"),
    ("processed_audit", "Processed audit missing"),
    ("processed_file", "Processed source missing"),
    ("source_link", "Inventory source link mismatch"),
    ("source_count", "row count mismatch"),
    ("quote_rank", "source ranks differ"),
    ("path_escape", "outside data/raw"),
])
def test_missing_and_changed_inputs_fail_closed(tmp_path, damage, message):
    context = make_snapshot(tmp_path)
    raw = context["raw"]
    sidecar = raw.with_name(raw.name + ".source.json")
    audit_path = tmp_path / "data/processed/inventory_audit.json"
    audit = context["inventory"]
    if damage == "raw_and_sidecar":
        raw.unlink()
        sidecar.unlink()
    elif damage == "all_raw":
        shutil.rmtree(tmp_path / "data/raw")
    elif damage == "sidecar":
        sidecar.unlink()
    elif damage == "raw_bytes":
        raw.write_text("changed source bytes")
    elif damage in ("sidecar_sha", "sidecar_url"):
        value = json.loads(sidecar.read_text())
        value["sha256" if damage == "sidecar_sha" else "url"] = ""
        _write_json(sidecar, value)
    elif damage == "manifest_sha":
        del audit["source_hashes"][0]["sha256"]
    elif damage == "manifest_source":
        audit["source_hashes"].pop()
    elif damage == "manifest_path":
        del audit["source_hashes"][0]["raw_file"]
    elif damage == "processed_sha":
        del audit["processed_sha256"]
    elif damage in ("processed_value", "source_link"):
        path = tmp_path / "data/processed/inventory.csv"
        frame = pd.read_csv(path)
        frame.loc[0, "cushing_mbbl" if damage == "processed_value" else "source_url"] = 999 if damage == "processed_value" else "https://example.invalid/wrong"
        frame.to_csv(path, index=False)
        if damage == "source_link":
            audit["processed_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    elif damage == "processed_audit":
        audit_path.unlink()
    elif damage == "processed_file":
        (tmp_path / "data/processed/inventory.csv").unlink()
    elif damage == "source_count":
        audit["rows"] = 999
    elif damage == "quote_rank":
        quote_path = tmp_path / "data/processed/public_futures_audit.json"
        quotes = json.loads(quote_path.read_text())
        quotes["sources"].pop()
        _write_json(quote_path, quotes)
    elif damage == "path_escape":
        audit["source_hashes"][0]["raw_file"] = "data/raw/../../outside.csv"
    if damage != "processed_audit":
        _write_json(audit_path, audit)
    with pytest.raises(ValueError, match=message):
        verify_snapshot(tmp_path)


def test_cited_supporting_source_is_required_even_if_both_files_deleted(tmp_path):
    context = make_snapshot(tmp_path)
    supporting = context["inventory"]["source_hashes"][0].copy()
    supporting.pop("report_id")
    supporting["raw_file"] = "data/raw/supporting.csv"
    context["inventory"]["supporting_sources"] = [supporting]
    _write_json(tmp_path / "data/processed/inventory_audit.json", context["inventory"])
    with pytest.raises(ValueError, match="Cached source missing: data/raw/supporting.csv"):
        verify_snapshot(tmp_path)


def test_optional_crosscheck_is_required_when_audit_claims_it_complete(tmp_path):
    context = make_snapshot(tmp_path)
    context["inventory"]["latest_vintage_crosscheck"] = {"status": "complete"}
    _write_json(tmp_path / "data/processed/inventory_audit.json", context["inventory"])
    with pytest.raises(ValueError, match="Source path must be relative"):
        verify_snapshot(tmp_path)
