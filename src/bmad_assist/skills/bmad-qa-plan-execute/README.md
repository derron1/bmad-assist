# bmad-qa-plan-execute (bundled bmad-assist skill source)

Bundled source for the `bmad-qa-plan-execute` skill — bmad-assist's
own E2E test runner (no upstream BMAD equivalent). Phase 3.5 authored
the SKILL.md outcome-based so the skill-layout compiler can drive it.

## Files

- `SKILL.md` — outcome-based skill definition (frontmatter + body).
- `customize.toml` — default workflow customization.
- `result-template.yaml` — YAML shape for the per-run results file.
- `summary-template.md` — Markdown shape for the human-readable summary.

## Provenance

bmad-assist authored — no upstream BMAD source. Refresh the templates
from `src/bmad_assist/workflows/qa-plan-execute/` if the legacy
shapes ever change.

## Regenerating

There is no upstream sync for this skill. When you update SKILL.md,
also update the legacy patch under
`.bmad-assist/patches/qa-plan-execute.patch.yaml` if the patch's
`must_contain` / `must_not_contain` rules need to follow the rewrite.
Project-specific overrides belong in `_bmad/custom/bmad-qa-plan-execute.toml`
(team) or `bmad-qa-plan-execute.user.toml` (personal).
