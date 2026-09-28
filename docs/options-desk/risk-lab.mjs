// Risk Lab uses immutable engine tickets and full-repricing unit P&L vectors.
// No optimization or market calibration runs in the browser.
const TOLERANCE = 0.01;
const methods = {unhedged: "No hedge", delta: "Futures only", proxy: "Futures + options"};
const identityFields = ["bundle_id", "snapshot_id", "vol_version_id", "portfolio_id", "settings_id"];
const scenarioFields = ["parallel_dollars", "twist_dollars", "normal_vol_factor", "lognormal_vol_factor", "normal_vol_additive", "elapsed_days"];
const statuses = new Set(["optimal", "feasible_limit", "fallback_zero", "fallback_futures"]);
const finite = value => typeof value === "number" && Number.isFinite(value);
const close = (left, right) => finite(left) && finite(right) && Math.abs(left - right) <= TOLERANCE;
const assert = (test, message) => { if (!test) throw new Error(message); };

export async function demoHash(bytes) {
  assert(globalThis.crypto?.subtle, "Secure checksum verification is unavailable.");
  return Array.from(new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", bytes)), b => b.toString(16).padStart(2, "0")).join("");
}

export function validateSidecar(sidecar, base, hash) {
  assert(sidecar?.schema_version === "1.0", "Unsupported Risk Lab data version.");
  assert(/^[a-f0-9]{64}$/.test(hash || "") && sidecar.meta?.base_demo_sha256 === hash, "Risk Lab checksum does not match the desk data.");
  assert(sidecar.validation?.passed === true, "Risk Lab generation checks did not pass.");
  assert(sidecar.cases?.length === base.cases.length, "Risk Lab case coverage is incomplete.");
  const sets = sidecar.scenario_sets;
  assert(sets?.optimization?.length === 100 && sets?.challenge?.length === 12, "Risk Lab scenario coverage is incomplete.");
  for (const [name, rows] of Object.entries(sets)) {
    if (!Array.isArray(rows)) continue;
    assert(new Set(rows.map(row => row.name)).size === rows.length, `Duplicate ${name} scenario identifiers.`);
    rows.forEach(row => assert(typeof row.name === "string" && scenarioFields.every(field => finite(row[field])), "Invalid scenario configuration."));
  }
  const signature = row => JSON.stringify(scenarioFields.map(field => row[field]));
  const optimizationSignatures = new Set(sets.optimization.map(signature));
  assert(new Set(sets.challenge.map(signature)).size === 12, "Duplicate challenge scenarios.");
  sets.challenge.forEach(row => assert(row.elapsed_days === 1 && !optimizationSignatures.has(signature(row)), "Challenge overlaps the optimization grid or uses a different horizon."));
  const index = new Map();
  for (const item of sidecar.cases) {
    assert(!index.has(item.bundle_id), "Duplicate Risk Lab result identity.");
    index.set(item.bundle_id, item);
  }
  for (const original of base.cases) {
    const item = index.get(original.bundle.bundle_id);
    const snapshot = base.snapshots.find(row => row.index === original.snapshot_index);
    assert(item && snapshot && identityFields.every(key => item[key] === original.bundle[key]), "Risk Lab result identity mismatch.");
    assert(item.snapshot_index === original.snapshot_index && item.cso_shift === original.cso_shift && item.vanilla_shift_pp === original.vanilla_shift_pp, "Risk Lab preset identity mismatch.");
    const required = new Set(snapshot.portfolio.map(row => row.contract_id));
    for (const method of ["delta", "proxy"]) {
      const hedge = original.bundle.hedges[method];
      assert(hedge && statuses.has(hedge.status) && hedge.position_constraints?.compliant === true, "Risk Lab requires a valid original hedge ticket.");
      assert(new Set(hedge.trades.map(t => t.contract_id)).size === hedge.trades.length, "Duplicate original ticket leg.");
      let cost = 0;
      for (const trade of hedge.trades) {
        assert(Number.isInteger(trade.quantity) && finite(trade.total_cost) && trade.total_cost >= 0 && finite(trade.cost_per_lot) && trade.cost_per_lot >= 0 && close(trade.total_cost, Math.abs(trade.quantity) * trade.cost_per_lot), "Invalid original leg quantity or fee.");
        required.add(trade.contract_id);
        cost += trade.total_cost;
      }
      assert(close(cost, hedge.cost), "Original ticket fees do not reconcile.");
    }
    for (const set of ["optimization", "challenge"]) {
      for (const id of required) {
        const vector = item.unit_pnl?.[set]?.[id];
        assert(Array.isArray(vector) && vector.length === sets[set].length && vector.every(finite), "Risk Lab unit P&L is missing or invalid.");
      }
    }
    const rows = original.bundle.risk.scenarios;
    assert(rows.length === 100 && rows.every((row, i) => row.name === sets.optimization[i].name && sets.optimization[i].elapsed_days === snapshot.settings.horizon_days && scenarioFields.filter(field => field !== "elapsed_days").every(field => row[field] === sets.optimization[i][field])), "Risk Lab optimization scenario order or configuration mismatch.");
    for (const method of Object.keys(methods)) {
      const result = evaluateTicket(snapshot, original, item, method);
      assert(close(result.worstLoss, original.bundle.risk.summary[method].worst_loss), "Risk Lab original worst loss does not reconcile.");
      result.net.forEach((value, i) => assert(close(value, rows[i][method === "unhedged" ? "unhedged" : `${method}_net`]), "Risk Lab original scenario P&L does not reconcile."));
    }
  }
  return index;
}

export function summarize(gross, cost, limit) {
  assert(Array.isArray(gross) && gross.length > 0 && gross.every(finite) && finite(cost) && cost >= 0 && finite(limit) && limit >= 0, "Invalid scenario calculation input.");
  const net = gross.map(value => value - cost);
  const minimum = Math.min(...net);
  const worstIndex = net.indexOf(minimum);
  const stressMeasure = Math.max(...gross.map(Math.abs)) + cost;
  return {gross, net, cost, worstIndex, worstLoss: Math.max(0, -minimum), stressMeasure, riskCompliant: stressMeasure <= limit + 1e-5, excessCount: gross.filter(value => Math.abs(value) + cost > limit + 1e-5).length};
}

export function evaluateTicket(snapshot, original, item, method = "proxy", kept = null, multiplier = 1, set = "optimization") {
  assert(Object.hasOwn(methods, method) && ["optimization", "challenge"].includes(set) && finite(multiplier) && multiplier >= 0, "Invalid Risk Lab selection.");
  const vectors = item.unit_pnl[set];
  const count = set === "optimization" ? 100 : 12;
  const gross = Array(count).fill(0);
  const positions = {};
  const add = (id, quantity) => {
    assert(Number.isInteger(quantity) && vectors[id]?.length === count && vectors[id].every(finite), "Invalid position or missing unit P&L.");
    vectors[id].forEach((value, i) => { gross[i] += quantity * value; });
    positions[id] = (positions[id] || 0) + quantity;
  };
  snapshot.portfolio.forEach(row => add(row.contract_id, row.quantity));
  const ticket = method === "unhedged" ? [] : original.bundle.hedges[method].trades;
  const trades = kept === null ? ticket : ticket.filter(trade => kept.has(trade.contract_id));
  trades.forEach(trade => add(trade.contract_id, trade.quantity));
  const cost = trades.reduce((sum, trade) => sum + trade.total_cost, 0) * multiplier;
  const result = summarize(gross, cost, snapshot.settings.risk_limit_dollars);
  const lots = trades.reduce((sum, trade) => sum + Math.abs(trade.quantity), 0);
  const positionsCompliant = Object.values(positions).every(quantity => Math.abs(quantity) <= snapshot.settings.position_limit);
  const lotsCompliant = lots <= snapshot.settings.gross_lot_limit;
  const boundsCompliant = trades.every(trade => {
    const kind = snapshot.contracts.find(contract => contract.contract_id === trade.contract_id)?.kind;
    return kind && (kind !== "cso" || snapshot.settings.allow_cso_hedge) && Math.abs(trade.quantity) <= (kind === "future" ? snapshot.settings.future_bound : snapshot.settings.option_bound);
  });
  return {...result, trades, lots, positionsCompliant, lotsCompliant, boundsCompliant, compliant: result.riskCompliant && positionsCompliant && lotsCompliant && boundsCompliant};
}

// Equality to the unhedged worst loss, using the fixed ticket's constant gross P&L.
export function lossBreakEven(gross, cost, unhedgedLoss) {
  assert(gross.length > 0 && gross.every(finite) && finite(cost) && cost >= 0 && finite(unhedgedLoss) && unhedgedLoss >= 0, "Invalid break-even input.");
  if (unhedgedLoss === 0 || Math.max(0, -Math.min(...gross)) >= unhedgedLoss) return {kind: "no_advantage", multiplier: 0};
  if (cost === 0) return {kind: "no_crossing", multiplier: null};
  return {kind: "crossing", multiplier: (unhedgedLoss + Math.min(...gross)) / cost};
}

const text = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[ch]));
const number = (value, digits = 0) => value.toLocaleString("en-US", {minimumFractionDigits: digits, maximumFractionDigits: digits});
const money = (value, digits = 0) => `${value < 0 ? "−" : ""}$${number(Math.abs(value), digits)}`;
const short = value => value.replaceAll("FIXTURE_", "");
const scenarioName = value => value.replaceAll("_", " ");
const status = result => `<span class="lab-status ${result.compliant ? "pass" : "breach"}">${result.compliant ? "Within displayed limits" : "Limit exceeded"}</span>`;
const tableWrap = (caption, head, rows) => `<div class="lab-table-wrap" role="region" aria-label="${text(caption)}" tabindex="0"><table class="lab-table"><caption>${text(caption)}</caption><thead><tr>${head.map(label => `<th scope="col">${label}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
const pnlCell = value => `<td class="lab-number${value < 0 ? " negative" : ""}">${money(value, 2)}</td>`;
function constraints(result, limit) {
  return `<div class="lab-constraints">${status(result)}<span>max |gross P&amp;L| + costs <b>${money(result.stressMeasure)}</b> / ${money(limit)}</span><span>Position ${result.positionsCompliant ? "✓" : "exceeded"} · trade lots ${result.lotsCompliant ? "✓" : "exceeded"} · leg bounds ${result.boundsCompliant ? "✓" : "exceeded"}</span></div>`;
}

