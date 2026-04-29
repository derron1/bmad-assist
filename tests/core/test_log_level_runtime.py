"""Tests for the runtime log-level control file behaviour.

The runner periodically polls ``.bmad-assist/runtime/log-level`` so the
dashboard / TUI can change verbosity mid-run. A previous run can leave a
stale value behind; without baseline tracking that stale value silently
demotes the next run's CLI ``--debug`` flag and breaks DEBUG-gated
subsystems (DebugJsonLogger writes nothing, etc.). These tests pin the
fix: the first poll captures a baseline and never applies it; only later
writes (during the run) take effect.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from bmad_assist import cli_utils
from bmad_assist.cli_utils import (
    check_log_level_file,
    reset_log_level_file_baseline,
    update_log_level,
)


@pytest.fixture(autouse=True)
def _isolate_module_state() -> None:
    """Reset module-level baseline + log-level state between tests."""
    reset_log_level_file_baseline()
    cli_utils._current_log_level = "WARNING"
    yield
    reset_log_level_file_baseline()
    cli_utils._current_log_level = "WARNING"


def _write_level(project: Path, level: str) -> Path:
    runtime = project / ".bmad-assist" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    f = runtime / "log-level"
    f.write_text(level)
    return f


def test_stale_file_does_not_override_cli_flag(tmp_path: Path) -> None:
    """A pre-existing log-level file from a previous run is captured as
    baseline and ignored — the CLI flag's level wins.
    """
    _write_level(tmp_path, "info")
    # Simulate CLI flag having set DEBUG already.
    update_log_level("DEBUG")
    assert cli_utils._current_log_level == "DEBUG"

    check_log_level_file(tmp_path)

    # Logging level is unchanged — the stale "info" file did not demote it.
    assert cli_utils._current_log_level == "DEBUG"


def test_dashboard_write_during_run_is_applied(tmp_path: Path) -> None:
    """A new value the dashboard writes *during* the run is applied."""
    _write_level(tmp_path, "info")
    update_log_level("DEBUG")

    # First poll: baseline capture (no change).
    check_log_level_file(tmp_path)
    assert cli_utils._current_log_level == "DEBUG"

    # Dashboard writes a different level while running.
    _write_level(tmp_path, "warning")
    check_log_level_file(tmp_path)

    assert cli_utils._current_log_level == "WARNING"


def test_dashboard_can_re_apply_baseline_value_after_change(tmp_path: Path) -> None:
    """Once we've moved off baseline, returning to a previously-seen value
    is treated as a real change and applied.
    """
    _write_level(tmp_path, "info")
    update_log_level("DEBUG")

    check_log_level_file(tmp_path)  # baseline = "INFO"
    _write_level(tmp_path, "warning")
    check_log_level_file(tmp_path)  # baseline now "WARNING"
    assert cli_utils._current_log_level == "WARNING"

    # Dashboard flips back to "INFO" — that's a fresh intent now, apply it.
    _write_level(tmp_path, "info")
    check_log_level_file(tmp_path)

    assert cli_utils._current_log_level == "INFO"


def test_no_file_means_no_override(tmp_path: Path) -> None:
    """Absence of the file is a no-op (no baseline to apply, no change)."""
    update_log_level("DEBUG")
    check_log_level_file(tmp_path)
    assert cli_utils._current_log_level == "DEBUG"


def test_repeated_polls_with_unchanged_file_do_not_log(tmp_path: Path) -> None:
    """Polling repeatedly with the file unchanged shouldn't emit the
    "Log level changed to" log line. Catching this guards against the
    chatty behaviour where stale files re-fired the log on every tick.
    """
    _write_level(tmp_path, "info")
    update_log_level("DEBUG")

    with patch.object(logging, "info") as mock_info:
        check_log_level_file(tmp_path)  # baseline capture
        check_log_level_file(tmp_path)  # second poll, file unchanged
        check_log_level_file(tmp_path)  # third poll, file unchanged

    assert not mock_info.called, (
        f"Expected silent baseline-only polls but got {mock_info.call_args_list}"
    )


def test_malformed_file_is_ignored_silently(tmp_path: Path) -> None:
    """Garbage in the file shouldn't crash the runner or downgrade level."""
    _write_level(tmp_path, "not-a-real-level")
    update_log_level("DEBUG")

    check_log_level_file(tmp_path)

    assert cli_utils._current_log_level == "DEBUG"
