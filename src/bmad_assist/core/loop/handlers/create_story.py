"""CREATE_STORY phase handler.

Creates a new story file based on epic requirements using the Master LLM.

Includes story file rescue: if the LLM fails to save the story file,
extracts the story content from stdout and writes it to disk.

"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bmad_assist.core.loop.handlers.base import BaseHandler
from bmad_assist.core.loop.types import PhaseResult
from bmad_assist.core.paths import get_paths
from bmad_assist.core.state import State
from bmad_assist.providers.tool_guard import GUARD_TERMINATION_PREFIX

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
MIN_STORY_CONTENT_LENGTH = 400
REQUIRED_SECTION_PATTERNS = (
    re.compile(r"^#{2,}\s+Story\b", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^#{2,}\s+Acceptance\s+Criteria\b", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^#{2,}\s+Tasks?\b", re.MULTILINE | re.IGNORECASE),
)
# Keep for backward compat (exported in tests)
REQUIRED_SECTIONS = ("## Story", "## Acceptance Criteria", "## Tasks")

_STORY_HEADER_PATTERN = re.compile(r"^# Story\s+[\w.-]+\s*:\s*(.+)$", re.MULTILINE)

_STORY_END_PATTERNS = (
    "I've created the story",
    "I've written the story",
    "I have created the story",
    "I have written the story",
    "The story has been created",
    "The story has been written",
    "The story file has been",
    "Let me now",
    "Let me create",
    "Let me save",
    "Now let me",
    "Now I'll",
    "Now I will",
    "<tool_call>",
    "<invoke",
    "</result>",
)

_FINALIZATION_SUFFIX = (
    "\n\n--- RATE LIMIT RECOVERY ---\n"
    "The previous attempt was interrupted by a rate limit. "
    "Finalize the story NOW from the context already analyzed. "
    "Do NOT start new exploration or file reads. "
    "Write the story file immediately from what you know."
)


def _no_file_recovery_suffix(next_attempt: int, max_attempts: int, state: State) -> str:
    """Build a per-retry suffix for the no-file rescue loop.

    Each retry needs a distinct suffix for two reasons:

    1. Anthropic's prompt cache returns a deterministic shallow response
       (~5 min TTL) when the same prompt is sent repeatedly — that's what
       produces the 5–17s "Claude exited but wrote no file" misses we see
       on attempts 1-3 before attempt 4 finally does real work. Embedding
       the attempt number in the suffix changes the bytes, busting the
       cache.
    2. It also gives the LLM explicit feedback ("you didn't write the
       file") plus the expected output path for the Write tool, and an
       inline-rescue fallback so we can recover from stdout if Write is
       blocked.
    """
    epic = state.current_epic or "?"
    story_num = (
        state.current_story.split(".")[-1]
        if state.current_story and "." in state.current_story
        else "?"
    )
    try:
        stories_dir_hint = f"{get_paths().stories_dir}/"
    except RuntimeError:
        # Paths not initialized (e.g. unit tests). Skip the path hint.
        stories_dir_hint = ""
    return (
        f"\n\n--- NO-FILE RECOVERY (retry {next_attempt} of {max_attempts}) ---\n"
        f"The previous attempt did NOT save the story file to disk. "
        f"Stop exploration and write the story now using the Write tool. "
        f"Save to: {stories_dir_hint}{epic}-{story_num}-<slug>.md "
        f"(slug = kebab-case of story title).\n"
        f"If you cannot use Write, output the COMPLETE story markdown "
        f"inline starting with `# Story {epic}.{story_num}: <title>` so "
        f"the orchestrator can rescue it from stdout."
    )


def _find_story_file(state: State) -> Path | None:
    """Find the story file on disk after LLM execution.

    Globs for {epic}-{story_num}-*.md in the stories directory.

    Args:
        state: Current loop state with epic/story info.

    Returns:
        Path to story file if found, None otherwise.

    """
    if state.current_epic is None or state.current_story is None:
        return None

    # Extract story number from story ID (e.g., "3.2" -> "2")
    if "." not in state.current_story:
        return None
    story_num = state.current_story.split(".")[-1]

    paths = get_paths()
    stories_dir = paths.stories_dir

    if not stories_dir.exists():
        return None

    pattern = f"{state.current_epic}-{story_num}-*.md"
    matches = sorted(stories_dir.glob(pattern))
    return matches[0] if matches else None


def _extract_story_content(output: str) -> tuple[str | None, str | None]:
    """Extract story content from LLM stdout.

    Looks for a story header pattern (``# Story X.Y: Title``) and extracts
    from that point. Strips markdown code block wrappers if present,
    and trims trailing LLM commentary.

    Args:
        output: Raw LLM stdout.

    Returns:
        Tuple of (content, title) or (None, None) if not found.

    """
    if not output:
        return None, None

    from bmad_assist.core.io import strip_code_block

    text = strip_code_block(output)

    match = _STORY_HEADER_PATTERN.search(text)
    if not match:
        return None, None

    title = match.group(1).strip()
    content = text[match.start():]

    # Trim trailing LLM commentary
    for end_pattern in _STORY_END_PATTERNS:
        idx = content.find(end_pattern)
        if idx > 0:
            content = content[:idx].rstrip()

    return content.strip() if content.strip() else None, title


def _validate_story_content(content: str) -> bool:
    """Validate that extracted content looks like a real story.

    Checks minimum length and presence of required sections.

    Args:
        content: Extracted story content.

    Returns:
        True if content passes validation.

    """
    if len(content) < MIN_STORY_CONTENT_LENGTH:
        return False

    return all(pat.search(content) for pat in REQUIRED_SECTION_PATTERNS)


def _write_rescued_story(state: State, content: str, title: str | None) -> Path:
    """Write rescued story content to disk.

    Uses generate_story_slug for the filename and atomic_write for
    crash-safe persistence.

    Args:
        state: Current loop state with epic/story info.
        content: Validated story content.
        title: Story title extracted from header, or None.

    Returns:
        Path to the written story file.

    """
    from bmad_assist.core.io import atomic_write
    from bmad_assist.sprint.generator import generate_story_slug

    paths = get_paths()
    stories_dir = paths.stories_dir

    slug = generate_story_slug(title) if title else "untitled"
    story_num = (
        state.current_story.split(".")[-1]
        if state.current_story and "." in state.current_story
        else "1"
    )
    filename = f"{state.current_epic}-{story_num}-{slug}.md"
    story_path = stories_dir / filename

    atomic_write(story_path, content)
    logger.info("Rescued story file written: %s", story_path)

    return story_path


class CreateStoryHandler(BaseHandler):
    """Handler for CREATE_STORY phase.

    Invokes Master LLM to generate a new story file from epic context.
    Includes post-execution verification and rescue: if the LLM fails
    to save the story file, extracts content from stdout and writes it.

    """

    @property
    def phase_name(self) -> str:
        """Returns the name of the phase."""
        return "create_story"

    @property
    def track_timing(self) -> bool:
        """Enable timing tracking for this handler."""
        return True

    @property
    def timing_workflow_id(self) -> str:
        """Workflow ID for timing records."""
        return "create-story"

    def build_context(self, state: State) -> dict[str, Any]:
        """Build context for create_story prompt template.

        Available variables: epic_num, story_num, story_id, project_path

        """
        return self._build_common_context(state)

    def execute(self, state: State) -> PhaseResult:
        """Execute create_story with file verification and rescue.

        Renders the prompt directly, then invokes the provider with story-specific
        recovery logic:
        - guard/rate-limit termination: rescue from partial output or retry once
          with a finalization-only suffix
        - missing story file: preserve existing retry/rescue behavior

        Args:
            state: Current loop state.

        Returns:
            PhaseResult from story creation.

        """
        from bmad_assist.core.io import save_prompt

        self._compile_ms = None
        self._invoke_ms = None
        start_time = datetime.now(UTC) if self.track_timing else None

        try:
            prompt = self.render_prompt(state)
        except Exception as e:
            logger.error("Handler execution failed during prompt render: %s", e, exc_info=True)
            return PhaseResult.fail(f"Prompt compilation failed: {e}")

        epic = state.current_epic or "unknown"
        story = state.current_story or "unknown"
        save_prompt(self.project_path, epic, story, self.phase_name, prompt)

        finalization_attempted = False

        try:
            for attempt in range(MAX_RETRIES + 1):
                result = self.invoke_provider(prompt)

                term_metadata = None
                if result.termination_info:
                    term_metadata = {
                        "termination_info": result.termination_info,
                        "termination_reason": result.termination_reason,
                    }

                is_guard_term = bool(
                    result.termination_reason
                    and result.termination_reason.startswith(GUARD_TERMINATION_PREFIX)
                )

                if is_guard_term:
                    content, title = _extract_story_content(result.stdout or "")
                    if content and _validate_story_content(content):
                        rescued_path = _write_rescued_story(state, content, title)
                        outputs: dict[str, Any] = {
                            "response": result.stdout,
                            "model": result.model,
                            "duration_ms": result.duration_ms,
                            "rescued_file": str(rescued_path),
                            "rate_limit_rescued": True,
                            **self._timing_outputs(),
                        }
                        if term_metadata:
                            outputs["termination_metadata"] = term_metadata
                        if start_time and self.config.benchmarking.enabled:
                            self._save_timing_record(
                                state, start_time, datetime.now(UTC), result.stdout
                            )
                        return PhaseResult.ok(outputs)

                    if not finalization_attempted:
                        finalization_attempted = True
                        logger.warning(
                            "Guard terminated with no rescuable content "
                            "(attempt %d), retrying with finalization prompt",
                            attempt + 1,
                        )
                        prompt = prompt + _FINALIZATION_SUFFIX
                        save_prompt(
                            self.project_path,
                            epic,
                            story,
                            f"{self.phase_name}_finalization",
                            prompt,
                        )
                        continue

                    fail_outputs: dict[str, Any] = {}
                    if term_metadata:
                        fail_outputs["termination_metadata"] = term_metadata
                    return PhaseResult(
                        success=False,
                        error="Guard terminated on finalization retry with no rescuable story content",
                        outputs=fail_outputs,
                    )

                if result.exit_code != 0:
                    error_msg = result.stderr or f"Provider exited with code {result.exit_code}"
                    fail_outputs: dict[str, Any] = {}
                    if term_metadata:
                        fail_outputs["termination_metadata"] = term_metadata
                    return PhaseResult(success=False, error=error_msg, outputs=fail_outputs)

                if _find_story_file(state):
                    outputs: dict[str, Any] = {
                        "response": result.stdout,
                        "model": result.model,
                        "duration_ms": result.duration_ms,
                        **self._timing_outputs(),
                    }
                    if term_metadata:
                        outputs["termination_metadata"] = term_metadata
                    if start_time and self.config.benchmarking.enabled:
                        self._save_timing_record(
                            state, start_time, datetime.now(UTC), result.stdout
                        )
                    return PhaseResult.ok(outputs)

                content, title = _extract_story_content(result.stdout or "")
                if content and _validate_story_content(content):
                    rescued_path = _write_rescued_story(state, content, title)
                    outputs = {
                        "response": result.stdout,
                        "model": result.model,
                        "duration_ms": result.duration_ms,
                        "rescued_file": str(rescued_path),
                        **self._timing_outputs(),
                    }
                    if term_metadata:
                        outputs["termination_metadata"] = term_metadata
                    if start_time and self.config.benchmarking.enabled:
                        self._save_timing_record(
                            state, start_time, datetime.now(UTC), result.stdout
                        )
                    logger.info("Story rescued from LLM output on attempt %d", attempt + 1)
                    return PhaseResult.ok(outputs)

                if attempt < MAX_RETRIES:
                    logger.warning(
                        "Story file not found and rescue failed (attempt %d/%d), retrying...",
                        attempt + 1,
                        MAX_RETRIES + 1,
                    )
                    next_attempt = attempt + 2  # 1-indexed for display
                    prompt = prompt + _no_file_recovery_suffix(
                        next_attempt, MAX_RETRIES + 1, state
                    )
                    save_prompt(
                        self.project_path,
                        epic,
                        story,
                        f"{self.phase_name}_retry{next_attempt}",
                        prompt,
                    )
        except Exception as e:
            logger.error("Handler execution failed: %s", e, exc_info=True)
            return PhaseResult.fail(f"Handler error: {e}")

        return PhaseResult.fail(
            f"Story file not created after {MAX_RETRIES + 1} attempts and rescue extraction failed"
        )
