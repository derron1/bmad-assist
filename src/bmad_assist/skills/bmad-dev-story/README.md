# bmad-dev-story (bundled BMAD v6.4 skill source)

This directory holds the bundled copy of the upstream `bmad-dev-story`
skill. It ships with `bmad-assist` as the default source for the
v6.4+ "skill" layout compiler path. The compiler prefers a
project-local copy under `.claude/skills/bmad-dev-story/` (or
`.agents/skills/bmad-dev-story/`) when one exists; this bundled
copy is the fallback when no project install is present.

## Files

- `SKILL.md` — outcome-based skill definition (frontmatter + body).
- `customize.toml` — default workflow customization (activation steps,
  persistent facts, on-complete hook).
- `checklist.md` — definition-of-done checklist, referenced from
  `SKILL.md`.

dev-story is an action workflow (no template output), so there is
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
(`SKILL.md`, `customize.toml`, `checklist.md`) verbatim from the new
`.claude/skills/bmad-dev-story/` mirror. Do not hand-edit them
here; project-specific overrides belong in
`_bmad/custom/bmad-dev-story.toml` (team) or
`bmad-dev-story.user.toml` (personal).
