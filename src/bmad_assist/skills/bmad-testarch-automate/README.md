# bmad-testarch-automate (bundled BMAD v6.4 TEA skill source)

This directory holds the bundled copy of the upstream
`bmad-testarch-automate` skill from the BMAD Test Engineering
Architect (TEA) module. It ships with `bmad-assist` as the default
source for the v6.4+ "skill" layout compiler path. The compiler
prefers a project-local copy under
`.claude/skills/bmad-testarch-automate/` (or
`.agents/skills/bmad-testarch-automate/`) when one exists; this
bundled copy is the fallback when no project install is present.

## Files

- `SKILL.md` — outcome-based skill definition (frontmatter + body).
- `customize.toml` — default workflow customization (activation steps,
  persistent facts, on-complete hook).
- `checklist.md` — automation validation checklist, referenced from
  `SKILL.md`.
- `steps-c/`, `steps-v/`, `steps-e/` — tri-modal micro-step files
  (Create / Validate / Edit) loaded just-in-time during workflow
  execution.
- `resources/knowledge/*.md` — TEA knowledge fragments referenced by
  the step files.
- `resources/tea-index.csv` — knowledge-fragment index used by the TEA
  loader.

automate is an action workflow (no separate template output) so there
is intentionally no `*-template.md` here. If a future upstream release
adds one, copy it verbatim alongside the other files.

## Provenance

Sourced from the BMAD v6.4 install in this repository. Refresh from
`_bmad/_config/manifest.yaml` when bumping versions.

| Module | Version | Source   |
|--------|---------|----------|
| tea    | v1.15.1 | external |

## Regenerating

When syncing to a new upstream BMAD release, copy the upstream files
(`SKILL.md`, `customize.toml`, `checklist.md`, `steps-c/*.md`,
`steps-v/*.md`, `steps-e/*.md`, `resources/knowledge/*.md`,
`resources/tea-index.csv`) verbatim from the new
`.claude/skills/bmad-testarch-automate/` mirror. Do not hand-edit them
here; project-specific overrides belong in
`_bmad/custom/bmad-testarch-automate.toml` (team) or
`bmad-testarch-automate.user.toml` (personal).
