# Experiment Framework (Work in Progress)

The experiment framework enables systematic LLM comparison through controlled execution of BMAD workflows. It isolates fixtures, tracks metrics, and generates comparison reports to answer questions about model performance, prompt effectiveness, and workflow optimization.

> **Workflow names:** Examples in this doc use the canonical `bmad-<name>` skill ids (e.g., `bmad-create-story`, `bmad-dev-story`). Legacy short names (`create-story`, `dev-story`, …) still work as backwards-compatible aliases in loop templates and patch-set manifests for one more release — see [CHANGELOG.md](../CHANGELOG.md) `[Unreleased]`.

## Problem

When optimizing bmad-assist workflows, you need answers to:
- Which LLM model produces better stories or code reviews?
- Does a new prompt patch improve quality or introduce regressions?
- How do different workflow sequences affect outcomes?
- What's the cost/token tradeoff between configurations?

Without controlled experiments, comparing configurations requires manual execution and subjective analysis.

## Solution

The experiment framework provides:
- **Four-axis configuration**: Fixture × Config × Patch-Set × Loop defines each experiment
- **Fixture isolation**: Deep copy ensures reproducibility and prevents cross-contamination
- **Automatic metrics collection**: Duration, tokens, cost, success rates tracked per phase
- **Manifest persistence**: Complete configuration and results captured for audit
- **Comparison reports**: Statistical comparison across runs with winner determination
- **A/B testing**: Side-by-side variant comparison with git worktree isolation (see [A/B Testing](ab-testing.md))

## Directory Structure

Experiments require a specific directory structure in your project:

```
experiments/
├── configs/               # Config templates (LLM provider settings)
│   ├── opus-solo.yaml
│   └── opus-haiku-gemini-glm.yaml
├── loops/                 # Loop templates (workflow sequences)
│   ├── fast.yaml
│   └── standard.yaml
├── patch-sets/            # Patch-set manifests (prompt customizations)
│   ├── baseline.yaml
│   └── no-patches.yaml
├── fixtures/              # Test subject projects (directories or .tar archives)
│   ├── webhook-relay-001/
│   └── simple-portfolio/
├── workflows/             # Workflow sets for A/B variants
│   ├── baseline/
│   └── bmad-code-review-test-001/
├── templates/             # Pre-compiled template sets for A/B variants
├── ab-tests/              # A/B test definition YAML files
├── ab-results/            # A/B test results (gitignored)
├── runs/                  # Experiment run outputs (auto-created)
│   └── run-2026-02-03-001/
└── analysis/              # Scorecards and analysis artifacts
```

## Config Templates

Config templates define which LLM providers to use. Location: `experiments/configs/`

### Full Config (recommended)

A full `bmad-assist.yaml` config with `config_name` instead of `name`. All fields (`providers`, `phase_models`, `timeouts`, `compiler`, `deep_verify`, `security_agent`, etc.) are passed through to the experiment runner exactly as written.

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
```

The config is written as `bmad-assist.yaml` into the fixture snapshot, making it the sole config source. No global or CWD config merging occurs.

### Legacy Template

Minimal format with just `name` and `providers`. Only master/multi provider config is passed through.

```yaml
name: opus-solo
description: "Opus-only baseline"

providers:
  master:
    provider: claude-subprocess
    model: opus
  multi: []
```

Legacy templates don't support `phase_models`, `timeouts`, `compiler`, or other full-config fields.

### Variable Resolution

Config files support variables in string values:
- `${home}` — User home directory
- `${project}` — Project root (requires `--project` flag)

Full configs (`config_name`) additionally resolve `${...}` patterns from environment variables and `.env` file.

## Loop Templates

Loop templates define workflow execution sequence. Location: `experiments/loops/`

```yaml
name: standard
description: "Full BMAD loop with validation and review"

sequence:
  - workflow: bmad-create-story
    required: true           # Failure stops experiment
  - workflow: bmad-validate-story
    required: true
  - workflow: bmad-validate-story-synthesis
    required: false          # Optional - failure logged but continues
  - workflow: bmad-dev-story
    required: true
  - workflow: bmad-code-review
    required: true
  - workflow: bmad-code-review-synthesis
    required: false
