"""Tests for the standalone quality_gate.runner module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bmad_assist.quality_gate import QualityGateResult, run_quality_gate
from bmad_assist.quality_gate.runner import (
    _format_files,
    _substitute_files,
    parse_failed_hooks,
)

# ---------------------------------------------------------------------------
# Canonical pre-commit output fragments used across tests.
# ---------------------------------------------------------------------------

_ALL_PASS_OUTPUT = (
    "ruff (lint)..............................................................Passed\n"
    "ruff (format)............................................................Passed\n"
    "trim trailing whitespace.................................................Passed\n"
    "check yaml...............................................................Passed\n"
)

_MIXED_OUTPUT = (
    "ruff (lint)..............................................................Passed\n"
    "ruff (format)............................................................Passed\n"
    "trim trailing whitespace.................................................Passed\n"
    "check yaml...............................................................Passed\n"
    "check json...........................................(no files to check)Skipped\n"
    "detect-secrets...........................................................Failed\n"
)

_MULTI_FAIL_OUTPUT = (
    "ruff (lint)..............................................................Failed\n"
    "ruff (format)............................................................Passed\n"
    "mypy.....................................................................Failed\n"
)


# ---------------------------------------------------------------------------
# parse_failed_hooks: spec-mandated parser coverage.
# ---------------------------------------------------------------------------


class TestParseFailedHooks:
    """Pre-commit output parsing — extract only Failed hook names."""

    def test_canonical_mixed_output_returns_only_failed(self) -> None:
        """Mixed Pass/Skipped/Failed output yields exactly one failed hook."""
        assert parse_failed_hooks(_MIXED_OUTPUT) == ("detect-secrets",)

    def test_all_passed_returns_empty_tuple(self) -> None:
        """No Failed lines means an empty tuple."""
        assert parse_failed_hooks(_ALL_PASS_OUTPUT) == ()

    def test_hook_name_with_parentheses_is_preserved(self) -> None:
        """A hook display name containing parentheses survives parsing."""
        line = "ruff (lint)............Failed\n"
        assert parse_failed_hooks(line) == ("ruff (lint)",)

    def test_multiple_failed_hooks_preserved_in_order(self) -> None:
        """Multiple Failed lines are reported in source order."""
        assert parse_failed_hooks(_MULTI_FAIL_OUTPUT) == ("ruff (lint)", "mypy")

    def test_skipped_status_is_ignored(self) -> None:
        """Lines ending with Skipped are not collected as failures."""
        line = "check json...........................................(no files to check)Skipped\n"
        assert parse_failed_hooks(line) == ()

    def test_empty_string_returns_empty_tuple(self) -> None:
        """An empty output blob yields an empty tuple."""
        assert parse_failed_hooks("") == ()

    def test_non_matching_lines_are_ignored(self) -> None:
        """Arbitrary noise without the hook-line shape is ignored."""
        noise = "Some prelude line\n\n[INFO] Initializing environment...\n"
        assert parse_failed_hooks(noise) == ()

    def test_mixed_noise_and_failed_lines(self) -> None:
        """Failed lines are extracted from a noisy log."""
        blob = (
            "[INFO] Installing environment for pre-commit.\n"
            "ruff (lint)..............................................................Failed\n"
            "Some trailing diagnostic noise\n"
        )
        assert parse_failed_hooks(blob) == ("ruff (lint)",)


# ---------------------------------------------------------------------------
# File-path substitution helpers.
# ---------------------------------------------------------------------------


class TestFileSubstitution:
    """Files placeholder handling and shell-safe quoting."""

    def test_relative_paths_inside_project_root(self, tmp_path: Path) -> None:
        """Files under project_root are rendered as relative paths."""
        f1 = tmp_path / "src" / "a.py"
        f2 = tmp_path / "src" / "b.py"
        f1.parent.mkdir(parents=True, exist_ok=True)
        f1.write_text("")
        f2.write_text("")

        rendered = _format_files([f1, f2], tmp_path)

        assert "src/a.py" in rendered
        assert "src/b.py" in rendered
        # Absolute path should not leak.
        assert str(tmp_path) not in rendered

    def test_path_with_spaces_is_quoted(self, tmp_path: Path) -> None:
        """A path containing spaces is shell-quoted by shlex.quote."""
        spaced = tmp_path / "dir with space" / "weird name.py"
        spaced.parent.mkdir(parents=True, exist_ok=True)
        spaced.write_text("")

        rendered = _format_files([spaced], tmp_path)

        # shlex.quote wraps strings containing spaces in single quotes.
        assert "'dir with space/weird name.py'" in rendered

    def test_path_outside_project_root_falls_back_to_given_form(
        self, tmp_path: Path
    ) -> None:
        """Paths not under project_root fall through to their absolute form."""
        # A sibling path outside the "project_root" parameter — does not need
        # to exist on disk; Path.resolve() is non-strict and _format_files
        # falls back to the path-as-given when relative_to raises ValueError.
        outside = Path("/this/path/is/definitely/elsewhere.py")
        rendered = _format_files([outside], tmp_path)
        # The full path should appear in the rendered command verbatim.
        assert "/this/path/is/definitely/elsewhere.py" in rendered

    def test_substitute_files_replaces_placeholder(self) -> None:
        """The literal {files} marker is expanded to the file list."""
        cmd = "pre-commit run --files {files}"
        assert (
            _substitute_files(cmd, "a.py b.py")
            == "pre-commit run --files a.py b.py"
        )

    def test_substitute_files_no_placeholder_leaves_command_intact(self) -> None:
        """A command without {files} is unchanged by substitution."""
        cmd = "pre-commit run --all-files"
        assert _substitute_files(cmd, "a.py") == "pre-commit run --all-files"


# ---------------------------------------------------------------------------
# run_quality_gate: skip paths.
# ---------------------------------------------------------------------------


class TestRunQualityGateSkipPaths:
    """Skip cases short-circuit before subprocess invocation."""

    def test_empty_files_list_is_skipped(self, tmp_path: Path) -> None:
        """No changed files → skipped with the expected reason."""
        result = run_quality_gate(
            changed_files=[],
            project_root=tmp_path,
        )

        assert isinstance(result, QualityGateResult)
        assert result.skipped is True
        assert result.passed is False
        assert result.skip_reason == "no files changed"
        assert result.duration_ms == 0

    def test_missing_pre_commit_config_is_skipped(self, tmp_path: Path) -> None:
        """Missing .pre-commit-config.yaml triggers a skip by default."""
        f = tmp_path / "a.py"
        f.write_text("")

        result = run_quality_gate(
            changed_files=[f],
            project_root=tmp_path,
            skip_if_no_config=True,
        )

        assert result.skipped is True
        assert result.passed is False
        assert result.skip_reason == "no .pre-commit-config.yaml found"

    def test_missing_config_with_skip_disabled_attempts_command(
        self, tmp_path: Path
    ) -> None:
        """skip_if_no_config=False forces the command to run anyway."""
        f = tmp_path / "a.py"
        f.write_text("")
        (tmp_path / ".pre-commit-config.yaml").unlink(missing_ok=True)

        # Use a command that DOES exist and exits 0 to demonstrate the gate
        # actually invoked something despite no config file present.
        mock_completed = subprocess.CompletedProcess(
            args=["pre-commit"], returncode=0, stdout="ok\n", stderr=""
        )

        with patch(
            "bmad_assist.quality_gate.runner.subprocess.run",
            return_value=mock_completed,
        ) as mocked, patch(
            "bmad_assist.quality_gate.runner.shutil.which",
            return_value="/usr/bin/pre-commit",
        ):
            result = run_quality_gate(
                changed_files=[f],
                project_root=tmp_path,
                skip_if_no_config=False,
            )

        assert mocked.called, "subprocess.run should have been invoked"
        assert result.skipped is False
        assert result.passed is True

    def test_executable_not_on_path_is_skipped(self, tmp_path: Path) -> None:
        """Missing executable on PATH triggers a skip with a clear reason."""
        f = tmp_path / "a.py"
        f.write_text("")
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")

        result = run_quality_gate(
            changed_files=[f],
            project_root=tmp_path,
            hook_command="definitely-not-a-real-command-zzz {files}",
        )

        assert result.skipped is True
        assert result.passed is False
        assert result.skip_reason is not None
        assert "definitely-not-a-real-command-zzz" in result.skip_reason


# ---------------------------------------------------------------------------
# run_quality_gate: pass / fail / timeout paths (subprocess mocked).
# ---------------------------------------------------------------------------


@pytest.fixture
def project_with_config(tmp_path: Path) -> Path:
    """Return a tmp project root with a pre-commit config and one source file."""
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
    f = tmp_path / "src" / "module.py"
    f.parent.mkdir(parents=True)
    f.write_text("")
    return tmp_path


def _make_run_mock(
    *, returncode: int, stdout: str = "", stderr: str = ""
) -> MagicMock:
    """Build a MagicMock for subprocess.run with the given completed-process payload."""
    completed = subprocess.CompletedProcess(
        args=["pre-commit"], returncode=returncode, stdout=stdout, stderr=stderr
    )
    return MagicMock(return_value=completed)


class TestRunQualityGateOutcomes:
    """All hooks pass, some hooks fail, timeout."""

    def test_all_hooks_pass(self, project_with_config: Path) -> None:
        """Exit 0 with pass-only output → passed=True with output captured."""
        f = project_with_config / "src" / "module.py"
        run_mock = _make_run_mock(returncode=0, stdout=_ALL_PASS_OUTPUT)

        with patch(
            "bmad_assist.quality_gate.runner.subprocess.run", run_mock
        ), patch(
            "bmad_assist.quality_gate.runner.shutil.which",
            return_value="/usr/bin/pre-commit",
        ):
            result = run_quality_gate(
                changed_files=[f], project_root=project_with_config
            )

        assert result.passed is True
        assert result.skipped is False
        assert result.failed_hooks == ()
        assert "ruff (lint)" in result.output
        assert result.duration_ms >= 0

    def test_some_hooks_fail_populates_failed_hooks(
        self, project_with_config: Path
    ) -> None:
        """Non-zero exit with a mixed report extracts only the Failed hook."""
        f = project_with_config / "src" / "module.py"
        run_mock = _make_run_mock(returncode=1, stdout=_MIXED_OUTPUT)

        with patch(
            "bmad_assist.quality_gate.runner.subprocess.run", run_mock
        ), patch(
            "bmad_assist.quality_gate.runner.shutil.which",
            return_value="/usr/bin/pre-commit",
        ):
            result = run_quality_gate(
                changed_files=[f], project_root=project_with_config
            )

        assert result.passed is False
        assert result.skipped is False
        assert result.failed_hooks == ("detect-secrets",)
        assert "detect-secrets" in result.output

    def test_multiple_failed_hooks_all_reported(
        self, project_with_config: Path
    ) -> None:
        """All failed hooks are surfaced in source order."""
        f = project_with_config / "src" / "module.py"
        run_mock = _make_run_mock(returncode=1, stdout=_MULTI_FAIL_OUTPUT)

        with patch(
            "bmad_assist.quality_gate.runner.subprocess.run", run_mock
        ), patch(
            "bmad_assist.quality_gate.runner.shutil.which",
            return_value="/usr/bin/pre-commit",
        ):
            result = run_quality_gate(
                changed_files=[f], project_root=project_with_config
            )

        assert result.passed is False
        assert result.failed_hooks == ("ruff (lint)", "mypy")

    def test_combined_stdout_and_stderr_in_output(
        self, project_with_config: Path
    ) -> None:
        """Both stdout and stderr are included in the captured output."""
        f = project_with_config / "src" / "module.py"
        run_mock = _make_run_mock(
            returncode=1, stdout="stdout-content\n", stderr="stderr-content\n"
        )

        with patch(
            "bmad_assist.quality_gate.runner.subprocess.run", run_mock
        ), patch(
            "bmad_assist.quality_gate.runner.shutil.which",
            return_value="/usr/bin/pre-commit",
        ):
            result = run_quality_gate(
                changed_files=[f], project_root=project_with_config
            )

        assert "stdout-content" in result.output
        assert "stderr-content" in result.output

    def test_timeout_kills_process_and_marks_failed(
        self, project_with_config: Path
    ) -> None:
        """A TimeoutExpired exception is converted to passed=False with marker."""
        f = project_with_config / "src" / "module.py"

        def _raise_timeout(*args: Any, **kwargs: Any) -> None:
            raise subprocess.TimeoutExpired(
                cmd="pre-commit run --files src/module.py",
                timeout=2,
                output="partial-stdout-content\n",
                stderr="partial-stderr-content\n",
            )

        with patch(
            "bmad_assist.quality_gate.runner.subprocess.run",
            side_effect=_raise_timeout,
        ), patch(
            "bmad_assist.quality_gate.runner.shutil.which",
            return_value="/usr/bin/pre-commit",
        ):
            result = run_quality_gate(
                changed_files=[f],
                project_root=project_with_config,
                timeout_seconds=2,
            )

        assert result.passed is False
        assert result.skipped is False
        assert "TIMEOUT" in result.output
        assert "partial-stdout-content" in result.output
        # partial-stderr should also be preserved.
        assert "partial-stderr-content" in result.output

    def test_timeout_with_no_partial_output(
        self, project_with_config: Path
    ) -> None:
        """Timeout with no captured output still surfaces a clear marker."""
        f = project_with_config / "src" / "module.py"

        def _raise_timeout(*args: Any, **kwargs: Any) -> None:
            raise subprocess.TimeoutExpired(
                cmd="pre-commit", timeout=1, output=None, stderr=None
            )

        with patch(
            "bmad_assist.quality_gate.runner.subprocess.run",
            side_effect=_raise_timeout,
        ), patch(
            "bmad_assist.quality_gate.runner.shutil.which",
            return_value="/usr/bin/pre-commit",
        ):
            result = run_quality_gate(
                changed_files=[f],
                project_root=project_with_config,
                timeout_seconds=1,
            )

        assert result.passed is False
        assert "TIMEOUT" in result.output


class TestRunQualityGateCommandHandling:
    """The hook_command template and the rendered subprocess invocation."""

    def test_files_placeholder_is_expanded_with_quoting(
        self, project_with_config: Path
    ) -> None:
        """A path with spaces is shell-quoted in the rendered command tokens."""
        spaced = project_with_config / "dir with space" / "weird name.py"
        spaced.parent.mkdir(parents=True, exist_ok=True)
        spaced.write_text("")
        run_mock = _make_run_mock(returncode=0, stdout=_ALL_PASS_OUTPUT)

        with patch(
            "bmad_assist.quality_gate.runner.subprocess.run", run_mock
        ), patch(
            "bmad_assist.quality_gate.runner.shutil.which",
            return_value="/usr/bin/pre-commit",
        ):
            run_quality_gate(
                changed_files=[spaced],
                project_root=project_with_config,
            )

        # subprocess.run is invoked with a token list (no shell=True).
        called_args, called_kwargs = run_mock.call_args
        command_tokens = called_args[0]
        assert isinstance(command_tokens, list)
        assert command_tokens[0] == "pre-commit"
        assert "run" in command_tokens
        assert "--files" in command_tokens
        # The quoted spaced filename should have been split back into one
        # token by shlex (preserving the space inside the token).
        assert "dir with space/weird name.py" in command_tokens
        # cwd must be the project root, not the file's parent.
        assert called_kwargs["cwd"] == str(project_with_config)
        # shell=True must never be used.
        assert called_kwargs.get("shell", False) is False

    def test_custom_hook_command_is_honored(
        self, project_with_config: Path
    ) -> None:
        """An alternative hook_command template is used verbatim."""
        f = project_with_config / "src" / "module.py"
        run_mock = _make_run_mock(returncode=0)

        with patch(
            "bmad_assist.quality_gate.runner.subprocess.run", run_mock
        ), patch(
            "bmad_assist.quality_gate.runner.shutil.which",
            return_value="/usr/local/bin/lefthook",
        ):
            run_quality_gate(
                changed_files=[f],
                project_root=project_with_config,
                hook_command="lefthook run pre-commit --files {files}",
            )

        called_args, _ = run_mock.call_args
        command_tokens = called_args[0]
        assert command_tokens[0] == "lefthook"
        assert "src/module.py" in command_tokens

    def test_malformed_hook_command_returns_fail_not_skip(
        self, project_with_config: Path
    ) -> None:
        """Unparseable hook_command returns a fail result (not a skip)."""
        f = project_with_config / "src" / "module.py"
        # Unbalanced quote — shlex.split will raise ValueError.
        result = run_quality_gate(
            changed_files=[f],
            project_root=project_with_config,
            hook_command='pre-commit run "unclosed {files}',
        )

        assert result.passed is False
        assert result.skipped is False
        assert "failed to parse" in result.output
