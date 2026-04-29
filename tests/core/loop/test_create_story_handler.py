"""Tests for CreateStoryHandler."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bmad_assist.core.config import Config, MasterProviderConfig, ProviderConfig
from bmad_assist.core.loop.handlers.create_story import (
    _FINALIZATION_SUFFIX,
    MAX_RETRIES,
    MIN_STORY_CONTENT_LENGTH,
    REQUIRED_SECTIONS,
    CreateStoryHandler,
    _extract_story_content,
    _find_story_file,
    _validate_story_content,
    _write_rescued_story,
)
from bmad_assist.core.loop.types import PhaseResult
from bmad_assist.core.state import State
from bmad_assist.providers.base import ProviderResult
from bmad_assist.providers.tool_guard import GUARD_TERMINATION_PREFIX


def _make_handler(project_path: Path | None = None) -> CreateStoryHandler:
    config = Config(
        providers=ProviderConfig(
            master=MasterProviderConfig(provider="claude", model="opus"),
        ),
    )
    return CreateStoryHandler(config, project_path or Path("/tmp"))


def _make_story_content(
    story_id: str = "3.2",
    title: str = "Widget Factory",
    extra: str = "",
) -> str:
    """Build a realistic story content string."""
    return (
        f"# Story {story_id}: {title}\n\n"
        "## Story\nAs a user I want to do something useful.\n\n"
        "## Acceptance Criteria\n- AC1: Something works\n- AC2: Something else works\n\n"
        "## Tasks\n- [ ] Task 1: Implement the thing\n- [ ] Task 2: Test the thing\n\n"
        "## Technical Notes\nSome detailed notes about implementation that make "
        "this content long enough to pass the minimum length check. "
        "We need at least 400 characters so let's add some more detail here. "
        "The widget factory should support multiple widget types including "
        "standard widgets, premium widgets, and custom widgets.\n" + extra
    )


def _make_provider_result(
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
    duration_ms: int = 1234,
    model: str | None = "opus",
    termination_info: dict[str, object] | None = None,
    termination_reason: str | None = None,
) -> ProviderResult:
    """Build a ProviderResult for handler tests."""
    return ProviderResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        duration_ms=duration_ms,
        model=model,
        command=("claude",),
        termination_info=termination_info,
        termination_reason=termination_reason,
    )


class TestCreateStoryTimingTracking:
    """Test timing tracking for create-story workflow."""

    def test_track_timing_enabled(self) -> None:
        """CreateStoryHandler has track_timing = True."""
        handler = _make_handler()
        assert handler.track_timing is True

    def test_timing_workflow_id(self) -> None:
        """CreateStoryHandler has timing_workflow_id = 'create-story'."""
        handler = _make_handler()
        assert handler.timing_workflow_id == "create-story"

    def test_phase_name(self) -> None:
        """CreateStoryHandler has phase_name = 'create_story'."""
        handler = _make_handler()
        assert handler.phase_name == "create_story"

    def test_only_required_methods_implemented(self) -> None:
        """CreateStoryHandler implements expected methods plus timing."""
        defined_methods = [
            name
            for name, value in CreateStoryHandler.__dict__.items()
            if callable(value) or isinstance(value, property)
        ]

        expected = {"phase_name", "build_context", "track_timing", "timing_workflow_id", "execute"}
        actual = {name for name in defined_methods if not name.startswith("_")}

        assert actual == expected, f"Extra methods: {actual - expected}"

    def test_execute_overrides_base(self) -> None:
        """CreateStoryHandler overrides execute() from BaseHandler."""
        from bmad_assist.core.loop.handlers.base import BaseHandler

        assert CreateStoryHandler.execute is not BaseHandler.execute


class TestFindStoryFile:
    """Tests for _find_story_file."""

    def test_file_exists_numeric_epic(self, tmp_path: Path) -> None:
        """Finds story file with numeric epic ID."""
        stories_dir = tmp_path / "stories"
        stories_dir.mkdir()
        story_file = stories_dir / "3-2-widget-factory.md"
        story_file.write_text("# Story 3.2: Widget Factory")

        state = State(current_epic=3, current_story="3.2")

        with patch("bmad_assist.core.loop.handlers.create_story.get_paths") as mock_paths:
            mock_paths.return_value = MagicMock(stories_dir=stories_dir)
            result = _find_story_file(state)

        assert result == story_file

    def test_file_exists_string_epic(self, tmp_path: Path) -> None:
        """Finds story file with string epic ID."""
        stories_dir = tmp_path / "stories"
        stories_dir.mkdir()
        story_file = stories_dir / "testarch-1-framework-init.md"
        story_file.write_text("# Story testarch.1: Framework Init")

        state = State(current_epic="testarch", current_story="testarch.1")

        with patch("bmad_assist.core.loop.handlers.create_story.get_paths") as mock_paths:
            mock_paths.return_value = MagicMock(stories_dir=stories_dir)
            result = _find_story_file(state)

        assert result == story_file

    def test_file_not_found(self, tmp_path: Path) -> None:
        """Returns None when no matching story file exists."""
        stories_dir = tmp_path / "stories"
        stories_dir.mkdir()

        state = State(current_epic=3, current_story="3.2")

        with patch("bmad_assist.core.loop.handlers.create_story.get_paths") as mock_paths:
            mock_paths.return_value = MagicMock(stories_dir=stories_dir)
            result = _find_story_file(state)

        assert result is None

    def test_none_state(self) -> None:
        """Returns None when state has no epic/story."""
        state = State(current_epic=None, current_story=None)
        result = _find_story_file(state)
        assert result is None

    def test_story_without_dot(self) -> None:
        """Returns None when story ID has no dot separator."""
        state = State(current_epic=3, current_story="nodot")
        result = _find_story_file(state)
        assert result is None

    def test_stories_dir_missing(self, tmp_path: Path) -> None:
        """Returns None when stories directory doesn't exist."""
        state = State(current_epic=3, current_story="3.2")

        with patch("bmad_assist.core.loop.handlers.create_story.get_paths") as mock_paths:
            mock_paths.return_value = MagicMock(stories_dir=tmp_path / "nonexistent")
            result = _find_story_file(state)

        assert result is None