```

### Supported Workflows

Workflow names accept both kebab-case (`bmad-create-story`) and snake_case (`bmad_create_story`). Legacy aliases without the `bmad-` prefix (e.g., `create-story`) are still resolved for one more release.

| Workflow | Description |
|----------|-------------|
| `bmad-create-story` | Story generation from epic |
| `bmad-validate-story` | Multi-LLM story validation |
| `bmad-validate-story-synthesis` | Validation consensus |
| `bmad-dev-story` | Implementation phase |
| `bmad-code-review` | Multi-LLM code review |
| `bmad-code-review-synthesis` | Review consensus |
| `bmad-retrospective` | Epic retrospective |
| `bmad-testarch-atdd` | Test-driven development |
| `bmad-testarch-test-review` | Test review phase |
| `bmad-testarch-test-design` | ATDD test planning |
| `bmad-qa-plan-generate` | QA test plan generation |
| `bmad-qa-plan-execute` | QA test execution |

## Patch-Set Manifests

Patch-sets define which prompt patches to apply. Location: `experiments/patch-sets/`

```yaml
name: baseline
description: "Production patches from project"

patches:
  bmad-create-story: ${project}/.bmad-assist/patches/bmad-create-story.patch.yaml
  bmad-validate-story: ${project}/.bmad-assist/patches/bmad-validate-story.patch.yaml
  bmad-dev-story: null           # null = no patch, use raw workflow
  bmad-code-review: ${project}/.bmad-assist/patches/bmad-code-review.patch.yaml

workflow_overrides:          # Alternative workflow implementations (optional)
  bmad-testarch-atdd: /path/to/custom-atdd-workflow/
```

### Patch Resolution

1. If `workflow_overrides[workflow]` exists, use that directory (takes precedence)
2. If `patches[workflow]` is a path, copy patch to fixture snapshot
3. If `patches[workflow]` is `null`, use raw BMAD workflow (no patch)
4. If workflow not listed, no patch applied

## Fixtures

Fixtures are complete project directories for experiment execution. Location: `experiments/fixtures/`

### Discovery

- Only subdirectories of `experiments/fixtures/` are considered
- Tar archives (`.tar`, `.tar.gz`, `.tar.bz2`) are extracted on use
- Hidden directories (`.hidden-*`) are ignored
- Directory name becomes fixture ID (must match `^[a-zA-Z_][a-zA-Z0-9_-]*$`)

### Optional Metadata

Add a `.bmad-assist.yaml` or `bmad-assist.yaml` in fixture root:

```yaml
fixture:
  name: "Auth Microservice"
  description: "E-commerce auth API"
  tags:
    - go
    - microservices
  difficulty: medium                  # easy | medium | hard
  estimated_cost: "$0.50"             # Must match $X.XX pattern
```

If no metadata file exists, defaults are used (ID as name, empty description/tags).

### Fixture Structure

A well-formed fixture includes:

```
my-fixture/
├── .bmad-assist.yaml          # Optional metadata
├── bmad-assist.yaml           # Project config (required for loop)
├── docs/
│   ├── prd.md
│   ├── architecture.md
│   └── epics/
│       └── epic-1.md
├── _bmad/
│   └── bmm/
│       └── config.yaml
├── _bmad-output/
│   └── implementation-artifacts/
│       ├── sprint-status.yaml
│       └── stories/
└── [source code]
```

### Fixture Isolation

When an experiment runs, the fixture is deep-copied to prevent mutation:

1. Check for tar archive (`{fixture}.tar`, `.tar.gz`, `.tar.bz2`)
2. If tar exists: extract to `runs/{run_id}/fixture-snapshot/`
3. Otherwise: recursive directory copy with filters

**Skipped during copy:**
- `.git/`, `__pycache__/`, `.venv/`, `node_modules/`, `.pytest_cache/`
- `*.pyc`, `*.pyo`

Dotfiles (`.gitignore`, `.env.example`) are copied. External symlinks are skipped with a warning, internal symlinks are dereferenced.

## CLI Commands

### Run Single Experiment

```bash
bmad-assist experiment run \
  -f minimal \              # Fixture ID
  -c opus-solo \            # Config template
  -P baseline \             # Patch-set manifest
  -l standard               # Loop template
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-f`, `--fixture` | string | required | Fixture ID |
| `-c`, `--config` | string | required | Config template name |
| `-P`, `--patch-set` | string | required | Patch-set manifest name |
| `-l`, `--loop` | string | required | Loop template name |
| `-p`, `--project` | path | `.` | Project directory |
| `-v`, `--verbose` | flag | false | Enable debug logging |
| `-n`, `--dry-run` | flag | false | Validate without executing |
| `--qa` | flag | false | Include Playwright tests (category A+B) |
| `--fail-fast` | flag | false | Stop on first story failure |

**Exit Codes:** `0` = completed, `1` = runtime error, `2` = configuration error.

### Batch Experiments

Run cartesian product of fixtures × configs:

```bash
bmad-assist experiment batch \
  --fixtures minimal,complex \
  --configs opus-solo,haiku-solo \
  --patch-set baseline \
  --loop standard
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--fixtures` | string | required | Comma-separated fixture IDs |
| `--configs` | string | required | Comma-separated config names |
| `--patch-set` | string | required | Patch-set for all runs |
| `--loop` | string | required | Loop template for all runs |
| `-n`, `--dry-run` | flag | false | Show combinations without running |

Failed experiments are logged but don't stop the batch.

### A/B Testing

Run controlled A/B comparisons with git worktree isolation:

```bash
# Run A/B test
bmad-assist experiment ab experiments/ab-tests/prompt-v2-test.yaml

