"""Synthetic report fixtures only: no invented records enter real outputs."""
import json
import runpy
import shutil
import subprocess
from pathlib import Path

import pytest
from pypdf import PdfReader

from cushing_research.report import PUBLIC_QUESTION, PUBLIC_PDF_QUESTION, FORECAST_QUESTION, _context, _safe_json, _plot_data, _robustness_views, render_report


def synthetic_report(full=False):
    """Small invented dataset to test presentation, not evidence about markets."""
    inv = [
        {"report_id": "TEST-1", "week_ending": "2023-01-06", "release_date": "2023-01-11", "knowledge_cutoff": "2023-01-11T23:59:00Z", "cushing_mbbl": 25.0, "us_mbbl": 410.0, "inv_z": -1.5, "state": "Low"},
        {"report_id": "TEST-2", "week_ending": "2023-01-13", "release_date": "2023-01-18", "knowledge_cutoff": "2023-01-18T23:59:00Z", "cushing_mbbl": 32.0, "us_mbbl": 412.0, "inv_z": 0.0, "state": "Normal"},
        {"report_id": "TEST-3", "week_ending": "2023-01-20", "release_date": "2023-01-25", "knowledge_cutoff": "2023-01-25T23:59:00Z", "cushing_mbbl": 40.0, "us_mbbl": 415.0, "inv_z": 1.5, "state": "High"},
    ]
    obs = [{**x, "decision_date": x["release_date"], "price_date": p, "F2": 75 + spread, "F3": 75.0, "spread": spread} for x, p, spread in zip(inv, ["2023-01-10", "2023-01-17", "2023-01-24"], [.4, -.1, -.5])]
    result = {
        "schema_version": "1", "title": "TEST ONLY", "author": "Synthetic QA", "generated_at": "2026-09-14T00:00:00Z", "data_cutoff": "2026-09-14", "run_id": "synthetic-test-do-not-share", "status": "full_research" if full else "public_evidence_only",
        "headline": "Synthetic fixture for report validation.", "conclusion": "These invented records test formatting and branch behavior only.",
        "inventory": inv, "public_observations": obs,
        "descriptive": {"sample_count": 3, "start": "2023-01-11", "end": "2023-01-25", "spearman": -1.0,
                        "regimes": [{"state": s, "n": 1, "median_spread": m, "q25": m, "q75": m, "mean_spread": m} for s, m in zip(["Low", "Normal", "High"], [.4, -.1, -.5])], "yearly": [{"year": 2023, "n": 3, "spearman": -1.0}]},
        "blockers": ["INTERNAL_TRACE unavailable/input.csv"], "limitations": ["Synthetic fixture: not market evidence."], "data_audit": {"test_only": True}, "config": {}, "coverage": {},
        "sources": [{"title": "Test source", "url": "https://www.eia.gov/", "role": "Fixture link only"}],
        "cases": [{"id": case_id, "title": "Synthetic " + case_id + " window", "start": "2023-01-01", "end": "2023-02-01", "summary": "Synthetic window only.", "source_url": "https://www.eia.gov/",
                   "selection": "Synthetic case selection; no market evidence.", "known_then": "Invented dated stock and prior-quote record.", "subsequently_observed": "Invented later outcome for rendering checks.",
                   "interpretation": "Synthetic interpretation only.", "next_check": "Verify operational flow records.", "falsifier": "A documented inflow would challenge this invented interpretation.", "commercial_impact": "Use a dated operational check before a view.",
                   "evidence": [{**row, "quote_identity": "Public rank, not fixed-contract return", "source_url": "https://www.eia.gov/", "sha256": "testhash"} for row in obs],
                   "anchors": [{**row, "anchor_label": label, "source_url":"https://www.eia.gov/"} for row, label in zip(obs, ["Window start", "Decision focus", "Window end"])]}
                  for case_id in ("2020", "2023", "2019")],
        "research": None, "ledgers": [],
        "mechanism": {"status": "complete_exploration", "sample": {"n": 3, "tail_support": {s: {"n": 1,"years_n": 1,"inference_eligible": False} for s in ("Low","Normal","High")}},
                      "main_test": {"p_value": .7753}, "models": {name: {"in_sample_r_squared": .6,"in_sample_mae": .2,"in_sample_rmse": .3,"slopes": {s: {"estimate": value} for s,value in zip(("Low","Normal","High"),(-.12,-.22,-.04))}} for name in ("D1","D2")},
                      "curves": [{"z": z,"D1": -.2*z,"D2": -.22*z} for z in (-2,-1,0,1,2)],
                      "bootstrap": {"8": {"models": {"D2": {"slopes": {s: {"status": "unavailable","ci_lower": None,"ci_upper": None,"valid_repetitions": 0} for s in ("Low","Normal","High")}}}}},
                      "leave_one_year_out": [{"excluded_year": 2018,"n": 2,"main_test": {"p_value": .000001326}}, {"excluded_year": 2020,"n": 2,"main_test": {"p_value": .6882}}],
                      "conclusion": {"finding": "The synthetic fixture has insufficient evidence of a shape difference.","category": "insufficient_nonlinearity_evidence"}},
    }
    if full:
        preds=[]
        for model,pred in [("B1",.05),("B3",.15)]:
            for i,row in enumerate(inv):
                preds.append({"event_id": row["report_id"], "decision_date": row["release_date"], "known_price_date": obs[i]["price_date"], "near_contract": "TESTH23", "far_contract": "TESTJ23", "entry_date": row["release_date"], "exit_date": "2023-02-01", "model": model, "prediction": pred, "y": .1, "inv_z": row["inv_z"], "partition": "test", "evaluation_role":"chronological_out_of_sample"})
        preds.extend([{**preds[-1], "event_id":"DEV-2020", "decision_date":"2020-04-15", "partition":"validation", "evaluation_role":"development_selected"},
                      {**preds[-1], "event_id":"RECENT-2026", "decision_date":"2026-01-07", "partition":"recent", "evaluation_role":"chronological_out_of_sample"}])
        result["research"]={"predictions": preds, "metrics": [{"partition": "test", "scope": "overall", "year": None, "model": m, "n": 3, "mae": v} for m,v in [("B0", .10),("B1", .08),("B2", .075),("B3", .07)]],
                            "comparisons": [{"partition": "test", "candidate": "B3", "baseline": "B1", "comparison": "B3_vs_B1", "block_size": 8, "n": 3, "mae_improvement": .01, "ci_low": -.02, "ci_high": .03, "evidence": "no_clear_improvement"}],
                            "robustness": [{"check": "synthetic_only", "partition": "test", "n": 3, "mae_improvement": .01}], "metadata": {"test_only": True,"status":"complete"}}
        result["research"]["robustness"].extend([
            {"name":"M3-M4","status":"complete","validation_coverage":{"status":"complete"},"test_year_coverage":{"expected_years":[2022,2023,2024,2025],"actual_event_counts":{"2023":3},"missing_years":[2022,2024,2025]},"metrics":result["research"]["metrics"],"comparisons":result["research"]["comparisons"]},
            {"name":"extra_execution_day","common_test_events":3,"forecast_policy":"Frozen original forecasts; shifted execution only"}])
        for ticks,final in [(1,110.0),(2,80.0),(4,20.0)]:
            result["ledgers"].append({"partition": "test", "model": "B3", "cost_ticks": ticks, "force_roundtrip": False,
                                      "daily": [{"trade_date": "2023-01-12", "cumulative_pnl": 150.0, "net_pnl": 150.0, "drawdown": 0.0, "contracts_traded": 2}, {"trade_date": "2023-01-13", "cumulative_pnl": final, "net_pnl": final-150, "drawdown": final-150, "contracts_traded": 2}],
                                      "contract_daily": [{"trade_date": "2023-01-12", "contract_id": "TESTH23", "start_position": 1,"previous_settlement":70.0,"current_settlement":70.1,"gross_pnl":100.0,"quantity_change":0,"contracts_traded":0,"slippage":0.0,"fees":0.0,"net_contribution":100.0,"end_position":1}],
                                      "summary": {"model": "B3", "status":"complete", "net_pnl": final, "max_drawdown": 150-final, "contracts_traded": 4, "fees": 4.0, "slippage": 20.0*ticks, "economic_evidence": "synthetic_only", "variation_margin_outflow": 25.0,"break_even_roundtrip_cost_per_bbl": .2345}})
        result["ledgers"].append({**result["ledgers"][0], "model": "B1", "daily": [{**row,"cumulative_pnl":value,"net_pnl":value if i == 0 else value-50} for i,(row,value) in enumerate(zip(result["ledgers"][0]["daily"],[50.0,45.0]))], "summary": {**result["ledgers"][0]["summary"],"model":"B1","net_pnl":45.0}})
        result["ledgers"].append({**result["ledgers"][0], "scenario": "extra_execution_day", "execution_dates": [{"event_id": "TEST-1","original_entry_date":"2023-01-12","entry_date":"2023-01-13","original_exit_date":"2023-01-19","exit_date":"2023-01-20"}]})
        result["ledgers"].append({**result["ledgers"][0], "scenario": "forced_roundtrip", "force_roundtrip":True})
        result["cases"].append({"id": "failure", "title": "Largest B3 test forecast error", "start": "2023-01-11", "end": "2023-01-25", "decision_date": "2023-01-11", "event_id": "TEST-1", "near_contract":"TESTH23","far_contract":"TESTJ23", "prediction": .15,"actual": .1,
            "selection": "Maximum absolute B3 test error; first chronological event wins ties.", "known_then": "Synthetic stock and actual-contract pair.", "model_judgment": "Synthetic test forecast.", "failed_hypothesis": "The synthetic conditional estimate missed this interval.",
            "actual_predictions": preds, "actual_intervals": [{"event_id":"TEST-1","quantity":1,"gross_pnl":-50.0,"fees":5.0,"slippage":20.0,"net_pnl":-75.0}],
            "contract_contributions": [{"event_id":"TEST-1","contract_id":contract,"leg":leg,"gross_pnl":gross,"fees":2.5,"slippage":10.0,"net_pnl":gross-12.5} for contract,leg,gross in [("TESTH23","near",40.0),("TESTJ23","far",-90.0)]]})
    return result


