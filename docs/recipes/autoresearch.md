# Recipe: empirical research-authority loop ("autoresearch")

## What this is

A pattern for resolving **research-authority CRITICALs** that can't be fixed by a normal dev cycle — defects where the right answer requires *empirical validation of candidate methods against a benchmark*, not narrative literature review.

Inspired by [karpathy/autoresearch](https://github.com/karpathy/autoresearch). The shape:

- One editable file (`experiment.py`) containing candidate implementations.
- One frozen file (`benchmark.py`) running a fixed benchmark and writing a scorecard.
- A hard pass rule: the candidate must clear specific numeric thresholds; nothing else counts.
- An iteration loop driven by any LLM-capable tool (codex `/goal`, `/bmad-quick-dev`, claude-code interactive, manual). The harness is tool-agnostic.

This is a **manual recipe today**. When the deferred-research handler lands as part of bmad-assist's loop (Step 4 in the rework-resilience roadmap), it may scaffold a harness from this template automatically — but iteration will remain operator-driven for the foreseeable future, because the 74% of code that's domain-specific cannot be templated away.

## When to use this (and when not to)

**Use this recipe when** a code-review synthesis emits a `[Review][Defer]` CRITICAL that:

- Requires choosing a method (statistical test, algorithm, heuristic) where multiple candidates exist in the literature.
- Has a tractable benchmark — synthetic data + real-data replay can measure whether a candidate is correct.
- Cannot be unblocked by a literature survey alone (the synthesis already knows the candidates exist; the gap is *which one actually works on this data*).

Examples:
- "Permutation-FST is degenerate; needs an order-sensitive replacement statistic." (canonical — see worked example below)
- "Outlier-rejection threshold is arbitrary; need a calibrated method against labeled data."
- "Caching strategy is suboptimal; need a benchmark-driven choice between LRU/LFU/2Q/ARC."

**Don't use this recipe when** the research blocker is:
- A literature gap ("which methods exist?") — that's narrative research, use [bmad-technical-research](../../.claude/skills/bmad-technical-research/SKILL.md) instead.
- An architectural decision ("which integration pattern fits our system?") — that's design, not empirical validation.
- A specification ambiguity ("what should the AC actually mean?") — that's PM/PO work, escalate to humans.
- Anything where the right answer is provably not measurable on a finite benchmark.

The narrative and empirical modes pair naturally: **narrative research first** to enumerate candidates, **empirical research second** to validate which one passes. For the canonical case (Permutation-FST), the literature was already known; the gap was empirical.

## The architecture

Five files, fixed roles:

| File | Role | Editable? |
|---|---|---|
| `program.md` | Mission statement, candidate priorities, pass rule, run commands | Once at setup; rarely after |
| `experiment.py` | Candidate implementations, dispatcher, `evaluate_candidate()` entrypoint | **Yes — the only editable surface during iteration** |
| `benchmark.py` | Fixed benchmark cases (synthetic + real), confusion-matrix scorer, scorecard writer | Only if the benchmark itself is demonstrably wrong |
| `scorecard.json` | Latest results — overwritten on every benchmark run | Generated, never hand-edited |
| `README.md` | Orientation, run commands, iteration contract | Once at setup |

**The contract:**

1. `benchmark.py` dynamically imports `experiment.py` and calls `evaluate_candidate(returns, ..., candidate_name=...)` for each Case.
2. Each Case has a ground-truth label (e.g., `should_flag: bool`). The scorer compares `result.flagged` against that label.
3. Scorecard captures: pass-rule booleans, overall metrics (TP/FP/TN/FN, precision, F1), per-source metrics, calibration (`median_null_std`, `degenerate_rate`, `deterministic`, `runtime_seconds`), and per-case rows.
4. Benchmark exits 1 if the candidate fails the pass rule, 0 if it passes. This makes the harness CI-friendly.

## Pass rule: hardness is the point

A pass rule is a conjunction of numeric thresholds the candidate must clear simultaneously. Example from the canonical case:

```
f1 >= 0.833 AND tnr >= 0.333 AND median_null_std > 1e-8
              AND degenerate_rate <= 0.05 AND deterministic == true
              AND runtime_seconds <= practical_budget
```

Three properties of a good pass rule:

1. **Numeric and binary.** No "looks reasonable" judgment calls. Every gate is a comparison against a constant.
2. **Calibration gates, not just accuracy.** `median_null_std > 1e-8` and `degenerate_rate <= 0.05` are calibration; F1 and TNR are accuracy. A candidate that only optimizes accuracy can produce a degenerate null distribution and pass classification metrics while being *statistically meaningless* — both kinds of gate are needed.
3. **Determinism.** A candidate that flips its verdict between identical runs is unfit for production regardless of its average score. The benchmark should run each Case twice with the same seed and assert identical output.

If you find yourself wanting to relax the pass rule mid-iteration, **stop and audit the benchmark labels first**. Mislabeling is more often the issue than method weakness — see the worked example below.

## Iteration tools — pick whichever fits

The harness is deliberately tool-agnostic. Any LLM-capable agent that can read files, edit `experiment.py`, run a Python command, and parse JSON can drive the loop:

- **codex `/goal` continuation** — what the canonical case used. Each iteration is a fresh `/goal` invocation with a continuation prompt.
- **claude-code `/bmad-quick-dev`** — invoke with a task description; let the skill drive multiple file edits and benchmark runs in one session.
- **claude-code interactive** — paste the program.md content as system context, ask "run a candidate iteration."
- **Manual** — read `scorecard.json`, hand-edit `experiment.py`, run `benchmark.py`, repeat.

Termination signal in all cases: `scorecard.passed == true`. No iteration is "done" until that flips.

## Worked example: Permutation-FST in the algo project

The canonical case study lives at `<algo-project>/_bmad-output/planning-artifacts/research/autoresearch/permutation_fst_autoresearch/`.

**The defect:** `_annualized_sharpe(perm_returns)` is a function of `mean(returns)` and `std(returns)` only. Both are invariant under permutation, so a block-permuted sample of the same return values produces a null Sharpe identical (modulo floating-point) to the observed Sharpe. Result: `null_std ≈ 2.9e-16`, `p_value ≈ 1.0` always, the test flags 100% of strategies. The pass rule needed an order-sensitive statistic.

**The path through the loop:**

| Iteration | Change | Result |
|---|---|---|
| 1 (control) | Default candidate is the broken production code | `null_std ≈ 4.9e-16`, fails as designed (sanity check on harness) |
| 2 | Add `centered_block_bootstrap_sharpe` candidate | Better null calibration, fails accuracy |
| 3 | Add `white_reality_check_max_sharpe` (cohort max-stat bootstrap) | Non-degenerate null, fails TNR on consistent market controls |
| 4 | Hybrid: White-RC for searched cohorts + DSR fallback for single-strategy cases | F1=0.857, TNR=0.694 — passes |
| 5 | **Audit market_replay_consistent labels** (pivot — not formula) | Discovered most "consistent" controls had weak/negative Sharpe; relabeled into `market_no_edge` (should flag) and `market_genuine` (should not flag) |
| 6 | Re-run benchmark with corrected taxonomy | F1=0.899, TNR=0.829 — passes more cleanly |

**Two lessons from the actual run:**

1. The pivot at iteration 5 (label audit, not method tweaking) was the highest-leverage move. It would not have happened if the loop had been fully autonomous — a human noticing the per-source breakdown smelled wrong is what triggered it. **Plan for human checkpoints around the per-source metrics, not just the overall F1.**
2. The control candidate (deliberately broken production code) verifies the harness can detect failure. Always include one — if the harness ever passes the broken control, the benchmark is the bug.

The final research-authority note (which is what eventually authorizes a production migration) lives at `<algo-project>/_bmad-output/planning-artifacts/research/epic-4-test/phacking/PERMUTATION_FST_RESEARCH_AUTHORITY_WHITE_RC_DSR_HYBRID.md`. **The scorecard authorizes the *candidate*; the research-authority note authorizes the *production change*. They are separate artifacts.**

## Using the template (binary-classification flavor)

The template at [./autoresearch/template/](./autoresearch/template/) is *one instance* of the pattern — a minimal binary-classification harness, the proven shape from the canonical Permutation-FST case. The pattern itself (one editable surface, fixed pass rule, scorecard, iteration loop) is universal across domains; the binary-classification flavor is what fits "does this method correctly flag X / not flag Y?" research. For non-classification problems see the next section.

Copy and adapt:

```
cp -r <bmad-assist-repo>/docs/recipes/autoresearch/template/ \
      <consumer-project>/_bmad-output/planning-artifacts/research/<topic>_autoresearch/
cd <consumer-project>/_bmad-output/planning-artifacts/research/<topic>_autoresearch/
# Edit program.md: state your research question, list candidate methods, set pass rule
# Edit experiment.py: implement at least one real candidate (keep control_degenerate)
# Edit benchmark.py: replace synthetic_cases TODO with your domain's data generators;
#                    optionally add real_data_cases for replay against logged data
python benchmark.py --synthetic-only --samples-per-class 30 --permutations 100
```

The template is *almost* runnable out of the box — running it executes `control_degenerate` (a deliberate failure baseline) and writes a scorecard showing the pass rule fails. That's the smoke test the harness is wired correctly. Fill in domain code, swap `control_degenerate` for a real candidate, and run again.

## Adapting for other domains

The binary-classification template captures one instance of the pattern. The pattern itself adapts cleanly to other empirical-validation shapes — none require new template files; they're modifications to the existing files when you copy the template into your project.

**What stays the same in every flavor:**

- `program.md` structure (mission, editable surface, pass rule, run commands, iteration contract)
- `experiment.py` having a single `evaluate_candidate()` entrypoint and a `CANDIDATES` dispatcher
- `benchmark.py` having a frozen orchestrator + scorecard writer + CLI exit code 0/1
- The control-baseline pattern (a deliberate-failure candidate that catches harness bugs)
- The hard pass rule: numeric, binary, all gates must pass simultaneously, no relaxation mid-iteration

**What changes per flavor:**

### Regression / loss-tuning

Use when the deferred CRITICAL is "find a model / loss function / preprocessing pipeline that minimizes prediction error against held-out data."

| Element | What changes |
|---|---|
| `Case.should_flag: bool` | → `Case.targets: np.ndarray` (ground-truth values per input) |
| `confusion()` | → `regression_metrics()` returning `{rmse, mae, r2, mape}` |
| Null-distribution machinery | drop; the "statistic" is held-out RMSE itself |
| Pass rule | `rmse <= baseline_rmse * 0.95 AND r2 >= 0.5 AND deterministic AND runtime_seconds <= budget` |
| `_result()` calibration fields (`p_value`, `null_std`, `n_permutations_used`) | drop; replace `DiagnosticResult` with `RegressionResult{rmse, mae, r2, predictions, deterministic_check_passed}` |
| CLI flags | drop `--permutations` / `--block-size`; add `--folds` for cross-validation |

The control-baseline candidate becomes "predict the training mean" — guaranteed to fail any RMSE floor better than 1×baseline.

### Latency optimization

Use when the deferred CRITICAL is "find a caching strategy / index choice / query plan that meets a p99 latency budget under a measured workload."

| Element | What changes |
|---|---|
| `Case.returns: np.ndarray` | → `Case.workload: list[Request]` (replayable trace) |
| `Case.should_flag: bool` | → `Case.budget_ns: dict[str, int]` (per-percentile budget) |
| `confusion()` | → `latency_metrics()` returning `{p50, p95, p99, p999, throughput, total_runtime}` |
| `evaluate_candidate` body | runs the candidate against the workload, measures elapsed timings |
| Pass rule | `p99 <= budget["p99"] AND p999 <= budget["p999"] AND throughput >= floor AND deterministic` |
| `_result()` | replace with `LatencyResult{percentiles, sample_timings, deterministic_check_passed}` |

Determinism here means percentile stability across runs — wider tolerance than bit-for-bit equality.

### Other shapes (sketch only)

- **Cost / multi-metric Pareto.** `Case` adds `cost_model` and `workload`; pass rule becomes a Pareto-frontier check (`dominates_baseline_in_at_least_one_metric AND no_metric_worse`); `_result()` becomes a vector of metrics; scorecard's `metrics_all` becomes `pareto_frontier`.
- **Correctness / spec-conformance.** `Case` becomes `inputs + expected_outputs`; scorer becomes `correctness_metrics{spec_pass_rate, edge_case_coverage}`; pass rule is `spec_pass_rate == 1.0 AND edge_case_coverage >= 0.95`; control-baseline returns `None` — guaranteed 0% spec-pass.

These can use the same template structure; the diffs are localized to the Case shape, the scorer, and the `evaluate_candidate` body.

## Common pitfalls

**Label noise.** "Things I think are negative" is not the same as "things that are objectively negative under the test's null hypothesis." The canonical case lost two iterations on this: market controls labeled "consistent" were actually weak/negative-Sharpe, and the candidate correctly flagged them. Audit per-source TNR/TPR before tweaking the method.

**Flag-everything reward hacking.** A candidate that flags every input gets perfect TPR. If your pass rule has only TPR/F1, the optimizer can satisfy it with a trivial flag-everything implementation. **Always pair TPR with TNR** (or precision, or false-positive rate) and a calibration gate (`median_null_std`, `degenerate_rate`).

**Calibration vs accuracy.** A candidate can have F1=0.95 and a degenerate null distribution. Production trust depends on the null behaving correctly, not just classification accuracy. Hard-gate calibration metrics independently.

**Determinism drift.** Floating-point operations on shuffled data can produce equality-comparison surprises (e.g., `p_value == 1.0` when `null_std` is `4e-16`). Run each Case twice with the same seed and assert identical output. If a method can't pass determinism, it can't be in production.

**Benchmark is wrong.** Sometimes the candidate is right and the benchmark is wrong. Symptoms: a candidate that should obviously work (per literature) consistently fails; per-source breakdowns show patterns inconsistent with domain knowledge. Audit the benchmark before iterating further. **Edit `benchmark.py` only when you've proven the benchmark is the bug; otherwise leave it frozen.**

**Scope creep.** The harness is for *one* research question. Don't try to make `experiment.py` solve three different research-authority CRITICALs simultaneously. Spawn separate harness directories for separate questions.

## Integration with the bmad-assist loop (future Step 4)

The bmad-assist rework-resilience roadmap is:

- **Step 1** — synthesis prompt classifies findings with correct `[Review][...]` markers (committed: `12d5410`)
- **Step 2** — score-side filter excludes Defer findings from verdict (committed: `9358d38`)
- **Step 3** — LLM-emitted resolution honors Defer in `remaining_*` count (committed: `3c272e3`)
- **Step 4** — deferred-research handler (this section's subject; not yet implemented)

After Step 3, synthesis emits `resolution: resolved` even when deferred CRITICALs remain — the loop advances correctly, but those deferred items just accumulate in `deferred-work.md` with no follow-up action. Step 4 closes that gap.

### The deferred-research handler's contract

Runs as the last step of synthesis (or as a separate post-synthesis phase) when `deferred_critical + deferred_high > 0`. Approximate shape, ~300 LOC:

```text
1. Read deferred-work.md and the latest synthesis report.
2. For each [Review][Defer] item, heuristic-classify:
   - narrative-only            → literature gap; "what candidates exist?"
   - empirical                 → "which candidate works on this data?" (most common)
   - architectural             → design decision; handoff to PM/architect
   - out-of-scope-no-research  → log only, no follow-up artifact
3. For each empirical item:
   - cp -r <bmad-assist>/docs/recipes/autoresearch/template/ \
          {project-root}/_bmad-output/planning-artifacts/research/{topic}_autoresearch/
   - Append a follow-up entry to deferred-work.md with:
       - harness path
       - run command
       - the originating finding's body text (so program.md's TODOs have context)
4. For each narrative item: optionally invoke bmad-technical-research (if bundled)
   to produce a literature report. Otherwise scaffold a research-note stub and
   emit a follow-up.
5. For each architectural item: scaffold a decision doc and route to a human.
6. For out-of-scope items: log to deferred-work.md, no follow-up.
7. Append a "Follow-ups created" summary block to the synthesis report.
8. Return PhaseResult.success — the orchestrator advances to the next story.
```

### What the handler does NOT do

- **Does not drive iteration.** Scaffolding the harness is the handler's job; running the loop is the operator's. The recipe's "Iteration tools" section explains why — autonomous iteration would have missed the label-audit pivot that actually solved Permutation-FST. Human-in-the-loop is the feature, not a bug.
- **Does not bundle new skills.** Step 4 is a handler addition + scaffold logic; the recipe's template stays in the bmad-assist repo's `docs/recipes/` (this directory) and gets `cp -r`'d into consumer projects on demand.
- **Does not block story progress.** Synthesis already correctly emits `resolution: resolved`; Step 4 just adds follow-up artifacts. If Step 4 fails or is disabled, the loop still works — the deferred items just don't get auto-scaffolded.

### Why the template lives in `docs/recipes/` (not bundled-skill territory)

The template is documentation that gets `cp -r`'d into a consumer project on demand. It's deliberately NOT a bundled skill (`src/bmad_assist/skills/...`) for two reasons:

1. **Bundled skills are LLM prompts** with a `SKILL.md` activation contract. Autoresearch harness files are *executable code* the consumer runs; they don't activate via the skill resolver.
2. **The 74% domain-specific code** (per the pre-bundling investigation) means each consumer copy diverges immediately. Bundling implies "auto-sync from source-of-truth"; here we want frozen-at-copy semantics so iteration changes don't get clobbered by the next bootstrap.

The template's location at [./autoresearch/template/](./autoresearch/template/) is a deliberate choice — it lives where documentation lives, gets cross-referenced from CLAUDE.md, and is a one-shot scaffold not subject to bootstrap re-sync.

## See also

- Canonical worked example: `<algo-project>/_bmad-output/planning-artifacts/research/autoresearch/permutation_fst_autoresearch/`
- Source pattern: [karpathy/autoresearch](https://github.com/karpathy/autoresearch)
- Sibling skill (narrative research): [bmad-technical-research](../../.claude/skills/bmad-technical-research/SKILL.md)
- Defer-marker mechanics: [docs/workflow-patches.md](../workflow-patches.md), [bmad-code-review-synthesis SKILL.md](../../src/bmad_assist/skills/bmad-code-review-synthesis/SKILL.md)