# Validate without executing
bmad-assist experiment ab my-test.yaml --dry-run

# Re-run LLM analysis on existing results
bmad-assist experiment ab-analysis experiments/ab-results/my-test-20260208-082603
```

See [A/B Testing](ab-testing.md) for full documentation on test definitions, workflow/template sets, and result structure.

### List Runs

```bash
bmad-assist experiment list
bmad-assist experiment list --status completed --fixture minimal -n 10
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-s`, `--status` | string | all | Filter: completed, failed, cancelled, running, pending |
| `-f`, `--fixture` | string | all | Filter by fixture name |
| `-c`, `--config` | string | all | Filter by config name |
| `-n`, `--limit` | int | 20 | Maximum runs to display |

### Show Run Details

```bash
bmad-assist experiment show run-2026-02-03-001
```

Displays status, timing, configuration, results summary, metrics (cost, tokens, duration), and phase-by-phase breakdown.

### Compare Runs

Generate comparison report for 2-10 runs:

```bash
bmad-assist experiment compare run-001 run-002
bmad-assist experiment compare run-001 run-002 run-003 --output comparison.md
bmad-assist experiment compare run-001 run-002 --format json -o comparison.json
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o`, `--output` | path | stdout | Output file path |
| `-f`, `--format` | string | markdown | Output format: markdown or json |

**Comparison Metrics:**

| Metric | Direction | Description |
|--------|-----------|-------------|
| `total_cost` | lower better | Total API cost |
| `total_tokens` | lower better | Total tokens used |
| `total_duration_seconds` | lower better | Total execution time |
| `avg_tokens_per_phase` | lower better | Average tokens per phase |
| `avg_cost_per_phase` | lower better | Average cost per phase |
| `stories_completed` | higher better | Successfully completed stories |
| `stories_failed` | lower better | Failed stories |
| `success_rate` | higher better | completed / (completed + failed) |

### List Templates

```bash
bmad-assist experiment templates
bmad-assist experiment templates --type config
bmad-assist experiment templates --type loop
bmad-assist experiment templates --type patch-set
bmad-assist experiment templates --type fixture
```

### Quality Scorecard

```bash
bmad-assist test scorecard <fixture-name>
bmad-assist test scorecard webhook-relay-001 -o scorecard.yaml -v
```

Assesses fixture quality across dimensions: completeness (TODOs, placeholders, empty files), functionality (build, tests), code quality (linting, complexity, security), and documentation.

## Run Output Structure

Each experiment creates a run directory:

```
experiments/runs/run-2026-02-03-001/
├── manifest.yaml             # Complete configuration and results
├── metrics.yaml              # Aggregated metrics
├── state.yaml                # Loop state (for crash recovery)
├── output/                   # Run output artifacts
└── fixture-snapshot/         # Isolated copy of fixture
    ├── bmad-assist.yaml      # Config from template
    ├── .bmad-assist/
    │   └── patches/          # Patches copied here
    ├── _bmad/
    ├── _bmad-output/
    ├── docs/
    └── [source files]
```

### Manifest Schema

```yaml
run_id: run-2026-02-03-001
started: "2026-02-03T10:30:00+00:00"
completed: "2026-02-03T11:45:30+00:00"
status: completed                      # pending | running | completed | failed | cancelled
schema_version: "1.0"

input:
  fixture: minimal
  config: opus-solo
  patch_set: baseline
  loop: standard

resolved:
  fixture:
    name: minimal
    source: /path/to/experiments/fixtures/minimal
    snapshot: ./fixture-snapshot
  config:
    name: opus-solo
    source: /path/to/experiments/configs/opus-solo.yaml
    providers:
      master:
        provider: claude-subprocess
        model: opus
      multi: []
  patch_set:
    name: baseline
    source: /path/to/experiments/patch-sets/baseline.yaml
    patches:
      bmad-create-story: /path/to/patches/bmad-create-story.patch.yaml
      bmad-dev-story: null
  loop:
    name: standard
    source: /path/to/experiments/loops/standard.yaml
    sequence:
      - bmad-create-story
      - bmad-validate-story
      - bmad-dev-story
      - bmad-code-review

