# bmad-validate-story (bundled bmad-assist skill source)

This directory holds the bundled source for the `bmad-validate-story`
skill. Unlike `bmad-create-story` / `bmad-dev-story` / `bmad-retrospective`,
the upstream BMAD distribution does NOT ship this workflow — it is
bmad-assist's own adversarial story validator. Phase 3.5 authored
the SKILL.md outcome-based (in BMAD v6.4+ shape) so the skill-layout
compiler can drive it.

## Files

- `SKILL.md` — outcome-based skill definition (frontmatter + body).
- `customize.toml` — default workflow customization (activation steps,
  persistent facts, on-complete hook).
- `template.md` — output template the validator fills in (Evidence
  Score, INVEST table, categorized findings).

## Provenance

bmad-assist authored — no upstream BMAD source. Refresh the
`template.md` from `src/bmad_assist/workflows/validate-story/template.md`
if the legacy template ever changes.

## Regenerating

There is no upstream sync for this skill. When you update SKILL.md,
also update the legacy patch under `.bmad-assist/patches/validate-story.patch.yaml`
if the patch's `must_contain` / `must_not_contain` rules need to
follow the rewrite. Project-specific overrides belong in
`_bmad/custom/bmad-validate-story.toml` (team) or
`bmad-validate-story.user.toml` (personal).
