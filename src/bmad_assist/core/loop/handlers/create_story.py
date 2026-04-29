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


def _check_slug_consistency(state: State) -> str | None:
    """Detect a slug mismatch between sprint-status and the epic file's
    Story X.Y title. Returns a `<critical>` warning block to prepend to
    the prompt, or None if everything is consistent / can't be checked.

    Why this matters: when an epic is course-corrected (story 12.3
    rewritten from "build filterable listing" to "update class admin
    management") the sprint-status key keeps the old slug. The LLM
    then burns 3-5 minutes mid-prompt diagnosing the divergence by
    reading the epic, sprint-status, and any change-proposal docs.
    Surfacing the divergence upfront — with the canonical title — lets
    Claude skip the investigation and start writing immediately.

    Failure mode is silent: if any expected file is missing, the helper
    returns None and the prompt goes through unaltered. We only inject
    a warning when we have high-confidence evidence of a mismatch.
    """
    if state.current_epic is None or state.current_story is None:
        return None
    story_id = state.current_story
    if "." not in story_id:
        return None
    epic = state.current_epic
    story_num = story_id.split(".")[-1]

    try:
        from bmad_assist.bmad.parser import parse_epic_file
        from bmad_assist.sprint.generator import generate_story_slug
        from bmad_assist.sprint.parser import parse_sprint_status

        paths = get_paths()
    except (ImportError, RuntimeError):
        return None

    # Find the epic file (e.g. epic-12-class-booking.md).
    epics_dir = paths.epics_dir
    if not epics_dir.exists():
        return None
    matches = list(epics_dir.glob(f"epic-{epic}-*.md"))
    if not matches:
        return None
    try:
        epic_doc = parse_epic_file(matches[0])
    except Exception as exc:
        logger.debug("Slug check: could not parse epic %s: %s", matches[0], exc)
        return None

    # Find the matching story heading inside the epic.
    epic_title: str | None = None
    for s in epic_doc.stories:
        if str(s.number) == story_id:
            epic_title = s.title
            break
    if not epic_title:
        return None
    expected_slug = generate_story_slug(epic_title)

    # Find the current sprint-status key for this story.
    sprint_path = paths.find_sprint_status()
    if sprint_path is None or not sprint_path.exists():
        return None
    try:
        status = parse_sprint_status(sprint_path)
    except Exception as exc:
        logger.debug("Slug check: could not parse sprint-status: %s", exc)
        return None
    prefix = f"{epic}-{story_num}-"
    current_keys = [k for k in status.entries if k.startswith(prefix)]
    if not current_keys:
        return None
    current_key = current_keys[0]
    current_slug = current_key[len(prefix):]

    if current_slug == expected_slug:
        return None  # consistent

    canonical_key = f"{prefix}{expected_slug}"
    return (
        "<critical>STORY-SLUG MISMATCH (course correction detected)\n"
        f"  sprint-status.yaml key:  {current_key}\n"
        f"  epic file Story {story_id} title: {epic_title}\n"
        f"  canonical key from title: {canonical_key}\n"
        "  Treat the epic file's title as authoritative. The sprint-status "
        "slug was retained from pre-correction planning and is stale. "
        "Do not re-investigate this divergence — write the new story per "
        "the canonical title and let downstream sync update the key.\n"
        "</critical>\n\n"
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


def _stat_story_file_mtime(state: State) -> float | None:
    """Return the mtime of any pre-existing story file, or ``None`` if absent.

    Snapshotted at the top of :meth:`CreateStoryHandler.execute` so the
    retry loop can detect "the LLM didn't actually write anything but a
    stale file from a previous run is still on disk." See
    :func:`_find_fresh_story_file`.
    """
    try:
        path = _find_story_file(state)
    except RuntimeError:
        # Paths not initialized (unit tests). Treat as no pre-existing file.
        return None
    if path is None:
        return None
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _find_fresh_story_file(state: State, baseline_mtime: float | None) -> Path | None:
    """Like :func:`_find_story_file`, but ignores stale leftovers.

    The story file lives at a deterministic path keyed by epic + story
    number + slug. If a previous create_story run wrote one, the next
    run finds it via glob even when the current LLM call did zero work
    (empty 2-turn response, exit 0, no Write tool call). That short-
    circuits the retry loop into a false success.

    With ``baseline_mtime`` set, we require the matched file's mtime to
    have advanced — i.e. the LLM actually touched it during this run.
    A baseline of ``None`` means no pre-existing file, so any match is
    fresh by definition.
    """
    path = _find_story_file(state)
    if path is None:
        return None
    if baseline_mtime is None:
        return path
    try:
        if path.stat().st_mtime <= baseline_mtime:
            return None
    except OSError:
        return None
    return path


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

        # Pre-flight slug check: if the sprint-status key disagrees with
        # the epic file's Story X.Y title, prepend a `<critical>` block
        # so the LLM doesn't burn minutes mid-prompt re-discovering the
        # divergence (observed: ~3-5 min on epic-12 course correction).
        slug_warning = _check_slug_consistency(state)
        if slug_warning:
            logger.info(
                "Pre-flight slug mismatch detected for story %s — "
                "prepending canonical-title warning to prompt",
                story,
            )
            prompt = slug_warning + prompt

        save_prompt(self.project_path, epic, story, self.phase_name, prompt)

        # Snapshot any pre-existing story file's mtime so the freshness
        # check can distinguish "LLM wrote a new file" from "stale
        # leftover from a previous run is still on disk."
        pre_existing_mtime = _stat_story_file_mtime(state)

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

                if _find_fresh_story_file(state, pre_existing_mtime) is not None:
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
