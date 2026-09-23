"use strict";

const $ = id => document.getElementById(id);
const escapeText = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const number = (value, digits = 2) => Number.isFinite(value) ? value.toLocaleString("en-US", {minimumFractionDigits:digits,maximumFractionDigits:digits}) : "Unavailable";
const money = (value, digits = 0) => Number.isFinite(value) ? `${value < 0 ? "−" : ""}$${number(Math.abs(value), digits)}` : "Unavailable";
const signed = (value, digits = 1) => `${value > 0 ? "+" : ""}${number(value, digits)}`;
const cleanId = value => String(value).replaceAll("FIXTURE_", "");
const validStatuses = new Set(["optimal", "feasible_limit", "fallback_zero", "fallback_futures"]);
const validHedge = hedge => !!hedge && validStatuses.has(hedge.status) && hedge.position_constraints?.compliant === true;
const caseKey = (snapshot, cso, vanilla) => `${Number(snapshot)}:${Number(cso)}:${Number(vanilla)}`;
let data, cases, snapshotIndex = 0, activeCase, draftCase;

function snapshot() { return data.snapshots.find(item => item.index === snapshotIndex); }
function targetPrice(item) { return item.bundle.prices.find(price => price.contract_id === snapshot().settings.target_id); }
function targetQuote(item) { return item.bundle.quotes.find(quote => quote.contract_id === snapshot().settings.target_id); }
function resolveCase(cso, vanilla) {
  const result = cases.get(caseKey(snapshotIndex, cso, vanilla));
  if (!result) throw new Error("This preset is unavailable. No previous result has been substituted.");
  return result;
}
function isChanged() { return activeCase !== draftCase; }
function metric(label, value, detail, accent = false) {
  return `<div class="metric${accent ? " accent" : ""}"><span>${escapeText(label)}</span><strong>${escapeText(value)}</strong><small>${escapeText(detail)}</small></div>`;
}
function instrumentLabel(id) {
  const contract = snapshot().contracts.find(item => item.contract_id === id);
  if (!contract) return {name:cleanId(id), detail:"Synthetic instrument"};
  if (contract.kind === "future") return {name:`${cleanId(id)} futures`, detail:"1,000 bbl / contract"};
  return {name:`${contract.underlyings.map(cleanId).join(" / ")} ${number(contract.strike, contract.kind === "cso" ? 1 : 0)} ${contract.right}`, detail:`${contract.kind === "cso" ? "Calendar spread option" : "European vanilla"} · ${contract.expiry.slice(0,10)}`};
}
function chartNodes(nodes) {
  const target = snapshot().contracts.find(item => item.contract_id === snapshot().settings.target_id);
  return nodes.filter(node => node.model === "normal" && node.product === target.product && node.expiry === target.expiry && JSON.stringify(node.underlyings) === JSON.stringify(target.underlyings)).sort((a,b) => a.strike - b.strike);
}
function renderVolatility() {
  const market = chartNodes(snapshot().market_nodes);
  const active = chartNodes(activeCase.active_nodes);
  const draft = chartNodes(draftCase.active_nodes);
  if (!market.length || !active.length || !draft.length) {
    $("vol-chart").textContent = "No covered volatility nodes in this slice.";
    return;
  }
  const node = market[0];
  $("slice-label").textContent = `${node.underlyings.map(cleanId).join(" / ")} · expiry ${node.expiry.slice(0,10)} · $/bbl/√year`;
  const all = [...market,...active,...draft], values = all.map(n => n.value), strikes = all.map(n => n.strike);
  const minX = Math.min(...strikes), maxX = Math.max(...strikes);
  const low = Math.min(...values), high = Math.max(...values), pad = Math.max(0.3,(high-low)*0.3);
  const minY = Math.max(0,low-pad), maxY = high+pad;
  const x = v => 50 + (v-minX)/Math.max(maxX-minX,0.01)*470;
  const y = v => 185 - (v-minY)/(maxY-minY)*150;
  let svg = '<svg viewBox="0 0 560 225" role="img" aria-label="Market, active and draft CSO volatility at supplied strikes">';
  for(let i=0;i<4;i++) {
    const value = minY + (maxY-minY)*i/3, py = y(value);
    svg += `<line x1="50" y1="${py}" x2="520" y2="${py}" stroke="#293642"/><text x="38" y="${py+4}" text-anchor="end">${number(value,1)}</text>`;
  }
  [...new Set(strikes)].sort((a,b)=>a-b).forEach(strike => {svg += `<text x="${x(strike)}" y="208" text-anchor="middle">${number(strike,2)}</text>`;});
  const series = [{nodes:market,color:"#8997a7",name:"Market",width:5},{nodes:active,color:"#6dd8c0",name:"Active",width:2.5}];
  if(isChanged()) series.push({nodes:draft,color:"#edba68",name:"Draft",width:2});
  series.forEach(s => {
    svg += `<polyline points="${s.nodes.map(n=>`${x(n.strike)},${y(n.value)}`).join(" ")}" fill="none" stroke="${s.color}" stroke-width="${s.width}" ${s.name === "Draft" ? 'stroke-dasharray="6 5"' : ""}/>`;
    s.nodes.forEach(n=>{svg += `<circle cx="${x(n.strike)}" cy="${y(n.value)}" r="4" fill="${s.color}"><title>${s.name}: strike ${number(n.strike)}, volatility ${number(n.value,3)}</title></circle>`;});
  });
  svg += '<text x="285" y="224" text-anchor="middle">Strike · $/bbl</text></svg>';
  $("vol-chart").innerHTML = svg;
}
function renderDraft() {
  $("cso-value").textContent = signed(draftCase.cso_shift);
  $("vanilla-value").textContent = signed(draftCase.vanilla_shift_pp,0);
  $("apply").disabled = !isChanged();
  $("preview").hidden = !isChanged();
  const manual = activeCase.cso_shift !== 0 || activeCase.vanilla_shift_pp !== 0;
  $("change-state").textContent = isChanged() ? "Draft ready. Apply to update all active results together." : manual ? "Active: manual volatility adjustment. Synthetic market inputs remain fixed." : "Active: calibrated synthetic market. Move a slider to preview a change.";
  if(isChanged()) {
    const from = targetPrice(activeCase), to = targetPrice(draftCase);
    const rows = [
      ["Model price / bbl", money(from.model_price,3), money(to.model_price,3), `Change ${money(to.model_price-from.model_price,3)}`],
      ["Proxy worst scenario loss", validHedge(activeCase.bundle.hedges.proxy) ? money(activeCase.bundle.risk.summary.proxy.worst_loss) : "Unavailable", validHedge(draftCase.bundle.hedges.proxy) ? money(draftCase.bundle.risk.summary.proxy.worst_loss) : "Unavailable", "Net of estimated costs"],
      ["Hedge trading cost", validHedge(activeCase.bundle.hedges.proxy) ? money(activeCase.bundle.hedges.proxy.cost) : "Unavailable", validHedge(draftCase.bundle.hedges.proxy) ? money(draftCase.bundle.hedges.proxy.cost) : "Unavailable", "Whole-contract proposal"]
    ];
    $("preview-values").innerHTML = rows.map(([label,before,after,detail])=>`<div><span>${escapeText(label)}</span><strong>${escapeText(before)} → ${escapeText(after)}</strong><em>${escapeText(detail)}</em></div>`).join("");
  }
  renderVolatility();
}
function renderActive() {
  const b = activeCase.bundle, price = targetPrice(activeCase), quote = targetQuote(activeCase), hedge = b.hedges.proxy;
  const available = price?.status === "available" && b.status === "analysis_only";
  const goodProxy = available && validHedge(hedge);
  $("calculation-warning").hidden = available && goodProxy;
  $("calculation-warning").textContent = !available ? "Calculation unavailable for this case. A previous hedge result is not displayed." : !goodProxy ? "No compliant proxy hedge is available for this case." : "";
  const target = snapshot().contracts.find(item=>item.contract_id===snapshot().settings.target_id);
  const futurePrices = target.underlyings.map(id=>b.prices.find(p=>p.contract_id===id)?.model_price);
  const spread = futurePrices.length === 2 && futurePrices.every(Number.isFinite) ? futurePrices[0]-futurePrices[1] : NaN;
  $("metrics").innerHTML = metric("CALENDAR SPREAD", money(spread,2), `${target.underlyings.map(cleanId).join(" − ")} · $/bbl`) + metric("CSO MODEL PRICE",available ? money(price.model_price,3) : "Unavailable","Bachelier · $/bbl") + metric("ACTIVE NORMAL VOL",available ? number(price.vol,2) : "Unavailable","$/bbl/√year · not a percentage") + metric("PROXY WORST SCENARIO LOSS",goodProxy ? money(b.risk.summary.proxy.worst_loss) : "Unavailable",`${b.risk.scenarios.length} configured stresses · after costs`,true);
  $("quote-label").textContent = instrumentLabel(target.contract_id).name + " · synthetic instrument";
  $("quote-board").innerHTML = ["bid","ask"].map(side=>{
    const shown = available && quote?.status === "indicative" && quote[`${side}_size`] > 0 && Number.isFinite(quote[side]);
    return `<div class="quote-cell${shown ? "" : " suppressed"}"><span>${side.toUpperCase()}</span><strong>${shown ? number(quote[side],2) : "—"}</strong><small>${shown ? `${number(quote[`${side}_size`],0)} contract · indicative` : "Suppressed · 0 contracts"}</small></div>`;
  }).join("");
  $("position").textContent = `${signed(price?.position,0)} contracts`;
  const bidEffect = quote?.bid_marginal_risk, askEffect = quote?.ask_marginal_risk;
  $("quote-explanation").textContent = available && quote ? `At ${quote.inventory} contracts, the engine starts from the ${money(quote.model_price,3)} model value, then adds BBO crossing costs, fees and an inventory-risk adjustment. A full bid clip changes absolute stress exposure by ${money(bidEffect)}; an ask clip changes it by ${money(askEffect)}. The configured risk limit is ${money(snapshot().settings.risk_limit_dollars)}. A side is suppressed when its clip fails the risk or position checks.` : "Quote unavailable.";
  $("quote-policy").textContent = quote?.position_policy || "";
  const kinds = [{id:"unhedged",label:"No hedge"},{id:"delta",label:"Futures only"},{id:"proxy",label:"Futures + vanilla options"}];
  const maximum = Math.max(1,...kinds.map(k=>b.risk.summary[k.id]?.worst_loss || 0));
  $("risk-chart").innerHTML = kinds.map(k=>{
    const valid = available && (k.id === "unhedged" || validHedge(b.hedges[k.id]));
    const value = b.risk.summary[k.id]?.worst_loss;
    return `<div class="risk-row ${k.id}"><div><span>${k.label}</span><strong>${valid ? money(value) : "Unavailable"}</strong></div><div class="bar-track"><div class="bar-fill" style="width:${valid ? Math.max(0,value)/maximum*100 : 0}%"></div></div></div>`;
  }).join("");
  const basis = b.risk.scenarios.find(s=>s.name === "cso_basis_vol_up_1_normal");
  $("basis-loss").textContent = goodProxy && basis ? money(basis.proxy_net) : "Unavailable";
  $("basis-loss").style.color = basis?.proxy_net > 0 ? "var(--teal)" : "var(--red)";
  $("basis-horizon").textContent = `Full repricing over ${snapshot().settings.horizon_days} day, including time decay and estimated trading costs. Futures prices are unchanged in this stress.`;
  $("solver").textContent = !goodProxy ? "Unavailable · no compliant hedge displayed" : hedge.status === "optimal" ? "Optimal under the configured scenario objective and constraints" : hedge.status === "feasible_limit" ? "Feasible integer proposal · search limit reached · optimality unproven" : `${hedge.status} · fallback proposal`;
  $("hedge-trades").innerHTML = !goodProxy ? '<p class="muted">Hedge ticket unavailable.</p>' : !hedge.trades.length ? '<p class="muted">Zero-trade proposal. No new hedge contracts.</p>' : hedge.trades.map(trade=>{
    const label = instrumentLabel(trade.contract_id);
    return `<div class="trade-row"><span class="direction ${trade.quantity < 0 ? "sell" : ""}">${trade.quantity < 0 ? "SELL" : "BUY"}</span><span class="quantity">${number(Math.abs(trade.quantity),0)}</span><span class="instrument" title="${escapeText(trade.contract_id)}">${escapeText(label.name)}<small>${escapeText(label.detail)}</small></span></div>`;
  }).join("");
  $("hedge-cost").textContent = goodProxy ? money(hedge.cost,2) : "Unavailable";
  const manual = activeCase.cso_shift !== 0 || activeCase.vanilla_shift_pp !== 0;
  const audit = [
    ["Data",data.meta.data_label],["Source commit",data.meta.source_commit],["Engine",b.engine_version],
    ["Snapshot",b.snapshot_id],["Active vol",b.vol_version_id],["Vol origin",manual ? "Manual adjustment to calibrated synthetic nodes" : "Calibrated synthetic market"],
    ["Portfolio",b.portfolio_id],["Settings",b.settings_id],["Result bundle",b.bundle_id],
    ["Proxy status",hedge.status],["Termination",hedge.termination_reason],["Solver message",hedge.message],
    ["Optimality gap",Number.isFinite(hedge.mip_gap) ? `${number(hedge.mip_gap*100,2)}%` : "Not reported"],
    ["Scenario horizon",`${snapshot().settings.horizon_days} day`],["Warnings",b.warnings.join(" · ") || "None"],
    ["Presets",`CSO ${signed(activeCase.cso_shift)} $/bbl/√year; vanilla ${signed(activeCase.vanilla_shift_pp,0)} percentage points`]
  ];
  $("audit-meta").innerHTML = `<dl>${audit.map(([key,value])=>`<dt>${escapeText(key)}</dt><dd>${escapeText(value)}</dd>`).join("")}</dl>`;
  $("workspace").dataset.bundleId = b.bundle_id;
  $("workspace").dataset.volVersionId = b.vol_version_id;
  $("workspace").dataset.snapshotId = b.snapshot_id;
  renderDraft();
}
function selectSnapshot(index) {
  snapshotIndex = Number(index);
  activeCase = draftCase = resolveCase(0,0);
  $("snapshot").value = String(snapshotIndex);
  $("cso").value = "0";
  $("vanilla").value = "0";
  $("snapshot-time").textContent = `${new Date(snapshot().as_of).toLocaleString("en-US",{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit",second:"2-digit",timeZone:"UTC",hour12:false})} UTC · synthetic · resets volatility`;
  renderActive();
}
function guard(action) {
  try { action(); }
  catch(error) {
    $("calculation-warning").hidden = false;
    $("calculation-warning").textContent = error.message;
    $("apply").disabled = true;
  }
}
async function initialize() {
  try {
    const response = await fetch("demo-data.json");
    if(!response.ok) throw new Error(`Could not load the scenario file (${response.status}).`);
    data = await response.json();
    if(data.cases?.length !== 45 || data.snapshots?.length !== 3) throw new Error("Incomplete scenario dataset.");
    cases = new Map(data.cases.map(item=>[caseKey(item.snapshot_index,item.cso_shift,item.vanilla_shift_pp),item]));
    if(cases.size !== 45) throw new Error("Duplicate scenario keys.");
    data.snapshots.forEach(s=>{
      data.meta.cso_shifts.forEach(cso=>data.meta.vanilla_shifts_pp.forEach(vanilla=>{
        const item = cases.get(caseKey(s.index,cso,vanilla));
        if(!item || item.bundle.snapshot_id !== s.snapshot_id || !item.bundle.bundle_id || !item.bundle.vol_version_id) throw new Error("Scenario version mismatch.");
      }));
    });
    $("snapshot").innerHTML = data.snapshots.map((s,i)=>`<option value="${s.index}">Snapshot ${i+1} / ${data.snapshots.length}</option>`).join("");
    $("snapshot").addEventListener("change",event=>guard(()=>selectSnapshot(event.target.value)));
    $("next").addEventListener("click",()=>guard(()=>{
      const current = data.snapshots.findIndex(s=>s.index===snapshotIndex);
      selectSnapshot(data.snapshots[(current+1)%data.snapshots.length].index);
    }));
    ["cso","vanilla"].forEach(id=>$(id).addEventListener("input",()=>guard(()=>{
      draftCase = resolveCase(Number($("cso").value),Number($("vanilla").value));
      renderDraft();
    })));
    $("apply").addEventListener("click",()=>guard(()=>{ activeCase = draftCase; renderActive(); }));
    $("reset").addEventListener("click",()=>guard(()=>selectSnapshot(snapshotIndex)));
    $("download").addEventListener("click",()=>{
      const payload = {meta:data.meta,snapshot:snapshot(),active_case:activeCase};
      const url = URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:"application/json"}));
      const link = document.createElement("a"); link.href = url; link.download = `wti-desk-${activeCase.bundle.bundle_id}.json`; link.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
    selectSnapshot(data.snapshots[0].index);
    $("workspace").hidden = false;
    $("load-status").hidden = true;
  } catch(error) {
    $("load-status").textContent = `The desk could not load: ${error.message} Please reload the page.`;
    $("load-status").classList.add("error");
  }
}
initialize();
