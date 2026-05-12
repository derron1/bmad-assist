"""Tests for the D.7 quality-gate integration in dev_story / create_story.

The gate sits between provider-exit-0 and PhaseResult.ok in both handlers.
These tests mock the gate's behavior (Agent 1's territory) and verify the
handler-side wiring:

- Gate disabled / phase not in list → handler returns success unchanged.
- Gate skipped (no config) → handler returns success.
- Gate passed → handler returns success.
- Gate failed → handler returns PhaseResult.fail with output mentioned.
- ``_get_changed_files_for_gate`` returns absolute paths of changed files
  and skips non-existent (deleted) paths.

The actual subprocess behavior of pre-commit is verified in Agent 1's
``tests/quality_gate/`` suite — not here.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bmad_assist.core.config import (
    Config,
    MasterProviderConfig,
    ProviderConfig,
    QualityGateConfig,
)
from bmad_assist.core.loop.handlers.create_story import CreateStoryHandler
from bmad_assist.core.loop.handlers.dev_story import DevStoryHandler
from bmad_assist.core.state import Phase, State
from bmad_assist.providers.base import ProviderResult
from bmad_assist.quality_gate import QualityGateResult

# =============================================================================
# Helpers
# =============================================================================


def _make_config(quality_gate: QualityGateConfig | None = None) -> Config:
    """Build a minimal Config with optional quality_gate override."""
    return Config(
        providers=ProviderConfig(
            master=MasterProviderConfig(provider="claude", model="opus-4"),
        ),
        timeout=300,
        quality_gate=quality_gate,
    )


def _make_provider_success() -> ProviderResult:
    return ProviderResult(
        stdout="ok",
        stderr="",
        exit_code=0,
        duration_ms=1000,
        model="opus-4",
        command=("claude",),
    )


@pytest.fixture
def dev_state() -> State:
    """State positioned at the DEV_STORY phase of story 1.2."""
    return State(current_epic=1, current_story="1.2", current_phase=Phase.DEV_STORY)


@pytest.fixture
def create_state() -> State:
    """State positioned at the CREATE_STORY phase of story 1.2."""
    return State(current_epic=1, current_story="1.2", current_phase=Phase.CREATE_STORY)


@pytest.fixture
def project_with_paths(tmp_path: Path) -> Path:
    """Initialize paths singleton so create_story handler's helpers work."""
    from bmad_assist.core.paths import init_paths

    paths = init_paths(tmp_path)
    paths.ensure_directories()
    return tmp_path


# =============================================================================
# QualityGateConfig.is_enabled_for_phase
# =============================================================================


class TestQualityGateConfigPredicate:
    """Tests for :meth:`QualityGateConfig.is_enabled_for_phase`."""

    def test_default_phases(self) -> None:
        """Default config gates dev_story and create_story; nothing else."""
        cfg = QualityGateConfig()
        assert cfg.is_enabled_for_phase("dev_story") is True
        assert cfg.is_enabled_for_phase("create_story") is True
        assert cfg.is_enabled_for_phase("code_review") is False

    def test_disabled_master_switch(self) -> None:
        """enabled=False short-circuits regardless of phases list."""
        cfg = QualityGateConfig(enabled=False)
        assert cfg.is_enabled_for_phase("dev_story") is False
        assert cfg.is_enabled_for_phase("create_story") is False

    def test_custom_phase_list(self) -> None:
        """Only listed phases match; others return False."""
        cfg = QualityGateConfig(phases=["dev_story"])
        assert cfg.is_enabled_for_phase("dev_story") is True
        assert cfg.is_enabled_for_phase("create_story") is False


# =============================================================================
# get_quality_gate_config loader helper
# =============================================================================


class TestGetQualityGateConfig:
    """Tests for the :func:`get_quality_gate_config` loader helper."""

    def test_returns_defaults_when_unset(self) -> None:
        """config.quality_gate=None falls back to a fresh QualityGateConfig()."""
        from bmad_assist.core.config import get_quality_gate_config

        config = _make_config(quality_gate=None)
        qg = get_quality_gate_config(config)
        assert qg.enabled is True
        assert "dev_story" in qg.phases
        assert "create_story" in qg.phases

    def test_returns_set_config(self) -> None:
        """A configured quality_gate is returned by identity (no copy)."""
        from bmad_assist.core.config import get_quality_gate_config

        custom = QualityGateConfig(enabled=False)
        config = _make_config(quality_gate=custom)
        qg = get_quality_gate_config(config)
        assert qg is custom


# =============================================================================
# _get_changed_files_for_gate
# =============================================================================


