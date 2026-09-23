"""Chronological, paired one-session hedge evaluation; never a live performance claim.

An observation contains decision-time marks and next-observation marks. Strategies
receive only DecisionView, so realized marks cannot enter hedge selection. The
final 40 distinct sessions are holdout; all preceding sessions (at least 80) train.
Each session is a fresh, round-trip episode, not a continuous position ledger.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
import random
from typing import Callable, Mapping, Sequence


class HistoryValidationError(ValueError):
    pass


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HistoryValidationError(f"{field} must include a timezone")


@dataclass(frozen=True)
class HedgeMark:
    contract_id: str
    kind: str  # future or vanilla
    multiplier: float
    mark: float
    next_mark: float
    half_spread: float  # price units, used for entry AND exit
    fee_per_contract: float  # USD, used for entry AND exit


@dataclass(frozen=True)
class HistoryObservation:
    observation_id: str
    session_date: date
    decision_at: datetime
    known_at: datetime
    realized_at: datetime
    snapshot_id: str
    next_snapshot_id: str
    source: str
    raw_sha256: str
    data_kind: str
    target_contract_id: str
    target_quantity: int
    target_multiplier: float
    target_mark: float
    next_target_mark: float
    cost_schedule_id: str
    hedges: tuple[HedgeMark, ...]


@dataclass(frozen=True)
class DecisionHedge:
    contract_id: str
    kind: str
    multiplier: float
    mark: float
    half_spread: float
    fee_per_contract: float


@dataclass(frozen=True)
class DecisionView:
    observation_id: str
    session_date: date
    decision_at: datetime
    snapshot_id: str
    target_contract_id: str
    target_quantity: int
    target_multiplier: float
    target_mark: float
    cost_schedule_id: str
    hedges: tuple[DecisionHedge, ...]


Strategy = Callable[[DecisionView], Mapping[str, int]]
Trainer = Callable[[tuple[HistoryObservation, ...]], Strategy]


def fit_ridge_hedge(training: tuple[HistoryObservation, ...], *, futures_only: bool = False,
                    ridge_strength: float = 1e-6, lot_bound: int = 20) -> Strategy:
    """Fit fixed-contract hedge lots using training P&L only, then freeze them.

    This small benchmark uses ridge least squares followed by integer rounding
    and lot clipping. It is not an integer optimum, cost-aware execution policy,
    or rolling-contract strategy. It refuses contract-universe changes instead
    of assuming unrelated expiries have identical risk.
    """
    import numpy as np
    if not training or ridge_strength <= 0 or lot_bound < 1:
        raise HistoryValidationError("Invalid ridge benchmark configuration")
    universes = [{h.contract_id for h in o.hedges if not futures_only or h.kind == 'future'} for o in training]
    target_spec = (training[0].target_contract_id, training[0].target_quantity, training[0].target_multiplier)
    if any((o.target_contract_id, o.target_quantity, o.target_multiplier) != target_spec for o in training):
        raise HistoryValidationError("Fixed-contract benchmark requires unchanged target exposure; rollover needs an explicit policy")
    if any(universe != universes[0] for universe in universes) or not universes[0]:
        raise HistoryValidationError("Fixed-contract benchmark requires an unchanged nonempty training hedge universe; rollover needs an explicit policy")
    identifiers = sorted(universes[0])
    matrix = np.array([[next(h.multiplier*(h.next_mark-h.mark) for h in o.hedges if h.contract_id == key)
                        for key in identifiers] for o in training], dtype=float)
    target = np.array([o.target_quantity*o.target_multiplier*(o.next_target_mark-o.target_mark) for o in training])
    gram = matrix.T @ matrix
    penalty = ridge_strength * max(float(np.trace(gram))/len(identifiers), 1.0)
    weights = np.linalg.solve(gram+penalty*np.eye(len(identifiers)), -(matrix.T @ target))
    lots = {key: int(np.clip(np.rint(value), -lot_bound, lot_bound)) for key, value in zip(identifiers, weights)}
    def strategy(decision: DecisionView) -> Mapping[str, int]:
        if (decision.target_contract_id, decision.target_quantity, decision.target_multiplier) != target_spec:
            raise HistoryValidationError("Holdout target exposure differs; fixed-contract benchmark cannot infer rollover or rescaling")
        universe = {h.contract_id for h in decision.hedges if not futures_only or h.kind == 'future'}
        if universe != set(identifiers):
            raise HistoryValidationError("Holdout contract universe differs; fixed-contract benchmark cannot infer rollover")
        return dict(lots)
    return strategy


def futures_only_ridge(training: tuple[HistoryObservation, ...]) -> Strategy:
    return fit_ridge_hedge(training, futures_only=True)


def futures_vanilla_ridge(training: tuple[HistoryObservation, ...]) -> Strategy:
    return fit_ridge_hedge(training)


def _paired_bootstrap(series: Mapping[str, list[float]], *, block_length: int, samples: int, seed: int) -> dict:
    n = len(series['unhedged'])
    if not 1 <= block_length <= n or not 100 <= samples <= 5000:
        raise HistoryValidationError("Bootstrap needs block length 1..holdout length and 100..5000 samples")
    rng = random.Random(seed)
    draws = []
    for _ in range(samples):
        indices = []
        while len(indices) < n:
            start = rng.randrange(n-block_length+1)
            indices.extend(range(start, start+block_length))
        draws.append(indices[:n])
    def std(values):
        mean = sum(values)/len(values)
        return math.sqrt(sum((v-mean)**2 for v in values)/(len(values)-1))
    def interval(values):
        values = sorted(values)
        def quantile(p):
            index=(len(values)-1)*p; low=math.floor(index); high=math.ceil(index)
            return values[low]+(values[high]-values[low])*(index-low)
        return [quantile(.025), quantile(.975)]
    result = {'method': 'paired_moving_block_bootstrap', 'block_length': block_length,
              'resamples': samples, 'seed': seed, 'confidence_level': .95,
              'assumption': 'Holdout episodes are approximately stationary; adjacent dependence is approximated by fixed contiguous blocks. These intervals do not capture model or regime uncertainty.',
              'comparisons': {}}
    for name, values in series.items():
        if name == 'unhedged':
            continue
        base = series['unhedged']; mean_differences=[]; std_differences=[]
        for draw in draws:
            a=[values[i] for i in draw]; b=[base[i] for i in draw]
            mean_differences.append(sum(x-y for x,y in zip(a,b))/n)
            std_differences.append(std(a)-std(b))
        result['comparisons'][name+'_minus_unhedged'] = {
            'mean_net_pnl_difference_usd': sum(x-y for x,y in zip(values,base))/n,
            'mean_net_pnl_difference_ci95_usd': interval(mean_differences),
            'std_net_pnl_difference_usd': std(values)-std(base),
            'std_net_pnl_difference_ci95_usd': interval(std_differences),
        }
    return result


def _decision(observation: HistoryObservation) -> DecisionView:
    return DecisionView(
        observation.observation_id, observation.session_date,
        observation.decision_at, observation.snapshot_id,
        observation.target_contract_id, observation.target_quantity,
        observation.target_multiplier, observation.target_mark,
        observation.cost_schedule_id,
        tuple(DecisionHedge(h.contract_id, h.kind, h.multiplier, h.mark,
                            h.half_spread, h.fee_per_contract)
              for h in observation.hedges),
    )


def _validate(observation: HistoryObservation) -> None:
    for name in ("decision_at", "known_at", "realized_at"):
        _aware(getattr(observation, name), name)
    if observation.known_at > observation.decision_at:
        raise HistoryValidationError("future information is unavailable at decision time")
    if observation.realized_at <= observation.decision_at:
        raise HistoryValidationError("realized_at must follow decision_at")
    if observation.realized_at > datetime.now(timezone.utc):
        raise HistoryValidationError("Historical outcomes cannot be dated in the future")
    for name in ("observation_id", "snapshot_id", "next_snapshot_id", "source",
                 "target_contract_id", "cost_schedule_id"):
        if not getattr(observation, name).strip():
            raise HistoryValidationError(f"missing {name}")
    if len(observation.raw_sha256) != 64 or any(c not in '0123456789abcdef' for c in observation.raw_sha256):
        raise HistoryValidationError("raw_sha256 must identify the source data")
    if observation.data_kind not in {"observed", "test_fixture"}:
        raise HistoryValidationError("history must explicitly identify observed or test_fixture data")
    if isinstance(observation.target_quantity, bool) or not isinstance(observation.target_quantity, int):
        raise HistoryValidationError("target quantity must be integer contracts")
    if not all(math.isfinite(x) for x in (observation.target_multiplier, observation.target_mark, observation.next_target_mark)) or observation.target_multiplier <= 0:
        raise HistoryValidationError("invalid target marks/multiplier")
    # The target in this protocol is a CSO, whose premium is nonnegative even
    # when its underlying calendar spread or strike is below zero.
    if observation.target_mark < 0 or observation.next_target_mark < 0:
        raise HistoryValidationError("target option premium cannot be negative")
    ids = [h.contract_id for h in observation.hedges]
    if len(ids) != len(set(ids)):
        raise HistoryValidationError("duplicate hedge contract")
    for h in observation.hedges:
        if not h.contract_id or h.kind not in {"future", "vanilla"}:
            raise HistoryValidationError("only identified futures/vanilla hedges are supported")
        if not all(math.isfinite(x) for x in (h.multiplier, h.mark, h.next_mark, h.half_spread, h.fee_per_contract)):
            raise HistoryValidationError("non-finite hedge input")
        if h.multiplier <= 0 or h.half_spread < 0 or h.fee_per_contract < 0:
            raise HistoryValidationError("invalid hedge multiplier/cost")
        if h.kind == "vanilla" and (h.mark < 0 or h.next_mark < 0):
            raise HistoryValidationError("vanilla option premium cannot be negative")


def _metric(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    n = len(values)
    mean = sum(values) / n
    return {
        "episodes": n, "total_net_pnl_usd": sum(values),
        "mean_net_pnl_usd": mean,
        "std_net_pnl_usd": math.sqrt(sum((x - mean) ** 2 for x in values) / max(1, n - 1)),
        "worst_net_pnl_usd": min(values),
        "p05_net_pnl_usd": ordered[max(0, math.ceil(.05 * n) - 1)],
    }


def evaluate_history(
    observations: Sequence[HistoryObservation], *,
    futures_only_trainer: Trainer | None = None,
    futures_vanilla_trainer: Trainer | None = None,
    train_sessions: int = 80, test_sessions: int = 40,
    bootstrap_block_length: int = 5, bootstrap_samples: int = 500,
    bootstrap_seed: int = 20260923,
) -> dict:
    """Use common observations, marks and round-trip costs for all three baselines.

    Insufficient history produces ``pending``; malformed/future data raises.
    Final40 chronological sessions are holdout; preceding sessions train. Trainers see only
    the training segment. Source labels are user-supplied evidence, not proof of
    provenance; raw file hashes should be checked against the import manifest.
    """
    if train_sessions != 80 or test_sessions != 40:
        raise HistoryValidationError("the registered protocol requires at least80 training/final40 holdout sessions")
    if len(observations) > 10000:
        raise HistoryValidationError("maximum 10000 observations per evaluation")
    for o in observations:
        _validate(o)
    ordered = sorted(observations, key=lambda x: x.decision_at)
    if len({o.observation_id for o in ordered}) != len(ordered):
        raise HistoryValidationError("duplicate observation IDs")
    if len({o.session_date for o in ordered}) != len(ordered):
        raise HistoryValidationError("exactly one observation per session is required")
    if [o.session_date for o in ordered] != sorted(o.session_date for o in ordered):
        raise HistoryValidationError("session dates and decision times disagree")
    report = {
        "status": "pending", "protocol": "all_prior_min80_train_final40_holdout_v1",
        "evaluation_type": "paired_one_session_round_trip_episodes",
        "required_sessions": 120, "available_sessions": len(ordered),
        "ignored_sessions": 0,
        "strategies": ["unhedged", "futures_only", "futures_vanilla"],
        "cost_convention": "2 * abs(contracts) * (half_spread * multiplier + fee_per_contract)",
        "limitations": ["No continuous rebalancing ledger or execution/slippage guarantee.",
                        "No live evidence; holdout performance does not establish future returns."],
        "pending_reasons": [], "metrics": {}, "episodes": [],
    }
    if len(ordered) < 120:
        report["pending_reasons"].append(f"Need {120-len(ordered)} additional distinct observed sessions.")
    if any(o.data_kind != "observed" for o in ordered):
        report["pending_reasons"].append("Test fixtures cannot establish real-data evidence.")
    if report["pending_reasons"]:
        return report
    selected = ordered
    train, holdout = tuple(selected[:-40]), selected[-40:]
    if max(o.realized_at for o in train) > holdout[0].decision_at:
        raise HistoryValidationError("training outcomes extend beyond first holdout decision")
    futures_only_trainer = futures_only_trainer or futures_only_ridge
    futures_vanilla_trainer = futures_vanilla_trainer or futures_vanilla_ridge
    strategies = {"unhedged": lambda _: {},
                  "futures_only": futures_only_trainer(train),
                  "futures_vanilla": futures_vanilla_trainer(train)}
    for o in holdout:
        available = {h.contract_id: h for h in o.hedges}
        target_pnl = o.target_quantity * o.target_multiplier * (o.next_target_mark-o.target_mark)
        for name, strategy in strategies.items():
            holdings = dict(strategy(_decision(o)))
            gross = target_pnl
            cost = 0.0
            for contract_id, quantity in holdings.items():
                if contract_id not in available:
                    raise HistoryValidationError(f"{name} selected unavailable {contract_id}")
                if isinstance(quantity, bool) or not isinstance(quantity, int):
                    raise HistoryValidationError("strategy quantities must be integer contracts")
                h = available[contract_id]
                if name == "futures_only" and h.kind != "future" and quantity:
                    raise HistoryValidationError("futures_only selected a vanilla option")
                gross += quantity * h.multiplier * (h.next_mark-h.mark)
                cost += 2 * abs(quantity) * (h.half_spread*h.multiplier+h.fee_per_contract)
            report["episodes"].append({
                "observation_id": o.observation_id, "session_date": str(o.session_date),
                "snapshot_id": o.snapshot_id, "next_snapshot_id": o.next_snapshot_id,
                "strategy": name, "holdings": holdings, "cost_schedule_id": o.cost_schedule_id,
                "gross_pnl_usd": gross, "cost_usd": cost, "net_pnl_usd": gross-cost,
            })
    report.update(status="complete", pending_reasons=[],
                  train_start=str(train[0].session_date), train_end=str(train[-1].session_date),
                  holdout_start=str(holdout[0].session_date), holdout_end=str(holdout[-1].session_date),
                  observation_ids=[o.observation_id for o in selected],
                  source_hashes=sorted({o.raw_sha256 for o in selected}))
    report["metrics"] = {name: _metric([r["net_pnl_usd"] for r in report["episodes"] if r["strategy"] == name]) for name in strategies}
    report['training_sessions'] = len(train)
    report['holdout_sessions'] = len(holdout)
    report['trainer_names'] = [futures_only_trainer.__qualname__, futures_vanilla_trainer.__qualname__]
    report['default_benchmark_limitations'] = 'Training-only fixed-contract ridge weights, rounded/clipped to integer lots; heuristic, no rollover mapping or continuous rebalancing.'
    report['uncertainty'] = _paired_bootstrap({name:[r['net_pnl_usd'] for r in report['episodes'] if r['strategy']==name] for name in strategies},
                                            block_length=bootstrap_block_length, samples=bootstrap_samples, seed=bootstrap_seed)
    canonical = json.dumps([asdict(o) for o in selected], default=str, sort_keys=True, separators=(",", ":"))
    report["evaluation_input_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return report
