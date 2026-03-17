"""Tests for provider trace writing and phase telemetry in BaseHandler.

Covers:
- _write_provider_trace: written when debug logging enabled, absent otherwise
- _timing_outputs: compile_ms / invoke_ms captured and surfaced in PhaseResult
- PhaseInvocation / PhaseEvent: accept compile_ms / invoke_ms fields
"""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bmad_assist.core.config import Config, MasterProviderConfig, ProviderConfig
from bmad_assist.core.loop.run_tracking import PhaseEvent, PhaseEventType, PhaseInvocation, PhaseStatus
from bmad_assist.providers.base import ProviderResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Logger that _write_provider_trace checks for debug-level enablement
_BASE_LOGGER = "bmad_assist.core.loop.handlers.base"


def _make_config(project_path: Path) -> Config:
    """Return a Config object with minimal valid settings."""
    return Config(
        providers=ProviderConfig(
            master=MasterProviderConfig(provider="claude", model="claude-3-opus-20240229"),
        ),
        timeout=300,
    )


def _make_handler(tmp_path: Path):
    """Return a DevStoryHandler instance pointed at tmp_path."""
    from bmad_assist.core.loop.handlers.dev_story import DevStoryHandler

    config = _make_config(tmp_path)
    return DevStoryHandler(config=config, project_path=tmp_path)


def _make_provider_result(**kwargs) -> ProviderResult:
    """Return a ProviderResult with sensible defaults."""
    defaults = dict(
        stdout="response text",
        stderr="",
        exit_code=0,
        model="claude-3-opus",
        duration_ms=1234,
        termination_reason=None,
        termination_info=None,
        command="claude --model claude-3-opus",
    )
    defaults.update(kwargs)
    return ProviderResult(**defaults)


# ---------------------------------------------------------------------------
# Provider trace: written in debug mode
# ---------------------------------------------------------------------------


