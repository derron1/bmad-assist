# CLAUDE.md — Working notes for bmad-assist

## What this project is

bmad-assist is a Python orchestrator that drives BMAD-METHOD V6+ skills through a story-development loop, layering parallel-LLM validation, deep-verify, an A/B experiment harness, and TEA (Test Architect Enterprise) integration on top of upstream BMAD. It consumes upstream BMAD skills bundled under `src/bmad_assist/skills/` and adds the orchestration layer — a Master LLM writes code, multiple Multi LLMs review/validate in parallel, and synthesis phases consolidate verdicts. CLI entrypoint: `bmad-assist` (see `[project.scripts]` in `pyproject.toml`).

## Layout (high-level only)

- `src/bmad_assist/` — package root
  - `cli.py`, `cli_start_point.py`, `__main__.py` — Typer CLI entrypoints
  - `core/` — loop state machine + handlers
    - `loop/` — runner, dispatch, guardian, story/epic transitions, synthesis_contract
    - `loop/handlers/` — one handler per phase (create_story, validate_story, dev_story, code_review, synthesis variants)
    - `config/` — pydantic config models (frozen, extra fields silently ignored)
  - `compiler/` — prompt compiler (workflow + SKILL.md → prompt XML); `variables/`, `patching/`, `strategic_context.py`
  - `skill_layout/` — BMAD v6.4+ skill resolver: `resolver.py`, `parser.py`, `manifest.py`, `variable_resolver.py`
  - `skills/` — bundled BMAD skill copies (overwritten on bootstrap)
  - `testarch/` — TEA module integration (engagement, atdd_eligibility, knowledge_base)
  - `validation/`, `providers/`, `experiments/`, `dashboard/`, `tui/`
  - `deep_verify/`, `antipatterns/`, `code_review/`, `qa/`, `security/`, `sprint/`, `retrospective/`
- `tests/` — pytest, ~1200 tests, ~25s wall clock
- `docs/` — user-facing docs (configuration, providers, ab-testing, tea-configuration, ...)
- `experiments/` — A/B manifests, fixtures, scorecards
- `_bmad/`, `_bmad-output/` — local project working dirs (gitignored output)

## Key concepts

- **Loop phases (story-level)**: `create_story` → `validate_story` → `validate_story_synthesis` → `atdd` → `dev_story` → `code_review` → `code_review_synthesis` → `test_review`. Plus epic-level: `epic_setup` / `epic_teardown` (TEA-driven).
- **Master / Multi pattern**: One Master LLM writes (create_story, dev_story, all synthesis). Multi LLMs run in parallel for `validate_story` and `code_review` only — they are read-only and never touch files.
- **Skill layout v6.4+ only**: As of 0.6.0 the legacy `workflow.yaml` + `instructions.xml` pipeline is gone. Phase 1 → 7.2 migration completed 2026-04 to 2026-05. Every workflow runs through `SkillLayoutCompilerBase`.
- **Customization layers** (merged in order):
  1. Bundled `customize.toml` in `src/bmad_assist/skills/<skill>/` — framework default, top of file says "DO NOT EDIT — overwritten on every update"
  2. Project `_bmad/custom/<skill>.toml` — project-level override
  3. Project `_bmad/custom/<skill>.user.toml` — user-level override
- **Customization surface**: NEVER edit bundled `customize.toml` at runtime — it is overwritten on bootstrap. Project overrides go in `{project-root}/_bmad/custom/`. The bundled file IS source-of-truth for the framework default; edit there only when knowingly changing the default.
- **Variable substitution** — two layers:
  1. [skill_layout/variable_resolver.py](src/bmad_assist/skill_layout/variable_resolver.py) — path aliases + customization tokens
  2. [compiler/variable_utils.py](src/bmad_assist/compiler/variable_utils.py) `substitute_variables` — `{var}` and `{{var}}` from the resolved_variables dict
  Unknown tokens are left intact for downstream — by design.
- **Providers**: `claude-subprocess` (Anthropic CLI), `codex` (OpenAI), `gemini`, `opencode`/`opencode-sdk`, `amp`, `cursor-agent`, `copilot`. Per-phase routing via `phase_models:` in `{project}/bmad-assist.yaml`. Fallback chains supported per-provider.
- **Deep Verify**: parallel critical-path validation alongside main validators; verdict aggregation in [core/loop/handlers/validate_story.py](src/bmad_assist/core/loop/handlers/validate_story.py).
- **A/B harness**: `experiments/` consumed by `bmad-assist experiment ab <manifest.yaml>`. Runs variants over fixtures with git-worktree isolation; scoring via `experiments/evaluation/` adapters.
- **Bootstrap**: every run version-stamps installed skills and auto-refreshes on mismatch ("updated legacy → 0.6.0" log line).

## Where to look for X