def payload_from_html(text):
    return json.loads(text.split('<script id="report-data" type="application/json">',1)[1].split('</script>',1)[0])


def test_public_report_has_four_pages_real_statistics_and_no_performance(tmp_path):
    result=synthetic_report()
    paths=render_report(result,tmp_path)
    assert set(paths)=={"html","pdf","memo","interview_notes"}
    reader=PdfReader(paths["pdf"])
    assert len(reader.pages)==4
    text=" ".join(" ".join(page.extract_text().split()) for page in reader.pages)
    assert "-1.000" in text
    assert "Predictive and trading evidence is pending" in text
    assert "not confidence intervals" in text
    output=Path(paths["html"]).read_text()
    assert '<script src=' not in output
    assert 'id="ledger-pnl-chart"' not in output
    assert 'id="predictions-chart"' not in output
    assert 'INTERNAL_TRACE' not in output
    embedded=payload_from_html(output)
    assert embedded["predictions"]==[] and embedded["ledgers"]==[]
    assert embedded["plots"]["relationship"]["data"][0]["x"]==[-1.5]
    assert "posterior" not in Path(paths["memo"]).read_text()
    notes=Path(paths["interview_notes"]).read_text()
    assert "预测与交易测试尚未完成" in notes
    assert "### 8." in notes
    assert "previous three calendar years" in text
    assert "28 days" in text and "at least 20 observations" in text
    assert "Low: z < -1" in text and "High: z > 1" in text
    assert "Post-review exploratory extension" in text
    assert "0.7753" in text and "1.326e-06" in text and "0.6882" in text
    assert "FITTED SPREAD CHANGE PER Z UNIT" in text
    assert "cost of one" not in text.lower()
    source_links=[annotation.get_object().get("/A",{}).get("/URI") for page in reader.pages for annotation in page.get("/Annots",[])]
    assert len([url for url in source_links if url and url.startswith("https://")]) >= 2
    assert 'id="mechanism-chart"' in output and 'id="inventory-record"' in output
    assert embedded["cases"][0]["falsifier"].startswith("A documented inflow")
    assert embedded["cases"][0]["evidence"][0]["price_date"]=="2023-01-10"
    memo=Path(paths["memo"]).read_text()
    assert PUBLIC_QUESTION in output and PUBLIC_QUESTION in memo
    assert PUBLIC_PDF_QUESTION in text
    assert "I studied how Cushing inventory relative to its seasonal history relates to the WTI curve, and where that relationship breaks down." in notes
    assert FORECAST_QUESTION not in output.split('<section id="known">',1)[0]
    assert "What would weaken the view" in memo and "Actual records" not in memo
    assert "Every year-deletion diagnostic" in memo


