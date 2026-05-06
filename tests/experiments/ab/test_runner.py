"""Tests for A/B test runner.

Tests cover:
- _normalize_phase_to_workflow conversion
- _reset_all_singletons calls all three resets
- ABVariantResult / ABTestResult dataclasses
- ABTestRunner._validate_inputs error accumulation
- ABTestRunner.run orchestration (mocked execute_phase)
- Signal cancellation between variants
- _write_ab_manifest output
"""

import os
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from bmad_assist.core.exceptions import ConfigError
from bmad_assist.experiments.ab.config import ABTestConfig, ABVariantConfig, StoryRef
from bmad_assist.experiments.ab.runner import (
    ABTestResult,
    ABTestRunner,
    ABVariantResult,
    _normalize_phase_to_workflow,
    _reset_all_singletons,
)
from bmad_assist.experiments.runner import ExperimentStatus


class TestNormalizePhaseToWorkflow:
    """Tests for _normalize_phase_to_workflow."""

    def test_snake_to_kebab(self) -> None:
        """Convert snake_case to kebab-case."""
        assert _normalize_phase_to_workflow("create_story") == "create-story"

    def test_already_kebab(self) -> None:
        """Already kebab-case passes through."""
        assert _normalize_phase_to_workflow("create-story") == "create-story"

    def test_multiple_underscores(self) -> None:
        """Multiple underscores all converted."""
        assert (
            _normalize_phase_to_workflow("validate_story_synthesis") == "validate-story-synthesis"
        )


class TestResetAllSingletons:
    """Tests for _reset_all_singletons."""

    @patch("bmad_assist.experiments.ab.runner._reset_loop_config")
    @patch("bmad_assist.experiments.ab.runner._reset_config")
    @patch("bmad_assist.experiments.ab.runner._reset_paths")
    def test_calls_all_three(
        self,
        mock_paths: MagicMock,
        mock_config: MagicMock,
        mock_loop: MagicMock,
    ) -> None:
        """All three singleton reset functions are called."""
        _reset_all_singletons()
        mock_paths.assert_called_once()
        mock_config.assert_called_once()
        mock_loop.assert_called_once()


class TestABVariantResult:
    """Tests for ABVariantResult dataclass."""

    def test_create(self, tmp_path: Path) -> None:
        """Create a variant result with all fields."""
        r = ABVariantResult(
            label="baseline",
            status=ExperimentStatus.COMPLETED,
            stories_attempted=3,
            stories_completed=2,
            stories_failed=1,
            duration_seconds=42.5,
            worktree_path=tmp_path / "wt",
            result_dir=tmp_path / "result",
            error=None,
        )
        assert r.label == "baseline"
        assert r.stories_completed == 2
        assert r.error is None

    def test_frozen(self, tmp_path: Path) -> None:
        """Frozen dataclass rejects attribute mutation."""
        r = ABVariantResult(
            label="x",
            status=ExperimentStatus.COMPLETED,
            stories_attempted=0,
            stories_completed=0,
            stories_failed=0,
            duration_seconds=0,
            worktree_path=tmp_path,
            result_dir=tmp_path,
        )
        with pytest.raises(FrozenInstanceError):
            r.label = "y"  # type: ignore[misc]

    def test_with_error(self, tmp_path: Path) -> None:
        """Variant result stores error message."""
        r = ABVariantResult(
            label="x",
            status=ExperimentStatus.FAILED,
            stories_attempted=1,
            stories_completed=0,
            stories_failed=1,
            duration_seconds=5.0,
            worktree_path=tmp_path,
            result_dir=tmp_path,
            error="something broke",
        )
        assert r.error == "something broke"


