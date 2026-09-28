import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {createHash} from "node:crypto";
import {validateSidecar, evaluateTicket, summarize, lossBreakEven} from "../docs/options-desk/risk-lab.mjs";

const bytes = readFileSync(new URL("../docs/options-desk/demo-data.json", import.meta.url));
const base = JSON.parse(bytes);
const sidecar = JSON.parse(readFileSync(new URL("../docs/options-desk/risk-lab-data.json", import.meta.url)));
const hash = createHash("sha256").update(bytes).digest("hex");
const index = validateSidecar(sidecar, base, hash);
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) <= 0.01, `${actual} differs from ${expected}`);
const snapshotFor = original => base.snapshots.find(row => row.index === original.snapshot_index);

test("all 45 cases reconcile to the original 1× scenarios and summaries", () => {
  assert.equal(index.size, 45);
  for (const original of base.cases) {
    for (const method of ["unhedged", "delta", "proxy"]) {
      const result = evaluateTicket(snapshotFor(original), original, index.get(original.bundle.bundle_id), method);
      near(result.worstLoss, original.bundle.risk.summary[method].worst_loss);
      original.bundle.risk.scenarios.forEach((row, i) => near(result.net[i], row[method === "unhedged" ? "unhedged" : `${method}_net`]));
    }
  }
});

test("fixed quantities have monotonically increasing loss and absolute stress as cost rises", () => {
  for (const original of base.cases) for (const method of ["delta", "proxy"]) {
    const snapshot = snapshotFor(original), item = index.get(original.bundle.bundle_id);
    const results = [1,2,4].map(scale => evaluateTicket(snapshot, original, item, method, null, scale));
    for (let i = 1; i < results.length; i++) {
      assert.ok(results[i].worstLoss >= results[i-1].worstLoss);
      assert.ok(results[i].stressMeasure >= results[i-1].stressMeasure);
      assert.deepEqual(results[i].gross, results[0].gross);
      assert.deepEqual(results[i].trades, results[0].trades);
    }
  }
});

test("removing every leg returns the original portfolio, with zero cost, on both grids", () => {
  for (const original of base.cases) for (const set of ["optimization", "challenge"]) {
    const snapshot = snapshotFor(original), item = index.get(original.bundle.bundle_id);
    const noHedge = evaluateTicket(snapshot, original, item, "unhedged", null, 4, set);
    for (const method of ["delta", "proxy"]) {
      const removed = evaluateTicket(snapshot, original, item, method, new Set(), 4, set);
      assert.deepEqual(removed.net, noHedge.net);
      assert.equal(removed.cost, 0);
      assert.equal(removed.lots, 0);
      assert.equal(removed.trades.length, 0);
    }
  }
});

test("omitting one leg subtracts its signed contribution and recovers its fee", () => {
  for (const original of base.cases) for (const method of ["delta", "proxy"]) {
    const snapshot = snapshotFor(original), item = index.get(original.bundle.bundle_id);
    const serialized = JSON.stringify(original.bundle.hedges[method]);
    for (const set of ["optimization", "challenge"]) {
      const full = evaluateTicket(snapshot, original, item, method, null, 2, set);
      for (const leg of full.trades) {
        const kept = new Set(full.trades.filter(trade => trade !== leg).map(trade => trade.contract_id));
        const removed = evaluateTicket(snapshot, original, item, method, kept, 2, set);
        removed.net.forEach((value, i) => near(value, full.net[i] - leg.quantity * item.unit_pnl[set][leg.contract_id][i] + leg.total_cost * 2));
        near(removed.cost, full.cost - leg.total_cost * 2);
      }
    }
    assert.equal(JSON.stringify(original.bundle.hedges[method]), serialized);
  }
});

test("gross absolute risk rejects a large positive tail even if net worst loss is small", () => {
  const result = summarize([30000, -1000], 100, 25000);
  assert.equal(result.worstLoss, 1100);
  assert.equal(result.stressMeasure, 30100);
  assert.equal(result.riskCompliant, false);
  assert.equal(result.excessCount, 1);
});

