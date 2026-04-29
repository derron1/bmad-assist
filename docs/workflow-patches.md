# Workflow Patches

> **Migration note (0.6.0):** Phase 6 retired the legacy `workflow.yaml` + `instructions.xml` pipeline; Phase 7 inlined every workflow compiler under `bmad_assist.compiler.skills.*`. The patch system described below now augments BMAD v6.4+ skill source (`SKILL.md` + `customize.toml`) instead of the old XML workflow tree. Patch file format and discovery are unchanged. In 0.5.x and earlier, short names like `create-story` worked as aliases — these were removed in 0.6.0; use `bmad-create-story` everywhere a workflow name appears in user-facing surfaces (CLI args, configs, docs). Internal dispatch still auto-prepends `bmad-`, so production handlers keep working.

## Overview

Upstream BMAD workflows are written for a human in the loop. They pause to ask questions, present menus, and expect a developer to answer `[c] Continue` between steps. Run one of those workflows headless and it will sit there forever waiting for input that never comes.

Workflow patches are how bmad-assist resolves that tension. Each patch sits on top of an upstream BMAD v6.4+ skill (`SKILL.md` + `customize.toml` + per-skill assets) and applies the bmad-assist-specific transforms BMAD itself does not need:

- **Strip interactive elements** that block headless execution (`<ask>` blocks, `<elicit>` prompts, user menus).
- **Inject compile-time context** (git intelligence, project facts) so the LLM does not need to run those tool calls at runtime.
- **Remove sprint-status references** that the loop owns programmatically — the LLM should never read or write `sprint-status.yaml`.
- **Renumber/restructure** the prompt after step removals so the workflow still reads as a linear procedure.

The result is a deterministic, automation-friendly prompt that the runner caches under `.bmad-assist/cache/skills/<skill-id>.tpl.xml`. If you are debugging a workflow run, that cached file is the real artifact the LLM saw — start there.

