"""A/B Test Runner - orchestrates A/B workflow experiments.

Runs the same set of stories through two different configurations (variants)
using git worktree isolation, then produces a comparison report.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from bmad_assist.core.config import (
    Config,
    ProviderConfig,
    _reset_config,
    _reset_loop_config,
    get_config,
)
from bmad_assist.core.config.loaders import load_config
from bmad_assist.core.exceptions import ConfigError
from bmad_assist.core.io import BMAD_ORIGINAL_CWD_ENV, get_timestamp, init_run_prompts_dir
from bmad_assist.core.loop.dispatch import execute_phase, init_handlers
from bmad_assist.core.loop.interactive import set_non_interactive
from bmad_assist.core.loop.signals import (
    register_signal_handlers,
    reset_shutdown,
    shutdown_requested,
    unregister_signal_handlers,
)
from bmad_assist.core.paths import _reset_paths, init_paths
from bmad_assist.core.state import (
    Phase,
    State,
    save_state,
    start_epic_timing,
    start_phase_timing,
    start_project_timing,
    start_story_timing,
)
from bmad_assist.core.types import EpicId, parse_epic_id
from bmad_assist.experiments.ab.config import ABTestConfig, ABVariantConfig, StoryRef
from bmad_assist.experiments.ab.worktree import (
    WorktreeInfo,
    checkout_ref,
    cleanup_ab_worktrees,
    create_ab_worktrees,
    validate_fixture_is_git_repo,
    validate_ref_exists,
)
from bmad_assist.experiments.config import ConfigRegistry, ConfigTemplate
from bmad_assist.experiments.fixture import FixtureManager
from bmad_assist.experiments.patchset import PatchSetManifest, PatchSetRegistry
from bmad_assist.experiments.runner import WORKFLOW_TO_PHASE, ExperimentStatus

logger = logging.getLogger(__name__)


def _normalize_phase_to_workflow(phase_name: str) -> str:
    """Convert snake_case phase name to kebab-case workflow name."""
    return phase_name.replace("_", "-")


def _reset_all_singletons() -> None:
    """Reset all global singletons for clean variant execution."""
    _reset_paths()
    _reset_config()
    _reset_loop_config()


@dataclass(frozen=True)
class ABVariantResult:
    """Result of executing one variant of an A/B test."""

    label: str
    status: ExperimentStatus
    stories_attempted: int
    stories_completed: int
    stories_failed: int
    duration_seconds: float
    worktree_path: Path
    result_dir: Path
    error: str | None = None


@dataclass(frozen=True)
class ABTestResult:
    """Complete result of an A/B test."""

    test_name: str
    variant_a: ABVariantResult
    variant_b: ABVariantResult
    result_dir: Path
    comparison_path: Path | None = None
    analysis_path: Path | None = None
    scorecard_a_path: Path | None = None
    scorecard_b_path: Path | None = None


class ABTestRunner:
    """Orchestrates A/B workflow experiments.

    Creates git worktrees for each variant, runs the specified phase sequence
    on each, collects metrics, and generates a comparison report.

    Usage:
        runner = ABTestRunner(experiments_dir, project_root)
        result = runner.run(ab_config)

    """

    def __init__(
        self,
        experiments_dir: Path,
        project_root: Path | None = None,
    ) -> None:
        """Initialize the runner.

        Args:
            experiments_dir: Path to experiments directory.
            project_root: Optional project root for resource resolution.

        """
        self._experiments_dir = experiments_dir
        self._project_root = project_root

        self._config_registry: ConfigRegistry | None = None
        self._patchset_registry: PatchSetRegistry | None = None
        self._fixture_manager: FixtureManager | None = None

    def _ensure_registries(self) -> None:
        """Lazily initialize registries."""
        if self._config_registry is None:
            self._config_registry = ConfigRegistry(
                self._experiments_dir / "configs",
                self._project_root,
            )
        if self._patchset_registry is None:
            self._patchset_registry = PatchSetRegistry(
                self._experiments_dir / "patch-sets",
                self._project_root,
            )
        if self._fixture_manager is None:
            self._fixture_manager = FixtureManager(
                self._experiments_dir / "fixtures",
            )

    def run(self, config: ABTestConfig) -> ABTestResult:
        """Execute a complete A/B test.

        Args:
            config: Validated ABTestConfig.

        Returns:
            ABTestResult with both variant results and comparison.

        """
        self._ensure_registries()
        assert self._fixture_manager is not None  # for type checker

        self._validate_inputs(config)

        fixture_path = self._fixture_manager.get_path(config.fixture)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        result_dir = self._experiments_dir / "ab-results" / f"{config.name}-{timestamp}"
        result_dir.mkdir(parents=True, exist_ok=True)

        # Save test definition for reproducibility
        test_def_path = result_dir / "test-definition.yaml"
        with open(test_def_path, "w") as f:
            yaml.dump(config.model_dump(mode="json"), f, default_flow_style=False)

        base_dir = Path(f"/tmp/bmad-ab-{config.name}-{timestamp}")
        worktree_a: WorktreeInfo | None = None
        worktree_b: WorktreeInfo | None = None

        try:
            first_ref = config.stories[0].ref
            worktree_a, worktree_b = create_ab_worktrees(
                fixture_path=fixture_path,
                base_dir=base_dir,
                ref=first_ref,
                test_name=config.name,
            )

            reset_shutdown()
            register_signal_handlers()

            try:
                # Run variant A
                logger.info("=" * 60)
                logger.info("Running variant A: %s", config.variant_a.label)
                logger.info("=" * 60)

                variant_a_result = self._run_variant(
                    config=config,
                    variant_config=config.variant_a,
                    worktree=worktree_a,
                    result_dir=result_dir / "variant-a",
                    variant_label="A",
                )

                _reset_all_singletons()

                # Run variant B (unless cancelled)
                if not shutdown_requested():
                    logger.info("=" * 60)
                    logger.info("Running variant B: %s", config.variant_b.label)
                    logger.info("=" * 60)

                    variant_b_result = self._run_variant(
                        config=config,
                        variant_config=config.variant_b,
                        worktree=worktree_b,
                        result_dir=result_dir / "variant-b",
                        variant_label="B",
                    )
                else:
                    variant_b_result = ABVariantResult(
                        label=config.variant_b.label,
                        status=ExperimentStatus.CANCELLED,
                        stories_attempted=0,
                        stories_completed=0,
                        stories_failed=0,
                        duration_seconds=0.0,
                        worktree_path=worktree_b.path,
                        result_dir=result_dir / "variant-b",
                        error="Cancelled before variant B started",
                    )

            finally:
                unregister_signal_handlers()
                _reset_all_singletons()
                set_non_interactive(False)

            # Generate comparison report
            comparison_path = None
            if (
                variant_a_result.status == ExperimentStatus.COMPLETED
                and variant_b_result.status == ExperimentStatus.COMPLETED
            ):
                from bmad_assist.experiments.ab.report import generate_ab_comparison

                comparison_path = generate_ab_comparison(
                    config=config,
                    variant_a=variant_a_result,
                    variant_b=variant_b_result,
                    output_path=result_dir / "comparison.md",
                )

            # Generate LLM analysis report
            analysis_path = None
            if config.analysis and comparison_path is not None:
                from bmad_assist.experiments.ab.analysis import generate_ab_analysis

                try:
                    analysis_path = generate_ab_analysis(
                        config=config,
                        result_dir=result_dir,
                        experiments_dir=self._experiments_dir,
                    )
                except Exception:
                    logger.exception("Analysis generation failed")

            # Optional scorecards
            scorecard_a = None
            scorecard_b = None
            if config.scorecard and worktree_a is not None and worktree_b is not None:
                scorecard_a = self._run_scorecard(worktree_a.path, result_dir / "scorecard-a.yaml")
                scorecard_b = self._run_scorecard(worktree_b.path, result_dir / "scorecard-b.yaml")

            self._write_ab_manifest(config, variant_a_result, variant_b_result, result_dir)

            return ABTestResult(
                test_name=config.name,
                variant_a=variant_a_result,
                variant_b=variant_b_result,
                result_dir=result_dir,
                comparison_path=comparison_path,
                analysis_path=analysis_path,
                scorecard_a_path=scorecard_a,
                scorecard_b_path=scorecard_b,
            )

        finally:
            cleanup_ab_worktrees(fixture_path, worktree_a, worktree_b, base_dir)

    def _validate_inputs(self, config: ABTestConfig) -> None:
        """Validate all referenced resources exist."""
        assert self._fixture_manager is not None
        assert self._config_registry is not None
        assert self._patchset_registry is not None

        errors: list[str] = []

        try:
            self._fixture_manager.get(config.fixture)
            fixture_path = self._fixture_manager.get_path(config.fixture)
            # Validate all per-story refs exist in fixture
            validate_fixture_is_git_repo(fixture_path)
            for story_ref in config.stories:
                try:
                    validate_ref_exists(fixture_path, story_ref.ref)
                except Exception:
                    errors.append(
                        f"Story {story_ref.id}: ref '{story_ref.ref}' not found in fixture"
                    )
        except ConfigError as e:
            errors.append(str(e))

        for label, variant in [("A", config.variant_a), ("B", config.variant_b)]:
            try:
                self._config_registry.get(variant.config)
            except ConfigError as e:
                errors.append(f"Variant {label}: {e}")
            try:
                self._patchset_registry.get(variant.patch_set)
            except ConfigError as e:
                errors.append(f"Variant {label}: {e}")
            if variant.customize_set:
                cs_dir = self._experiments_dir / "customize-sets" / variant.customize_set
                if not cs_dir.is_dir():
                    errors.append(f"Variant {label}: Customize set directory not found: {cs_dir}")
            if variant.template_set:
                ts_dir = self._experiments_dir / "templates" / variant.template_set
                if not ts_dir.is_dir():
                    errors.append(f"Variant {label}: Template set directory not found: {ts_dir}")

        if errors:
            raise ConfigError("A/B test validation failed:\n  " + "\n  ".join(errors))

    def _run_variant(
        self,
        config: ABTestConfig,
        variant_config: ABVariantConfig,
        worktree: WorktreeInfo,
        result_dir: Path,
        variant_label: str,
    ) -> ABVariantResult:
        """Run a single variant through all stories and phases."""
        assert self._config_registry is not None
        assert self._patchset_registry is not None

        started = datetime.now(UTC)
        stories_attempted = 0
        stories_completed = 0
        stories_failed = 0
        error: str | None = None

        result_dir.mkdir(parents=True, exist_ok=True)
        worktree_path = worktree.path

        try:
            set_non_interactive(True)
            _reset_all_singletons()

            # Load config template and patchset from registries
            config_template = self._config_registry.get(variant_config.config)
            patchset_manifest = self._patchset_registry.get(variant_config.patch_set)

            # Build config dict and write to worktree as bmad-assist.yaml
            # This makes the worktree self-contained — the config is the sole source.
            config_dict = self._build_config_dict(config_template)
            worktree_config_path = worktree_path / "bmad-assist.yaml"
            with open(worktree_config_path, "w", encoding="utf-8") as f:
                yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)

            load_config(config_dict)
            experiment_config = get_config()

            # Initialize paths for this worktree
            bmad_config_path = worktree_path / "_bmad" / "bmm" / "config.yaml"
            paths_config: dict[str, str] = {}
            if bmad_config_path.exists():
                with open(bmad_config_path) as f:
                    bmad_config = yaml.safe_load(f) or {}
                for key in (
                    "output_folder",
                    "planning_artifacts",
                    "implementation_artifacts",
                    "project_knowledge",
                ):
                    if key in bmad_config:
                        paths_config[key] = bmad_config[key]

            init_paths(worktree_path, paths_config)

            # Initialize handlers
            init_handlers(experiment_config, worktree_path)

            # Copy compiled template cache from project root
            if self._project_root:
                source_cache = self._project_root / ".bmad-assist" / "cache"
                if source_cache.exists() and source_cache.is_dir():
                    dest_cache = worktree_path / ".bmad-assist" / "cache"
                    dest_cache.mkdir(parents=True, exist_ok=True)
                    for cache_file in source_cache.glob("*.tpl.xml*"):
                        shutil.copy2(cache_file, dest_cache / cache_file.name)

            # Copy customize set to worktree for v6.4 customization resolver
            self._apply_customize_set(variant_config, worktree_path)

            # Copy template set to worktree cache (overwrites project cache)
            self._apply_template_set(variant_config, worktree_path)

            # Initialize run prompts directory
            run_timestamp = get_timestamp()
            init_run_prompts_dir(worktree_path, run_timestamp)

            # Set BMAD_ORIGINAL_CWD for patch discovery
            if self._project_root:
                os.environ[BMAD_ORIGINAL_CWD_ENV] = str(self._project_root)

            # Initialize state
            first_story = config.stories[0]
            first_epic = parse_epic_id(first_story.id.split(".")[0])
            first_phase_workflow = _normalize_phase_to_workflow(config.phases[0])
            first_phase = WORKFLOW_TO_PHASE.get(first_phase_workflow, Phase.CREATE_STORY)

            state = State(
                current_epic=first_epic,
                current_story=first_story.id,
                current_phase=first_phase,
            )
            start_project_timing(state)

            # Group stories by epic (preserving StoryRef objects)
            stories_by_epic: dict[EpicId, list[StoryRef]] = {}
            for story_ref in config.stories:
                epic_part = story_ref.id.split(".")[0]
                epic_id = parse_epic_id(epic_part)
                stories_by_epic.setdefault(epic_id, []).append(story_ref)

            # Gate AB phases through variant's loop config (if defined).
            # When a variant's config has a `loop:` section, only phases
            # present in that loop config are executed. This lets variants
            # differ in which phases run (e.g., QA phases in one, not other).
            effective_phases = list(config.phases)
            loop_data = config_dict.get("loop")
            if loop_data and isinstance(loop_data, dict):
                try:
                    from bmad_assist.core.config.models.loop import LoopConfig as LoopModel

                    variant_loop = LoopModel.model_validate(loop_data)
                    allowed = set(
                        variant_loop.epic_setup + variant_loop.story + variant_loop.epic_teardown
                    )
                    filtered = [p for p in config.phases if p in allowed]
                    if len(filtered) < len(config.phases):
                        skipped = [p for p in config.phases if p not in allowed]
                        logger.info(
                            "[%s] Loop config gates phases: running %s, skipped %s",
                            variant_label,
                            filtered,
                            skipped,
                        )
                    effective_phases = filtered
                except Exception as e:
                    logger.warning(
                        "[%s] Failed to parse loop config for phase gating: %s",
                        variant_label,
                        e,
                    )

            # Execute stories with per-story ref checkout and snapshots
            is_first_story = True
            for epic_id, epic_stories in stories_by_epic.items():
                if shutdown_requested():
                    break

                state.current_epic = epic_id
                start_epic_timing(state)

                for story_ref in epic_stories:
                    if shutdown_requested():
                        break

                    # Checkout story-specific ref (skip first, worktree starts there)
                    if not is_first_story:
                        checkout_ref(worktree_path, story_ref.ref)
                    is_first_story = False

                    # Record pre-run file state for per-story snapshot
                    pre_files = self._collect_worktree_files(worktree_path)

                    story_failed = False
                    state.current_story = story_ref.id
                    start_story_timing(state)
                    stories_attempted += 1

                    logger.info(
                        "[%s/%s] Story %s (ref: %s)",
                        variant_label,
                        variant_config.label,
                        story_ref.id,
                        story_ref.ref,
                    )

                    for phase_name in effective_phases:
                        if shutdown_requested():
                            break

                        workflow = _normalize_phase_to_workflow(phase_name)
                        phase = WORKFLOW_TO_PHASE.get(workflow)
                        if phase is None:
                            logger.warning("Skipping unknown phase: %s", phase_name)
                            continue

                        state.current_phase = phase
                        start_phase_timing(state)

                        # Apply patches
                        self._apply_patches(workflow, patchset_manifest, worktree_path)

                        # Execute phase
                        result = execute_phase(state)

                        if not result.success:
                            story_failed = True
                            logger.warning(
                                "[%s] Story %s failed at %s: %s",
                                variant_label,
                                story_ref.id,
                                phase_name,
                                result.error,
                            )
                            break

                    if story_failed:
                        stories_failed += 1
                    else:
                        stories_completed += 1
                        if story_ref.id not in state.completed_stories:
                            state.completed_stories.append(story_ref.id)

                    # Per-story snapshot: only files produced during this story
                    story_result_dir = result_dir / f"story-{story_ref.id}"
                    self._snapshot_story_artifacts(worktree_path, story_result_dir, pre_files)

                    save_state(state, result_dir / "state.yaml")

            status = ExperimentStatus.COMPLETED
            if shutdown_requested():
                status = ExperimentStatus.CANCELLED
            elif stories_failed > 0 and stories_completed == 0:
                status = ExperimentStatus.FAILED

        except Exception as e:
            status = ExperimentStatus.FAILED
            error = str(e)
            logger.exception("[%s] Variant failed: %s", variant_label, e)

        duration = (datetime.now(UTC) - started).total_seconds()

        return ABVariantResult(
            label=variant_config.label,
            status=status,
            stories_attempted=stories_attempted,
            stories_completed=stories_completed,
            stories_failed=stories_failed,
            duration_seconds=duration,
            worktree_path=worktree_path,
            result_dir=result_dir,
            error=error,
        )

    def _build_config_dict(self, template: ConfigTemplate) -> dict[str, object]:
        """Build config dict from template for load_config().

        For full configs (config_name), passes through the entire raw config.
        For legacy templates (name + providers only), builds minimal dict.

        """
        if template.raw_config:
            config_dict = dict(template.raw_config)
            for key in ("name", "config_name", "description"):
                config_dict.pop(key, None)
            return config_dict
        # Legacy fallback: build minimal from providers
        if template.providers is not None:
            return Config(
                providers=ProviderConfig(
                    master=template.providers.master,
                    multi=template.providers.multi,
                ),
            ).model_dump()
        return {}

    def _apply_customize_set(
        self,
        variant_config: ABVariantConfig,
        worktree_path: Path,
    ) -> None:
        """Copy v6.4 customize.toml files to worktree's `_bmad/custom/` directory.

        Each ``*.toml`` (and ``*.user.toml``) file in the customize-set source
        directory is copied through unchanged into ``{worktree}/_bmad/custom/``.
        File names are preserved — e.g. ``bmad-code-review.toml`` lands at
        ``_bmad/custom/bmad-code-review.toml``, where the v6.4 customization
        resolver discovers it automatically (no compiler changes needed).

        """
        if not variant_config.customize_set:
            return

        source_dir = self._experiments_dir / "customize-sets" / variant_config.customize_set
        if not source_dir.is_dir():
            logger.warning("Customize set directory not found: %s", source_dir)
            return

        dest_dir = worktree_path / "_bmad" / "custom"
        dest_dir.mkdir(parents=True, exist_ok=True)

        for toml_file in source_dir.glob("*.toml"):
            if not toml_file.is_file():
                continue
            dest = dest_dir / toml_file.name
            shutil.copy2(toml_file, dest)
            logger.info(
                "Copied customize file '%s' → %s",
                toml_file.name,
                dest,
            )

    def _apply_template_set(
        self,
        variant_config: ABVariantConfig,
        worktree_path: Path,
    ) -> None:
        """Copy pre-compiled templates to worktree cache.

        Copies `.tpl.xml` and `.tpl.xml.meta.yaml` files from the experiment
        template set into the worktree's `.bmad-assist/cache/` directory.
        Called after project cache copy so template_set files take priority.

        """
        if not variant_config.template_set:
            return

        source_dir = self._experiments_dir / "templates" / variant_config.template_set
        if not source_dir.is_dir():
            logger.warning("Template set directory not found: %s", source_dir)
            return

        dest_cache = worktree_path / ".bmad-assist" / "cache"
        dest_cache.mkdir(parents=True, exist_ok=True)

        for template_file in source_dir.glob("*.tpl.xml*"):
            shutil.copy2(template_file, dest_cache / template_file.name)
            logger.info(
                "Copied template '%s' → %s",
                template_file.name,
                dest_cache / template_file.name,
            )

    def _apply_patches(
        self,
        workflow: str,
        patchset: PatchSetManifest,
        worktree_path: Path,
    ) -> None:
        """Apply patches from patch-set to worktree."""
        patch_dir = worktree_path / ".bmad-assist" / "patches"

        if workflow in patchset.workflow_overrides:
            override_path_str = patchset.workflow_overrides[workflow]
            override_path = self._resolve_patch_path(override_path_str)
            if override_path.exists() and override_path.is_dir():
                override_dest = worktree_path / ".bmad-assist" / "overrides" / workflow
                if override_dest.exists():
                    shutil.rmtree(override_dest)
                shutil.copytree(override_path, override_dest, dirs_exist_ok=True)
            return

        if workflow in patchset.patches:
            patch_path_str = patchset.patches[workflow]
            if patch_path_str is None:
                return

            patch_path = self._resolve_patch_path(patch_path_str)
            if patch_path.exists() and patch_path.is_file():
                patch_dir.mkdir(parents=True, exist_ok=True)
                dest = patch_dir / f"{workflow}.patch.yaml"
                shutil.copy2(patch_path, dest)
            else:
                logger.warning("Patch file for '%s' not found: %s", workflow, patch_path)

    def _resolve_patch_path(self, path_str: str) -> Path:
        """Resolve a patch file path."""
        path = Path(path_str)

        if path_str.startswith("~"):
            return path.expanduser().resolve()
        if path.is_absolute():
            return path.resolve()
        if self._project_root and (self._project_root / path).exists():
            return (self._project_root / path).resolve()
        if (self._experiments_dir / path).exists():
            return (self._experiments_dir / path).resolve()
        return path.resolve()

    def _run_scorecard(self, worktree_path: Path, output_path: Path) -> Path | None:
        """Run scorecard against a variant's worktree."""
        import subprocess
        import sys

        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bmad_assist.experiments.testing.scorecard",
                    str(worktree_path),
                    "--output",
                    str(output_path),
                ],
                cwd=self._project_root or self._experiments_dir.parent,
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode == 0:
                logger.info("Scorecard generated: %s", output_path)
                return output_path
            else:
                logger.warning("Scorecard failed: %s", result.stderr[:500])
                return None
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning("Scorecard execution error: %s", e)
            return None

    def _collect_worktree_files(self, worktree_path: Path) -> set[str]:
        """Collect all file paths in artifact and runtime directories."""
        files: set[str] = set()
        for base in (
            "_bmad-output/implementation-artifacts",
            "_bmad-output/qa-artifacts",
            ".bmad-assist",
        ):
            base_path = worktree_path / base
            if base_path.is_dir():
                for f in base_path.rglob("*"):
                    if f.is_file():
                        files.add(str(f.relative_to(worktree_path)))
        return files

    def _snapshot_story_artifacts(
        self,
        worktree_path: Path,
        story_result_dir: Path,
        pre_files: set[str],
    ) -> None:
        """Snapshot only files produced during a single story's phases.

        Compares current file state against pre-run snapshot to identify
        new files, then copies them to a per-story result directory.
        Excludes compiled templates.

        """
        post_files = self._collect_worktree_files(worktree_path)
        new_files = post_files - pre_files

        copied = 0
        for rel_path in sorted(new_files):
            if rel_path.startswith("_bmad-output/implementation-artifacts/"):
                dest_rel = "artifacts/" + rel_path[len("_bmad-output/implementation-artifacts/") :]
            elif rel_path.startswith("_bmad-output/qa-artifacts/"):
                dest_rel = "qa-artifacts/" + rel_path[len("_bmad-output/qa-artifacts/") :]
            elif rel_path.startswith(".bmad-assist/"):
                suffix = rel_path[len(".bmad-assist/") :]
                if suffix.endswith(".tpl.xml") or suffix.endswith(".tpl.xml.meta.yaml"):
                    continue
                dest_rel = "bmad-assist/" + suffix
            else:
                continue

            src = worktree_path / rel_path
            if not src.is_file():
                continue
            dest = story_result_dir / dest_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            copied += 1

        if copied:
            logger.info("Snapshotted %d files for story to %s", copied, story_result_dir)

    def _write_ab_manifest(
        self,
        config: ABTestConfig,
        variant_a: ABVariantResult,
        variant_b: ABVariantResult,
        result_dir: Path,
    ) -> None:
        """Write the overall AB test manifest to results directory."""
        manifest = {
            "test_name": config.name,
            "fixture": config.fixture,
            "stories": [{"id": s.id, "ref": s.ref} for s in config.stories],
            "phases": list(config.phases),
            "variant_a": {
                "label": variant_a.label,
                "status": variant_a.status.value,
                "stories_completed": variant_a.stories_completed,
                "stories_failed": variant_a.stories_failed,
                "duration_seconds": round(variant_a.duration_seconds, 2),
                "error": variant_a.error,
            },
            "variant_b": {
                "label": variant_b.label,
                "status": variant_b.status.value,
                "stories_completed": variant_b.stories_completed,
                "stories_failed": variant_b.stories_failed,
                "duration_seconds": round(variant_b.duration_seconds, 2),
                "error": variant_b.error,
            },
        }

        manifest_path = result_dir / "manifest.yaml"
        temp_path = manifest_path.with_suffix(".yaml.tmp")
        try:
            with open(temp_path, "w") as f:
                yaml.dump(manifest, f, default_flow_style=False, sort_keys=False)
            os.replace(temp_path, manifest_path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise
