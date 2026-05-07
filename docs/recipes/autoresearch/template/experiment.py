"""Candidate diagnostic implementations for autoresearch.

The only editable file during iteration. See program.md for the iteration
contract and pass rule. Add new candidates as functions decorated with
@register_candidate and dispatched via CANDIDATES.

The DiagnosticResult dataclass and evaluate_candidate() entrypoint are the
contract benchmark.py depends on. Keep them stable across iterations.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass

import numpy as np

# --- Result shape and helpers (generic — leave alone) ------------------------

ALPHA = 0.05  # significance threshold; pair with multiple-testing correction below


@dataclass(frozen=True)
class DiagnosticResult:
    """Output of a candidate evaluation. Generic across binary-classification cases."""

    p_value: float
    p_adjusted: float
    null_mean: float
    null_std: float
    statistic: float
    flagged: bool
    n_permutations_used: int

    def to_dict(self) -> Mapping[str, object]:
        """Return the result as a plain dict for serialization."""
        return asdict(self)


def _result(
    *,
    statistic: float,
    null_samples: np.ndarray,
    n_trials: int,
    n_permutations_used: int,
) -> DiagnosticResult:
    """Build a DiagnosticResult from a statistic and its null distribution.

    Conventions:
      p_value:     fraction of null samples >= observed statistic (one-sided, upper tail).
                   Floored at 1/(n+1) to avoid exact-zero p-values.
      p_adjusted:  Sidak correction across n_trials searches. With n_trials=1 it equals p_value.
      flagged:     True iff p_adjusted >= ALPHA — i.e. INSUFFICIENT evidence vs the null.
                   This is the convention used in p-hacking detection: "flag" means
                   "cannot rule out the null." Invert in your candidates if your domain
                   uses the opposite convention.
    """
    null_samples = np.asarray(null_samples, dtype=float)
    null_mean = float(null_samples.mean()) if null_samples.size else 0.0
    null_std = float(null_samples.std(ddof=0)) if null_samples.size else 0.0
    if null_samples.size == 0:
        p_value = 1.0
    else:
        p_value = float((null_samples >= statistic).sum()) / float(null_samples.size)
        p_value = max(p_value, 1.0 / (null_samples.size + 1.0))
    n_trials = max(1, int(n_trials))
    p_adjusted = 1.0 - (1.0 - p_value) ** n_trials if n_trials > 1 else p_value
    flagged = p_adjusted >= ALPHA
    return DiagnosticResult(
        p_value=p_value,
        p_adjusted=p_adjusted,
        null_mean=null_mean,
        null_std=null_std,
        statistic=float(statistic),
        flagged=bool(flagged),
        n_permutations_used=int(n_permutations_used),
    )


# --- Candidate dispatcher (generic — leave alone) ----------------------------

CANDIDATES: dict[str, Callable[..., DiagnosticResult]] = {}


def register_candidate(name: str) -> Callable[..., Callable[..., DiagnosticResult]]:
    """Decorator: register a candidate under a name in CANDIDATES."""

    def deco(fn: Callable[..., DiagnosticResult]) -> Callable[..., DiagnosticResult]:
        CANDIDATES[name] = fn
        return fn

    return deco


# Default candidate to run when --candidate is not passed on the CLI.
# Edit this string to switch to your latest candidate.
CURRENT_CANDIDATE = "control_degenerate"


# --- Candidates (DOMAIN-SPECIFIC — replace these with your methods) ----------


@register_candidate("control_degenerate")
def _control_degenerate(
    returns: np.ndarray,
    *,
    candidate_returns: np.ndarray | None = None,
    n_trials: int = 1,
    n_permutations: int = 100,
    seed: int = 20260506,
    block_size: int | None = None,
) -> DiagnosticResult:
    """Deliberate-failure baseline. Always KEEP THIS in the file.

    Computes mean(returns) as the statistic, then "permutes" by shuffling and
    recomputing — but mean is permutation-invariant, so the null distribution
    collapses near the observed statistic. This produces a degenerate null
    (null_std → 0) and demonstrates the pass-rule's calibration gates work.
    The benchmark should always FAIL this candidate.

    Keep this candidate even after you add real ones — it is the sanity check
    that the harness can detect failure. If your real candidates ever look
    suspiciously identical to this one's output, the bug is upstream.
    """
    rng = np.random.default_rng(seed)
    statistic = float(np.mean(returns))
    nulls = []
    for _ in range(int(n_permutations)):
        shuffled = rng.permutation(returns)
        nulls.append(float(np.mean(shuffled)))
    return _result(
        statistic=statistic,
        null_samples=np.array(nulls),
        n_trials=n_trials,
        n_permutations_used=int(n_permutations),
    )


# TODO: Replace control_degenerate (as the active candidate) with real methods.
#       Each candidate should:
#         - Have a unique @register_candidate("name") decorator.
#         - Accept the same kwargs as control_degenerate.
#         - Return a DiagnosticResult via _result(...).
#         - Use a non-trivial test statistic (one that is NOT trivially
#           invariant under whatever resampling scheme it uses, e.g. don't
#           pair mean+std with permutation).
#
# Skeleton:
#
# @register_candidate("my_method")
# def _my_method(
#     returns: np.ndarray,
#     *,
#     candidate_returns: np.ndarray | None = None,
#     n_trials: int = 1,
#     n_permutations: int = 300,
#     seed: int = 20260506,
#     block_size: int | None = None,
# ) -> DiagnosticResult:
#     rng = np.random.default_rng(seed)
#     # 1. Compute observed statistic from returns / candidate_returns.
#     statistic = ...  # TODO
#     # 2. Build null distribution under your null hypothesis.
#     nulls: list[float] = []
#     for _ in range(int(n_permutations)):
#         resampled = ...  # TODO: bootstrap, sign-flip, block-permute, etc.
#         nulls.append(float(...))  # TODO: statistic on resampled data
#     return _result(
#         statistic=statistic,
#         null_samples=np.array(nulls),
#         n_trials=n_trials,
#         n_permutations_used=int(n_permutations),
#     )


# --- Public entrypoint (generic — leave alone) -------------------------------


def evaluate_candidate(
    returns: np.ndarray,
    *,
    candidate_returns: np.ndarray | None = None,
    n_trials: int = 1,
    n_permutations: int = 300,
    seed: int = 20260506,
    block_size: int | None = None,
    candidate_name: str | None = None,
) -> DiagnosticResult:
    """Dispatch to the named candidate (or CURRENT_CANDIDATE if None).

    benchmark.py calls this for every Case. Keep the signature stable across
    iterations — add new candidates by registering them, not by changing this
    function.
    """
    name = candidate_name or CURRENT_CANDIDATE
    if name not in CANDIDATES:
        raise KeyError(f"Unknown candidate {name!r}. Available: {sorted(CANDIDATES.keys())}")
    return CANDIDATES[name](
        returns,
        candidate_returns=candidate_returns,
        n_trials=n_trials,
        n_permutations=n_permutations,
        seed=seed,
        block_size=block_size,
    )
