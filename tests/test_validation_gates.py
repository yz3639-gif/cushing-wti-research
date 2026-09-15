"""Validation gates for source integrity and evaluation coverage."""
import hashlib
import json

import pandas as pd
import pytest

from cushing_research.pipeline import _write_ledger_table
from cushing_research.research import run_research
from tests.test_research import synthetic_fixture
from tests.test_snapshot import make_snapshot
from cushing_research.snapshot import verify_snapshot


def test_auxiliary_research_cannot_claim_complete_when_a_locked_test_year_is_missing():
    events, sessions, config = synthetic_fixture()
    events = events.loc[~events.decision_date.str.startswith("2022")]
    result = run_research(events, sessions, config)
    assert result["metadata"]["status"] == "incomplete"
    coverage = result["metadata"]["test_year_coverage"]
    assert coverage["missing_years"] == [2022]
    assert coverage["complete"] is False
    assert set(coverage["actual_event_counts"]) == {"2023", "2024", "2025"}
    # Partial diagnostics may be retained but their metadata cannot claim the locked test ran in full.
    assert result["metadata"]["selected_alphas"]


def test_empty_trade_and_contract_day_exports_have_an_auditable_schema(tmp_path):
    for key in ["trades", "contract_daily"]:
        path = tmp_path / (key + ".csv")
        _write_ledger_table(path, {"partition": "test", key: []}, key)
        frame = pd.read_csv(path)
        assert frame.empty
        assert {"partition", "trade_date", "contract_id", "scenario", "fees", "slippage"} <= set(frame.columns)


def test_supporting_manifest_itself_cannot_disappear(tmp_path):
    make_snapshot(tmp_path)
    path=tmp_path/'data/processed/inventory.csv'
    frame=pd.read_csv(path)
    frame.loc[0,'report_id']='2020_03_18'
    frame.to_csv(path,index=False)
    audit_path=tmp_path/'data/processed/inventory_audit.json'
    audit=json.loads(audit_path.read_text())
    audit['source_hashes'][0]['report_id']='2020_03_18'
    audit['processed_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    audit_path.write_text(json.dumps(audit))
    with pytest.raises(ValueError,match='supporting-source manifest missing'):
        verify_snapshot(tmp_path)
