# bmad-create-story (bundled BMAD v6.4 skill source)

This directory holds the bundled copy of the upstream `bmad-create-story`
skill. It ships with `bmad-assist` as the default source for the
v6.4+ "skill" layout compiler path. The compiler prefers a
project-local copy under `.claude/skills/bmad-create-story/` (or
`.agents/skills/bmad-create-story/`) when one exists; this bundled
copy is the fallback when no project install is present.

## Files

- `SKILL.md` — outcome-based skill definition (frontmatter + body).
- `customize.toml` — default workflow customization (activation steps,
  persistent facts, on-complete hook).
- `template.md` — story output template, referenced from `SKILL.md`.
- `checklist.md` — story-quality checklist, referenced from `SKILL.md`.
- `discover-inputs.md` — input-file discovery protocol, referenced
  from `SKILL.md`.

## Provenance

Sourced from the BMAD v6.4 install in this repository. Refresh from
`_bmad/_config/manifest.yaml` when bumping versions.

| Module       | Version | Source     |
|--------------|---------|------------|
| installation | 6.4.0   | built-in   |
| bmm          | 6.4.0   | built-in   |
| core         | 6.4.0   | built-in   |

## Regenerating

When syncing to a new upstream BMAD release, copy the four upstream
files (`SKILL.md`, `customize.toml`, `template.md`, `checklist.md`,
`discover-inputs.md`) verbatim from the new
`.claude/skills/bmad-create-story/` mirror. Do not hand-edit them
here; project-specific overrides belong in
`_bmad/custom/bmad-create-story.toml` (team) or
`bmad-create-story.user.toml` (personal).
