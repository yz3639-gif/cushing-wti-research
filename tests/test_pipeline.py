import pandas as pd
import pytest

from cushing_research.pipeline import verify_snapshot, descriptive_statistics
from tests.test_snapshot import make_snapshot


def test_cached_byte_change_stops_offline_build(tmp_path):
    raw=make_snapshot(tmp_path)['raw']
    assert verify_snapshot(tmp_path)['verified_raw_files']==5
    raw.write_text('changed source\n')
    with pytest.raises(ValueError,match='SHA256 mismatch'):
        verify_snapshot(tmp_path)


def test_processed_source_change_stops_offline_build(tmp_path):
    make_snapshot(tmp_path)
    processed=tmp_path/'data/processed'
    path=processed/'inventory.csv'
    verify_snapshot(tmp_path)
    path.write_text('a,b\n1,3\n')
    with pytest.raises(ValueError,match='Processed source SHA256 mismatch'):
        verify_snapshot(tmp_path)


def test_descriptive_stats_use_barrel_spread_without_return_denominator():
    rows=pd.DataFrame({'decision_date':['2020-04-01','2020-04-08','2020-04-15'],
        'state':['Low','Normal','High'],'inv_z':[-2,0,2],'spread':[2,0,-3]})
    result=descriptive_statistics(rows)
    assert result['spearman']==pytest.approx(-1)
    assert result['regimes'][2]['median_spread']==-3
    assert result['sample_count']==3