def test_full_report_contains_supplied_ledger_and_event_data(tmp_path):
    paths=render_report(synthetic_report(True),tmp_path)
    output=Path(paths["html"]).read_text()
    assert FORECAST_QUESTION in output
    assert PUBLIC_QUESTION not in output
    embedded=payload_from_html(output)
    assert len(PdfReader(paths["pdf"]).pages)==4
    assert 'id="ledger-variant"' in output
    assert 'id="ledger-pnl-chart"' in output
    assert 'id="ledger-dd-chart"' in output
    assert 'id="prediction-event"' in output
    assert embedded["ledgers"][1]["daily"][-1]["cumulative_pnl"]==80.0
    assert embedded["predictions"][0]["near_contract"]=="TESTH23"
    assert "no clear improvement" in Path(paths["memo"]).read_text()
    assert "-0.0200" in Path(paths["memo"]).read_text()
    reader=PdfReader(paths["pdf"])
    pages=[" ".join(page.extract_text().split()) for page in reader.pages]
    assert all(model in pages[0] for model in ("B0","B1","B2","B3"))
    assert "0.0100" in pages[0] and "-0.0200" in pages[0] and "0.0300" in pages[0]
    for value in ("B1 baseline net P&L", "+45.00 USD", "B3 baseline net P&L", "+110.00 USD", "40.00 USD", "0.2345 $/bbl", "0.05 $/bbl", "0.09 $/bbl", "0.17 $/bbl", "+80.00 USD", "+20.00 USD"):
        assert value in pages[2]
    for value in ("LARGEST B3 TEST FORECAST ERROR","2023-01-11","TESTH23 / TESTJ23","+0.1500","+0.1000","-75.00 USD","Near TESTH23","Far TESTJ23","not maximum trading loss"):
        assert value in pages[3]
    assert "insufficient evidence that inventory improves prediction" in pages[0]
    assert embedded["ledgers"][0]["scenario_name"] != embedded["ledgers"][4]["scenario_name"]
    assert embedded["ledgers"][0]["contract_daily"][0]["net_contribution"]==100
    assert all("2023" in date for trace in embedded["plots"]["predictions"]["data"] for date in trace["x"])
    assert all(date.startswith("2026") for trace in embedded["plots"]["recent_predictions"]["data"] for date in trace["x"])
    assert "M3-M4: complete" in output and "Frozen original forecasts" in output
    assert "95% upper" in output and "Evidence / status" in output
    assert "actual counts" in output and "missing years" in output
    main_table=output.split('<h3>Primary test forecast metrics</h3>',1)[1].split('</table>',1)[0]
    assert all(f'<th>{name}</th>' in main_table for name in ("Model","n","MAE","RMSE","Direction accuracy"))
    assert all(f'<th>{name}</th>' not in main_table for name in ("Period","Scope","Year","State"))
    assert embedded["research"]["metadata"]["status"]=="complete"
    memo=Path(paths["memo"]).read_text()
    assert "#### M3-M4: complete" in memo and "#### Extra execution-day delay: complete" in memo
    assert "Development-selected reconstruction" in memo