class TestProviderTraceWritten:
    def test_debug_enabled_writes_trace_file(self, tmp_path: Path) -> None:
        """When DEBUG logging is on at construction, invoke_provider() writes a JSONL trace file."""
        # Set logger to DEBUG *before* constructing handler so _trace_enabled captures it
        base_logger = logging.getLogger(_BASE_LOGGER)
        original_level = base_logger.level
        original_propagate = base_logger.propagate
        try:
            base_logger.setLevel(logging.DEBUG)
            base_logger.propagate = False

            handler = _make_handler(tmp_path)
            result = _make_provider_result()

            with patch.object(handler, "get_provider") as mock_get_provider, \
                 patch.object(handler, "get_model", return_value="claude-3-opus"), \
                 patch.object(handler, "get_cli_model", return_value="claude-3-opus-20240229"), \
                 patch("bmad_assist.core.loop.handlers.base.invoke_with_timeout_retry",
                       return_value=result), \
                 patch("bmad_assist.core.loop.handlers.base.get_phase_timeout", return_value=60), \
                 patch("bmad_assist.core.loop.handlers.base.get_phase_retries", return_value=1):

                mock_get_provider.return_value = MagicMock(provider_name="claude")
                handler.invoke_provider("test prompt")

        finally:
            base_logger.setLevel(original_level)
            base_logger.propagate = original_propagate

        debug_dir = tmp_path / ".bmad-assist" / "debug"
        trace_files = list(debug_dir.glob(f"provider-{handler.phase_name}-*.jsonl"))
        assert len(trace_files) == 1, f"Expected 1 trace file, found: {trace_files}"

        record = json.loads(trace_files[0].read_text().strip())
        assert record["phase"] == handler.phase_name
        assert "prompt_tokens" in record
        assert "response_tokens" in record
        assert "duration_ms" in record

    def test_debug_disabled_no_trace_file(self, tmp_path: Path) -> None:
        """When logging is at WARNING at construction, no trace file is written."""
        base_logger = logging.getLogger(_BASE_LOGGER)
        original_level = base_logger.level
        try:
            base_logger.setLevel(logging.WARNING)

            handler = _make_handler(tmp_path)
            result = _make_provider_result()

            with patch.object(handler, "get_provider") as mock_get_provider, \
                 patch.object(handler, "get_model", return_value="claude-3-opus"), \
                 patch.object(handler, "get_cli_model", return_value="claude-3-opus-20240229"), \
                 patch("bmad_assist.core.loop.handlers.base.invoke_with_timeout_retry",
                       return_value=result), \
                 patch("bmad_assist.core.loop.handlers.base.get_phase_timeout", return_value=60), \
                 patch("bmad_assist.core.loop.handlers.base.get_phase_retries", return_value=1):

                mock_get_provider.return_value = MagicMock(provider_name="claude")
                handler.invoke_provider("test prompt")

        finally:
            base_logger.setLevel(original_level)

        debug_dir = tmp_path / ".bmad-assist" / "debug"
        assert not debug_dir.exists() or not list(debug_dir.glob("provider-*.jsonl"))

    def test_trace_write_failure_does_not_raise(self, tmp_path: Path) -> None:
        """An OSError during trace write is suppressed — phase is not affected."""
        base_logger = logging.getLogger(_BASE_LOGGER)
        original_level = base_logger.level
        original_propagate = base_logger.propagate
        try:
            base_logger.setLevel(logging.DEBUG)
            base_logger.propagate = False

            handler = _make_handler(tmp_path)
            result = _make_provider_result()

            with patch.object(handler, "get_provider") as mock_get_provider, \
                 patch.object(handler, "get_model", return_value="claude-3-opus"), \
                 patch.object(handler, "get_cli_model", return_value="claude-3-opus-20240229"), \
                 patch("bmad_assist.core.loop.handlers.base.invoke_with_timeout_retry",
                       return_value=result), \
                 patch("bmad_assist.core.loop.handlers.base.get_phase_timeout", return_value=60), \
                 patch("bmad_assist.core.loop.handlers.base.get_phase_retries", return_value=1), \
                 patch("builtins.open", side_effect=OSError("disk full")):

                mock_get_provider.return_value = MagicMock(provider_name="claude")
                # Should NOT raise despite the OSError
                returned = handler.invoke_provider("test prompt")
                assert returned is result  # result still returned normally

        finally:
            base_logger.setLevel(original_level)
            base_logger.propagate = original_propagate

    def test_trace_json_error_does_not_raise(self, tmp_path: Path) -> None:
        """A TypeError from JSON serialization is suppressed."""
        base_logger = logging.getLogger(_BASE_LOGGER)
        original_level = base_logger.level
        original_propagate = base_logger.propagate
        try:
            base_logger.setLevel(logging.DEBUG)
            base_logger.propagate = False

            handler = _make_handler(tmp_path)
            result = _make_provider_result()

            # Patch json.dumps inside the base module to raise TypeError
            with patch.object(handler, "get_provider") as mock_get_provider, \
                 patch.object(handler, "get_model", return_value="claude-3-opus"), \
                 patch.object(handler, "get_cli_model", return_value="claude-3-opus-20240229"), \
                 patch("bmad_assist.core.loop.handlers.base.invoke_with_timeout_retry",
                       return_value=result), \
                 patch("bmad_assist.core.loop.handlers.base.get_phase_timeout", return_value=60), \
                 patch("bmad_assist.core.loop.handlers.base.get_phase_retries", return_value=1), \
                 patch("bmad_assist.core.loop.handlers.base.json.dumps",
                       side_effect=TypeError("not serializable")):

                mock_get_provider.return_value = MagicMock(provider_name="claude")
                # Must not raise despite json.dumps TypeError
                returned = handler.invoke_provider("test prompt")
                assert returned is result

        finally:
            base_logger.setLevel(original_level)
            base_logger.propagate = original_propagate

    def test_env_var_overrides_log_level(self, tmp_path: Path) -> None:
        """BMAD_PROVIDER_TRACE=1 forces traces on even at WARNING level."""
        base_logger = logging.getLogger(_BASE_LOGGER)
        original_level = base_logger.level
        try:
            base_logger.setLevel(logging.WARNING)

            with patch.dict("os.environ", {"BMAD_PROVIDER_TRACE": "1"}):
                handler = _make_handler(tmp_path)

            result = _make_provider_result()

            with patch.object(handler, "get_provider") as mock_get_provider, \
                 patch.object(handler, "get_model", return_value="claude-3-opus"), \
                 patch.object(handler, "get_cli_model", return_value="claude-3-opus-20240229"), \
                 patch("bmad_assist.core.loop.handlers.base.invoke_with_timeout_retry",
                       return_value=result), \
                 patch("bmad_assist.core.loop.handlers.base.get_phase_timeout", return_value=60), \
                 patch("bmad_assist.core.loop.handlers.base.get_phase_retries", return_value=1):

                mock_get_provider.return_value = MagicMock(provider_name="claude")
                handler.invoke_provider("test prompt")

        finally:
            base_logger.setLevel(original_level)

        debug_dir = tmp_path / ".bmad-assist" / "debug"
        trace_files = list(debug_dir.glob(f"provider-{handler.phase_name}-*.jsonl"))
        assert len(trace_files) == 1, f"Expected 1 trace file, found: {trace_files}"

    def test_env_var_disables_trace(self, tmp_path: Path) -> None:
        """BMAD_PROVIDER_TRACE=0 suppresses traces even at DEBUG level."""
        base_logger = logging.getLogger(_BASE_LOGGER)
        original_level = base_logger.level
        try:
            base_logger.setLevel(logging.DEBUG)

            with patch.dict("os.environ", {"BMAD_PROVIDER_TRACE": "0"}):
                handler = _make_handler(tmp_path)

            result = _make_provider_result()

            with patch.object(handler, "get_provider") as mock_get_provider, \
                 patch.object(handler, "get_model", return_value="claude-3-opus"), \
                 patch.object(handler, "get_cli_model", return_value="claude-3-opus-20240229"), \
                 patch("bmad_assist.core.loop.handlers.base.invoke_with_timeout_retry",
                       return_value=result), \
                 patch("bmad_assist.core.loop.handlers.base.get_phase_timeout", return_value=60), \
                 patch("bmad_assist.core.loop.handlers.base.get_phase_retries", return_value=1):

                mock_get_provider.return_value = MagicMock(provider_name="claude")
                handler.invoke_provider("test prompt")

        finally:
            base_logger.setLevel(original_level)

        debug_dir = tmp_path / ".bmad-assist" / "debug"
        assert not debug_dir.exists() or not list(debug_dir.glob("provider-*.jsonl"))

    def test_log_level_change_after_construction_still_traces(self, tmp_path: Path) -> None:
        """Trace flag captured at construction — later log level change has no effect."""
        base_logger = logging.getLogger(_BASE_LOGGER)
        original_level = base_logger.level
        original_propagate = base_logger.propagate
        try:
            # Construct at DEBUG
            base_logger.setLevel(logging.DEBUG)
            base_logger.propagate = False
            handler = _make_handler(tmp_path)

            # Change to WARNING after construction
            base_logger.setLevel(logging.WARNING)

            result = _make_provider_result()

            with patch.object(handler, "get_provider") as mock_get_provider, \
                 patch.object(handler, "get_model", return_value="claude-3-opus"), \
                 patch.object(handler, "get_cli_model", return_value="claude-3-opus-20240229"), \
                 patch("bmad_assist.core.loop.handlers.base.invoke_with_timeout_retry",
                       return_value=result), \
                 patch("bmad_assist.core.loop.handlers.base.get_phase_timeout", return_value=60), \
                 patch("bmad_assist.core.loop.handlers.base.get_phase_retries", return_value=1):

                mock_get_provider.return_value = MagicMock(provider_name="claude")
                handler.invoke_provider("test prompt")

        finally:
            base_logger.setLevel(original_level)
            base_logger.propagate = original_propagate

        debug_dir = tmp_path / ".bmad-assist" / "debug"
        trace_files = list(debug_dir.glob(f"provider-{handler.phase_name}-*.jsonl"))
        assert len(trace_files) == 1, f"Trace should still be written despite log level change"


