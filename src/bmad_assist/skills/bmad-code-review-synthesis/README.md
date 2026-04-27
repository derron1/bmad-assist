# bmad-code-review-synthesis (bundled bmad-assist skill source)

Bundled source for the `bmad-code-review-synthesis` skill —
bmad-assist's own multi-LLM code-review synthesis aggregator (no
upstream BMAD equivalent). Phase 3.5 authored the SKILL.md
outcome-based so the skill-layout compiler can drive it.

## Files

- `SKILL.md` — outcome-based skill definition (frontmatter + body).
- `customize.toml` — default workflow customization.

This is an action workflow with no template output and (as of v0.5.1)
no patch — the SKILL.md body is final once SKILL.md substitution
finishes. The legacy `CodeReviewSynthesisCompiler` injects the
anonymized reviewer outputs as virtual `[Reviewer X]` files, the git
diff as `[git-diff]`, source files as their real paths, and optional
Deep Verify / Security / TEA findings as additional virtual files.
That shape is preserved by delegating to the legacy compiler from
the new path.

## Provenance

bmad-assist authored — no upstream BMAD source.

## Regenerating

There is no upstream sync for this skill. When you update SKILL.md,
verify the contract / metrics / output-format blocks remain
byte-compatible with the resolution-extraction logic in the loop
runtime. Project-specific overrides belong in
`_bmad/custom/bmad-code-review-synthesis.toml` (team) or
`bmad-code-review-synthesis.user.toml` (personal).
