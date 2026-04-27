# bmad-testarch-nfr (bundled BMAD v6.4 TEA skill source)

This directory holds the bundled copy of the upstream `bmad-testarch-nfr`
skill from the BMAD Test Engineering Architect (TEA) module. It ships
with `bmad-assist` as the default source for the v6.4+ "skill" layout
compiler path. The compiler prefers a project-local copy under
`.claude/skills/bmad-testarch-nfr/` (or
`.agents/skills/bmad-testarch-nfr/`) when one exists; this bundled
copy is the fallback when no project install is present.

## Naming note (Phase 3.2-B rename)

The legacy bmad-assist workflow was named `testarch-nfr-assess`. The
v6.4+ canonical skill drops the `-assess` suffix and is published as
`bmad-testarch-nfr` (this directory). The patch file at
`.bmad-assist/patches/testarch-nfr-assess.patch.yaml` still uses the
legacy name; the routing alias in
`compiler/core.py::_SKILL_LAYOUT_COMPILERS` maps both
`testarch-nfr-assess` and `bmad-testarch-nfr` to the canonical
`bmad-testarch-nfr` compiler.

## Files

- `SKILL.md` — outcome-based skill definition (frontmatter + body).
- `customize.toml` — default workflow customization (activation steps,
  persistent facts, on-complete hook).
- `checklist.md` — NFR assessment validation checklist, referenced
  from `SKILL.md`.
- `nfr-report-template.md` — output template for the NFR assessment
  report.
- `steps-c/`, `steps-v/`, `steps-e/` — tri-modal micro-step files
  (Create / Validate / Edit) loaded just-in-time during workflow
  execution. NFR ships subagent steps for security, performance,
  reliability, and scalability.
- `resources/knowledge/*.md` — TEA knowledge fragments (NFR criteria,
  evidence patterns, scoring rubric) referenced by the step files.
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
(`SKILL.md`, `customize.toml`, `checklist.md`, `nfr-report-template.md`,
`steps-c/*.md`, `steps-v/*.md`, `steps-e/*.md`,
`resources/knowledge/*.md`, `resources/tea-index.csv`) verbatim from
the new `.claude/skills/bmad-testarch-nfr/` mirror. Do not hand-edit
them here; project-specific overrides belong in
`_bmad/custom/bmad-testarch-nfr.toml` (team) or
`bmad-testarch-nfr.user.toml` (personal).
