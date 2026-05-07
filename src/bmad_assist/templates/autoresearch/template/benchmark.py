"""Fixed benchmark and scorecard writer for autoresearch.

DO NOT EDIT during iteration unless you have proven the benchmark itself is
wrong. The pass rule, scorecard schema, and orchestrator are part of the
contract; only the case generators (synthetic_cases, real_data_cases) and
domain helpers should ever change.

Exits 0 if the candidate passes the pass rule, 1 otherwise. CI-friendly.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent


# --- Pass rule (DOMAIN: tune for your problem; defaults match recipe) --------

DEFAULT_THRESHOLDS: dict[str, float] = {
    "f1_floor": 0.833,
    "tnr_floor": 0.333,
    "null_std_floor": 1e-8,
    "degenerate_rate_ceiling": 0.05,
    "runtime_budget_seconds": 60.0,
}


# --- Case shape (generic) ----------------------------------------------------


@dataclass
class Case:
    """One benchmark case. Generic across binary-classification problems."""

    case_id: str
    source: str  # bucket label for per-source metrics (e.g. "synthetic_null")
    should_flag: bool  # ground truth
    returns: np.ndarray  # the data the candidate sees
    n_trials: int = 1  # for multiple-testing correction
    candidate_returns: np.ndarray | None = None  # full search cohort, if applicable
    metadata: dict[str, Any] = field(default_factory=dict)


# --- Dynamic experiment loader (generic — leave alone) -----------------------


def _load_experiment_module() -> Any:
    """Import experiment.py from the same dir.

    Registers the module under a name so dataclasses defined inside resolve
    correctly across reload boundaries.
    """
    path = HERE / "experiment.py"
    spec = importlib.util.spec_from_file_location("experiment", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load experiment.py from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["experiment"] = module
    spec.loader.exec_module(module)
    return module


# --- Case generators (DOMAIN-SPECIFIC — fill in for your problem) ------------


def synthetic_cases(samples_per_class: int, seed: int) -> list[Case]:
    """Generate synthetic test cases with known ground truth.

    TODO: replace this stub with cases for your domain. The key invariant is
    that EVERY Case has a defensible should_flag label — otherwise the
    benchmark is measuring noise.

    Recommended buckets at minimum:
      - 'synthetic_null'    : data generated under the null hypothesis;
                              should_flag=True (the test should fail to reject)
      - 'synthetic_genuine' : data with a real effect; should_flag=False
                              (the test should reject the null)
      - 'synthetic_cherry'  : data cherry-picked from many candidates with
                              n_trials > 1; should_flag=True (multiple-testing
                              should bring the adjusted p-value back inside
                              the null)

    The 'cherry-picked' bucket protects against flag-everything reward hacking:
    a candidate that just flags everything will pass null metrics but fail
    genuine TNR.
    """
    rng = np.random.default_rng(seed)
    cases: list[Case] = []

    # TODO: replace with your domain's null-data generator
    for i in range(samples_per_class):
        cases.append(
            Case(
                case_id=f"synthetic_null_{i:03d}",
                source="synthetic_null",
                should_flag=True,  # data has no real edge → should flag
                returns=rng.normal(0.0, 0.01, 252),  # placeholder: noise
            )
        )

    # TODO: replace with your domain's genuine-effect generator
    for i in range(samples_per_class):
        cases.append(
            Case(
                case_id=f"synthetic_genuine_{i:03d}",
                source="synthetic_genuine",
                should_flag=False,  # data has a real edge → should NOT flag
                returns=rng.normal(0.001, 0.01, 252),  # placeholder: drift
            )
        )

    # TODO: add a cherry-picked bucket — generate K candidates, keep the best.
    # This is what protects against flag-everything reward hacking.
    return cases


def real_data_cases(samples_per_class: int, seed: int, *, quick: bool) -> list[Case]:
    """Optional: load real data for replay.

    TODO: implement if you have logged data to replay. Same Case shape as
    synthetic_cases. Keep should_flag labels HONEST — fixed-parameter is not
    the same as genuinely-positive (see recipe's 'Common pitfalls').

    Return [] if no real-data replay is available; the benchmark will run
    synthetic-only.
    """
    return []


# --- Confusion matrix and metrics (generic — leave alone) --------------------


def confusion(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute TP/FP/TN/FN, precision, TPR, TNR, F1, accuracy."""
    tp = sum(1 for r in rows if r["should_flag"] and r["flagged"])
    fp = sum(1 for r in rows if not r["should_flag"] and r["flagged"])
    tn = sum(1 for r in rows if not r["should_flag"] and not r["flagged"])
    fn = sum(1 for r in rows if r["should_flag"] and not r["flagged"])
    n = tp + fp + tn + fn
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    tpr = tp / (tp + fn) if (tp + fn) else 0.0
    tnr = tn / (tn + fp) if (tn + fp) else 0.0
    f1 = 2 * precision * tpr / (precision + tpr) if (precision + tpr) else 0.0
    accuracy = (tp + tn) / n if n else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "n": n,
        "precision": precision,
        "tpr": tpr,
        "tnr": tnr,
        "f1": f1,
        "accuracy": accuracy,
    }


# --- Benchmark orchestrator (generic — leave alone) --------------------------