results:
  stories_attempted: 2
  stories_completed: 2
  stories_failed: 0
  retrospective_completed: true
  qa_completed: false
  phases:
    - phase: bmad-create-story
      story: "1.1"
      epic: 1
      status: completed
      duration_seconds: 45.3
      tokens: 2500
      cost: 0.05
      error: null
```

## Python API

```python
from pathlib import Path
from bmad_assist.experiments import (
    ExperimentRunner,
    ExperimentInput,
    ExperimentOutput,
    ExperimentStatus,
    ComparisonGenerator,
    MetricsCollector,
    FixtureManager,
    ConfigRegistry,
    LoopRegistry,
    PatchSetRegistry,
)

# Run experiment
experiments_dir = Path("experiments")
runner = ExperimentRunner(experiments_dir, project_root=Path("."))

exp_input = ExperimentInput(
    fixture="minimal",
    config="opus-solo",
    patch_set="baseline",
    loop="standard",
    fail_fast=False,
)
output: ExperimentOutput = runner.run(exp_input)

print(f"Status: {output.status}")
print(f"Stories: {output.stories_completed}/{output.stories_attempted}")
print(f"Duration: {output.duration_seconds:.1f}s")

# Compare runs
generator = ComparisonGenerator(experiments_dir / "runs")
report = generator.compare(["run-001", "run-002"])
markdown = generator.generate_markdown(report)

# Load metrics
collector = MetricsCollector(experiments_dir / "runs" / "run-001")
metrics = collector.load()
print(f"Total cost: ${metrics.summary.total_cost:.2f}")

# List and filter fixtures
fixtures = FixtureManager(experiments_dir / "fixtures")
for entry in fixtures.discover():
    print(f"{entry.id}: {entry.name} ({entry.difficulty})")

go_fixtures = fixtures.filter_by_tags(["go", "microservices"])
easy_fixtures = fixtures.filter_by_difficulty("easy")
```

### Key Classes

| Class | Purpose |
|-------|---------|
| `ExperimentRunner` | Main orchestrator for experiment execution |
| `ExperimentInput` | Frozen dataclass — fixture, config, patch_set, loop, qa_category, fail_fast |
| `ExperimentOutput` | Frozen dataclass — run_id, status, stories, duration, error |
| `ExperimentStatus` | Enum — PENDING, RUNNING, COMPLETED, FAILED, CANCELLED |
| `ConfigRegistry` | Discovers and loads config templates |
| `ConfigTemplate` | Pydantic model — name, description, providers, raw_config |
| `LoopRegistry` | Discovers and loads loop templates |
| `LoopTemplate` | Pydantic model — name, description, sequence (list of LoopStep) |
| `PatchSetRegistry` | Discovers and loads patch-set manifests |
| `PatchSetManifest` | Pydantic model — name, description, patches, workflow_overrides |
| `FixtureManager` | Discovers and manages fixtures (get, list, filter_by_tags, filter_by_difficulty) |
| `FixtureIsolator` | Deep-copies fixtures for run isolation |
| `ManifestManager` | Manages run manifest lifecycle |
| `MetricsCollector` | Collects and aggregates per-phase metrics |
| `ComparisonGenerator` | Generates comparison reports (compare, generate_markdown, save) |

## Troubleshooting

### Config template not found

```
ConfigError: Config template 'my-config' not found
```

Check:
1. File exists: `ls experiments/configs/my-config.yaml`
2. Internal `name` or `config_name` field matches filename stem
3. YAML is valid: `python -c "import yaml; yaml.safe_load(open('my-config.yaml'))"`

### Fixture not discovered

Check:
1. Fixture is a directory: `ls -la experiments/fixtures/`
2. Directory name is valid (no spaces, starts with letter/underscore)
3. No `.` prefix (hidden directories are skipped)

### Isolation fails

**`IsolationError: Source path does not exist`** — Fixture directory was moved or deleted.

**`IsolationError: Unsafe path in tar archive`** — Tar archive contains path traversal. Recreate:
```bash
cd experiments/fixtures && tar -cvf my-fixture.tar my-fixture/
```

### Comparison fails

**At least 2 runs required** — Provide 2-10 run IDs to compare.

**Run not found** — List available runs: `ls experiments/runs/`

### Metrics show 0 tokens/cost

Expected if the LLM provider doesn't report token usage, benchmarking extraction is disabled, or running in mock mode. Enable benchmarking in config:
```yaml
benchmarking:
  enabled: true
providers:
  helper:
    provider: claude-subprocess
    model: haiku
```

## See Also

- [A/B Testing](ab-testing.md) — Side-by-side variant comparison with git worktree isolation
- [Prerequisites](experiments/prerequisites.md) — Required tools for scoring
- [Workflow Patches](workflow-patches.md) — Creating custom patches
- [Configuration Reference](configuration.md) — Main configuration options