> **Reading this doc?** If you are *authoring* a new patch, skip to [Authoring guidelines](#authoring-guidelines). If a patch is *misbehaving*, jump to [Troubleshooting](#troubleshooting). If you want to know *what the compile pipeline actually does* on each run, read [Compile pipeline](#compile-pipeline).

## Two-tier customization model

Most customizations have an obvious home; the question is only which tier to use.

| Concern | Where it belongs | Why |
|---|---|---|
| Add new prepend/append steps, persistent facts, mode toggles, named blocks | `customize.toml` | BMAD-native. Survives upstream skill upgrades cleanly. |
| Remove an upstream step, renumber, strip `<ask>`/`<elicit>` blocks, rewrite prose, embed git context | `.bmad-assist/patches/<workflow>.patch.yaml` | Subtractive and dynamic transforms that have no `customize.toml` equivalent. |

A concrete example of the boundary:

- *"Inject our team's coding standards as a persistent fact for `bmad-dev-story`."* → **`customize.toml`**. You are *adding* a fact the upstream skill is happy to consume; the next BMAD upgrade will not touch it.
- *"Delete the upstream `<elicit>Confirm before proceeding</elicit>` block in step 3, then renumber the steps below it."* → **patch file**. You are *subtracting* and *rewriting* — there is no TOML field for "remove the third step." This is the kind of work an LLM transform plus a deterministic regex safety net handles.

The base SKILL.md is processed first (variables substituted, `customize.toml` chain merged); then the patch's LLM transforms and regex post-process rules run on the resulting body. Reaching for a patch when `customize.toml` would do is a maintenance trap — the next BMAD release will likely move the lines your regex anchors on.

## Patch file location

Patches are discovered in this order (first found wins):

1. **Project**: `.bmad-assist/patches/<workflow>.patch.yaml`
2. **CWD**: `./bmad-assist/patches/<workflow>.patch.yaml`
3. **Global**: `~/.bmad-assist/patches/<workflow>.patch.yaml`

The patch filename uses the **legacy short name** (e.g., `create-story.patch.yaml`, not `bmad-create-story.patch.yaml`) because patches were authored before the canonical rename. The skill-layout compilers map `bmad-<name>` skill ids back to the short name when probing for a patch.

A patch is **optional**. Three of the bundled workflows ship with no patch on purpose — they are outcome-based SKILL.md files authored without an upstream BMAD skill to subtract from:

- `bmad-validate-story-synthesis`
- `bmad-code-review-synthesis`
- `bmad-security-review`

For these, the substituted SKILL.md body is the final body. No LLM transforms, no regex post-process, no `must_contain` assertions.

## Patch file structure

A complete patch from `.bmad-assist/patches/create-story.patch.yaml`:

```yaml
patch:
  name: "create-story-optimizer"
  version: "3.0.0"
  author: "Pawel N"
  description: "Optimizes create-story workflow - removes programmatically handled steps, embeds all context"

compatibility:
  bmad_version: "6.0.0-alpha.22"
  workflow: "create-story"

git_intelligence:
  enabled: true
  embed_marker: "git-intelligence"
  no_git_message: |
    This project is not under git version control.
    Do NOT attempt to run git commands - they will fail.
  commands:
    - name: "Recent Commits (last 5)"
      command: "git log --oneline -5"
    - name: "Related Story Commits"
      command: "git log --grep='{{epic_num}}\\.' --oneline -5 2>/dev/null || echo '(no related commits)'"

transforms:
  - "Remove step 1 (Determine target story) completely - story discovery is handled programmatically by the compiler"
  - "Remove ALL sprint status related content: references to sprint-status.yaml, any operations that read/write/update sprint status"
  - "Renumber remaining steps sequentially starting from 1"
  - "Add instruction in <critical> section: Git Intelligence is EMBEDDED at the start of the prompt - do NOT run git commands yourself"

post_process:
  - pattern: '\s*<check if="sprint status[^"]*">.*?</check>\s*'
    replacement: ""
    flags: "DOTALL"

  - pattern: '(<workflow>\s*)'
    replacement: |
      \1<critical>SCOPE LIMITATION: Your ONLY task is to create the story markdown file. Sprint tracking is handled programmatically.</critical>
    flags: "DOTALL"

validation:
  must_contain:
    - "<step"
    - "<critical"
  must_not_contain:
    - "sprint-status"
    - "{installed_path}"
```

### Section reference

| Section | Purpose |
|---|---|
| `patch` | Metadata: `name`, `version`, optional `author`, `description`. |
| `compatibility` | `bmad_version` + `workflow` (the legacy short name; matches the patch filename stem). |
| `git_intelligence` | Optional. Compile-time git commands whose output is embedded under a single XML tag (default `<git-intelligence>`). Each command supports `{{variable}}` placeholders resolved against compiler context. |
| `transforms` | Natural-language instructions for the master LLM to apply (list of strings). The LLM gets all transforms in one call and must return a `<transformed-document>` block. |
| `post_process` | Deterministic regex find/replace rules applied after LLM transforms. Each rule has `pattern`, `replacement`, optional `flags` (space-separated: `IGNORECASE` `MULTILINE` `DOTALL`, or single letters `I` `M` `S`). |
| `validation` | `must_contain` / `must_not_contain` assertions. Plain strings are substring matches; values wrapped in `/.../` are regex. |

## Compile pipeline

When `bmad-assist run` reaches a workflow phase, the skill-layout compiler in `bmad_assist.compiler.skills.<skill_module>` executes `SkillLayoutCompilerBase.compile`. The pipeline has three logical phases — *find the source*, *apply the patch*, *cache the result* — but a lot happens inside each.

### Phase 1: Source resolution

Before anything is patched, the compiler has to assemble the canonical pre-patch body — the merged, variable-substituted SKILL.md the patch will operate on.

1. **`find_skill()`** — locate `SKILL.md` for the canonical `bmad-<name>` skill id. Probe order: project install (`.claude/skills/<id>/`, `.agents/skills/<id>/`), then bundled fallback (`src/bmad_assist/skills/<id>/`).
2. **`parse_skill()`** — read frontmatter (`name`, `description`) + body.
3. **`resolve_customization()`** — merge the `customize.toml` chain (`<skill>/customize.toml` → `_bmad/custom/<skill>.toml` → `_bmad/custom/<skill>.user.toml`) using the BMAD-defined merge rules.
4. **Variable substitution** — substitute `{skill-root}`, `{project-root}`, workflow path tokens, and `{workflow.*}` prose. Each subclass contributes its own `build_extra_vars()` (e.g., epic/story numbers for `bmad-create-story`, story file context for `bmad-dev-story`). Then `resolve_skill_variables()` produces the final pre-patch body.

### Phase 2: Patch application

This is where bmad-assist's transforms — the LLM rewrite plus the deterministic regex safety net — actually mutate the body.

5. **`apply_llm_transforms()`** — if a patch exists and a master provider is configured, send the body + `transforms` list to the master LLM via `PatchSession`. The LLM returns the rewritten body inside `<transformed-document>`. The retry loop runs **up to 3 attempts**: each attempt re-runs the LLM with a "RETRY ATTEMPT N" hint prepended, then the per-attempt validator re-checks XML well-formedness and the patch's `must_contain` rules. If no master provider is configured, the compiler logs that and skips this stage (regex-only mode).
6. **`post_process_compiled()`** — apply the patch's `post_process` regex rules, plus the shared rules from `defaults.yaml` (and `defaults-testarch.yaml` for TEA workflows).
7. **`validate_output()`** — enforce the patch's `must_contain` / `must_not_contain` assertions on the post-processed body.

### Phase 3: Validation and cache

The body the LLM will eventually consume must be well-formed *and* persisted, otherwise a transient transform glitch turns into a silent regression.

8. **XML well-formedness** — for workflows whose patched body is XML, `validate_workflow_xml()` parses the result to catch mismatched tags before the runner consumes it.
9. **Cache write** — persist the patched body and metadata under `<project>/.bmad-assist/cache/skills/<skill-id>.tpl.xml` (+ `.meta.yaml` sidecar).

The workhorse compile step is implemented per-workflow as `_run_workflow_compile()` (a hook on `SkillLayoutCompilerBase`). This is where the workflow-specific glue (context-file building, mission text, XML output) lives — every legacy compiler body was inlined into its respective `_run_workflow_compile()` during Phase 7.

## Defaults files

The `defaults.yaml` and `defaults-testarch.yaml` files in `.bmad-assist/patches/` ship shared `post_process` rules applied to every workflow patch (`defaults-testarch.yaml` only applies to the eight TEA workflows).

Typical contents:

```yaml
# defaults.yaml — applied to every patch
post_process:
  # Cleanup workflow YAML cruft
  - pattern: '^\s*<var\s+name="template"[^>]*>.*?</var>\s*$'
    replacement: ""
    flags: "MULTILINE DOTALL"

  # Sprint-status references the loop owns programmatically
  - pattern: '<step[^>]*tag="sprint-status"[^>]*>.*?</step>'
    replacement: "<!-- step removed: sprint-status managed by loop handler -->"
    flags: "DOTALL IGNORECASE"

  - pattern: 'sprint-status\.yaml'
    replacement: ""
    flags: "IGNORECASE"
```

Defaults are loaded by `load_defaults()` (in `bmad_assist.compiler.patching.discovery`) and concatenated with the patch's own `post_process` rules before the regex pass runs.

## Cache system

Compiled bodies are cached at two levels:

- **Project cache** (`<project>/.bmad-assist/cache/skills/<skill-id>.tpl.xml`) — written on every successful compile.
- **Bundled cache** (`src/bmad_assist/skills/cache/<skill-id>.tpl.xml`) — empty by default; can be pre-warmed and shipped with the package. When present and valid, the bundled entry is copied into the project cache on first run.

Cache validity is gated on a metadata sidecar (`<skill-id>.tpl.xml.meta.yaml`) containing four keys:

| Key | Purpose |
|---|---|
| `skill_md_hash` | SHA-256 of `SKILL.md`. Source change → invalidate. |
| `customize_toml_hash` | SHA-256 of `customize.toml`. Customization change → invalidate. |
| `patch_hash` | SHA-256 of the patch file. Transform change → invalidate. |
| `transform_mode` | `"llm"` (master provider configured) or `"regex_only"` (no master). Switching modes invalidates because the LLM stage produces materially different output. |

To clear the project cache manually:

```bash
rm -rf .bmad-assist/cache/skills/
```

The next workflow run will recompile and refill it.

## Snapshot tests

Phase 7 added pinned-output snapshot tests at `tests/skill_layout/snapshots/<skill-id>.tpl.xml` — one snapshot per migrated workflow (18 total). The test (`tests/skill_layout/test_snapshots.py`) compiles each skill against a frozen project context with `freeze_clock` applied (so date/time tokens stay stable) and asserts byte-equality against the snapshot.

Any change to a patch file, `customize.toml`, or skill source that affects the compiled body will fail the snapshot. To accept the new output:

```bash
UPDATE_SNAPSHOTS=1 pytest tests/skill_layout/test_snapshots.py
```

Then **review the diff** before committing — the snapshots are the regression safety net for skill-layout compiler refactors.

## CLI commands

The `bmad-assist patch` subcommand group is a developer tool for inspecting and pre-warming patches. Workflow runs (`bmad-assist run`) invoke the compile pipeline directly and do not require these.

```bash
# Compile a single patch (legacy code path; reads workflow.yaml from a _bmad/ install).
# Use this only when developing patches against a project with a _bmad/... install.
bmad-assist patch compile <workflow>

# Compile every patch found in .bmad-assist/patches/.
bmad-assist patch compile-all

# List discovered patches and their cache status.
bmad-assist patch list

# Inspect a patch file's parsed contents.
bmad-assist patch show <workflow>
```

Note: `bmad-assist patch compile` exercises the legacy `compile_patch()` code path (which expects `_bmad/.../workflow.yaml` + `instructions.xml`), not the skill-layout pipeline used by `bmad-assist run`. The bundled-cache fast path on this code path was removed in 0.6.0 — every invocation hits the LLM. For most users, the implicit cache management performed by `bmad-assist run` is sufficient and `patch compile` is unnecessary.

## Authoring guidelines

When writing or modifying a patch:

- **Prefer `customize.toml` for additive concerns.** Patches should describe what bmad-assist needs to *remove* or *rewrite* in the upstream prompt; net-new prepend steps and persistent facts belong in `customize.toml`.
- **Make transforms specific.** "Remove step 1 (file discovery) - handled by compiler" beats "remove the file discovery step." LLMs interpret vague instructions inconsistently.
- **Mirror critical removals in `post_process`.** Use a regex rule as a deterministic safety net for things you absolutely need gone (sprint-status references, `<ask>` blocks). The LLM transform handles intent; the regex handles exactness.
- **Use `validation` to lock in invariants.** `must_contain: ["<critical"]` catches a transform that accidentally stripped the `<critical>` block; `must_not_contain: ["sprint-status"]` catches a leak through both the transform and the post-process pass.
- **Refresh snapshots in the same commit as the patch change.** Reviewers will look at the snapshot diff to understand what the patch actually does.

> **Common pitfalls**
>
> - **Naming the patch with the canonical id.** The discovery probe uses the legacy short name — `create-story.patch.yaml`, never `bmad-create-story.patch.yaml`. The latter is silently ignored.
> - **Regex without `DOTALL`.** Most XML blocks span newlines; a pattern that matches by hand against a one-line snippet will fail against the real body. Reach for `DOTALL` (`S`) by default for block-level matches.
> - **Forgetting to bump the snapshot.** A patch change that affects compiled output but ships without a snapshot refresh will fail CI for everyone else. Run `UPDATE_SNAPSHOTS=1 pytest tests/skill_layout/test_snapshots.py` and review the diff in the same commit.
> - **Loose `must_contain` strings.** `must_contain: ["story"]` will pass against almost anything. Anchor on something the transform is genuinely supposed to preserve, e.g. `must_contain: ["<critical", "<step n=\"1\""]`.

## Troubleshooting

### Patch did not apply

**What you see:** You modified `.bmad-assist/patches/dev-story.patch.yaml`, ran the workflow, and the cached body in `.bmad-assist/cache/skills/bmad-dev-story.tpl.xml` still looks like the unpatched upstream — `<ask>` blocks intact, your transforms ignored.

1. Verify the patch file exists at one of the discovery locations and is named `<workflow>.patch.yaml` using the **legacy short name** (`dev-story.patch.yaml`, not `bmad-dev-story.patch.yaml`).
2. Check `compatibility.workflow` matches the legacy short name (e.g., `create-story`, not `bmad-create-story`).
3. Clear the cache and recompile: `rm -rf .bmad-assist/cache/skills/`.
4. Run the workflow with `-v` to see the compile-pipeline log lines (they identify which stage was skipped or failed).

### Validation failure during compile

**What you see:** The compile pipeline keeps retrying the LLM transform stage and finally hard-fails with:

```
PatchError: Validation failed after 3 attempts: ['must_contain failed: /red-green-refactor/']
```

Translation: the LLM rewrote the body three times in a row and every result was missing a string the patch said had to be present. Likely causes:

- A transform instruction told the LLM to remove content the validation rule expects to keep.
- A `post_process` regex stripped the required content after the validator passed.

Inspect the cached body in `.bmad-assist/cache/skills/<skill-id>.tpl.xml` and the runner log for the exact LLM output that failed validation.

### Regex post-process not matching

**What you see:** Your `post_process` rule looks correct in isolation, but the cached body still contains the text you expected the regex to strip. No error — just nothing happened.

1. Test the pattern in isolation: `python -c "import re; print(re.search(r'pattern', text))"`.
2. Add `DOTALL` (`S`) when matching across newlines; add `MULTILINE` (`M`) for `^`/`$` anchors per line.
3. Escape special characters: `\.` for literal dot, `\{` for literal brace, `\\` for backslash inside YAML strings.

### Transform skipped silently

**What you see:** The compile finishes "successfully" but your transforms were never applied — none of the `<ask>` blocks you wanted removed are gone, and the runner log buried this line: `Skipping LLM transforms (mode=regex_only, ...)`.

No master provider is configured, so the LLM stage was skipped entirely. Add a `providers.master` block to `bmad-assist.yaml` to enable LLM transforms; otherwise the patch runs with `post_process` rules only.

## See also

- [Configuration Reference](configuration.md) — Provider, timeout, and compiler settings.
- [Strategic Context](strategic-context.md) — Document injection settings.
- [Troubleshooting](troubleshooting.md) — Other common issues.