class TestExtractStoryContent:
    """Tests for _extract_story_content."""

    def test_clean_content(self) -> None:
        """Extracts clean story content."""
        content = _make_story_content("3.2", "Widget Factory")
        result_content, result_title = _extract_story_content(content)

        assert result_content is not None
        assert result_title == "Widget Factory"
        assert "## Acceptance Criteria" in result_content

    def test_with_leading_noise(self) -> None:
        """Extracts content with LLM preamble before story."""
        output = "Sure, I'll create the story for you.\n\n" + _make_story_content()
        result_content, result_title = _extract_story_content(output)

        assert result_content is not None
        assert result_content.startswith("# Story")

    def test_with_trailing_commentary(self) -> None:
        """Trims trailing LLM commentary."""
        output = _make_story_content() + "\n\nI've created the story file for you."
        result_content, _ = _extract_story_content(output)

        assert result_content is not None
        assert "I've created the story" not in result_content

    def test_with_code_blocks(self) -> None:
        """Handles content wrapped in code blocks."""
        output = "```markdown\n" + _make_story_content() + "\n```"
        result_content, result_title = _extract_story_content(output)

        assert result_content is not None
        assert result_title is not None

    def test_no_header(self) -> None:
        """Returns None when no story header found."""
        result_content, result_title = _extract_story_content("Some random text")
        assert result_content is None
        assert result_title is None

    def test_empty_input(self) -> None:
        """Returns None for empty input."""
        result_content, result_title = _extract_story_content("")
        assert result_content is None
        assert result_title is None

    def test_tool_call_trimming(self) -> None:
        """Trims content at tool call markers."""
        output = _make_story_content() + "\n<tool_call>\nwrite_file(...)\n</tool_call>"
        result_content, _ = _extract_story_content(output)

        assert result_content is not None
        assert "<tool_call>" not in result_content