class TestABTestResult:
    """Tests for ABTestResult dataclass."""

    def test_create(self, tmp_path: Path) -> None:
        """Create a test result with optional fields defaulting to None."""
        va = ABVariantResult(
            label="a",
            status=ExperimentStatus.COMPLETED,
            stories_attempted=1,
            stories_completed=1,
            stories_failed=0,
            duration_seconds=1.0,
            worktree_path=tmp_path,
            result_dir=tmp_path / "a",
        )
        vb = ABVariantResult(
            label="b",
            status=ExperimentStatus.COMPLETED,
            stories_attempted=1,
            stories_completed=1,
            stories_failed=0,
            duration_seconds=2.0,
            worktree_path=tmp_path,
            result_dir=tmp_path / "b",
        )
        result = ABTestResult(
            test_name="test",
            variant_a=va,
            variant_b=vb,
            result_dir=tmp_path,
        )
        assert result.test_name == "test"
        assert result.comparison_path is None
        assert result.scorecard_a_path is None


def _make_ab_config(**overrides: object) -> ABTestConfig:
    """Build an ABTestConfig with sensible defaults."""
    defaults: dict[str, object] = {
        "name": "test-ab",
        "fixture": "minimal",
        "stories": [StoryRef(id="3.1", ref="HEAD")],
        "phases": ["create-story"],
        "variant_a": ABVariantConfig(label="baseline", config="opus-solo", patch_set="baseline"),
        "variant_b": ABVariantConfig(label="experimental", config="haiku-solo", patch_set="exp"),
    }
    defaults.update(overrides)
    return ABTestConfig(**defaults)  # type: ignore[arg-type]


class TestABTestRunnerValidateInputs:
    """Tests for ABTestRunner._validate_inputs."""

    def test_missing_fixture_raises(self, tmp_path: Path) -> None:
        """Raise ConfigError if fixture is not found."""
        exp_dir = tmp_path / "experiments"
        (exp_dir / "configs").mkdir(parents=True)
        (exp_dir / "patch-sets").mkdir(parents=True)
        (exp_dir / "fixtures").mkdir(parents=True)

        runner = ABTestRunner(exp_dir)
        runner._ensure_registries()

        config = _make_ab_config(fixture="nonexistent")

        with pytest.raises(ConfigError, match="validation failed"):
            runner._validate_inputs(config)

    def test_missing_config_template_raises(self, tmp_path: Path) -> None:
        """Raise ConfigError if config template is not found."""
        exp_dir = tmp_path / "experiments"
        (exp_dir / "configs").mkdir(parents=True)
        (exp_dir / "patch-sets").mkdir(parents=True)

        # Create a valid fixture directory with git repo
        fixture_dir = exp_dir / "fixtures" / "minimal"
        fixture_dir.mkdir(parents=True)
        _init_fixture_git(fixture_dir)

        runner = ABTestRunner(exp_dir)
        runner._ensure_registries()

        config = _make_ab_config()

        with pytest.raises(ConfigError, match="validation failed"):
            runner._validate_inputs(config)


