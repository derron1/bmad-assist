# A/B Workflow Testing

A/B workflow testing compares two configurations side-by-side against the same fixture and story set. It uses git worktree isolation to run each variant independently, then generates a comparison report showing metric differences.

> **Workflow names:** YAML examples in this doc use the canonical `bmad-<name>` skill ids (e.g., `bmad-create-story`, `bmad-code-review`). In 0.5.x and earlier, short names like `create-story` worked as aliases — these were removed in 0.6.0; use `bmad-create-story` everywhere a workflow name appears in user-facing surfaces (CLI args, configs, `phases:` lists). See [CHANGELOG.md](../CHANGELOG.md) `[0.6.0]` for the migration notes.

## Problem

When iterating on prompts, models, or patch-sets, you need answers to:
- Does the new prompt patch actually improve story quality?
- Is Opus worth the cost difference over Haiku for code review?
- Does a custom patch-set produce better results than the baseline?
- Does adding "agents team" instructions to code review change finding quality?

Without controlled comparison, you're left with subjective impressions across separate experiment runs that may differ in fixture state, story selection, or phase order.

## Solution

A/B testing provides:
- **YAML-driven test definitions** specifying exactly what to compare
- **Git worktree isolation** — both variants start from the same git ref with independent working directories
- **Full config pass-through** — experiment configs support the complete `bmad-assist.yaml` format (phase_models, timeouts, compiler, deep_verify, etc.)
- **Sequential execution** — variant A runs first, singletons reset, then variant B
- **Self-contained worktrees** — config is written as `bmad-assist.yaml` into each worktree
- **Workflow and template sets** — per-variant custom workflows and pre-compiled templates
- **Automatic comparison** — markdown report with metric deltas and configuration diff
- **Optional scorecards** — quality scoring for both variants
- **Signal handling** — graceful cancellation between variants

## Prerequisites