class TestValidateStoryContent:
    """Tests for _validate_story_content."""

    def test_valid_content(self) -> None:
        """Passes for well-formed story content."""
        content = _make_story_content()
        assert _validate_story_content(content) is True

    def test_too_short(self) -> None:
        """Fails for content shorter than MIN_STORY_CONTENT_LENGTH."""
        assert (
            _validate_story_content("# Story\n## Story\n## Acceptance Criteria\n## Tasks") is False
        )

    def test_missing_sections(self) -> None:
        """Fails when required sections are missing."""
        content = "x" * (MIN_STORY_CONTENT_LENGTH + 100) + "\n## Story\n## Tasks\n"
        assert _validate_story_content(content) is False  # Missing "## Acceptance Criteria"

    def test_boundary_length(self) -> None:
        """Content exactly at MIN_STORY_CONTENT_LENGTH with sections passes."""
        # Build content that's exactly at the boundary
        # Sections must start at beginning of line (valid markdown headings)
        sections = "\n## Story\nContent\n## Acceptance Criteria\nAC\n## Tasks\nTask\n"
        padding = "x" * (MIN_STORY_CONTENT_LENGTH - len(sections))
        content = padding + sections
        assert len(content) >= MIN_STORY_CONTENT_LENGTH
        assert _validate_story_content(content) is True

    def test_h3_required_sections(self) -> None:
        """Story with ### heading level passes validation."""
        content = (
            "x" * MIN_STORY_CONTENT_LENGTH
            + "\n### Story\nAs a user...\n"
            + "### Acceptance Criteria\n- AC1\n"
            + "### Tasks\n- Task 1\n"
        )
        assert _validate_story_content(content) is True


class TestWriteRescuedStory:
    """Tests for _write_rescued_story."""

    def test_correct_path(self, tmp_path: Path) -> None:
        """Writes to correct path with slug from title."""
        state = State(current_epic=3, current_story="3.2")
        content = _make_story_content()

        with patch("bmad_assist.core.loop.handlers.create_story.get_paths") as mock_paths:
            mock_paths.return_value = MagicMock(stories_dir=tmp_path)
            result = _write_rescued_story(state, content, "Widget Factory")

        assert result.parent == tmp_path
        assert result.name == "3-2-widget-factory.md"
        assert result.read_text(encoding="utf-8") == content

    def test_none_title_uses_untitled(self, tmp_path: Path) -> None:
        """Uses 'untitled' slug when title is None."""
        state = State(current_epic=5, current_story="5.1")
        content = _make_story_content()

        with patch("bmad_assist.core.loop.handlers.create_story.get_paths") as mock_paths:
            mock_paths.return_value = MagicMock(stories_dir=tmp_path)
            result = _write_rescued_story(state, content, None)

        assert result.name == "5-1-untitled.md"

    def test_string_epic(self, tmp_path: Path) -> None:
        """Works with string-based epic IDs."""
        state = State(current_epic="testarch", current_story="testarch.3")
        content = _make_story_content("testarch.3", "Test Setup")

        with patch("bmad_assist.core.loop.handlers.create_story.get_paths") as mock_paths:
            mock_paths.return_value = MagicMock(stories_dir=tmp_path)
            result = _write_rescued_story(state, content, "Test Setup")

        assert result.name == "testarch-3-test-setup.md"


class TestExecuteIntegration:
    """Integration tests for CreateStoryHandler.execute()."""

    def test_happy_path_file_exists(self, tmp_path: Path) -> None:
        """Returns success when story file exists after first attempt."""
        handler = _make_handler(tmp_path)
        state = State(current_epic=3, current_story="3.2")
        provider_result = _make_provider_result(stdout="done")

        with (
            patch.object(handler, "render_prompt", return_value="prompt"),
            patch.object(handler, "invoke_provider", return_value=provider_result) as mock_invoke,
            patch(
                "bmad_assist.core.loop.handlers.create_story._find_story_file",
                return_value=tmp_path / "3-2-story.md",
            ),
            patch("bmad_assist.core.io.save_prompt"),
        ):
            result = handler.execute(state)

        assert result.success
        mock_invoke.assert_called_once_with("prompt")

    def test_rescue_path(self, tmp_path: Path) -> None:
        """Rescues story from stdout when file is missing."""
        handler = _make_handler(tmp_path)
        state = State(current_epic=3, current_story="3.2")
        story_content = _make_story_content("3.2", "Widget Factory")
        provider_result = _make_provider_result(stdout=story_content)

        with (
            patch.object(handler, "render_prompt", return_value="prompt"),
            patch.object(handler, "invoke_provider", return_value=provider_result),
            patch(
                "bmad_assist.core.loop.handlers.create_story._find_story_file",
                return_value=None,
            ),
            patch(
                "bmad_assist.core.loop.handlers.create_story._write_rescued_story",
                return_value=tmp_path / "3-2-widget-factory.md",
            ) as mock_write,
            patch("bmad_assist.core.io.save_prompt"),
        ):
            result = handler.execute(state)

        assert result.success
        assert "rescued_file" in result.outputs
        mock_write.assert_called_once()

    def test_retry_then_success(self, tmp_path: Path) -> None:
        """Retries when rescue fails, succeeds on subsequent attempt."""
        handler = _make_handler(tmp_path)
        state = State(current_epic=3, current_story="3.2")

        # First attempt: no file, no extractable content
        bad_result = _make_provider_result(stdout="I couldn't save the file")
        # Second attempt: file exists
        good_result = _make_provider_result(stdout="done")

        call_count = 0

        def mock_invoke(prompt: str) -> ProviderResult:
            nonlocal call_count
            call_count += 1
            return bad_result if call_count == 1 else good_result

        # _find_story_file is called once pre-loop (mtime baseline) and
        # then once per attempt. Calls 1 (pre-loop) and 2 (attempt 1)
        # return None — no file present yet — so the attempt-1 fresh
        # check fails. Call 3 (attempt 2) returns a path: the LLM has
        # written the file, fresh check passes, handler returns ok.
        find_count = 0

        def mock_find(s: State) -> Path | None:
            nonlocal find_count
            find_count += 1
            return None if find_count <= 2 else tmp_path / "3-2-story.md"

        with (
            patch.object(handler, "render_prompt", return_value="prompt"),
            patch.object(handler, "invoke_provider", side_effect=mock_invoke),
            patch(
                "bmad_assist.core.loop.handlers.create_story._find_story_file",
                side_effect=mock_find,
            ),
            patch("bmad_assist.core.io.save_prompt"),
        ):
            result = handler.execute(state)

        assert result.success
        assert call_count == 2

    def test_all_retries_exhausted(self, tmp_path: Path) -> None:
        """Fails after MAX_RETRIES + 1 attempts."""
        handler = _make_handler(tmp_path)
        state = State(current_epic=3, current_story="3.2")
        bad_result = _make_provider_result(stdout="random noise")

        with (
            patch.object(handler, "render_prompt", return_value="prompt"),
            patch.object(handler, "invoke_provider", return_value=bad_result) as mock_invoke,
            patch(
                "bmad_assist.core.loop.handlers.create_story._find_story_file",
                return_value=None,
            ),
            patch("bmad_assist.core.io.save_prompt"),
        ):
            result = handler.execute(state)

        assert not result.success
        assert "not created after" in result.error
        assert mock_invoke.call_count == MAX_RETRIES + 1

    def test_provider_error_no_retry(self, tmp_path: Path) -> None:
        """Does not retry on provider errors (result.success=False)."""
        handler = _make_handler(tmp_path)
        state = State(current_epic=3, current_story="3.2")
        fail_result = _make_provider_result(stderr="Provider crashed", exit_code=1)

        with (
            patch.object(handler, "render_prompt", return_value="prompt"),
            patch.object(handler, "invoke_provider", return_value=fail_result) as mock_invoke,
            patch("bmad_assist.core.io.save_prompt"),
        ):
            result = handler.execute(state)

        assert not result.success
        assert result.error == "Provider crashed"
        mock_invoke.assert_called_once_with("prompt")

    def test_stale_file_does_not_short_circuit_success(self, tmp_path: Path) -> None:
        """A leftover story file from a previous run must not satisfy success.

        Without freshness checking, the handler returned ``ok`` on attempt 1
        whenever a same-named file existed on disk — even when the current
        LLM call wrote zero tokens. This test pins the fix: with a stale
        pre-existing file and an LLM that produces no rescuable content,
        the handler must exhaust retries and fail.
        """
        # Real file on disk so stat() works (mock _find_story_file returns it).
        stale_file = tmp_path / "3-2-stale.md"
        stale_file.write_text("# Story 3.2: stale leftover\n\ndoes not match validate.\n")
        # Backdate mtime so the freshness check sees pre_existing_mtime > 0
        # and the same-mtime read on subsequent calls is treated as stale.
        old_mtime = stale_file.stat().st_mtime - 60
        os.utime(stale_file, (old_mtime, old_mtime))

        handler = _make_handler(tmp_path)
        state = State(current_epic=3, current_story="3.2")
        bad_result = _make_provider_result(stdout="random noise")

        with (
            patch.object(handler, "render_prompt", return_value="prompt"),
            patch.object(handler, "invoke_provider", return_value=bad_result) as mock_invoke,
            patch(
                "bmad_assist.core.loop.handlers.create_story._find_story_file",
                return_value=stale_file,
            ),
            patch("bmad_assist.core.io.save_prompt"),
        ):
            result = handler.execute(state)

        # Must NOT short-circuit on the stale file.
        assert not result.success
        assert "not created after" in result.error
        # All MAX_RETRIES + 1 attempts ran.
        assert mock_invoke.call_count == MAX_RETRIES + 1

    def test_retry_mutates_prompt_for_cache_busting(self, tmp_path: Path) -> None:
        """Each no-file retry sends a different prompt to bypass prompt cache.

        Anthropic's server-side prompt cache returns a deterministic shallow
        response (~5 min TTL) when the same prompt is sent repeatedly. The
        handler appends a per-attempt recovery suffix on each miss so the
        bytes change and the cache misses.
        """
        handler = _make_handler(tmp_path)
        state = State(current_epic=3, current_story="3.2")
        bad_result = _make_provider_result(stdout="random noise")

        prompts_sent: list[str] = []

        def mock_invoke(prompt: str) -> ProviderResult:
            prompts_sent.append(prompt)
            return bad_result

        with (
            patch.object(handler, "render_prompt", return_value="BASE_PROMPT"),
            patch.object(handler, "invoke_provider", side_effect=mock_invoke),
            patch(
                "bmad_assist.core.loop.handlers.create_story._find_story_file",
                return_value=None,
            ),
            patch("bmad_assist.core.io.save_prompt"),
        ):
            handler.execute(state)

        # MAX_RETRIES + 1 = 4 attempts total.
        assert len(prompts_sent) == MAX_RETRIES + 1
        # First attempt: base prompt unchanged.
        assert prompts_sent[0] == "BASE_PROMPT"
        # Subsequent attempts: each appends a fresh nudge with the upcoming
        # attempt number embedded.
        for i in range(1, len(prompts_sent)):
            assert prompts_sent[i].startswith("BASE_PROMPT")
            assert "NO-FILE RECOVERY" in prompts_sent[i]
            assert f"retry {i + 1} of {MAX_RETRIES + 1}" in prompts_sent[i]
        # All prompts after the first must differ from each other (cache-bust).
        assert len(set(prompts_sent)) == len(prompts_sent)