class TestABTestRunnerValidateCustomizeSet:
    """Tests for customize_set and template_set validation in _validate_inputs."""

    def test_missing_customize_set_raises(self, tmp_path: Path) -> None:
        """Raise ConfigError if customize_set directory doesn't exist."""
        exp_dir = _setup_experiment_dir(tmp_path)

        runner = ABTestRunner(exp_dir)
        runner._ensure_registries()

        config = _make_ab_config(
            variant_a=ABVariantConfig(
                label="baseline",
                config="opus-solo",
                patch_set="baseline",
                customize_set="nonexistent",
            ),
        )

        with pytest.raises(ConfigError, match="Customize set directory not found"):
            runner._validate_inputs(config)

    def test_missing_template_set_raises(self, tmp_path: Path) -> None:
        """Raise ConfigError if template_set directory doesn't exist."""
        exp_dir = _setup_experiment_dir(tmp_path)

        runner = ABTestRunner(exp_dir)
        runner._ensure_registries()

        config = _make_ab_config(
            variant_b=ABVariantConfig(
                label="experimental",
                config="haiku-solo",
                patch_set="exp",
                template_set="nonexistent",
            ),
        )

        with pytest.raises(ConfigError, match="Template set directory not found"):
            runner._validate_inputs(config)

    def test_none_sets_pass_validation(self, tmp_path: Path) -> None:
        """None customize_set and template_set pass validation (optional)."""
        exp_dir = _setup_experiment_dir(tmp_path)

        runner = ABTestRunner(exp_dir)
        runner._ensure_registries()

        config = _make_ab_config()
        # Should not raise — both sets are None by default
        runner._validate_inputs(config)

    def test_existing_customize_set_passes(self, tmp_path: Path) -> None:
        """Valid customize_set directory passes validation."""
        exp_dir = _setup_experiment_dir(tmp_path)
        (exp_dir / "customize-sets" / "agents-team-frame").mkdir(parents=True)

        runner = ABTestRunner(exp_dir)
        runner._ensure_registries()

        config = _make_ab_config(
            variant_a=ABVariantConfig(
                label="baseline",
                config="opus-solo",
                patch_set="baseline",
                customize_set="agents-team-frame",
            ),
        )
        # Should not raise
        runner._validate_inputs(config)

    def test_existing_template_set_passes(self, tmp_path: Path) -> None:
        """Valid template_set directory passes validation."""
        exp_dir = _setup_experiment_dir(tmp_path)
        (exp_dir / "templates" / "optimized-v1").mkdir(parents=True)

        runner = ABTestRunner(exp_dir)
        runner._ensure_registries()

        config = _make_ab_config(
            variant_b=ABVariantConfig(
                label="experimental",
                config="haiku-solo",
                patch_set="exp",
                template_set="optimized-v1",
            ),
        )
        # Should not raise
        runner._validate_inputs(config)


class TestApplyCustomizeSet:
    """Tests for ABTestRunner._apply_customize_set."""

    def test_copies_toml_files(self, tmp_path: Path) -> None:
        """TOML files are copied passthrough into worktree's _bmad/custom/."""
        exp_dir = tmp_path / "experiments"
        cs_dir = exp_dir / "customize-sets" / "agents-team-frame"
        cs_dir.mkdir(parents=True)
        (cs_dir / "bmad-code-review.toml").write_text(
            '[workflow]\npersistent_facts = ["team finding"]\n'
        )

        worktree = tmp_path / "worktree"
        worktree.mkdir()

        runner = ABTestRunner(exp_dir)
        variant = ABVariantConfig(
            label="x",
            config="c",
            patch_set="p",
            customize_set="agents-team-frame",
        )
        runner._apply_customize_set(variant, worktree)

        dest = worktree / "_bmad" / "custom" / "bmad-code-review.toml"
        assert dest.is_file()
        assert "team finding" in dest.read_text()

    def test_copies_user_toml_files(self, tmp_path: Path) -> None:
        """``*.user.toml`` files are also copied passthrough."""
        exp_dir = tmp_path / "experiments"
        cs_dir = exp_dir / "customize-sets" / "personal"
        cs_dir.mkdir(parents=True)
        (cs_dir / "bmad-dev-story.toml").write_text("[workflow]\n")
        (cs_dir / "bmad-dev-story.user.toml").write_text("[workflow]\n# user override\n")

        worktree = tmp_path / "worktree"
        worktree.mkdir()

        runner = ABTestRunner(exp_dir)
        variant = ABVariantConfig(
            label="x",
            config="c",
            patch_set="p",
            customize_set="personal",
        )
        runner._apply_customize_set(variant, worktree)

        custom_dir = worktree / "_bmad" / "custom"
        assert (custom_dir / "bmad-dev-story.toml").is_file()
        assert (custom_dir / "bmad-dev-story.user.toml").is_file()
        assert "user override" in (custom_dir / "bmad-dev-story.user.toml").read_text()

    def test_missing_source_dir_warns_and_skips(self, tmp_path: Path) -> None:
        """Missing source directory logs a warning and is a no-op."""
        exp_dir = tmp_path / "experiments"
        exp_dir.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        runner = ABTestRunner(exp_dir)
        variant = ABVariantConfig(
            label="x",
            config="c",
            patch_set="p",
            customize_set="missing",
        )
        # Must not raise
        runner._apply_customize_set(variant, worktree)
        assert not (worktree / "_bmad").exists()

    def test_noop_when_none(self, tmp_path: Path) -> None:
        """No-op when customize_set is None."""
        runner = ABTestRunner(tmp_path)
        variant = ABVariantConfig(label="x", config="c", patch_set="p")
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        runner._apply_customize_set(variant, worktree)
        assert not (worktree / "_bmad").exists()