- "Why is variable `{Y}` not substituted?" → [compiler/variables/core.py](src/bmad_assist/compiler/variables/core.py) `resolve_variables` (recently gained Step 4.5 for upstream config merge)
- "How does the orchestrator decide phases?" → [core/loop/runner.py](src/bmad_assist/core/loop/runner.py) + [core/loop/guardian.py](src/bmad_assist/core/loop/guardian.py) + `dispatch.py`
- "Which model runs which phase?" → `bmad-assist.yaml` `phase_models:` (consumer project) + [core/config/models/main.py](src/bmad_assist/core/config/models/main.py) for schema
- "How does ATDD eligibility work?" → `testarch/engagement.py` + `testarch/atdd_eligibility.py`
- "What does benchmarking record?" → `core/loop/handlers/*.py` emit `eval-*.yaml` records to `{output}/benchmarks/`
- "Where do synthesis verdicts come from?" → [core/loop/synthesis_contract.py](src/bmad_assist/core/loop/synthesis_contract.py) + `validation/synthesis_parser.py`
- "How are interactive BMAD steps stripped?" → `compiler/patching/` + `default_patches/`
- "Where is the skill resolver entry?" → [skill_layout/resolver.py](src/bmad_assist/skill_layout/resolver.py)

## Conventions

- **Tests**: `pytest` from repo root (~1200 tests, ~25s). Slow tests deselected by default (`-m "not slow"`). LLM-friendly invocation: `pytest -q --tb=line --no-header`.
- **Lint/format**: `ruff format` + `ruff check src/`. Pre-commit hook runs `ruff format` and frequently reformats on first commit — just retry the commit (see auto-memory).
- **Types**: strict mypy (`disallow_untyped_defs = true`, `strict = true`). Pydantic models throughout `core/config/models/`. Frozen ConfigDict — extra fields silently ignored on parse.
- **Logging**: standard `logging` module. Debug logs emit JSONL to `~/.bmad-assist/debug/json/` when `--debug` is set.
- **Phase results**: structured `PhaseResult` (success/fail/skip with reason). Provider CLI errors surface as `PhaseResult.fail` (recent change in 71c10fe).
- **Workflow naming**: canonical `bmad-<name>` ids only (e.g. `bmad-create-story`). 0.5.x short-name aliases were removed in 0.6.0.
- **Output extraction**: validators/reviewers write to stdout with markers (`<!-- VALIDATION_REPORT_START -->`) for orchestrator extraction — never to files.

## Active workstreams (as of git log)

- Phase 7.2 skill-layout migration: COMPLETE. All bundled skills run through `SkillLayoutCompilerBase`. Legacy `workflow.yaml` pipeline deleted.
- Recent perf: pre-flight slug consistency for create_story; pre-trim validator reports before synthesis cache; skip ESLint/typecheck pre-commit on docs-only diffs.
- Recent fix: provider error termination is now structured. claude-subprocess surfaces CLI errors as `PhaseResult.fail` with reason.
- Recent fix: create_story requires fresh story-file mtime to count as success (catches silent-no-write failures).
- Recent fix: validation strips activation preamble, fails on empty validator output.
- Bootstrap version-stamps installed skills + auto-refreshes on mismatch.
- Current branch (`feat/budget-tracing-resilience`) has WIP on synthesis resolution and validation prompts.

## Common gotchas

- **Bootstrap re-syncs bundled skills on every run.** Per-project `customize.toml` at `.claude/skills/<skill>/customize.toml` is preserved if user-modified; the SOURCE bundled `customize.toml` in `src/bmad_assist/skills/` is NOT preserved — direct edits there ARE the framework default.
- **Tri-modal skills need `_detect_tri_modal()` on the synthetic IR.** `SkillLayoutCompilerBase._build_or_run` populates the tri-modal fields when constructing `WorkflowIR`; without this, every testarch skill falls into the "macro" path, mission renders `Mode: None`, and the agent halts at the SKILL's `[C/R/V/E]` menu instead of being routed into `step-01-*`. Fixed by populating tri-modal fields from `parser._detect_tri_modal(skill_dir)` — keep the call when adding new skill compilers.
- **Branch-switch failures abort silently** and continue on the current branch. Don't trust the branch name in mid-run logs — verify with `git status`.
- **Two-step variable substitution** means an unresolved `{var}` may be intentional (deferred to a later layer). Trace through both `skill_layout/variable_resolver.py` and `compiler/variable_utils.py` before assuming a bug.
- **Pydantic frozen configs silently ignore unknown fields.** Misspelled keys in `bmad-assist.yaml` will not raise — they will be dropped quietly.
- **Platform**: POSIX-only (uses `os.killpg`, `signal.SIGKILL`, `start_new_session`). On Windows use WSL2 with project on Linux filesystem.

## Don't do

- Don't add per-project workarounds in consumer projects (e.g. `algo/_bmad/custom/`) for bugs that are framework-level concerns. Fix in `src/bmad_assist/`.
- Don't run with `--no-verify` or skip pre-commit hooks. Ruff format may reformat — that is expected, just retry the commit.
- Don't edit bundled `SKILL.md` files unless you are knowingly forking from upstream BMAD.
- Don't edit `src/bmad_assist/skills/<skill>/customize.toml` thinking it is "just a config" — it is the framework default and lands in every consumer project on bootstrap.
- Don't rely on legacy short-name workflow ids (`create-story`) — they were removed in 0.6.0. Use `bmad-create-story`.
- Don't introduce new `workflow.yaml` / `instructions.xml` paths — that pipeline was deleted in Phase 6.
