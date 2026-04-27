# bmad-qa-plan-generate (bundled bmad-assist skill source)

Bundled source for the `bmad-qa-plan-generate` skill — bmad-assist's
own QA plan generator (no upstream BMAD equivalent). Phase 3.5
authored the SKILL.md outcome-based so the skill-layout compiler
can drive it.

## Files

- `SKILL.md` — outcome-based skill definition (frontmatter + body).
- `customize.toml` — default workflow customization.
- `template.md` — output template the agent fills in (Master Checklist,
  Cat A/B/C subsections, Traceability Matrix).

## Provenance

bmad-assist authored — no upstream BMAD source. Refresh the
`template.md` from `src/bmad_assist/workflows/qa-plan-generate/template.md`
if the legacy template ever changes.

## Regenerating

There is no upstream sync for this skill. When you update SKILL.md,
also update the legacy patch under
`.bmad-assist/patches/qa-plan-generate.patch.yaml` if the patch's
`must_contain` / `must_not_contain` rules need to follow the rewrite.
Project-specific overrides belong in `_bmad/custom/bmad-qa-plan-generate.toml`
(team) or `bmad-qa-plan-generate.user.toml` (personal).