# ---------------------------------------------------------------------------
# Phase telemetry: compile_ms / invoke_ms captured
# ---------------------------------------------------------------------------


class TestPhaseTelemetry:
    def test_compile_ms_captured_in_render_prompt(self, tmp_path: Path) -> None:
        """After render_prompt(), handler._compile_ms is a non-negative integer."""
        handler = _make_handler(tmp_path)

        with patch.object(handler, "_try_compile_workflow", return_value="<prompt/>"):
            handler.render_prompt(MagicMock())

        assert handler._compile_ms is not None
        assert handler._compile_ms >= 0

    def test_invoke_ms_captured_in_invoke_provider(self, tmp_path: Path) -> None:
        """After invoke_provider(), handler._invoke_ms is a non-negative integer."""
        handler = _make_handler(tmp_path)
        result = _make_provider_result()

        with patch.object(handler, "get_provider") as mock_get_provider, \
             patch.object(handler, "get_model", return_value="claude-3-opus"), \
             patch.object(handler, "get_cli_model", return_value="claude-3-opus-20240229"), \
             patch("bmad_assist.core.loop.handlers.base.invoke_with_timeout_retry",
                   return_value=result), \
             patch("bmad_assist.core.loop.handlers.base.get_phase_timeout", return_value=60), \
             patch("bmad_assist.core.loop.handlers.base.get_phase_retries", return_value=1):

            mock_get_provider.return_value = MagicMock(provider_name="claude")
            handler.invoke_provider("test prompt")

        assert handler._invoke_ms is not None
        assert handler._invoke_ms >= 0

    def test_timing_outputs_in_base_execute(self, tmp_path: Path) -> None:
        """DevStoryHandler.execute() (via BaseHandler) includes compile_ms and invoke_ms."""
        from bmad_assist.core.state import State

        handler = _make_handler(tmp_path)
        result = _make_provider_result()
        state = MagicMock(spec=State)
        state.current_epic = "1"
        state.current_story = "1.1"

        with patch.object(handler, "render_prompt", return_value="<prompt/>") as mock_render, \
             patch.object(handler, "invoke_provider", return_value=result) as mock_invoke, \
             patch("bmad_assist.core.io.save_prompt"):

            # Simulate that render_prompt set _compile_ms and invoke_provider set _invoke_ms
            def set_compile_ms(_state):
                handler._compile_ms = 42
                return "<prompt/>"

            def set_invoke_ms(_prompt, **kwargs):
                handler._invoke_ms = 300
                return result

            mock_render.side_effect = set_compile_ms
            mock_invoke.side_effect = set_invoke_ms

            phase_result = handler.execute(state)

        assert phase_result.success
        assert phase_result.outputs.get("compile_ms") == 42
        assert phase_result.outputs.get("invoke_ms") == 300

    def test_timing_outputs_empty_when_not_measured(self, tmp_path: Path) -> None:
        """_timing_outputs() returns {} when neither metric was captured."""
        handler = _make_handler(tmp_path)
        assert handler._timing_outputs() == {}

    def test_execute_resets_timing_at_start(self, tmp_path: Path) -> None:
        """execute() clears stale _compile_ms / _invoke_ms from a previous invocation."""
        from bmad_assist.core.state import State

        handler = _make_handler(tmp_path)
        handler._compile_ms = 9999  # Stale from previous run
        handler._invoke_ms = 8888

        result = _make_provider_result()
        state = MagicMock(spec=State)
        state.current_epic = "1"
        state.current_story = "1.1"

        with patch.object(handler, "render_prompt", return_value="<prompt/>") as mock_render, \
             patch.object(handler, "invoke_provider", return_value=result), \
             patch("bmad_assist.core.io.save_prompt"):

            # render_prompt does NOT set _compile_ms (simulates failed compile)
            mock_render.return_value = "<prompt/>"
            # Stale values should be cleared at the start of execute()
            def check_reset(_state):
                # At this point execute() should have already reset the attrs
                assert handler._compile_ms is None, "compile_ms not reset at execute() start"
                assert handler._invoke_ms is None, "invoke_ms not reset at execute() start"
                return "<prompt/>"

            mock_render.side_effect = check_reset
            handler.execute(state)