class TestGetChangedFilesForGate:
    """Tests for :meth:`BaseHandler._get_changed_files_for_gate`."""

    def test_returns_absolute_paths_for_existing_files(self, tmp_path: Path) -> None:
        """Git diff output -> absolute paths to extant files."""
        # Create two files that "will be changed"
        (tmp_path / "a.py").write_text("x = 1\n")
        (tmp_path / "b.py").write_text("y = 2\n")

        config = _make_config()
        handler = DevStoryHandler(config, tmp_path)

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = "a.py\nb.py\n"
        fake_proc.stderr = ""

        with patch("subprocess.run", return_value=fake_proc):
            files = handler._get_changed_files_for_gate()

        assert len(files) == 2
        assert all(p.is_absolute() for p in files)
        names = {p.name for p in files}
        assert names == {"a.py", "b.py"}

    def test_filters_out_deleted_files(self, tmp_path: Path) -> None:
        """Files that no longer exist on disk are skipped."""
        (tmp_path / "exists.py").write_text("z = 3\n")
        # "deleted.py" is in git diff output but not on disk

        config = _make_config()
        handler = DevStoryHandler(config, tmp_path)

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = "exists.py\ndeleted.py\n"
        fake_proc.stderr = ""

        with patch("subprocess.run", return_value=fake_proc):
            files = handler._get_changed_files_for_gate()

        assert len(files) == 1
        assert files[0].name == "exists.py"

    def test_non_zero_exit_returns_empty(self, tmp_path: Path) -> None:
        """Non-git directories / failed git invocation → empty list, no raise."""
        config = _make_config()
        handler = DevStoryHandler(config, tmp_path)

        fake_proc = MagicMock()
        fake_proc.returncode = 128
        fake_proc.stdout = ""
        fake_proc.stderr = "fatal: not a git repository"

        with patch("subprocess.run", return_value=fake_proc):
            files = handler._get_changed_files_for_gate()

        assert files == []

    def test_git_missing_returns_empty(self, tmp_path: Path) -> None:
        """FileNotFoundError when git binary is absent → empty list."""
        config = _make_config()
        handler = DevStoryHandler(config, tmp_path)

        with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
            files = handler._get_changed_files_for_gate()

        assert files == []

    def test_subprocess_error_returns_empty(self, tmp_path: Path) -> None:
        """SubprocessError (timeout, etc.) → empty list."""
        config = _make_config()
        handler = DevStoryHandler(config, tmp_path)

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 30)):
            files = handler._get_changed_files_for_gate()

        assert files == []


# =============================================================================
# Handler integration: dev_story (inherits BaseHandler.execute)
# =============================================================================


class TestDevStoryGateIntegration:
    """End-to-end gate wiring through DevStoryHandler.execute()."""

    def test_gate_disabled_returns_success_without_running(
        self, project_with_paths: Path, dev_state: State
    ) -> None:
        """enabled=False → handler returns ok and run_quality_gate is never called."""
        config = _make_config(quality_gate=QualityGateConfig(enabled=False))
        handler = DevStoryHandler(config, project_with_paths)

        with (
            patch.object(handler, "render_prompt", return_value="<p>"),
            patch.object(handler, "invoke_provider", return_value=_make_provider_success()),
            patch("bmad_assist.quality_gate.run_quality_gate") as mock_run_gate,
        ):
            result = handler.execute(dev_state)

        assert result.success
        mock_run_gate.assert_not_called()

    def test_phase_not_in_list_returns_success_without_running(
        self, project_with_paths: Path, dev_state: State
    ) -> None:
        """phases=[only-code_review] → dev_story skips gate."""
        config = _make_config(
            quality_gate=QualityGateConfig(phases=["code_review"]),
        )
        handler = DevStoryHandler(config, project_with_paths)

        with (
            patch.object(handler, "render_prompt", return_value="<p>"),
            patch.object(handler, "invoke_provider", return_value=_make_provider_success()),
            patch("bmad_assist.quality_gate.run_quality_gate") as mock_run_gate,
        ):
            result = handler.execute(dev_state)

        assert result.success
        mock_run_gate.assert_not_called()

    def test_gate_skipped_returns_success(self, project_with_paths: Path, dev_state: State) -> None:
        """Gate runs and returns skipped=True (no config) → handler ok."""
        handler = DevStoryHandler(_make_config(), project_with_paths)

        skip_result = QualityGateResult(
            passed=False,
            skipped=True,
            skip_reason="no .pre-commit-config.yaml",
            duration_ms=2,
        )

        with (
            patch.object(handler, "render_prompt", return_value="<p>"),
            patch.object(handler, "invoke_provider", return_value=_make_provider_success()),
            patch.object(handler, "_get_changed_files_for_gate", return_value=[]),
            patch(
                "bmad_assist.quality_gate.run_quality_gate",
                return_value=skip_result,
            ),
        ):
            result = handler.execute(dev_state)

        assert result.success

    def test_gate_passed_returns_success(self, project_with_paths: Path, dev_state: State) -> None:
        """Gate ran and passed → handler returns ok and gate was invoked."""
        handler = DevStoryHandler(_make_config(), project_with_paths)

        pass_result = QualityGateResult(passed=True, duration_ms=120)

        with (
            patch.object(handler, "render_prompt", return_value="<p>"),
            patch.object(handler, "invoke_provider", return_value=_make_provider_success()),
            patch.object(
                handler,
                "_get_changed_files_for_gate",
                return_value=[Path("/tmp/x.py")],
            ),
            patch(
                "bmad_assist.quality_gate.run_quality_gate",
                return_value=pass_result,
            ) as mock_run_gate,
        ):
            result = handler.execute(dev_state)

        assert result.success
        mock_run_gate.assert_called_once()

    def test_gate_failed_returns_phase_result_fail(
        self, project_with_paths: Path, dev_state: State
    ) -> None:
        """Hook failure → PhaseResult.fail with hook names in error message."""
        handler = DevStoryHandler(_make_config(), project_with_paths)

        fail_result = QualityGateResult(
            passed=False,
            output="ruff (lint).....Failed\nmypy............Failed\n",
            failed_hooks=("ruff (lint)", "mypy"),
            duration_ms=5000,
        )

        with (
            patch.object(handler, "render_prompt", return_value="<p>"),
            patch.object(handler, "invoke_provider", return_value=_make_provider_success()),
            patch.object(
                handler,
                "_get_changed_files_for_gate",
                return_value=[Path("/tmp/x.py")],
            ),
            patch(
                "bmad_assist.quality_gate.run_quality_gate",
                return_value=fail_result,
            ),
        ):
            result = handler.execute(dev_state)

        assert not result.success
        assert result.error is not None
        assert "Quality gate failed" in result.error
        assert "ruff (lint)" in result.error
        assert "mypy" in result.error


