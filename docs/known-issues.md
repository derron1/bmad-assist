# Known Issues

Tracked issues that have not yet been resolved. Filed here when GitHub issue tracking is unavailable or for lightweight local tracking.

---

## QA-001 — `qa_plan_generate` accepts stub plans, never verifies parser sees >0 tests

**Status**: open
**Filed**: 2026-05-16
**Component**: `src/bmad_assist/core/loop/handlers/qa_plan_generate.py`, `src/bmad_assist/qa/`

### Summary

The `qa_plan_generate` handler only checks `path.exists()` before reporting "QA plan already exists, skipping". When an earlier run produced a redirect/stub at the canonical path that parses to **0 tests**, every subsequent run skips regeneration. Downstream `qa_plan_execute` then embeds the empty stub into its prompt, the LLM works around it by reading a non-canonical file from disk, and the orchestrator reports a misleading pass rate.

### Canonical path (per bmad-assist)

`{project-root}/_bmad-output/qa-artifacts/test-plans/epic-{n}-e2e-plan.md`

Declared consistently across:

- [src/bmad_assist/skills/bmad-qa-plan-generate/SKILL.md:67](../src/bmad_assist/skills/bmad-qa-plan-generate/SKILL.md#L67) — where the LLM is told to write
- [src/bmad_assist/skills/bmad-qa-plan-execute/SKILL.md:68](../src/bmad_assist/skills/bmad-qa-plan-execute/SKILL.md#L68) — where the LLM is told to read
- [src/bmad_assist/qa/checker.py:59](../src/bmad_assist/qa/checker.py#L59) — `get_qa_plan_path()`

Upstream BMAD-METHOD v6.6.0 has no `qa-plan-generate` / `qa-plan-execute` / testarch skills — this convention is owned entirely by bmad-assist.

### Observed in

Run `run-20260514T203700Z-78a12463` (algo project, epic 4, story 4-13).

Two files present on disk at the time of the run:

- `_bmad-output/qa-artifacts/test-plans/epic-4-e2e-plan.md` — 612-byte redirect stub, 0 tests parsed
- `_bmad-output/qa/test-plans/epic-4-e2e-test-plan.md` — 43 KB, 24 Cat A tests (referenced by no code path or skill)

Log evidence:

```
[QA PLAN GENERATE] Epic 4
INFO     QA plan already exists for epic 4, skipping

[QA PLAN EXECUTE] Epic 4
INFO     Embedded test plan: epic-4-e2e-plan.md (2586 bytes)
DEBUG    Found 0 tests in checklist
INFO     Parsed 0 tests: A=0, B=0, C=0

# inside the executor LLM session:
[ASSISTANT] I need to flag a discrepancy in the workflow input. The `[test_plan]` block embedded in CONTEXT only...
[TOOL Read] .../qa/test-plans/epic-4-e2e-test-plan.md
[ASSISTANT] Full plan loaded — 24 Category A tests parsed. Starting execution

# end of run:
INFO     QA execution complete: 2/96 passed (2.1%)
```

Only 24 tests actually ran. The `96` count comes from category-summation accounting that doesn't reflect what executed.

### Root cause

`qa_plan_generate` skip-branch trusts `path.exists()` alone. A 612-byte stub satisfies that check.

### Suggested fix

In `src/bmad_assist/core/loop/handlers/qa_plan_generate.py`:

1. **Skip branch**: before skipping on `path.exists()`, run the existing checklist parser (`qa.parser.parse_checklist()` or equivalent) on the file. If `len(tests) == 0`, treat as missing and regenerate.
2. **Post-LLM validation**: after `qa_plan_generate` invokes the LLM, parse the just-written file. If `len(tests) == 0`, fail the phase with a structured `PhaseResult.fail` (matches the pattern recently introduced for provider errors at commit `71c10fe`).

`qa_plan_execute` should also surface a clearer error when the embedded plan parses to 0 tests rather than letting the LLM silently route around it.

### Stretch: pass-rate accounting

The `2/96 passed` figure should reflect what actually ran (`2/24`). Likely lives in [src/bmad_assist/qa/executor.py](../src/bmad_assist/qa/executor.py) or [src/bmad_assist/qa/summary.py](../src/bmad_assist/qa/summary.py) — accounting appears to read from the (broken) embedded plan's category counts rather than the executor's actual result set.
