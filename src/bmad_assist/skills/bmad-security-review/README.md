# bmad-security-review (bundled bmad-assist skill source)

Bundled source for the `bmad-security-review` skill — bmad-assist's own
CWE-based security vulnerability scanner. There is no upstream BMAD
equivalent. Phase 6 prep authored the SKILL.md outcome-based so the
skill-layout compiler can drive it.

## Files

- `SKILL.md` — outcome-based skill definition (frontmatter + body).
- `customize.toml` — default workflow customization (activation steps,
  persistent facts, on-complete hook).
- `patterns/` — bundled CWE pattern catalogue, copied byte-identical
  from the legacy workflow at `src/bmad_assist/workflows/security-review/patterns/`.
  At runtime the legacy `load_security_patterns()` loader still resolves
  patterns from the workflows directory; the copy here exists so the
  skill-source bundle is self-contained and Phase 6 can delete the
  legacy workflow tree without losing the catalogue.

## Provenance

bmad-assist authored — no upstream BMAD source. CWE pattern files were
authored under the legacy workflow and bundled here verbatim.

## Regenerating

There is no upstream sync for this skill. When you update the CWE
pattern files, update both `patterns/` here AND the legacy
`src/bmad_assist/workflows/security-review/patterns/` until Phase 6
removes the legacy tree, then this directory becomes the canonical
location and the loader's import path will be retargeted.
Project-specific overrides belong in
`_bmad/custom/bmad-security-review.toml` (team) or
`bmad-security-review.user.toml` (personal).