# =============================================================================
# Handler integration: create_story (custom execute with three success paths)
# =============================================================================


class TestCreateStoryGateIntegration:
    """Verify the gate is wired into create_story's three success paths.

    create_story has its own execute() with rescue logic; we use the
    "fresh-file-on-disk" success path (the most common one) here. The
    guard-rescue and stdout-rescue paths follow the same pattern and
    are covered structurally by the wiring (same _run_quality_gate_check
    call site shape).
    """

    def _provider_writes_fresh_story_side_effect(self) -> Any:
        """Side-effect for invoke_provider mock: write a story file with a
        fresh mtime so the handler's freshness check (mtime > pre_existing)
        passes. Real Master LLMs do this; tests must simulate it.
        """
        from bmad_assist.core.paths import get_paths

        def _side_effect(*args: Any, **kwargs: Any) -> Any:
            # Tiny sleep ensures mtime > pre_existing_mtime baseline captured
            # at phase entry (mtime resolution is ~1ms on most filesystems).
            time.sleep(0.02)
            stories_dir = get_paths().stories_dir
            stories_dir.mkdir(parents=True, exist_ok=True)
            (stories_dir / "1-2-some-slug.md").write_text("# Story 1.2: Test\n")
            return _make_provider_success()

        return _side_effect

    def test_gate_disabled_returns_success(
        self, project_with_paths: Path, create_state: State
    ) -> None:
        """enabled=False → handler returns ok without invoking the gate."""
        config = _make_config(quality_gate=QualityGateConfig(enabled=False))
        handler = CreateStoryHandler(config, project_with_paths)

        with (
            patch.object(handler, "render_prompt", return_value="<p>"),
            patch.object(
                handler,
                "invoke_provider",
                side_effect=self._provider_writes_fresh_story_side_effect(),
            ),
            patch("bmad_assist.quality_gate.run_quality_gate") as mock_run_gate,
        ):
            result = handler.execute(create_state)

        assert result.success
        mock_run_gate.assert_not_called()

    def test_gate_passed_returns_success(
        self, project_with_paths: Path, create_state: State
    ) -> None:
        """Gate ran and passed → handler returns ok and gate was invoked."""
        handler = CreateStoryHandler(_make_config(), project_with_paths)

        pass_result = QualityGateResult(passed=True, duration_ms=80)

        with (
            patch.object(handler, "render_prompt", return_value="<p>"),
            patch.object(
                handler,
                "invoke_provider",
                side_effect=self._provider_writes_fresh_story_side_effect(),
            ),
            patch.object(
                handler,
                "_get_changed_files_for_gate",
                return_value=[Path("/tmp/x.md")],
            ),
            patch(
                "bmad_assist.quality_gate.run_quality_gate",
                return_value=pass_result,
            ) as mock_run_gate,
        ):
            result = handler.execute(create_state)

        assert result.success
        mock_run_gate.assert_called_once()

    def test_gate_failed_returns_phase_result_fail(
        self, project_with_paths: Path, create_state: State
    ) -> None:
        """Hook failure → PhaseResult.fail; failing hook names surfaced in error."""
        handler = CreateStoryHandler(_make_config(), project_with_paths)

        fail_result = QualityGateResult(
            passed=False,
            output="markdownlint........Failed\n",
            failed_hooks=("markdownlint",),
            duration_ms=400,
        )

        with (
            patch.object(handler, "render_prompt", return_value="<p>"),
            patch.object(
                handler,
                "invoke_provider",
                side_effect=self._provider_writes_fresh_story_side_effect(),
            ),
            patch.object(
                handler,
                "_get_changed_files_for_gate",
                return_value=[Path("/tmp/x.md")],
            ),
            patch(
                "bmad_assist.quality_gate.run_quality_gate",
                return_value=fail_result,
            ),
        ):
            result = handler.execute(create_state)

        assert not result.success
        assert result.error is not None
        assert "Quality gate failed" in result.error
        assert "markdownlint" in result.error