class TestRateLimitRecovery:
    """Tests for ToolCallGuard termination recovery."""

    def test_rate_limit_with_rescuable_content(self, tmp_path: Path) -> None:
        """Guard termination rescues valid story output."""
        handler = _make_handler(tmp_path)
        state = State(current_epic=3, current_story="3.2")
        result = _make_provider_result(
            stdout=_make_story_content(),
            termination_info={"reason": "rate_exceeded"},
            termination_reason=f"{GUARD_TERMINATION_PREFIX}rate_exceeded:would_be_91/90",
        )

        with (
            patch.object(handler, "render_prompt", return_value="prompt"),
            patch.object(handler, "invoke_provider", return_value=result),
            patch(
                "bmad_assist.core.loop.handlers.create_story._write_rescued_story",
                return_value=tmp_path / "3-2-widget-factory.md",
            ),
            patch("bmad_assist.core.io.save_prompt"),
        ):
            phase_result = handler.execute(state)

        assert phase_result.success
        assert phase_result.outputs["rate_limit_rescued"] is True
        assert "termination_metadata" in phase_result.outputs

    def test_rate_limit_finalization_retry(self, tmp_path: Path) -> None:
        """Guard termination retries once with a finalization-only prompt."""
        handler = _make_handler(tmp_path)
        state = State(current_epic=3, current_story="3.2")
        guard_result = _make_provider_result(
            stdout="partial noise",
            termination_info={"reason": "rate_exceeded"},
            termination_reason=f"{GUARD_TERMINATION_PREFIX}rate_exceeded:would_be_91/90",
        )
        success_result = _make_provider_result(stdout="done")
        prompts: list[str] = []

        def mock_invoke(prompt: str) -> ProviderResult:
            prompts.append(prompt)
            return guard_result if len(prompts) == 1 else success_result

        with (
            patch.object(handler, "render_prompt", return_value="prompt"),
            patch.object(handler, "invoke_provider", side_effect=mock_invoke) as invoke_mock,
            patch(
                "bmad_assist.core.loop.handlers.create_story._find_story_file",
                return_value=tmp_path / "3-2-story.md",
            ),
            patch("bmad_assist.core.io.save_prompt"),
        ):
            phase_result = handler.execute(state)

        assert phase_result.success
        assert invoke_mock.call_count == 2
        assert prompts[1].endswith(_FINALIZATION_SUFFIX)

    def test_rate_limit_finalization_also_fails(self, tmp_path: Path) -> None:
        """Finalization retry fails after a second guard termination."""
        handler = _make_handler(tmp_path)
        state = State(current_epic=3, current_story="3.2")
        first_result = _make_provider_result(
            stdout="noise",
            termination_info={"reason": "rate_exceeded"},
            termination_reason=f"{GUARD_TERMINATION_PREFIX}rate_exceeded:would_be_91/90",
        )
        second_result = _make_provider_result(
            stdout="still noise",
            termination_info={"reason": "rate_exceeded"},
            termination_reason=f"{GUARD_TERMINATION_PREFIX}rate_exceeded:would_be_91/90",
        )

        with (
            patch.object(handler, "render_prompt", return_value="prompt"),
            patch.object(handler, "invoke_provider", side_effect=[first_result, second_result]),
            patch("bmad_assist.core.io.save_prompt"),
        ):
            phase_result = handler.execute(state)

        assert not phase_result.success
        assert "Guard terminated on finalization retry" in phase_result.error
        assert "termination_metadata" in phase_result.outputs


class TestInstructionsContextFirst:
    """Phase 6 placeholder: legacy bundled instructions.xml deleted.

    The CONTEXT-FIRST guidance previously asserted here lived in the
    bundled ``src/bmad_assist/workflows/create-story/instructions.xml``
    file, which Phase 6 removed. The same guidance is now applied via
    the LLM-driven patch transforms in
    ``.bmad-assist/patches/create-story.patch.yaml``; that flow is
    exercised end-to-end in ``tests/skill_layout/test_create_story_e2e.py``.
    """