class TestApplyTemplateSet:
    """Tests for ABTestRunner._apply_template_set."""

    def test_copies_template_files(self, tmp_path: Path) -> None:
        """Template .tpl.xml and .meta.yaml files are copied to worktree cache."""
        exp_dir = tmp_path / "experiments"
        ts_dir = exp_dir / "templates" / "optimized-v1"
        ts_dir.mkdir(parents=True)
        (ts_dir / "create-story.tpl.xml").write_text("<template/>")
        (ts_dir / "create-story.tpl.xml.meta.yaml").write_text("hash: abc\n")

        worktree = tmp_path / "worktree"
        worktree.mkdir()

        runner = ABTestRunner(exp_dir)
        variant = ABVariantConfig(
            label="x",
            config="c",
            patch_set="p",
            template_set="optimized-v1",
        )
        runner._apply_template_set(variant, worktree)

        cache = worktree / ".bmad-assist" / "cache"
        assert (cache / "create-story.tpl.xml").exists()
        assert (cache / "create-story.tpl.xml.meta.yaml").exists()

    def test_noop_when_none(self, tmp_path: Path) -> None:
        """No-op when template_set is None."""
        runner = ABTestRunner(tmp_path)
        variant = ABVariantConfig(label="x", config="c", patch_set="p")
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        runner._apply_template_set(variant, worktree)
        assert not (worktree / ".bmad-assist").exists()

    def test_overwrites_existing_cache(self, tmp_path: Path) -> None:
        """Template set files overwrite existing cache files."""
        exp_dir = tmp_path / "experiments"
        ts_dir = exp_dir / "templates" / "opt"
        ts_dir.mkdir(parents=True)
        (ts_dir / "create-story.tpl.xml").write_text("<new-template/>")

        worktree = tmp_path / "worktree"
        cache = worktree / ".bmad-assist" / "cache"
        cache.mkdir(parents=True)
        (cache / "create-story.tpl.xml").write_text("<old-template/>")

        runner = ABTestRunner(exp_dir)
        variant = ABVariantConfig(
            label="x",
            config="c",
            patch_set="p",
            template_set="opt",
        )
        runner._apply_template_set(variant, worktree)

        assert (cache / "create-story.tpl.xml").read_text() == "<new-template/>"


class TestABTestRunnerWriteManifest:
    """Tests for ABTestRunner._write_ab_manifest."""

    def test_manifest_content(self, tmp_path: Path) -> None:
        """Manifest YAML contains test metadata and variant results."""
        runner = ABTestRunner(tmp_path)
        config = _make_ab_config()
        va = ABVariantResult(
            label="baseline",
            status=ExperimentStatus.COMPLETED,
            stories_attempted=1,
            stories_completed=1,
            stories_failed=0,
            duration_seconds=10.0,
            worktree_path=tmp_path / "wt-a",
            result_dir=tmp_path / "a",
        )
        vb = ABVariantResult(
            label="experimental",
            status=ExperimentStatus.FAILED,
            stories_attempted=1,
            stories_completed=0,
            stories_failed=1,
            duration_seconds=5.0,
            worktree_path=tmp_path / "wt-b",
            result_dir=tmp_path / "b",
            error="phase failed",
        )
        result_dir = tmp_path / "results"
        result_dir.mkdir()

        runner._write_ab_manifest(config, va, vb, result_dir)

        manifest_path = result_dir / "manifest.yaml"
        assert manifest_path.exists()
        manifest = yaml.safe_load(manifest_path.read_text())
        assert manifest["test_name"] == "test-ab"
        assert manifest["variant_a"]["status"] == "completed"
        assert manifest["variant_b"]["status"] == "failed"
        assert manifest["variant_b"]["error"] == "phase failed"
        assert manifest["stories"] == [{"id": "3.1", "ref": "HEAD"}]
        assert manifest["phases"] == ["create-story"]

    def test_manifest_no_temp_file(self, tmp_path: Path) -> None:
        """Temp file does not remain after atomic write."""
        runner = ABTestRunner(tmp_path)
        config = _make_ab_config()
        va = ABVariantResult(
            label="a",
            status=ExperimentStatus.COMPLETED,
            stories_attempted=0,
            stories_completed=0,
            stories_failed=0,
            duration_seconds=0,
            worktree_path=tmp_path,
            result_dir=tmp_path,
        )
        result_dir = tmp_path / "res"
        result_dir.mkdir()
        runner._write_ab_manifest(config, va, va, result_dir)
        assert not (result_dir / "manifest.yaml.tmp").exists()