@pytest.mark.parametrize("missing", ["comparison", "cost_stress", "benchmark", "failure", "leg_attribution", "model_metric", "daily_total", "metadata_status", "daily", "contract_daily", "break_even"])
def test_full_report_refuses_incomplete_evidence(missing):
    result=synthetic_report(True)
    if missing=="comparison":
        result["research"]["comparisons"]=[]
    elif missing=="cost_stress":
        result["ledgers"]=[l for l in result["ledgers"] if l["cost_ticks"]!=4]
    elif missing=="benchmark":
        result["ledgers"]=[l for l in result["ledgers"] if l["model"]!="B1"]
    elif missing=="failure":
        result["cases"]=[c for c in result["cases"] if c["id"]!="failure"]
    elif missing=="leg_attribution":
        result["cases"][-1]["contract_contributions"]=[]
    elif missing=="model_metric":
        result["research"]["metrics"]=[r for r in result["research"]["metrics"] if r["model"]!="B2"]
    elif missing=="daily_total":
        result["ledgers"][0]["daily"][-1]["cumulative_pnl"]=1_000_000
        result["ledgers"][0]["summary"]["net_pnl"]=1_000_001
    elif missing=="metadata_status":
        result["research"]["metadata"]["status"]="incomplete"
    elif missing=="daily":
        result["ledgers"][0]["daily"]=[]
    elif missing=="contract_daily":
        result["ledgers"][0]["contract_daily"]=[]
    elif missing=="break_even":
        del result["ledgers"][0]["summary"]["break_even_roundtrip_cost_per_bbl"]
    with pytest.raises(ValueError,match="full_research"):
        _context(result)


