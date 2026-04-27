# bmad-retrospective (bundled BMAD v6.4 skill source)

This directory holds the bundled copy of the upstream `bmad-retrospective`
skill. It ships with `bmad-assist` as the default source for the
v6.4+ "skill" layout compiler path. The compiler prefers a
project-local copy under `.claude/skills/bmad-retrospective/` (or
`.agents/skills/bmad-retrospective/`) when one exists; this bundled
copy is the fallback when no project install is present.

## Files

- `SKILL.md` — outcome-based skill definition (frontmatter + body).
- `customize.toml` — default workflow customization (activation steps,
  persistent facts, on-complete hook).

retrospective is an action workflow (no template output) and ships
without a separate validation checklist; the patch-driven extraction
markers in step 11 enforce report shape. If a future upstream release
adds `template.md` or `checklist.md`, copy it verbatim alongside the
other files.

## Provenance

Sourced from the BMAD v6.4 install in this repository. Refresh from
`_bmad/_config/manifest.yaml` when bumping versions.

| Module       | Version | Source     |
|--------------|---------|------------|
| installation | 6.4.0   | built-in   |
| bmm          | 6.4.0   | built-in   |
| core         | 6.4.0   | built-in   |

## Regenerating

When syncing to a new upstream BMAD release, copy the upstream files
(`SKILL.md`, `customize.toml`) verbatim from the new
`.claude/skills/bmad-retrospective/` mirror. Do not hand-edit them
here; project-specific overrides belong in
`_bmad/custom/bmad-retrospective.toml` (team) or
`bmad-retrospective.user.toml` (personal).
