# Known Issues

Tracked issues that have not yet been resolved. Filed here when GitHub issue tracking is unavailable or for lightweight local tracking.

---

## BOOT-001 — Bootstrap stamp poisoning silently freezes stale customize.toml (blocks bundled-skill updates)

**Status**: open
**Filed**: 2026-05-28
**Component**: [src/bmad_assist/core/project_setup.py](../src/bmad_assist/core/project_setup.py) (`bootstrap_new_layout`, `_copy_skill_tree`, `_write_bundle_version`)

### Summary

Bootstrap can write a `.bundle-version` stamp that records the **current** bundled `customize.toml` hash while the **installed** `customize.toml` content is stale (never copied). The stamp then falsely certifies stale content as current-bundled. On every subsequent run the stamp-is-current fast-path fires and skips the skill, so the stale file is frozen forever. Net effect: changes to a bundled `customize.toml` silently never reach affected consumer projects.

### Observed in

serenityv2 run `run-20260528T200311Z`. This session added a `HEADLESS MULTI-LLM RETROSPECTIVE` directive to the bundled `bmad-retrospective/customize.toml` (commit `d1adbd3`) and headless directives to 8 testarch skills (commit `f6d5b17` / C.1). None reached serenityv2:

| | value |
|---|---|
| Current bundled hash | `89aa3cc39688` (87-line file, has directive) |
| Installed content hash (`.claude/.../customize.toml`) | `0b032c342129` (41-line file, `activation_steps_append = []`) |
| `.bundle-version` stamp records | `89aa3cc39688` ← the **current bundled** hash, not the installed content's |
| customize.toml mtime | May 1 (untouched) |
| .bundle-version mtime | May 28 21:03 (rewritten this run) |

Bootstrap log for all 18 skills: `Preserving modified customize.toml at .../serenityv2/.claude/skills/<skill>/customize.toml; bundled defaults were not overwritten`.

### Root cause — two poisoning vectors

Both write `current_customize_hash` to the stamp when the file on disk is not actually the current bundled content:

1. **Legacy-stamp upgrade** ([project_setup.py:408-417](../src/bmad_assist/core/project_setup.py#L408-L417)): when an old one-line stamp has `customize_toml_hash: None` and `version == current`, bootstrap calls `_write_bundle_version(dst_dir, current_version, current_customize_hash)` **without copying the file or verifying the on-disk content matches**. If the legacy install's content is stale, the stamp is now poisoned.
2. **Refresh-after-preserve** ([project_setup.py:428-434](../src/bmad_assist/core/project_setup.py#L428-L434)): `_copy_skill_tree` hits its "Preserving modified" branch ([line 288-294](../src/bmad_assist/core/project_setup.py#L288-L294), `continue` without copying), then the caller unconditionally stamps `current_customize_hash` anyway.

The invariant that should hold — *the stamp's `customize_toml_hash` equals the bundled default the on-disk file was last synced from* — is violated. Once `stamp == current` but `content != current`, the fast-path at [project_setup.py:419-422](../src/bmad_assist/core/project_setup.py#L419-L422) (`stamp_is_current` trusts the stamp, never hashes on-disk content) skips the skill permanently.

### Blast radius

Every consumer project bootstrapped by an older bmad-assist that used the legacy one-line stamp format. For those, **any** future bundled `customize.toml` change silently fails to propagate. Not specific to serenityv2 or to this session's directives.

### Recovery (for already-poisoned installs)

A normal `bmad-assist run` will NOT fix a poisoned install (the fast-path skips it). Options:

1. `bmad-assist init --reset-skills-force` — sets `preserve_customizations=False`, overwrites ALL customize.toml in both mirrors. Safe when the project keeps genuine overrides in `_bmad/custom/<skill>.toml` (the documented override location) rather than in `.claude/skills/`.
2. Manually delete the stale installed `customize.toml` (both `.claude/skills/` and `.agents/skills/` mirrors); bootstrap then takes the fresh-copy branch.

### Suggested fix

Make the stamp truthful: never write `current_customize_hash` unless the customize.toml was actually copied to the current bundled content. Concretely:

- Have `_copy_skill_tree` report whether it copied vs preserved the customize.toml (or compute the on-disk hash after the copy/preserve decision).
- Stamp the **on-disk content hash**, not `current_customize_hash`. When copied → on-disk == current → stamp current. When preserved → stamp the actual (stale/user) content's basis.
- Fix the legacy-upgrade path ([line 414](../src/bmad_assist/core/project_setup.py#L414)) the same way: stamp the on-disk hash, not `current`. This lets legacy installs **self-heal** on the next run (stamp ≠ current → refresh → `_copy_skill_tree` sees on-disk == prior stamp → copies the new bundled).
- Already-poisoned installs (stamp already == current, content stale) cannot self-heal — they need the one-time force-refresh above. Optionally add a content-hash verification to the `stamp_is_current` fast-path to detect desync, but it can't safely auto-overwrite without risking genuine user edits.

This is the deeper form of the handoff's **A.1** item ("bootstrap content-aware refresh… worth confirming behavior on every code path").

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

---

## RETRO-001 — Retrospective marker mismatch silently saves raw dialogue as the "report"

**Status**: resolved in code; pending live-run validation
**Filed**: 2026-05-16
**Resolved**: 2026-05-28 by commit `d1adbd3`
**Component**: [src/bmad_assist/skills/bmad-retrospective/](../src/bmad_assist/skills/bmad-retrospective/), [src/bmad_assist/core/extraction.py](../src/bmad_assist/core/extraction.py), [src/bmad_assist/core/loop/handlers/retrospective.py](../src/bmad_assist/core/loop/handlers/retrospective.py)

### Resolution

Two-part fix landed in commit `d1adbd3`:

1. **Activation guardrail strengthened** — [SKILL.md:76](../src/bmad_assist/skills/bmad-retrospective/SKILL.md#L76) now requires explicit confirmation that `activation_steps_prepend` and `activation_steps_append` were executed in order before the main workflow begins. Cherry-picked from upstream BMAD-METHOD PR [#2398](https://github.com/bmad-code-org/BMAD-METHOD/pull/2398) (v6.8.0), which applied the same prose to 23+ skills after diagnosing the same fragility class.
2. **Headless directive + marker enforcement** — [customize.toml `activation_steps_append`](../src/bmad_assist/skills/bmad-retrospective/customize.toml) now instructs the LLM to skip party-mode facilitation, synthesize the retrospective directly from embedded artifacts, and wrap the final report in the literal `<!-- RETROSPECTIVE_REPORT_START -->` / `<!-- RETROSPECTIVE_REPORT_END -->` strings the extractor requires. Pattern matches [src/bmad_assist/skills/bmad-validate-story/customize.toml](../src/bmad_assist/skills/bmad-validate-story/customize.toml) and the 8 testarch directives landed in commit `f6d5b17`.

Snapshot regenerated; 354 skill_layout + 12058 impact-suite tests green.

Validation outstanding: a live algo / serenityv2 run that reaches the retrospective phase, with the saved report wrapped in the markers and `Markers not found for retrospective report` log line absent. Reopen this entry if either symptom recurs.

### Subtle gotcha (worth knowing for future customize.toml directives)

The directive cannot contain literal upstream interactive-element XML syntax (e.g., the opening tag for the upstream ask element). The retrospective patch at [.bmad-assist/patches/retrospective.patch.yaml:43](../.bmad-assist/patches/retrospective.patch.yaml#L43) has a non-greedy regex stripper for ask blocks. If the directive uses the literal opening tag, the regex spans from the directive's occurrence to the SKILL.md's closing tag at line 201, consuming `<workflow>` and several `<step>` blocks in between. The directive refers to "interactive ask elements" instead. Same trap would catch any customize.toml directive that echoes upstream XML tags which have an active regex stripper — worth a generalized note in [docs/workflow-patches.md](workflow-patches.md) eventually.

### Original report (preserved for historical context)

### Summary

The retrospective extractor expects `<!-- RETROSPECTIVE_REPORT_START -->` / `<!-- RETROSPECTIVE_REPORT_END -->` markers around the final report. The compiled prompt only contains the vague directive "Generate retrospective report with extraction markers" — it never instructs the LLM to wrap output in the literal marker strings the extractor greps for. When the LLM produces party-mode dialogue or pauses mid-workflow, no markers and no fallback patterns match; the extractor saves the raw response body as the "report" and the phase reports `success=True`.

### Extractor contract

[src/bmad_assist/core/extraction.py:93-105](../src/bmad_assist/core/extraction.py#L93-L105):

- Primary markers: `<!-- RETROSPECTIVE_REPORT_START -->` / `<!-- RETROSPECTIVE_REPORT_END -->`
- Fallback patterns (tried in order):
  1. `^#\s*Epic\s+\d+\s+Retrospective`
  2. `^[═✅]+\s*RETROSPECTIVE\s+COMPLETE`
  3. `^#\s*Retrospective`
- Stage 3 fallback: return raw content if no markers and no fallback headers match.

### Observed in

Run `run-20260514T203700Z-78a12463` (algo project, epic 4, story 4-13). Log evidence:

```
INFO     Invoking claude-subprocess provider with model=sonnet, timeout=600
INFO     Claude CLI completed: duration=167118ms, exit_code=0
DEBUG    Markers not found for retrospective report, trying fallback patterns
WARNING  Could not extract structured retrospective report, using raw content (3756 chars)
INFO     Saved retrospective report: epic-4-retro-20260514.md
INFO     Phase retrospective completed: success=True
```

The saved 3756-char body contained party-mode dialogue between "Bob (Scrum Master)" and the user, paused at Step 7 of 12 of the bmad-retrospective skill's workflow waiting for user input that never came. No markers were emitted because the LLM never reached Step 11 (save report).

### Root cause

Two compounding problems:

1. **Prompt-extractor mismatch**: the skill's compiled prompt does not contain the literal marker strings the extractor expects. The directive "with extraction markers" is too vague for the LLM to consistently emit the exact comment syntax.
2. **No empty-output detection**: the handler accepts any non-empty response as a successful report. A mid-workflow dialogue trace passes that check trivially.

This is the same fragility pattern that validation hit before commit `d1c930b` (2026-04-29), which (a) stripped the "On Activation" preamble from the compiled validator prompt and (b) added hard failure on empty validator output. The retrospective handler has no equivalent fix.

### Suggested fix

1. **Bundle a marker-emit directive** into [src/bmad_assist/skills/bmad-retrospective/customize.toml](../src/bmad_assist/skills/bmad-retrospective/customize.toml) `activation_steps_append` instructing the LLM to wrap the final report in the literal `<!-- RETROSPECTIVE_REPORT_START -->` / `<!-- RETROSPECTIVE_REPORT_END -->` strings. Mirror the shape used by the testarch directives landed in commit `f6d5b17` (C.1) and validate-story's existing override.
2. **Strip the interactive preamble** from the compiled retrospective prompt the same way validation does, so the LLM cannot enter party-mode/dialogue mode in headless runs.
3. **Detect mid-workflow output** in the retrospective handler and fail with a structured `PhaseResult.fail` when no markers AND no fallback patterns match — rather than silently saving raw content as the report.

### Adjacent issue (out of scope for RETRO-001, worth flagging separately)

Prompt budget overrun: the compiled retrospective prompt was **305k tokens / 1.22 MB** at runtime versus a configured budget of 30 k tokens — a 10× overage. Logged as `WARNING Prompt may exceed budget for retrospective`. Likely lives in the budget-tracing layer; relates to the active branch's `feat/budget-tracing-resilience` work.
