# bmad-validate-story-synthesis (bundled bmad-assist skill source)

Bundled source for the `bmad-validate-story-synthesis` skill —
bmad-assist's own multi-LLM synthesis aggregator (no upstream BMAD
equivalent). Phase 3.5 authored the SKILL.md outcome-based so the
skill-layout compiler can drive it.

## Files

- `SKILL.md` — outcome-based skill definition (frontmatter + body).
- `customize.toml` — default workflow customization.

This is an action workflow with no template output and (as of v0.5.1)
no patch — the SKILL.md body is final once SKILL.md substitution
finishes. The legacy `ValidateStorySynthesisCompiler` injects the
anonymized validator outputs as virtual `[Validator X]` files in the
context section; that shape is preserved by delegating to the legacy
compiler from the new path.

## Provenance

bmad-assist authored — no upstream BMAD source.

## Regenerating

There is no upstream sync for this skill. When you update SKILL.md,
verify the contract / metrics / output-format blocks remain
byte-compatible with `validation/synthesis_parser.py`. Project-specific
overrides belong in `_bmad/custom/bmad-validate-story-synthesis.toml`
(team) or `bmad-validate-story-synthesis.user.toml` (personal).