A/B testing requires:
1. An existing [experiment directory structure](experiments.md#directory-structure) with configs, patch-sets, and fixtures
2. The fixture must be a **git repository** (worktrees require `.git`)
3. All `ref` values in the `stories` list must exist in the fixture repo

## Test Definition Schema

Test definitions are YAML files that specify the complete A/B test configuration.

```yaml
name: prompt-v2-test                # Test name (used in result directory)
fixture: minimal                    # Fixture ID from experiments/fixtures/
stories:                            # Stories with per-story git refs
  - id: "3.1"                       # Story ID in epic.story format
    ref: abc1234                    # Git ref (commit SHA, tag, branch)
  - id: "3.2"
    ref: def5678
phases:                             # Ordered phases to execute per story
  - bmad-create-story
  - bmad-dev-story
variant_a:                          # Baseline configuration
  label: baseline
  config: opus-solo                 # Config template name
  patch_set: baseline               # Patch-set manifest name
variant_b:                          # Experimental configuration
  label: prompt-v2
  config: opus-solo
  patch_set: prompt-v2
scorecard: false                    # Optional quality scoring (default: false)
analysis: false                     # Optional LLM analysis report (default: false)
```

### Field Reference

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Test name, used in result directory naming |
| `fixture` | Yes | Fixture ID (directory name in `experiments/fixtures/`) |
| `stories` | Yes | List of story objects with `id` and `ref` fields |
| `phases` | Yes | Ordered list of phases to run per story |
| `variant_a` | Yes | Baseline variant configuration |
| `variant_b` | Yes | Experimental variant configuration |
| `scorecard` | No | Run quality scorecard after completion (default: `false`) |
| `analysis` | No | Generate LLM-powered analysis report (default: `false`) |

### Story Fields

Each entry in `stories` is an object with:

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Story ID in `epic.story` format (e.g., `"3.1"`) |
| `ref` | Yes | Git ref to checkout for this story (commit SHA, tag, or branch) |

Per-story refs allow each story to pin to a specific commit — useful when reviewing code at the point of implementation, before subsequent refactoring.

### Variant Fields

| Field | Required | Description |
|-------|----------|-------------|
| `label` | Yes | Human-readable label (must differ between A and B) |
| `config` | Yes | Config template name from `experiments/configs/` |
| `patch_set` | Yes | Patch-set manifest name from `experiments/patch-sets/` |
| `workflow_set` | No | Directory name in `experiments/workflows/` containing raw workflow source files |
| `template_set` | No | Directory name in `experiments/templates/` containing pre-compiled templates |

### Story ID Format

Story IDs (the `id` field) must contain a dot separator: `epic.story` (e.g., `"3.1"`, `"10.5"`). IDs without a dot are rejected.

### Supported Phases

Phase names accept both kebab-case and snake_case (normalized internally). Use the canonical `bmad-<name>` form — legacy short aliases (e.g., `create-story`) were removed in 0.6.0.

| Phase | Description |
|-------|-------------|
| `bmad-create-story` | Story generation from epic |
| `bmad-validate-story` | Multi-LLM story validation |
| `bmad-validate-story-synthesis` | Validation consensus |
| `bmad-dev-story` | Implementation phase |
| `bmad-code-review` | Multi-LLM code review |
| `bmad-code-review-synthesis` | Review consensus |
| `bmad-retrospective` | Epic retrospective |

### Validation Rules

- `stories` must have at least one entry
- `phases` must have at least one entry
- All phase names must be recognized workflows
- Variant labels must be distinct (`"baseline"` and `"baseline"` is rejected)
- File size must not exceed 1MB

## Config Templates

Experiment configs live in `experiments/configs/` and support two formats.

### Full Config (recommended)

A full `bmad-assist.yaml` config with `config_name` instead of `name`. All fields (`providers`, `phase_models`, `timeouts`, `compiler`, `deep_verify`, `security_agent`, `notifications`, `loop`, `testarch`, etc.) are passed through to the experiment runner exactly as written.

```yaml
config_name: opus-haiku-gemini-glm
description: "Opus master + Haiku helper + Gemini fleet + GLM"

providers:
  master:
    provider: claude-subprocess
    model: opus
  helper:
    provider: claude-subprocess
    model: haiku
  multi:
    - provider: gemini
      model: gemini-2.5-flash
    - provider: claude-subprocess
      model: sonnet
      model_name: glm-4.7
      settings: ~/.claude/glm.json

phase_models:
  create_story:
    provider: claude-subprocess
    model: opus
  code_review:
    - provider: gemini
      model: gemini-2.5-flash
    - provider: claude-subprocess
      model: sonnet
      model_name: glm-4.7
      settings: ~/.claude/glm.json

timeouts:
  default: 600
  dev_story: 3600

deep_verify:
  enabled: true

compiler:
  source_context:
    budgets:
      default: 30000
```

The config is written as `bmad-assist.yaml` into the worktree before execution, making it the sole config source. No global or CWD config merging occurs.

**Variable resolution:** `${home}` and `${project}` are resolved as template variables. All other `${...}` patterns (e.g., `${TELEGRAM_BOT_TOKEN}`) are resolved from environment variables and `.env` file. Unresolved env vars are left as literal strings (useful when the referencing feature is disabled).

### Legacy Template

Minimal format with just `name` and `providers`. Only master/multi provider config is passed through.

```yaml
name: opus-solo
description: "Opus-only configuration"

providers:
  master:
    provider: claude-subprocess
    model: opus
  multi: []
```

Legacy templates require a `providers` section. They don't support `phase_models`, `timeouts`, `compiler`, or other full-config fields.

## Workflow Sets and Template Sets

If you are A/B testing only providers or models, you can skip this section — both variants will share the bundled workflow source. **Reach for workflow sets when the change you actually want to measure lives inside the prompt itself** — a reworded `bmad-code-review` SKILL.md, a tighter `bmad-create-story` template, a different `customize.toml` per variant. Template sets cover the same need at the *compiled* level: useful when you have already-patched `.tpl.xml` outputs and want to pin one variant to a specific compiled prompt without re-running the LLM transform stage.

Workflow sets and template sets allow per-variant customization of the BMAD workflow source files and pre-compiled templates used during compilation and execution.

### Workflow Sets

A **workflow set** is a directory in `experiments/workflows/` containing per-variant skill source. Each subdirectory represents one workflow in the BMAD v6.4+ skill layout (`SKILL.md` + `customize.toml`, plus any per-skill assets the workflow needs — `template.md`, `checklist.md`, `steps-c/` for TEA, `patterns/` for security-review).

```
experiments/workflows/
├── baseline/
│   ├── bmad-code-review/
│   │   ├── SKILL.md
│   │   └── customize.toml
│   └── bmad-create-story/
│       ├── SKILL.md
│       ├── customize.toml
│       └── template.md
├── bmad-code-review-test-001/
│   └── bmad-code-review/
│       ├── SKILL.md
│       └── customize.toml
└── minimal-set/
    └── bmad-create-story/
        ├── SKILL.md
        └── customize.toml
```

When a variant specifies `workflow_set`, the runner copies the skill directories into the worktree's installed skill location (`.claude/skills/<id>/` and `.agents/skills/<id>/`). The compiler's discovery logic (`find_skill()`) checks the installed location first, then falls back to the bundled skill source under `src/bmad_assist/skills/<id>/`.

### Template Sets

A **template set** is a directory in `experiments/templates/` containing pre-compiled (patched) template files (`.tpl.xml` and `.tpl.xml.meta.yaml`).

```
experiments/templates/
├── optimized-v1/
│   ├── bmad-create-story.tpl.xml
│   ├── bmad-create-story.tpl.xml.meta.yaml
│   ├── bmad-dev-story.tpl.xml
│   └── bmad-dev-story.tpl.xml.meta.yaml
└── baseline-compiled/
    └── ...
```

When a variant specifies `template_set`, the runner copies the template files into the worktree's `.bmad-assist/cache/` directory **after** the project cache copy, so template set files take priority over existing cached templates.

### Example with Workflow and Template Sets

```yaml
name: custom-workflow-test
fixture: minimal
stories:
  - id: "3.1"
    ref: HEAD
phases:
  - bmad-create-story
  - bmad-dev-story
variant_a:
  label: baseline
  config: opus-solo
  patch_set: baseline
variant_b:
  label: custom-prompts
  config: opus-solo
  patch_set: baseline
  workflow_set: custom-prompts-v2
  template_set: optimized-v1
```

## CLI Commands

### Run A/B Test

```bash
bmad-assist experiment ab <test-file> [options]
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `test-file` | Path to A/B test definition YAML file |

**Options:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-p`, `--project` | path | `.` | Project directory |
| `-v`, `--verbose` | flag | false | Show INFO-level logs (phase progress, provider status, file copies) |
| `-n`, `--dry-run` | flag | false | Validate without executing |

**Examples:**

```bash
# Run a test
bmad-assist experiment ab experiments/ab-tests/prompt-v2-test.yaml

# Validate configuration without executing
bmad-assist experiment ab my-test.yaml --dry-run

# Verbose output with explicit project path
bmad-assist experiment ab my-test.yaml -p ./my-project -v
```

**Exit Codes:**

| Code | Meaning |
|------|---------|
| `0` | Both variants completed successfully |
| `1` | Runtime error or variant failure |
| `2` | Configuration error (missing template, invalid YAML) |

### Re-run Analysis

Re-run LLM analysis on existing A/B test results without re-executing the test:

```bash
bmad-assist experiment ab-analysis <result-dir> [options]
```

| Argument | Description |
|----------|-------------|
| `result-dir` | Path to A/B test result directory (must contain `test-definition.yaml`) |

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-p`, `--project` | path | `.` | Project directory (used to locate `experiments/configs/`) |
| `-v`, `--verbose` | flag | false | Enable verbose output |

The command loads the config singleton from variant A's config template and invokes the master LLM. This is useful for re-generating analysis after fixing prompts or when the original run's analysis failed due to singleton reset.

```bash
bmad-assist experiment ab-analysis experiments/ab-results/agents-team-cr-001-20260208-082603
```

### Dry Run

Dry run validates the complete test configuration without executing any phases:
- Parses and validates the YAML definition
- Checks that the fixture exists
- Checks that config templates exist for both variants
- Checks that patch-set manifests exist for both variants
- Checks that workflow_set and template_set directories exist (if specified)

```bash
bmad-assist experiment ab my-test.yaml --dry-run
```

## How It Works

### Execution Flow

1. **Parse & validate** — Load YAML, validate all referenced resources exist
2. **Create result directory** — `experiments/ab-results/{name}-{timestamp}/`
3. **Save test definition** — Copy YAML for reproducibility
4. **Create worktrees** — `git worktree add --detach` for both variants from the first story's ref
5. **Run variant A:**
   a. Reset all singletons (config, paths, loop config)
   b. Build config dict from template (full pass-through or legacy providers)
   c. Write config to `worktree/bmad-assist.yaml` (self-contained)
   d. Load config singleton via `load_config()`
   e. Copy compiled template cache from project root
   f. Apply workflow set (if specified) → `.bmad-assist/workflows/`
   g. Apply template set (if specified) → `.bmad-assist/cache/` (overwrites cache)
   h. For each story: checkout its `ref`, purge runtime artifacts, execute all phases, snapshot artifacts to result dir
6. **Reset singletons** — Clear config, paths, and loop config between variants
7. **Run variant B** — Same as A with different config, patch-set, workflow/template sets
8. **Generate comparison** — Markdown report with metric deltas (if both completed)
9. **Optional analysis** — LLM-powered analysis report comparing both variants (if `analysis: true`)
10. **Optional scorecards** — Quality scoring for both worktrees
11. **Write manifest** — AB test metadata and outcomes
12. **Cleanup** — Remove worktrees (always, even on failure)

### Git Worktree Isolation

Each variant gets an independent worktree created from the fixture repo:

```
/tmp/bmad-ab-{name}-{timestamp}/
├── worktree-a/          # Detached HEAD, switches ref per story
│   ├── .git             # Linked to fixture repo
│   ├── bmad-assist.yaml # Config written from experiment template
│   ├── .bmad-assist/
│   │   ├── cache/       # Compiled templates (project + template_set)
│   │   └── workflows/   # Custom workflows (from workflow_set)
│   ├── docs/
│   ├── _bmad-output/    # Purged between stories
│   └── [source code]
└── worktree-b/          # Same structure, independent from A
    ├── .git
    ├── bmad-assist.yaml
    ├── .bmad-assist/
    │   ├── cache/
    │   └── workflows/
    ├── docs/
    ├── _bmad-output/
    └── [source code]
```

Before each story, the worktree is reset to the story's `ref` commit. Runtime artifact directories (`_bmad-output/`, `.bmad-assist/prompts/`) are purged, tracked changes are discarded, and the worktree is checked out at the new ref. Artifacts produced during a story are snapshotted to the result directory before the next story starts. Changes in one worktree do not affect the other. The fixture repository itself is never modified.

Worktrees are always cleaned up in a `finally` block, even if the test crashes or is cancelled. If `git worktree remove` fails, the runner falls back to `shutil.rmtree` + `git worktree prune`.

### Signal Handling

The runner registers signal handlers (SIGINT, SIGTERM) for graceful cancellation:
- If cancelled during variant A, variant B is skipped with status `CANCELLED`
- If cancelled during variant B, it completes with partial results
- Worktrees are always cleaned up regardless of cancellation

## Results Directory Structure

Results are saved to `experiments/ab-results/` (gitignored by default).

```
experiments/ab-results/{name}-{timestamp}/
├── test-definition.yaml       # Copy of input YAML
├── manifest.yaml              # AB test metadata + outcomes
├── comparison.md              # A vs B markdown report (if both completed)
├── analysis.md                # LLM analysis report (if analysis: true)
├── scorecard-a.yaml           # Quality scorecard for A (if scorecard: true)
├── scorecard-b.yaml           # Quality scorecard for B (if scorecard: true)
├── variant-a/
│   └── story-{id}/           # Per-story artifacts snapshot
│       ├── artifacts/         # Reviews, validations, syntheses
│       └── bmad-assist/cache/ # Mapping files
└── variant-b/
    └── story-{id}/
        ├── artifacts/
        └── bmad-assist/cache/
```

### Manifest Schema

```yaml
test_name: prompt-v2-test
fixture: minimal
stories:
  - id: "3.1"
    ref: abc1234
  - id: "3.2"
    ref: def5678
phases:
  - bmad-create-story
  - bmad-dev-story
variant_a:
  label: baseline
  status: completed              # completed | failed | cancelled
  stories_completed: 2
  stories_failed: 0
  duration_seconds: 142.5
  error: null
variant_b:
  label: prompt-v2
  status: completed
  stories_completed: 2
  stories_failed: 0
  duration_seconds: 98.3
  error: null
```

### Comparison Report

The comparison report is a markdown file with:
- **Header** — test name, fixture, stories (with refs), phases
- **Results summary table** — status, stories completed/failed, duration with delta and percentage
- **Configuration table** — config template, patch-set, workflow-set (if used), template-set (if used) for each variant
- **Errors section** — only present if either variant has errors

Example delta output:
```
| Metric | Variant A (baseline) | Variant B (prompt-v2) | Delta |
|--------|:---:|:---:|:---:|
| Stories Completed | 2 | 2 | 0 |
| Duration | 142.5s | 98.3s | -44.2s (-31.0%) |
```

### Analysis Report

When `analysis: true`, an LLM-powered analysis report (`analysis.md`) is generated after the comparison. The master LLM receives all artifacts from both variants — reviews, syntheses, benchmarks, and model deanonymization mappings — and produces a structured report including:

- **Deanonymized results** — maps anonymized validator labels to actual model names
- **Model-level comparison** — same model's performance across variants
- **Key findings** — stability, quality deltas, surprising patterns
- **Duration analysis** — timing breakdown and bottleneck identification
- **Verdict** — overall conclusion on whether the variant change was beneficial
- **Recommendations** — actionable next steps

Analysis requires both variants to complete successfully and a comparison report to exist.

## Example: Minimal Test

Compare two models on a single story and phase:

```yaml
name: model-comparison
fixture: simple-portfolio
stories:
  - id: "1.1"
    ref: HEAD
phases:
  - bmad-create-story
variant_a:
  label: opus
  config: opus-solo
  patch_set: baseline
variant_b:
  label: haiku
  config: haiku-solo
  patch_set: baseline
```

## Example: Full Config A/B Test

Compare code review quality with different workflow instructions, using a full config with phase_models. Each story pins to its implementation commit (before code review changes):

```yaml
name: agents-team-cr-001
fixture: webhook-relay-010
analysis: true                        # LLM-powered analysis report
stories:
  - id: "2.3"                         # Create Transformation Pipeline
    ref: b58faf3                      # feat(story-2.3): implement story
  - id: "3.4"                         # Implement Fan-Out
    ref: 2873bb0                      # feat(story-3.4): implement story
  - id: "3.5"                         # Add Destination Health Tracking
    ref: d2b1fba                      # feat(story-3.5): implement story
phases:
  - bmad-code-review
  - bmad-code-review-synthesis
variant_a:
  label: baseline
  config: opus-haiku-gemini-glm      # Full config with phase_models, timeouts, etc.
  patch_set: baseline
  workflow_set: baseline
variant_b:
  label: agents-team
  config: opus-haiku-gemini-glm
  patch_set: baseline
  workflow_set: bmad-code-review-test-001  # Modified bmad-code-review workflow
scorecard: false
```

## Example: Full Test with Scorecard

Compare prompt patches across multiple stories and the full development loop:

```yaml
name: prompt-v2-full
fixture: webhook-relay-001
stories:
  - id: "3.1"
    ref: v1.0.0
  - id: "3.2"
    ref: v1.0.0
  - id: "3.3"
    ref: v1.0.0
phases:
  - bmad-create-story
  - bmad-validate-story
  - bmad-validate-story-synthesis
  - bmad-dev-story
  - bmad-code-review
  - bmad-code-review-synthesis
variant_a:
  label: baseline
  config: opus-solo
  patch_set: baseline
variant_b:
  label: prompt-v2
  config: opus-solo
  patch_set: prompt-v2
scorecard: true
```

## Directory Layout

```
experiments/
├── ab-tests/                     # A/B test definition YAML files
│   └── agents-team-cr-001.yaml
├── ab-results/                   # Test results (gitignored)
│   └── agents-team-cr-001-20260207-190500/
│       ├── test-definition.yaml
│       ├── manifest.yaml
│       ├── comparison.md
│       ├── variant-a/
│       └── variant-b/
├── configs/                      # Config templates
│   ├── opus-solo.yaml            # Legacy: name + providers
│   └── opus-haiku-gemini-glm.yaml  # Full: config_name + everything
├── patch-sets/                   # Patch-set manifests
├── workflows/                    # Workflow sets for A/B variants (SKILL.md + customize.toml per skill)
│   ├── baseline/
│   │   └── bmad-code-review/
│   └── bmad-code-review-test-001/
│       └── bmad-code-review/
├── templates/                    # Pre-compiled template sets
├── fixtures/                     # Fixture repositories
└── loops/                        # Loop templates (legacy)
```

## Python API

For programmatic A/B testing:

```python
from pathlib import Path
from bmad_assist.experiments.ab import (
    ABTestRunner,
    ABTestResult,
    ABVariantResult,
    ABTestConfig,
    load_ab_test_config,
)

# Load test definition
config = load_ab_test_config(Path("experiments/ab-tests/my-test.yaml"))

# Run test
experiments_dir = Path("experiments")
runner = ABTestRunner(experiments_dir, project_root=Path("."))
result: ABTestResult = runner.run(config)

# Inspect results
print(f"Test: {result.test_name}")
print(f"Variant A: {result.variant_a.status.value} "
      f"({result.variant_a.stories_completed} stories, "
      f"{result.variant_a.duration_seconds:.1f}s)")
print(f"Variant B: {result.variant_b.status.value} "
      f"({result.variant_b.stories_completed} stories, "
      f"{result.variant_b.duration_seconds:.1f}s)")

if result.comparison_path:
    print(f"Report: {result.comparison_path}")
if result.analysis_path:
    print(f"Analysis: {result.analysis_path}")
```

### Key Classes

| Class | Purpose |
|-------|---------|
| `ABTestConfig` | Frozen Pydantic model for test definition |
| `ABVariantConfig` | Frozen Pydantic model for variant configuration (includes `workflow_set`, `template_set`) |
| `StoryRef` | Frozen Pydantic model for story ID + git ref pair |
| `ABTestRunner` | Main orchestrator — validation, worktrees, execution, reporting |
| `ABTestResult` | Frozen dataclass with both variant results, comparison and analysis paths |
| `ABVariantResult` | Frozen dataclass with single variant metrics |
| `WorktreeInfo` | Frozen dataclass with worktree path and ref |

### Standalone Report Generation

Generate a comparison report from existing variant results:

```python
from bmad_assist.experiments.ab.report import generate_ab_comparison

report_path = generate_ab_comparison(
    config=config,
    variant_a=result.variant_a,
    variant_b=result.variant_b,
    output_path=Path("my-comparison.md"),
)
```

## Troubleshooting

### Fixture is not a git repository

```
IsolationError: Fixture is not a git repository: /path/to/fixture (no .git directory)
```

A/B testing requires the fixture to be a git repository. Initialize one:
```bash
cd experiments/fixtures/my-fixture
git init && git add . && git commit -m "initial"
```

### Git ref not found

```
IsolationError: Git ref 'v2.0' not found in fixture my-fixture
```

A story's `ref` doesn't exist in the fixture. Check available refs:
```bash
cd experiments/fixtures/my-fixture
git log --oneline -10          # commits
git tag -l                     # tags
git branch -a                  # branches
```

### Worktree path already exists

```
IsolationError: Worktree path already exists: /tmp/bmad-ab-test-20260207/worktree-a
```

A previous run left behind worktrees (e.g., crash without cleanup). Clean up manually:
```bash
rm -rf /tmp/bmad-ab-*
cd experiments/fixtures/my-fixture && git worktree prune
```

### Config template not found

```
ConfigError: A/B test validation failed:
  Variant A: Config template 'my-config' not found
```

The config template referenced by `variant_a.config` or `variant_b.config` doesn't exist. Check:
```bash
ls experiments/configs/
```

Ensure the file uses `config_name:` (full config) or `name:` (legacy) matching the filename stem.

### Unknown variable error

```
ConfigError: Unknown variable: ${TELEGRAM_BOT_TOKEN}
```

Full configs (`config_name`) resolve `${...}` patterns from environment variables and `.env` file. If the variable is not set and the feature using it is disabled, the reference is left as a literal string. For legacy templates (`name`), only `${home}` and `${project}` are supported — other `${...}` patterns raise an error.

### Patch-set not found

```
ConfigError: A/B test validation failed:
  Variant B: Patch-set 'my-patches' not found
```

The patch-set manifest referenced by `variant_b.patch_set` doesn't exist. Check:
```bash
ls experiments/patch-sets/
```

### Variant labels must be distinct

```
ValidationError: Variant labels must be distinct, both are 'baseline'
```

Change one of the variant labels to be unique.

### Invalid story ID format

```
ValidationError: Invalid story ID '31': must be 'epic.story' format (e.g., '3.1')
```

Story IDs must contain a dot: `"3.1"` not `"31"`. Quote numeric values in YAML to avoid type coercion.

### Test cancelled — variant B shows CANCELLED

This is expected behavior. If you press Ctrl+C during variant A execution, variant B is skipped with status `CANCELLED` and an error message. Worktrees are still cleaned up.

## See Also

- [Experiments](experiments.md) — Full experiment framework (config templates, patch-sets, fixtures, comparison)
- [Workflow Patches](workflow-patches.md) — Creating custom prompt patches
- [Configuration Reference](configuration.md) — Main configuration options