# ---------------------------------------------------------------------------
# run_tracking model fields
# ---------------------------------------------------------------------------


class TestRunTrackingFields:
    def test_phase_invocation_records_compile_invoke_ms(self) -> None:
        """PhaseInvocation accepts and serialises compile_ms / invoke_ms."""
        from datetime import UTC, datetime

        inv = PhaseInvocation(
            phase="dev_story",
            started_at=datetime.now(UTC),
            provider="claude",
            model="claude-3-opus",
            status=PhaseStatus.SUCCESS,
            compile_ms=50,
            invoke_ms=2500,
        )
        data = inv.model_dump(mode="json")
        assert data["compile_ms"] == 50
        assert data["invoke_ms"] == 2500

    def test_phase_invocation_compile_invoke_ms_defaults_none(self) -> None:
        """PhaseInvocation compile_ms / invoke_ms default to None."""
        from datetime import UTC, datetime

        inv = PhaseInvocation(
            phase="code_review",
            started_at=datetime.now(UTC),
            provider="claude",
            model="claude-3-opus",
            status=PhaseStatus.SUCCESS,
        )
        assert inv.compile_ms is None
        assert inv.invoke_ms is None

    def test_phase_event_records_compile_invoke_ms(self) -> None:
        """PhaseEvent accepts and serialises compile_ms / invoke_ms."""
        from datetime import UTC, datetime

        event = PhaseEvent(
            event_type=PhaseEventType.COMPLETED,
            phase="dev_story",
            timestamp=datetime.now(UTC),
            provider="claude",
            model="claude-3-opus",
            compile_ms=75,
            invoke_ms=3100,
        )
        data = event.model_dump(mode="json")
        assert data["compile_ms"] == 75
        assert data["invoke_ms"] == 3100