test("position compliance is recomputed when a necessary repair leg is omitted", () => {
  const original = structuredClone(base.cases[0]);
  const snapshot = structuredClone(snapshotFor(original));
  const leg = original.bundle.hedges.proxy.trades[0];
  snapshot.portfolio.push({contract_id: leg.contract_id, quantity: snapshot.settings.position_limit + 1});
  leg.quantity = -1;
  leg.total_cost = leg.cost_per_lot;
  const item = index.get(original.bundle.bundle_id);
  assert.equal(evaluateTicket(snapshot, original, item, "proxy", new Set([leg.contract_id])).positionsCompliant, true);
  assert.equal(evaluateTicket(snapshot, original, item, "proxy", new Set()).positionsCompliant, false);
});

test("break-even handles zero fees, no advantage and loss clipped at zero", () => {
  assert.deepEqual(lossBreakEven([-100, 50], 10, 150), {kind:"crossing", multiplier:5});
  assert.deepEqual(lossBreakEven([100, 200], 10, 50), {kind:"crossing", multiplier:15});
  assert.deepEqual(lossBreakEven([-100, 50], 0, 150), {kind:"no_crossing", multiplier:null});
  assert.deepEqual(lossBreakEven([-150, 0], 10, 100), {kind:"no_advantage", multiplier:0});
  assert.equal(lossBreakEven([100, 200], 10, 0).kind, "no_advantage");
});

test("real-ticket break-even exactly equates the worst losses", () => {
  for (const original of base.cases) for (const method of ["delta", "proxy"]) {
    const snapshot = snapshotFor(original), item = index.get(original.bundle.bundle_id);
    const result = evaluateTicket(snapshot, original, item, method);
    const noHedge = evaluateTicket(snapshot, original, item, "unhedged");
    const point = lossBreakEven(result.gross, result.cost, noHedge.worstLoss);
    if (point.kind === "crossing") near(evaluateTicket(snapshot, original, item, method, null, point.multiplier).worstLoss, noHedge.worstLoss);
  }
});

test("challenge keeps adverse outcomes instead of filtering rows", () => {
  let deteriorations = 0, excesses = 0;
  for (const original of base.cases) {
    const snapshot = snapshotFor(original), item = index.get(original.bundle.bundle_id);
    const challenge = evaluateTicket(snapshot, original, item, "proxy", null, 1, "challenge");
    const noHedge = evaluateTicket(snapshot, original, item, "unhedged", null, 1, "challenge");
    assert.equal(challenge.net.length, 12);
    deteriorations += challenge.net.filter((value, i) => value < noHedge.net[i]).length;
    excesses += challenge.excessCount;
  }
  assert.ok(deteriorations > 0);
  assert.ok(excesses > 0);
});

test("identity, coverage, non-finite vectors and reconstruction errors fail closed", () => {
  assert.throws(() => validateSidecar(sidecar, base, "0".repeat(64)), /checksum/);
  const corruptions = [
    [copy => { copy.cases[0].bundle_id = "wrong"; }, /identity/],
    [copy => { copy.cases[0].vol_version_id = "wrong"; }, /identity/],
    [copy => { copy.cases.pop(); }, /coverage/],
    [copy => { copy.validation.passed = false; }, /checks/],
    [copy => { copy.scenario_sets.challenge[1] = copy.scenario_sets.challenge[0]; }, /Duplicate/],
    [copy => { copy.scenario_sets.challenge[0] = copy.scenario_sets.optimization[0]; }, /overlaps/],
    [copy => { copy.scenario_sets.optimization.reverse(); }, /order/],
    [copy => { copy.scenario_sets.optimization[0].elapsed_days = 2; }, /configuration/],
    [copy => { copy.cases[0].unit_pnl.optimization[snapshotFor(base.cases[0]).portfolio[0].contract_id][0] = null; }, /unit P&L/],
    [copy => { copy.cases[0].unit_pnl.optimization[snapshotFor(base.cases[0]).portfolio[0].contract_id][0] += 100; }, /reconcile/],
  ];
  for (const [mutate, pattern] of corruptions) {
    const copy = structuredClone(sidecar); mutate(copy);
    assert.throws(() => validateSidecar(copy, base, hash), pattern);
  }
});
