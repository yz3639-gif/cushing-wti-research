import json
from pathlib import Path

import pandas as pd

from cushing_research.market import validate_market


def test_missing_actual_data_fails_closed(tmp_path):
    _,audit=validate_market(tmp_path,{})
    assert not audit['passed']
    assert any('contracts.csv' in e for e in audit['errors'])
    assert any('market_metadata.json' in e for e in audit['errors'])


def test_generic_ticker_rejected_even_when_provenance_claims_verified(tmp_path):
    root=tmp_path/'data/input';root.mkdir(parents=True)
    pd.DataFrame([{'contract_id':'CL2 Comdty','delivery_month':'2026-02','last_trade_date':'2026-01-20',
        'multiplier':1000,'tick_size':.01,'exchange':'NYMEX','currency':'USD'}]).to_csv(root/'contracts.csv',index=False)
    pd.DataFrame([{'trade_date':'2014-10-01','contract_id':'CL2 Comdty','settlement':-37.63},
                  {'trade_date':'2025-12-31','contract_id':'CL2 Comdty','settlement':0.}]).to_csv(root/'prices.csv',index=False)
    pd.DataFrame([{'trade_date':'2014-10-01','settlement_time':'2014-10-01T14:30:00-04:00'},
                  {'trade_date':'2025-12-31','settlement_time':'2025-12-31T14:30:00-05:00'}]).to_csv(root/'sessions.csv',index=False)
    (root/'market_metadata.json').write_text(json.dumps({'source':'test','settlement_field':'verified test',
        'actual_contracts_verified':True,'expiry_dates_verified':True,'calendar_verified':True,
        'verification_notes':'Synthetic test fixture','verified_by':'test'}))
    _,audit=validate_market(tmp_path,{})
    assert not audit['passed']
    assert any('Continuous CL rank' in e for e in audit['errors'])
    assert not any('negative' in e.lower() for e in audit['errors'])