export async function mountRiskLab(root, data, bytes) {
  const hash = await demoHash(bytes);
  const response = await fetch("risk-lab-data.json");
  if (!response.ok) throw new Error(`Risk Lab data could not load (${response.status}).`);
  const sidecar = await response.json();
  const cases = validateSidecar(sidecar, data, hash);
  let snapshot, original, item, kept = new Set(), method = "proxy", multiplier = 1;
  root.innerHTML = `<div class="lab-heading"><div><p class="eyebrow">FIXED TICKET / FULL REPRICING</p><h2>Risk Lab</h2></div><a href="risk-lab-data.json">Inspect scenario data ↗</a></div>
    <p class="muted lab-intro">How sensitive is this hedge to costs, missing legs and a wider stress grid? Change the saved ticket assumptions below. No re-optimization takes place.</p>
    <section class="panel lab-cost"><div class="panel-heading"><div><span class="index">05</span><h3>When does cost erase the benefit?</h3></div></div><p class="muted">Original tickets held fixed · worst scenario loss after costs · all 100 optimization scenarios</p><div data-lab="cost-table"></div><div data-lab="thresholds" class="lab-thresholds"></div><details><summary>Loss and the engine constraint measure different things</summary><p>Worst loss = max(0, −min(gross P&amp;L − costs)). The engine's conservative stress constraint is max |gross P&amp;L| + costs ≤ the risk limit. A large positive P&amp;L can breach the absolute constraint even when worst loss is small. Changed costs are checked again; the original solver's feasibility status is not reused.</p><p>Costs multiply the entire recorded ticket fee, including crossing costs and per-contract fees. Gross repricing and integer quantities stay fixed. Break-even refers to the worst-loss benefit versus no hedge, not profitability or a new optimized hedge.</p></details></section>
    <div class="lab-controls"><div><label for="lab-method">Saved hedge ticket</label><select id="lab-method"><option value="proxy">Futures + options</option><option value="delta">Futures only</option></select></div><fieldset><legend>Cost assumption for both experiments</legend><div class="lab-radios">${[1,2,4].map(value => `<label><input type="radio" name="lab-cost" value="${value}" ${value === 1 ? "checked" : ""}>${value}× costs</label>`).join("")}</div></fieldset><p data-lab="identity" class="footnote"></p></div>
    <section class="panel lab-removal"><div class="panel-heading"><div><span class="index">06</span><h3>What does each leg protect?</h3></div><button class="secondary" data-lab="restore">Restore all legs</button></div><p class="muted">Uncheck a leg to model never including it at inception. Its trading cost disappears too; this is not a close-out trade.</p><div data-lab="legs" class="lab-legs"></div><div data-lab="removal-summary"></div><details><summary>Inspect all 100 original scenarios</summary><div data-lab="removal-table"></div></details></section>
    <section class="panel lab-challenge"><div class="panel-heading"><div><span class="index">07</span><h3>Does the remaining ticket survive a wider grid?</h3></div><span class="tag">12 EXTRA STRESSES</span></div><p class="muted">The selected legs and costs are held fixed in 12 predetermined synthetic scenarios excluded from optimization. All scenarios reprice over one day; these are not historical holdout observations.</p><div data-lab="challenge-summary"></div><div data-lab="challenge-table"></div><details><summary>Read the challenge assumptions</summary><p>Parallel and twist shifts are USD/bbl. Normal volatility is USD/bbl/√year; vanilla volatility is lognormal. Multipliers act on the active volatility state. The +2 normal-vol scenario is additive in native units. Each row shows its exact assumptions. Stress results have no assigned probabilities and do not bound future losses.</p><p>The same absolute stress limit is applied diagnostically to the wider grid. The original optimizer never saw these scenarios; passing a check does not establish robustness outside this grid.</p></details></section>
    <p class="footnote lab-statusline" data-lab="status" role="status" aria-live="polite"></p>`;
  const node = name => root.querySelector(`[data-lab="${name}"]`);
  const evaluate = (kind, selected = null, scale = multiplier, set = "optimization") => evaluateTicket(snapshot, original, item, kind, selected, scale, set);
  const grid = sidecar.scenario_sets.optimization;
  const challenge = sidecar.scenario_sets.challenge;
  function renderCosts() {
    const rows = Object.entries(methods).map(([kind, label]) => `<tr><th scope="row">${label}</th>${[1,2,4].map(scale => {
      const result = evaluate(kind, null, scale);
      return `<td class="lab-number"><strong>${money(result.worstLoss)}</strong><small>Cost ${money(result.cost)} · abs stress + cost ${money(result.stressMeasure)}</small>${status(result)}</td>`;
    }).join("")}</tr>`);
    node("cost-table").innerHTML = tableWrap("Fixed tickets · worst loss in USD", ["Strategy", "1× costs", "2× costs", "4× costs"], rows);
    const unhedged = evaluate("unhedged");
    node("thresholds").innerHTML = ["delta", "proxy"].map(kind => {
      const result = evaluate(kind, null, 1), point = lossBreakEven(result.gross, result.cost, unhedged.worstLoss);
      const label = point.kind === "no_advantage" ? "No worst-loss advantage, even at zero costs." : point.kind === "no_crossing" ? "No cost crossing: the ticket has zero recorded fees." : `Worst-loss advantage ends at ${number(point.multiplier, 2)}× costs${point.multiplier > 4 ? " (outside the 1–4× view)" : point.multiplier < 1 ? " (already lost at 1×)" : ""}.`;
      return `<p><b>${methods[kind]}</b><span>${label}</span></p>`;
    }).join("");
  }
  function renderLegs() {
    const ticket = original.bundle.hedges[method].trades;
    node("legs").innerHTML = ticket.length ? ticket.map((trade, index) => {
      const contribution = item.unit_pnl.optimization[trade.contract_id].map(value => value * trade.quantity - trade.total_cost * multiplier);
      const best = contribution.indexOf(Math.max(...contribution)), worst = contribution.indexOf(Math.min(...contribution));
      const protect = contribution.filter(value => value > TOLERANCE).length, hurt = contribution.filter(value => value < -TOLERANCE).length;
      return `<div class="lab-leg"><label for="lab-leg-${index}"><input id="lab-leg-${index}" type="checkbox" data-contract="${text(trade.contract_id)}" ${kept.has(trade.contract_id) ? "checked" : ""}><span><b>${trade.quantity < 0 ? "Sell" : "Buy"} ${Math.abs(trade.quantity)} · ${text(short(trade.contract_id))}</b><small>Cost ${money(trade.total_cost * multiplier, 2)} · improves P&amp;L in ${protect}/100, reduces it in ${hurt}/100</small></span></label><details><summary>Contribution by scenario</summary><p>Net marginal contribution versus omitting this leg: best ${money(contribution[best], 2)} in ${text(scenarioName(grid[best].name))}; worst ${money(contribution[worst], 2)} in ${text(scenarioName(grid[worst].name))}. Flat within $0.01: ${100-protect-hurt} scenarios. Contributions include this leg's cost at ${multiplier}×.</p></details></div>`;
    }).join("") : '<p class="muted">This saved ticket has zero legs. Both grids show the original portfolio.</p>';
    node("legs").querySelectorAll("input").forEach(input => input.addEventListener("change", () => {
      if (input.checked) kept.add(input.dataset.contract); else kept.delete(input.dataset.contract);
      refreshResults();
    }));
  }
  function refreshResults() {
    const selected = evaluate(method, kept), full = evaluate(method), noHedge = evaluate("unhedged");
    const limit = snapshot.settings.risk_limit_dollars;
    node("removal-summary").innerHTML = `<div class="lab-summary"><div><span>Selected worst loss</span><strong>${money(selected.worstLoss)}</strong><small>${text(scenarioName(grid[selected.worstIndex].name))}</small></div><div><span>Original ticket · ${multiplier}× costs</span><strong>${money(full.worstLoss)}</strong><small>Worst loss on the same grid</small></div><div><span>Retained cost / lots</span><strong>${money(selected.cost)} / ${selected.lots}</strong><small>${selected.trades.length} of ${original.bundle.hedges[method].trades.length} legs</small></div></div>${constraints(selected, limit)}`;
    node("removal-table").innerHTML = tableWrap("All 100 original scenarios · net P&L (USD)", ["Scenario", "No hedge", "Original ticket", "Selected legs", "Change vs original"], grid.map((row, i) => `<tr><th scope="row">${text(scenarioName(row.name))}</th>${pnlCell(noHedge.net[i])}${pnlCell(full.net[i])}${pnlCell(selected.net[i])}${pnlCell(selected.net[i] - full.net[i])}</tr>`));
    const wide = evaluate(method, kept, multiplier, "challenge"), wideNoHedge = evaluate("unhedged", null, multiplier, "challenge");
    const worsened = wide.net.filter((value, i) => value < wideNoHedge.net[i] - TOLERANCE).length;
    node("challenge-summary").innerHTML = `<div class="lab-summary"><div><span>Original grid worst loss</span><strong>${money(selected.worstLoss)}</strong><small>100 optimization scenarios</small></div><div><span>Challenge worst loss</span><strong>${money(wide.worstLoss)}</strong><small>${text(scenarioName(challenge[wide.worstIndex].name))}</small></div><div><span>Worse than no hedge</span><strong>${worsened} / 12</strong><small>${wide.excessCount} scenarios exceed the absolute stress limit</small></div></div>${constraints(wide, limit)}`;
    node("challenge-table").innerHTML = tableWrap("All 12 extra scenarios · selected ticket at the current costs", ["Scenario / assumptions", "No hedge P&amp;L", "Selected P&amp;L", "Change vs no hedge", "Absolute stress + cost"], challenge.map((row, i) => {
      const stress = Math.abs(wide.gross[i]) + wide.cost;
      return `<tr><th scope="row">${text(scenarioName(row.name))}<small>Price ${row.parallel_dollars >= 0 ? "+" : ""}${number(row.parallel_dollars, 0)} · twist ${row.twist_dollars >= 0 ? "+" : ""}${number(row.twist_dollars, 0)}<br>Normal ${number(row.normal_vol_factor, 1)}× ${row.normal_vol_additive ? `+ ${number(row.normal_vol_additive, 1)}` : ""} · vanilla ${number(row.lognormal_vol_factor, 1)}×</small></th>${pnlCell(wideNoHedge.net[i])}${pnlCell(wide.net[i])}${pnlCell(wide.net[i] - wideNoHedge.net[i])}<td class="lab-number">${money(stress)}<small class="${stress > limit + 1e-5 ? "negative" : ""}">${stress > limit + 1e-5 ? "EXCEEDS LIMIT" : "Within stress limit"}</small></td></tr>`;
    }));
    node("status").textContent = `${methods[method]} · ${selected.trades.length} selected legs · ${multiplier}× costs · original-grid worst loss ${money(selected.worstLoss)} · challenge worst loss ${money(wide.worstLoss)}. Each applied market preset restores its original ticket.`;
  }
  const restore = () => { kept = new Set(original.bundle.hedges[method].trades.map(trade => trade.contract_id)); renderLegs(); refreshResults(); };
  root.querySelector("#lab-method").addEventListener("change", event => { method = event.target.value; restore(); });
  root.querySelectorAll('input[name="lab-cost"]').forEach(input => input.addEventListener("change", event => { multiplier = Number(event.target.value); renderLegs(); refreshResults(); }));
  node("restore").addEventListener("click", restore);
  return {update(nextSnapshot, nextCase) {
    snapshot = nextSnapshot; original = nextCase; item = cases.get(original.bundle.bundle_id);
    assert(item && identityFields.every(key => item[key] === original.bundle[key]), "Active result does not match Risk Lab data.");
    root.dataset.bundleId = original.bundle.bundle_id;
    node("identity").textContent = `Active preset: CSO ${original.cso_shift >= 0 ? "+" : ""}${original.cso_shift} native units · vanilla ${original.vanilla_shift_pp >= 0 ? "+" : ""}${original.vanilla_shift_pp} pp. All experiments use the active result above.`;
    renderCosts(); restore();
  }};
}
