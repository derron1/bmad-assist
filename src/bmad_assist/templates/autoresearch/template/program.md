# Research program: <!-- TODO: name your research question -->

> Template for the autoresearch recipe. Replace every `<!-- TODO -->` with the
> domain-specific content. Delete this blockquote when done.

## Mission

<!-- TODO: 2-3 sentences on what defect or methodology gap this research is meant to resolve.
     Reference the synthesis report and the production code path that needs replacement.
     Example from the canonical Permutation-FST case:
       Find a replacement for the degenerate Tier 2 Permutation-FST statistic in
       app/core/p_hacking/permutation_fst.py. The current implementation block-permutes
       a selected return vector and recomputes annualized Sharpe, which is order-invariant
       under permutation and produces a degenerate null distribution. Need an order- or
       selection-sensitive statistic that satisfies the pass rule below. -->

## Editable surface

**Only edit `experiment.py`.** Do not edit `benchmark.py` unless you have proven
the benchmark itself is wrong (and document the proof in this file before editing).
Do not edit production application code from inside this loop — that is a separate
follow-up step gated on the research-authority note.

## Public entrypoint contract

`experiment.py` must export:

```python
def evaluate_candidate(
    returns,                  # the input the candidate is testing
    candidate_returns=None,   # optional: full search cohort (for max-stat methods)
    n_trials=1,               # for multiple-testing correction
    n_permutations=300,       # bootstrap / permutation budget
    seed=20260506,            # determinism
    block_size=None,          # optional override for block resampling
    candidate_name=None,      # which CANDIDATES[name] to dispatch to
) -> DiagnosticResult:
    ...
```

Keep this signature stable across iterations so `benchmark.py` does not need
modification. Add new candidate functions; register them in `CANDIDATES`;
optionally change `CURRENT_CANDIDATE` to point at the new one.

## Candidate priorities

<!-- TODO: list the candidate methods you want the iteration loop to try, in
     priority order. Cite literature where it applies. Example:

     1. White Reality Check max-statistic bootstrap — addresses cohort selection
        across n_trials. (White 2000)
     2. Romano-Wolf stepdown studentized proxy — multiple-testing correction with
        better small-sample behavior. (Romano & Wolf 2005)
     3. Bailey/López de Prado deflated Sharpe ratio (DSR) — for single-strategy
        contexts where no search cohort exists. (Bailey & López de Prado 2014)
     4. Hybrid: White-RC for searched cohorts, DSR fallback for single-strategy.
-->

## Pass rule

A candidate passes only if **all** of the following hold simultaneously:

<!-- TODO: define your numeric pass rule. Replace these defaults with values
     appropriate for your problem. Avoid relaxing them mid-iteration; if you
     need to relax them, audit the benchmark labels first. -->

- `f1 >= 0.833`           — accuracy floor
- `tnr >= 0.333`           — pair with TPR to prevent flag-everything reward hacking
- `median_null_std > 1e-8` — calibration gate; null distribution must be non-degenerate
- `degenerate_rate <= 0.05` — calibration gate; ≤5% of cases may have a degenerate null
- `deterministic == true`  — same seed → same result, no exceptions
- `runtime_seconds <= 60`  — practical budget

If a candidate passes everything except calibration gates, it is still a
**fail**. A statistically meaningless test with high accuracy is unfit for production.

## Run commands

From the directory containing this `program.md`:

```bash
# Smoke (fast, synthetic-only)
python benchmark.py --synthetic-only --samples-per-class 30 --permutations 100

# Fuller replay (synthetic + real-data, slower)
python benchmark.py --permutations 300 --samples-per-class 40

# Specific candidate (overrides experiment.CURRENT_CANDIDATE)
python benchmark.py --candidate <name> --permutations 200
```

## Iteration contract

Each iteration:

1. Read `scorecard.json` to see which gate(s) failed.
2. Read the per-case rows in `scorecard.tsv` (or the `rows` block in
   `scorecard.json`) to understand which Cases are responsible.
3. Edit `experiment.py` — typically: add a new candidate function, register it
   in `CANDIDATES`, point `CURRENT_CANDIDATE` at it.
4. Re-run `benchmark.py`.
5. If `scorecard.passed == true`, stop. Write the research-authority note.
   Otherwise, continue.

**Stop conditions:**
- `scorecard.passed == true` — success.
- 5 iterations with no F1 improvement — likely benchmark or label issue, audit before continuing.
- Per-source TNR/TPR pattern looks domain-implausible — audit labels before further candidates.

## Out of scope

- Rewriting production code. The migration to production is a separate dev story
  gated on a human-reviewed research-authority note.
- Iterating across multiple research questions in one harness. Spawn a separate
  `<topic>_autoresearch/` directory per question.
- Tuning the benchmark's pass-rule thresholds to make a candidate pass.

## Notes / running log

<!-- TODO: keep a short append-only log of what you tried and why each candidate
     failed. Useful for the research-authority note at the end and for stopping
     the loop when iteration becomes unproductive. -->