def run_benchmark(
    *,
    candidate: str | None,
    permutations: int,
    samples_per_class: int,
    seed: int,
    block_size: int | None,
    synthetic_only: bool,
    quick_market: bool,
) -> dict[str, Any]:
    """Run the benchmark for a single candidate and return a scorecard dict."""
    experiment = _load_experiment_module()
    candidate_name = candidate or experiment.CURRENT_CANDIDATE

    cases: list[Case] = []
    cases.extend(synthetic_cases(samples_per_class, seed))
    if not synthetic_only:
        cases.extend(real_data_cases(samples_per_class, seed, quick=quick_market))

    rows: list[dict[str, Any]] = []
    null_stds: list[float] = []
    determinism_ok = True
    t0 = time.perf_counter()

    for case in cases:
        case_t0 = time.perf_counter()
        result = experiment.evaluate_candidate(
            case.returns,
            candidate_returns=case.candidate_returns,
            n_trials=case.n_trials,
            n_permutations=permutations,
            seed=seed,
            block_size=block_size,
            candidate_name=candidate_name,
        )
        case_elapsed = time.perf_counter() - case_t0

        # Determinism: re-run with the same seed; result must match exactly
        result_repeat = experiment.evaluate_candidate(
            case.returns,
            candidate_returns=case.candidate_returns,
            n_trials=case.n_trials,
            n_permutations=permutations,
            seed=seed,
            block_size=block_size,
            candidate_name=candidate_name,
        )
        if dict(result.to_dict()) != dict(result_repeat.to_dict()):
            determinism_ok = False

        rows.append(
            {
                "case_id": case.case_id,
                "source": case.source,
                "should_flag": case.should_flag,
                "flagged": result.flagged,
                "p_value": result.p_value,
                "p_adjusted": result.p_adjusted,
                "null_std": result.null_std,
                "statistic": result.statistic,
                "n_trials": case.n_trials,
                "n_returns": int(len(case.returns)),
                "elapsed_seconds": case_elapsed,
            }
        )
        null_stds.append(result.null_std)

    runtime = time.perf_counter() - t0

    # Aggregate metrics
    metrics_all = confusion(rows)
    sources = sorted({r["source"] for r in rows})
    by_source = {s: confusion([r for r in rows if r["source"] == s]) for s in sources}

    median_null_std = float(np.median(null_stds)) if null_stds else 0.0
    degenerate_rate = (
        float(sum(1 for v in null_stds if v <= DEFAULT_THRESHOLDS["null_std_floor"]))
        / len(null_stds)
        if null_stds
        else 1.0
    )

    pass_rule = {
        "f1_floor": metrics_all["f1"] >= DEFAULT_THRESHOLDS["f1_floor"],
        "tnr_floor": metrics_all["tnr"] >= DEFAULT_THRESHOLDS["tnr_floor"],
        "null_std_floor": median_null_std > DEFAULT_THRESHOLDS["null_std_floor"],
        "degenerate_rate_ceiling": (
            degenerate_rate <= DEFAULT_THRESHOLDS["degenerate_rate_ceiling"]
        ),
        "deterministic": determinism_ok,
        "runtime_budget": runtime <= DEFAULT_THRESHOLDS["runtime_budget_seconds"],
    }
    passed = all(pass_rule.values())

    return {
        "candidate": candidate_name,
        "passed": passed,
        "pass_rule": pass_rule,
        "thresholds": DEFAULT_THRESHOLDS,
        "metrics_all": metrics_all,
        "by_source": by_source,
        "calibration": {
            "median_null_std": median_null_std,
            "degenerate_rate": degenerate_rate,
            "deterministic": determinism_ok,
            "case_count": len(rows),
            "runtime_seconds": runtime,
            "permutations": permutations,
        },
        "notes": "Generated by benchmark.py. Hand-edits will be overwritten on the next run.",
        "rows": rows,
    }


# --- Scorecard writers (generic — leave alone) -------------------------------


def write_scorecard(scorecard: dict[str, Any]) -> None:
    """Persist the scorecard to scorecard.json and a flat scorecard.tsv."""
    (HERE / "scorecard.json").write_text(json.dumps(scorecard, indent=2, default=str))
    rows = scorecard.get("rows", [])
    if rows:
        cols = list(rows[0].keys())
        lines = ["\t".join(cols)]
        for r in rows:
            lines.append("\t".join(str(r[c]) for c in cols))
        (HERE / "scorecard.tsv").write_text("\n".join(lines) + "\n")
    else:
        (HERE / "scorecard.tsv").write_text("")


# --- CLI (generic — leave alone) ---------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns 0 if the candidate passes, 1 otherwise."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--candidate",
        default=None,
        help="Candidate name from experiment.py; defaults to CURRENT_CANDIDATE",
    )
    parser.add_argument("--permutations", type=int, default=300)
    parser.add_argument("--samples-per-class", type=int, default=40)
    parser.add_argument("--seed", type=int, default=20260506)
    parser.add_argument("--block-size", type=int, default=None)
    parser.add_argument(
        "--synthetic-only",
        action="store_true",
        help="Skip real_data_cases; faster smoke runs",
    )
    parser.add_argument(
        "--quick-market",
        action="store_true",
        help="Domain-specific reduced grid; passed through to real_data_cases",
    )
    args = parser.parse_args(argv)

    scorecard = run_benchmark(
        candidate=args.candidate,
        permutations=args.permutations,
        samples_per_class=args.samples_per_class,
        seed=args.seed,
        block_size=args.block_size,
        synthetic_only=args.synthetic_only,
        quick_market=args.quick_market,
    )
    write_scorecard(scorecard)

    print(f"candidate: {scorecard['candidate']}")
    print(f"passed: {scorecard['passed']}")
    for gate, ok in scorecard["pass_rule"].items():
        print(f"  {gate}: {ok}")
    print(f"f1: {scorecard['metrics_all']['f1']:.4f}")
    print(f"tnr: {scorecard['metrics_all']['tnr']:.4f}")
    print(f"median_null_std: {scorecard['calibration']['median_null_std']:.4e}")
    print(f"degenerate_rate: {scorecard['calibration']['degenerate_rate']:.4f}")
    print(f"runtime_seconds: {scorecard['calibration']['runtime_seconds']:.2f}")

    return 0 if scorecard["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