def test_full_failure_can_be_flat_without_invented_loss(tmp_path):
    result=synthetic_report(True)
    failure=result["cases"][-1]
    failure["actual_intervals"][0].update(quantity=0,gross_pnl=0,fees=0,slippage=0,net_pnl=0)
    failure["contract_contributions"]=[]
    paths=render_report(result,tmp_path)
    text=" ".join(PdfReader(paths["pdf"]).pages[3].extract_text().split())
    assert "The rule was flat; no position was opened" in text
    assert "-75.00" not in text


def test_full_pdf_discloses_absent_public_mechanism_evidence(tmp_path):
    result=synthetic_report(True)
    result["mechanism"]={}
    result["public_observations"]=[]
    result["descriptive"].update(yearly=[],regimes=[])
    paths=render_report(result,tmp_path)
    text=" ".join(PdfReader(paths["pdf"]).pages[1].extract_text().split())
    assert "Public mechanism evidence is absent from this result" in text
    assert "Figure 2. Public market-state evidence" not in text
    output=Path(paths["html"]).read_text()
    section=output.split('<section id="evidence">',1)[1].split('</section>',1)[0]
    assert 'id="public-evidence-unavailable"' in section
    assert "Missing evidence does not imply a zero spread or no relationship" in section
    assert 'id="relationship-chart"' not in section and 'id="regimes-chart"' not in section
    assert 'id="alignment-chart"' not in section and 'Pooled Spearman rho =' not in section
    assert 'id="predictions-chart"' in output and 'id="ledger-pnl-chart"' in output
    assert 'id="stability-chart"' not in output
    assert 'id="annual-evidence-unavailable"' in output
    embedded=payload_from_html(output)
    for name,figure in embedded["plots"].items():
        if f'id="{name.replace("_", "-")}-chart"' in output:
            assert any(trace.get("x") and trace.get("y") for trace in figure["data"]),name


def test_full_pdf_keeps_public_evidence_without_controlled_curves(tmp_path):
    result=synthetic_report(True)
    result["mechanism"]={}
    assert result["public_observations"]
    paths=render_report(result,tmp_path)
    text=" ".join(PdfReader(paths["pdf"]).pages[1].extract_text().split())
    assert "Figure 2. Public market-state evidence supplies context" in text
    assert "Public mechanism evidence is absent" not in text
    output=Path(paths["html"]).read_text()
    assert 'id="relationship-chart"' in output and 'id="regimes-chart"' in output
    assert 'id="public-evidence-unavailable"' not in output
    assert 'id="stability-chart"' in output and 'id="annual-evidence-unavailable"' not in output


def test_genuine_zero_turnover_has_explicit_break_even_reason(tmp_path):
    result=synthetic_report(True)
    for ledger in result["ledgers"]:
        ledger["contract_daily"]=[]
        for day in ledger["daily"]:
            day.update(cumulative_pnl=0.0,net_pnl=0.0,drawdown=0.0,contracts_traded=0)
        ledger["summary"].update(net_pnl=0.0,max_drawdown=0.0,contracts_traded=0,fees=0.0,slippage=0.0,variation_margin_outflow=0.0,break_even_roundtrip_cost_per_bbl=None,n_active_intervals=0)
    result["cases"][-1]["actual_intervals"][0].update(quantity=0,gross_pnl=0,fees=0,slippage=0,net_pnl=0)
    result["cases"][-1]["contract_contributions"]=[]
    paths=render_report(result,tmp_path)
    text=" ".join(PdfReader(paths["pdf"]).pages[2].extract_text().split())
    assert "Not defined: zero contract turnover" in text
    assert "Not defined: zero contract turnover" in Path(paths["html"]).read_text()


def test_real_research_schema_separates_test_recent_and_development():
    helpers=runpy.run_path(str(Path(__file__).with_name('test_research.py')))
    events,prices,sessions,config=helpers['synthetic_research_and_prices']()
    from cushing_research.research import run_research
    research=run_research(events,sessions,config)
    ctx=_context(synthetic_report())
    ctx.update(public=False,research=research)
    plots=_plot_data(ctx)
    assert set(research["predictions"][0]) >= {"evaluation_role","partition","model"}
    assert all("2022"<=date[:4]<="2025" for trace in plots["predictions"]["data"] for date in trace["x"])
    assert all(date.startswith("2026") for trace in plots["recent_predictions"]["data"] for date in trace["x"])
    assert not any(date.startswith("2020") for trace in plots["predictions"]["data"] for date in trace["x"])
    development=[r for r in research["predictions"] if r["partition"]=="validation"]
    assert development and all(r["evaluation_role"]=="development_selected" for r in development)
    projected=_robustness_views(ctx)
    original=next(row for row in research["robustness"] if row["check"]=="exclude_2020_retrain_retune" and row["partition"]=="test")
    displayed=next(row for row in projected["flat"] if row["check"]=="exclude_2020_retrain_retune" and row["partition"]=="test")
    for key in ("n","mae_improvement","ci_low","ci_high","evidence"):
        assert displayed[key]==original[key]


def test_full_report_requires_actual_research_object():
    result=synthetic_report()
    result["status"]="full_research"
    with pytest.raises(ValueError,match="requires a real research result"):
        _context(result)


def test_schema_accepts_flat_or_nested_summary():
    flat=synthetic_report()
    nested=synthetic_report()
    desc=nested["descriptive"]
    nested["descriptive"]={"summary":{k:v for k,v in desc.items() if k not in {"regimes","yearly"}},"regimes":desc["regimes"],"yearly":desc["yearly"]}
    assert _context(flat)["rho"]==_context(nested)["rho"]==-1.0
    assert _context(flat)["n"]==_context(nested)["n"]==3


def test_embedded_json_cannot_end_script():
    data={"value":"</script><img src=x onerror=alert(1)>","bad":float("nan")}
    encoded=_safe_json(data)
    assert "</script>" not in encoded
    assert json.loads(encoded)=={"value":data["value"],"bad":None}


