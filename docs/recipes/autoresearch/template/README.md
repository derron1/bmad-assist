# Autoresearch harness — template

This is the bmad-assist autoresearch template. See
[../../autoresearch.md](../../autoresearch.md) for the recipe explanation.

## Contents

- `program.md` — research mission, candidate priorities, pass rule, iteration contract
- `experiment.py` — candidate implementations and the `evaluate_candidate()` entrypoint (the only editable file during iteration)
- `benchmark.py` — fixed benchmark, confusion-matrix scorer, scorecard writer
- `scorecard.json` / `scorecard.tsv` — generated, overwritten on every benchmark run
- This file

## Quickstart

```bash
# 1. Copy this template into your consumer project
cp -r <bmad-assist-repo>/docs/recipes/autoresearch/template/ \
      <consumer-project>/_bmad-output/planning-artifacts/research/<topic>_autoresearch/
cd <consumer-project>/_bmad-output/planning-artifacts/research/<topic>_autoresearch/

# 2. Smoke-test the harness with the deliberate-failure control
python benchmark.py --synthetic-only --samples-per-class 5 --permutations 20
# Expected: scorecard.passed == false (control_degenerate is meant to fail)

# 3. Fill in TODOs (program.md, experiment.py candidates, benchmark.py case generators)

# 4. Iterate — see program.md "Iteration contract"
python benchmark.py --synthetic-only --samples-per-class 30 --permutations 100
```

## Iteration tools

The harness is tool-agnostic. Any LLM-capable agent that can edit
`experiment.py`, run `python benchmark.py`, and parse `scorecard.json` can
drive the loop:

- codex `/goal` continuation
- claude-code `/bmad-quick-dev` (preferred for one-shot iteration sessions)
- claude-code interactive (paste `program.md` as context, ask "run a candidate iteration")
- Manual

Stop signal in all cases: `scorecard.passed == true`.

## Where the domain code goes

| File | What you fill in | What stays generic |
|---|---|---|
| `program.md` | Mission, candidate priorities, pass-rule numbers | Editable-surface doctrine, run-command shape, iteration contract |
| `experiment.py` | Test statistics, candidate functions, dispatcher entries | `DiagnosticResult` dataclass, `_result()` helper, `evaluate_candidate()` entrypoint signature |
| `benchmark.py` | `synthetic_cases()`, `real_data_cases()` (optional), domain-specific imports | `Case` dataclass, dynamic experiment loader, `confusion()` scorer, scorecard writer, argparse, `main()` exit code |

The recipe doc has a worked example showing roughly what the domain side looks
like for a specific case (Permutation-FST in the algo project). See
[autoresearch.md "Worked example"](../../autoresearch.md).