class TestCollectWorktreeFiles:
    """Tests for ABTestRunner._collect_worktree_files."""

    def test_collects_artifact_and_runtime_files(self, tmp_path: Path) -> None:
        """Files from both _bmad-output and .bmad-assist are collected."""
        worktree = tmp_path / "worktree"
        (worktree / "_bmad-output" / "implementation-artifacts").mkdir(parents=True)
        (worktree / "_bmad-output" / "implementation-artifacts" / "story.md").write_text("x")
        (worktree / ".bmad-assist" / "cache").mkdir(parents=True)
        (worktree / ".bmad-assist" / "cache" / "data.json").write_text("{}")

        runner = ABTestRunner(tmp_path)
        files = runner._collect_worktree_files(worktree)

        assert "_bmad-output/implementation-artifacts/story.md" in files
        assert ".bmad-assist/cache/data.json" in files

    def test_empty_worktree(self, tmp_path: Path) -> None:
        """Empty worktree returns empty set."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        runner = ABTestRunner(tmp_path)
        assert runner._collect_worktree_files(worktree) == set()


class TestSnapshotStoryArtifacts:
    """Tests for ABTestRunner._snapshot_story_artifacts."""

    def test_only_copies_new_files(self, tmp_path: Path) -> None:
        """Only files not in pre_files are copied."""
        worktree = tmp_path / "worktree"
        artifacts = worktree / "_bmad-output" / "implementation-artifacts"
        artifacts.mkdir(parents=True)
        (artifacts / "pre-existing.md").write_text("old")
        (artifacts / "code-reviews").mkdir()
        (artifacts / "code-reviews" / "new-review.md").write_text("new")

        pre_files = {"_bmad-output/implementation-artifacts/pre-existing.md"}
        result_dir = tmp_path / "story-2.3"

        runner = ABTestRunner(tmp_path)
        runner._snapshot_story_artifacts(worktree, result_dir, pre_files)

        assert (result_dir / "artifacts" / "code-reviews" / "new-review.md").exists()
        assert not (result_dir / "artifacts" / "pre-existing.md").exists()

    def test_excludes_compiled_templates(self, tmp_path: Path) -> None:
        """Compiled templates (.tpl.xml) are excluded."""
        worktree = tmp_path / "worktree"
        bmad = worktree / ".bmad-assist" / "cache"
        bmad.mkdir(parents=True)
        (bmad / "validations.json").write_text("{}")
        (bmad / "compiled.tpl.xml").write_text("<xml/>")
        (bmad / "compiled.tpl.xml.meta.yaml").write_text("hash: x")

        result_dir = tmp_path / "story-2.3"
        runner = ABTestRunner(tmp_path)
        runner._snapshot_story_artifacts(worktree, result_dir, set())

        assert (result_dir / "bmad-assist" / "cache" / "validations.json").exists()
        assert not (result_dir / "bmad-assist" / "cache" / "compiled.tpl.xml").exists()
        assert not (result_dir / "bmad-assist" / "cache" / "compiled.tpl.xml.meta.yaml").exists()

    def test_no_files_when_nothing_new(self, tmp_path: Path) -> None:
        """Nothing copied when no new files exist."""
        worktree = tmp_path / "worktree"
        artifacts = worktree / "_bmad-output" / "implementation-artifacts"
        artifacts.mkdir(parents=True)
        (artifacts / "existing.md").write_text("x")

        pre_files = {"_bmad-output/implementation-artifacts/existing.md"}
        result_dir = tmp_path / "story-2.3"

        runner = ABTestRunner(tmp_path)
        runner._snapshot_story_artifacts(worktree, result_dir, pre_files)

        assert not result_dir.exists()


def _init_fixture_git(fixture_path: Path) -> None:
    """Initialize a git repo with one commit so HEAD and refs are valid."""
    env = {
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": os.environ.get("PATH", ""),
    }
    subprocess.run(["git", "init"], cwd=fixture_path, capture_output=True, check=True, env=env)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=fixture_path,
        capture_output=True,
        check=True,
        env=env,
    )


def _setup_experiment_dir(tmp_path: Path) -> Path:
    """Create a minimal experiments directory with fixture, configs, and patchsets."""
    exp_dir = tmp_path / "experiments"
    (exp_dir / "configs").mkdir(parents=True)
    (exp_dir / "patch-sets").mkdir(parents=True)
    fixture_dir = exp_dir / "fixtures" / "minimal"
    fixture_dir.mkdir(parents=True)
    _init_fixture_git(fixture_dir)

    (exp_dir / "configs" / "opus-solo.yaml").write_text(
        "name: opus-solo\nproviders:\n  master:\n    provider: claude\n    model: opus\n  multi: []\n"
    )
    (exp_dir / "configs" / "haiku-solo.yaml").write_text(
        "name: haiku-solo\nproviders:\n  master:\n    provider: claude\n    model: haiku\n  multi: []\n"
    )
    (exp_dir / "patch-sets" / "baseline.yaml").write_text("name: baseline\npatches: {}\n")
    (exp_dir / "patch-sets" / "exp.yaml").write_text("name: exp\npatches: {}\n")
    return exp_dir


def _mock_worktrees(tmp_path: Path, mock_create_wt: MagicMock) -> tuple[MagicMock, MagicMock]:
    """Create mock worktree objects and configure create_ab_worktrees mock."""
    wt_a = MagicMock()
    wt_a.path = tmp_path / "wt-a"
    wt_a.path.mkdir(exist_ok=True)
    wt_b = MagicMock()
    wt_b.path = tmp_path / "wt-b"
    wt_b.path.mkdir(exist_ok=True)
    mock_create_wt.return_value = (wt_a, wt_b)
    return wt_a, wt_b


class TestABTestRunnerRun:
    """Tests for ABTestRunner.run with mocked execute_phase."""

    @patch("bmad_assist.experiments.ab.runner.cleanup_ab_worktrees")
    @patch("bmad_assist.experiments.ab.runner.create_ab_worktrees")
    @patch("bmad_assist.experiments.ab.runner.unregister_signal_handlers")
    @patch("bmad_assist.experiments.ab.runner.register_signal_handlers")
    @patch("bmad_assist.experiments.ab.runner.reset_shutdown")
    @patch("bmad_assist.experiments.ab.runner.shutdown_requested", return_value=False)
    @patch("bmad_assist.experiments.ab.runner.execute_phase")
    @patch("bmad_assist.experiments.ab.runner.init_handlers")
    @patch("bmad_assist.experiments.ab.runner.init_paths")
    @patch("bmad_assist.experiments.ab.runner.load_config")
    @patch("bmad_assist.experiments.ab.runner.set_non_interactive")
    @patch("bmad_assist.experiments.ab.runner._reset_all_singletons")
    def test_run_success_both_variants(
        self,
        mock_reset: MagicMock,
        _mock_non_interactive: MagicMock,
        _mock_load_config: MagicMock,
        _mock_init_paths: MagicMock,
        _mock_init_handlers: MagicMock,
        mock_execute: MagicMock,
        _mock_shutdown: MagicMock,
        _mock_reset_shutdown: MagicMock,
        _mock_register: MagicMock,
        _mock_unregister: MagicMock,
        mock_create_wt: MagicMock,
        mock_cleanup_wt: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Full run with both variants succeeding."""
        exp_dir = _setup_experiment_dir(tmp_path)
        _mock_worktrees(tmp_path, mock_create_wt)

        phase_result = MagicMock()
        phase_result.success = True
        mock_execute.return_value = phase_result

        with patch("bmad_assist.experiments.ab.runner.init_run_prompts_dir"):
            runner = ABTestRunner(exp_dir, project_root=tmp_path)
            config = _make_ab_config()
            result = runner.run(config)

        assert result.test_name == "test-ab"
        assert result.variant_a.status == ExperimentStatus.COMPLETED
        assert result.variant_b.status == ExperimentStatus.COMPLETED
        # execute_phase called once per story per phase per variant = 1*1*2 = 2
        assert mock_execute.call_count == 2
        # Singletons reset multiple times (in _run_variant + between variants + finally)
        assert mock_reset.call_count >= 3
        # Cleanup always called
        mock_cleanup_wt.assert_called_once()

    @patch("bmad_assist.experiments.ab.runner.cleanup_ab_worktrees")
    @patch("bmad_assist.experiments.ab.runner.create_ab_worktrees")
    @patch("bmad_assist.experiments.ab.runner.unregister_signal_handlers")
    @patch("bmad_assist.experiments.ab.runner.register_signal_handlers")
    @patch("bmad_assist.experiments.ab.runner.reset_shutdown")
    @patch("bmad_assist.experiments.ab.runner.execute_phase")
    @patch("bmad_assist.experiments.ab.runner.init_handlers")
    @patch("bmad_assist.experiments.ab.runner.init_paths")
    @patch("bmad_assist.experiments.ab.runner.load_config")
    @patch("bmad_assist.experiments.ab.runner.set_non_interactive")
    @patch("bmad_assist.experiments.ab.runner._reset_all_singletons")
    def test_run_cancellation_skips_variant_b(
        self,
        _mock_reset: MagicMock,
        _mock_non_interactive: MagicMock,
        _mock_load_config: MagicMock,
        _mock_init_paths: MagicMock,
        _mock_init_handlers: MagicMock,
        mock_execute: MagicMock,
        _mock_reset_shutdown: MagicMock,
        _mock_register: MagicMock,
        _mock_unregister: MagicMock,
        mock_create_wt: MagicMock,
        _mock_cleanup_wt: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Cancellation after variant A produces CANCELLED result for B."""
        exp_dir = _setup_experiment_dir(tmp_path)
        _mock_worktrees(tmp_path, mock_create_wt)

        phase_result = MagicMock()
        phase_result.success = True
        mock_execute.return_value = phase_result

        # shutdown_requested: False during variant A execution, True after.
        # variant A makes ~4 calls (epic loop, story loop, phase loop, post-loop),
        # then the outer run() check is call 5 — we want True there.
        call_count = 0

        def shutdown_side_effect() -> bool:
            nonlocal call_count
            call_count += 1
            return call_count > 4

        with (
            patch(
                "bmad_assist.experiments.ab.runner.shutdown_requested",
                side_effect=shutdown_side_effect,
            ),
            patch("bmad_assist.experiments.ab.runner.init_run_prompts_dir"),
        ):
            runner = ABTestRunner(exp_dir, project_root=tmp_path)
            config = _make_ab_config()
            result = runner.run(config)

        assert result.variant_b.status == ExperimentStatus.CANCELLED
        assert result.variant_b.error is not None
        assert "Cancelled" in result.variant_b.error
