# bmad-code-review (bundled BMAD v6.4 skill source)

This directory holds the bundled copy of the upstream `bmad-code-review`
skill. It ships with `bmad-assist` as the default source for the
v6.4+ "skill" layout compiler path. The compiler prefers a
project-local copy under `.claude/skills/bmad-code-review/` (or
`.agents/skills/bmad-code-review/`) when one exists; this bundled
copy is the fallback when no project install is present.

## Files

- `SKILL.md` — outcome-based skill definition (frontmatter + body).
- `customize.toml` — default workflow customization (activation steps,
  persistent facts, on-complete hook).
- `checklist.md` — code-review validation checklist, referenced from
  `SKILL.md`.
- `steps/` — micro-step files (gather-context, review, triage,
  present) loaded just-in-time during workflow execution.

code-review is an action workflow (no template output), so there is
intentionally no `template.md` here. If a future upstream release
adds one, copy it verbatim alongside the other files.

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
(`SKILL.md`, `customize.toml`, `steps/*.md`) verbatim from the new
`.claude/skills/bmad-code-review/` mirror. Do not hand-edit them
here; project-specific overrides belong in
`_bmad/custom/bmad-code-review.toml` (team) or
`bmad-code-review.user.toml` (personal).
