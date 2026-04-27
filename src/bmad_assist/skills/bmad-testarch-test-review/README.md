# bmad-testarch-test-review (bundled BMAD v6.4 TEA skill source)

This directory holds the bundled copy of the upstream
`bmad-testarch-test-review` skill from the BMAD Test Engineering
Architect (TEA) module. It ships with `bmad-assist` as the default
source for the v6.4+ "skill" layout compiler path. The compiler
prefers a project-local copy under
`.claude/skills/bmad-testarch-test-review/` (or
`.agents/skills/bmad-testarch-test-review/`) when one exists; this
bundled copy is the fallback when no project install is present.

## Files

- `SKILL.md` — outcome-based skill definition (frontmatter + body).
- `customize.toml` — default workflow customization (activation steps,
  persistent facts, on-complete hook).
- `checklist.md` — test-review validation checklist, referenced from
  `SKILL.md`.
- `test-review-template.md` — output template for the test-review
  report.
- `steps-c/`, `steps-v/`, `steps-e/` — tri-modal micro-step files
  (Create / Validate / Edit) loaded just-in-time during workflow
  execution. Test-review ships subagent steps for determinism,
  isolation, maintainability, and performance.
- `resources/knowledge/*.md` — TEA knowledge fragments (anti-patterns,
  flake-prevention, deterministic-fixtures) referenced by the step
  files.
- `resources/tea-index.csv` — knowledge-fragment index used by the TEA
  loader.

## Provenance

Sourced from the BMAD v6.4 install in this repository. Refresh from
`_bmad/_config/manifest.yaml` when bumping versions.

| Module | Version | Source   |
|--------|---------|----------|
| tea    | v1.15.1 | external |

## Regenerating

When syncing to a new upstream BMAD release, copy the upstream files
(`SKILL.md`, `customize.toml`, `checklist.md`,
`test-review-template.md`, `steps-c/*.md`, `steps-v/*.md`,
`steps-e/*.md`, `resources/knowledge/*.md`, `resources/tea-index.csv`)
verbatim from the new `.claude/skills/bmad-testarch-test-review/`
mirror. Do not hand-edit them here; project-specific overrides belong
in `_bmad/custom/bmad-testarch-test-review.toml` (team) or
`bmad-testarch-test-review.user.toml` (personal).