def test_full_interactions_use_selected_cost_and_actual_event(tmp_path):
    node=shutil.which("node")
    if not node:
        pytest.skip("Node unavailable for DOM interaction harness")
    paths=render_report(synthetic_report(True),tmp_path)
    output=Path(paths["html"]).read_text()
    payload=payload_from_html(output)
    script=output.rsplit('<script>',1)[1].split('</script>',1)[0]
    harness=r'''
const vm=require('vm'),assert=require('assert');
const DATA_INPUT=JSON.parse(process.argv[2]),SCRIPT=process.argv[3];
const nodes={};
function element(id){if(nodes[id])return nodes[id];const e={id,value:'',textContent:'',children:[],listeners:{},dataset:{},hidden:false,style:{},classList:{toggle(){},remove(){}},appendChild(x){this.children.push(x);if(this.value===''&&x.value!==undefined)this.value=x.value;return x},append(...xs){xs.forEach(x=>this.appendChild(x))},replaceChildren(...xs){this.children=[];this.value='';this.append(...xs)},addEventListener(k,f){this.listeners[k]=f},setAttribute(){},scrollIntoView(){},on(){}};Object.defineProperty(e,'options',{get(){return this.children}});nodes[id]=e;return e}
element('report-data').textContent=JSON.stringify(DATA_INPUT);
const calls={};const context=vm.createContext({document:{getElementById:element,querySelectorAll(){return []},createElement(tag){return {tag,value:'',textContent:'',children:[],append(...x){this.children.push(...x)}}}},window:{innerWidth:1280},Plotly:{newPlot(id,data,layout){calls[id]={data,layout};return Promise.resolve()},react(id,data,layout){calls[id]={data,layout};return Promise.resolve()},relayout(){}},console,Promise,Date});
vm.runInContext(SCRIPT,context);
assert.strictEqual(calls['ledger-pnl-chart'].data[0].y[1],110);
element('ledger-variant').value='1';element('ledger-variant').listeners.change();
assert.strictEqual(calls['ledger-pnl-chart'].data[0].y[1],80);
assert.strictEqual(calls['ledger-dd-chart'].data[0].y[1],-70);
assert(element('ledger-variant').children[0].textContent.includes('Baseline'));
assert(element('ledger-variant').children[1].textContent.includes('Cost stress'));
assert(element('ledger-variant').children[4].textContent.includes('Extra execution-day delay'));
assert(element('ledger-variant').children[5].textContent.includes('Forced close and reopen'));
const collect=e=>[e.textContent||'',...(e.children||[]).map(collect)].join('|');
element('ledger-variant').value='4';element('ledger-variant').listeners.change();
assert(collect(element('ledger-execution-dates')).includes('2023-01-12'));
assert(collect(element('ledger-execution-dates')).includes('2023-01-13'));
assert(collect(element('ledger-contract-table')).includes('TESTH23'));
element('ledger-day').value='2023-01-13';
vm.runInContext("Object.assign(DATA.ledgers[4].daily[1],{positions:{},contracts_traded:0});showLedgerDay()",context);
assert(collect(element('ledger-contract-table')).includes('Flat; no contracts held or traded this day'));
vm.runInContext("delete DATA.ledgers[4].daily[1].positions;showLedgerDay()",context);
assert(collect(element('ledger-contract-table')).includes('No supplied records'));
assert(!collect(element('ledger-contract-table')).includes('Flat;'));
vm.runInContext('showCase(3,false)',context);
assert(element('case-title').textContent.includes('Largest B3'));
assert(collect(element('case-actuals')).includes('TESTJ23'));
vm.runInContext('showCase(0,false)',context);
assert(collect(element('case-narrative')).includes('A documented inflow'));
element('case-record').value='1';element('case-record').listeners.change();
assert(collect(element('case-evidence')).includes('2023-01-17'));
assert(element('case-record-source').href==='https://www.eia.gov/');
assert.strictEqual(element('prediction-model').value,'B3');
element('prediction-model').value='B1';element('prediction-model').listeners.change();
const detail=element('prediction-detail').children.map(c=>c.children.map(x=>x.textContent).join(':')).join('|');
assert(detail.includes('Prediction ($/bbl):0.050'));
assert(detail.includes('TESTH23 / TESTJ23'));
element('prediction-model').value='B3';element('prediction-model').listeners.change();
element('prediction-partition').value='validation';element('prediction-partition').listeners.change();
assert(collect(element('prediction-detail')).includes('development_selected'));
assert(collect(element('prediction-detail')).includes('not independent final evidence'));
assert(element('prediction-event').children.every(o=>o.textContent.includes('2020')));
assert(calls['predictions-chart'].data.every(trace=>trace.x.every(x=>x.startsWith('2023'))));
console.log('Selected ledger cost and actual-contract event controls passed');
'''
    harness_path=tmp_path/'verify.cjs'
    harness_path.write_text(harness)
    proc=subprocess.run([node,str(harness_path),json.dumps(payload),script],capture_output=True,text=True)
    assert proc.returncode==0,proc.stderr
    assert "controls passed" in proc.stdout
