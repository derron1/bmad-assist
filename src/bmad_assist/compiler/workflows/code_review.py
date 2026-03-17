"""Compiler for the code-review workflow.

This module implements the WorkflowCompiler protocol for the code-review
workflow, producing standalone prompts for adversarial code review with
all necessary context embedded including git diff and modified source files.

Public API:
    CodeReviewCompiler: Workflow compiler class implementing WorkflowCompiler protocol
    DEFAULT_SOURCE_FILES_TOKEN_BUDGET: Token budget for source files from git diff
"""

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from bmad_assist.compiler.filtering import filter_instructions
from bmad_assist.compiler.output import generate_output
from bmad_assist.compiler.shared_utils import (
    apply_post_process,
    context_snapshot,
    estimate_tokens,
    find_sprint_status_file,
    resolve_story_file,
    safe_read_file,
)
from bmad_assist.compiler.source_context import (
    SourceContextService,
    extract_file_paths_from_story,
    get_git_diff_files,
)
from bmad_assist.compiler.strategic_context import StrategicContextService, _truncate_content
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext, WorkflowIR
from bmad_assist.compiler.variable_utils import substitute_variables
from bmad_assist.compiler.variables import resolve_variables
from bmad_assist.core.exceptions import CompilerError
from bmad_assist.git import get_validated_diff
from bmad_assist.testarch.context import collect_tea_context

logger = logging.getLogger(__name__)

# Maximum lines for git diff before truncation (legacy constant, config field preferred)
_MAX_DIFF_LINES = 500

# Timeout for git commands in seconds
_GIT_TIMEOUT = 30

# Pattern for parsing git diff --stat output
# Matches: " src/file.py | 42 ++++----" or " src/file.py | 42 +"
# Uses non-greedy match to support filenames with spaces
_STAT_PATTERN = re.compile(r"^\s*(.+?)\s*\|\s*(\d+)", re.MULTILINE)

# Pattern for binary files: " file.bin | Bin 1234 -> 5678 bytes"
_BINARY_PATTERN = re.compile(r"^\s*[^\s|]+\s*\|\s*Bin\s+", re.MULTILINE)

# Pattern for renamed files with changes: " old.py => new.py | 5"
# Also handles brace-style: " src/{old => new}/file.py | 10"
# Captures new path and change count
_RENAME_WITH_CHANGES_PATTERN = re.compile(
    r"^\s*(?:\{[^}]+\}\s*=>\s*)?(.+?)\s*=>\s*(.+?)\s*\|\s*(\d+)", re.MULTILINE
)


def _get_budgets_config():  # type: ignore[return]
    """Return SourceContextBudgetsConfig with a safe fallback to defaults."""
    try:
        from bmad_assist.core.config import get_config
        from bmad_assist.core.exceptions import ConfigError

        return get_config().compiler.source_context.budgets
    except Exception:  # noqa: BLE001
        from bmad_assist.core.config.models.source_context import SourceContextBudgetsConfig

        return SourceContextBudgetsConfig()


def _truncate_git_diff(diff: str, max_lines: int) -> str:
    """Truncate git diff to max_lines using a hunk-aware boundary cut.

    Walks lines and finds the last ``diff --git`` file header before the cap,
    then cuts there so no file hunk is partially included. Falls back to a raw
    line cut if no file boundary exists before the cap.

    A truncation notice is appended in the form:
        [DIFF TRUNCATED — N lines omitted. K files shown of T changed.]

    Args:
        diff: Raw git diff string.
        max_lines: Maximum number of lines to retain.

    Returns:
        Possibly-truncated diff string.

    """
    if max_lines <= 0 or not diff:
        return diff

    lines = diff.splitlines(keepends=True)
    if len(lines) <= max_lines:
        return diff

    total_lines = len(lines)
    total_files = sum(1 for line in lines if line.startswith("diff --git "))

    # Find the last "diff --git" header within the first max_lines lines so
    # we don't cut mid-hunk; cut at that index to exclude the partial file.
    cut_at = max_lines
    for i in range(min(max_lines, total_lines) - 1, -1, -1):
        if lines[i].startswith("diff --git "):
            cut_at = i  # Exclude this partially-visible file
            break

    # Guard: if retained slice would contain zero diff --git blocks,
    # advance cut_at to include the first file header so output always
    # contains at least one real patch.
    if cut_at > 0 and not any(
        lines[j].startswith("diff --git ") for j in range(cut_at)
    ):
        first_file_idx = next(
            (j for j in range(total_lines) if lines[j].startswith("diff --git ")),
            None,
        )
        if first_file_idx is not None:
            # Budget max_lines of content starting from the first file header
            end = min(first_file_idx + max_lines, total_lines)
            # Check if window contains a second diff --git (multi-file)
            second_file_idx = next(
                (j for j in range(first_file_idx + 1, end)
                 if lines[j].startswith("diff --git ")),
                None,
            )
            if second_file_idx is not None:
                # Multi-file: cut at the second file boundary (keep first complete)
                cut_at = second_file_idx
            else:
                # Single-file window: set cut_at = 0 so the existing
                # single-file fallback below handles first-file truncation
                # via @@ hunk boundaries.
                cut_at = 0

    if cut_at <= 0:
        # Single file exceeds cap — fall back to hunk-level cut.
        # Because cut_at slices from index 0, the file header lines
        # (diff --git, index, ---, +++) are always retained in lines[:cut_at].
        # Find the first @@ to know where hunks begin, then find the last
        # complete @@ header before max_lines for a clean boundary.
        first_hunk_start = None
        for i in range(1, min(max_lines, total_lines)):
            if lines[i].startswith("@@ "):
                first_hunk_start = i
                break

        if first_hunk_start is not None:
            # Find last @@ header before max_lines for a clean hunk boundary
            hunk_cut = max_lines
            for i in range(min(max_lines, total_lines) - 1, first_hunk_start, -1):
                if lines[i].startswith("@@ "):
                    hunk_cut = i
                    break
            cut_at = hunk_cut
        else:
            # No hunk headers found (binary diff or header-only); raw cap
            cut_at = max_lines

    shown_files = sum(1 for line in lines[:cut_at] if line.startswith("diff --git "))
    omitted_lines = total_lines - cut_at

    truncated = "".join(lines[:cut_at])
    truncated += (
        f"\n\n[DIFF TRUNCATED — {omitted_lines} lines omitted. "
        f"{shown_files} files shown of {total_files} changed.]"
    )

    logger.warning(
        "Git diff truncated: %d → %d lines (%d files shown of %d)",
        total_lines,
        cut_at,
        shown_files,
        total_files,
    )
    return truncated


def _apply_tea_budget(tea_files: dict[str, str], budget_tokens: int) -> dict[str, str]:
    """Enforce a token budget over TEA context artifacts.

    Thin wrapper around ``apply_section_budget`` from the shared budget module.

    Args:
        tea_files: Mapping of artifact name → content from collect_tea_context().
        budget_tokens: Maximum total tokens to retain.

    Returns:
        Budget-enforced mapping (same type, subset of input).

    """
    from bmad_assist.compiler.budget import apply_section_budget

    return apply_section_budget(tea_files, budget_tokens)


def _capture_git_diff(context: CompilerContext) -> str:
    """Capture git diff with intelligent filtering and validation.

    Uses get_validated_diff() which:
    - Detects proper merge-base (handles merge commits correctly)
    - Filters out cache/metadata files that cause false positives
    - Validates diff quality (warns if too much garbage)

    This addresses the 92% false positive rate issue identified in
    benchmark-analysis.md by ensuring reviewers only see source files.

    Args:
        context: Compilation context with project root.

    Returns:
        Filtered git diff wrapped in markers, or empty string on any error.

    """
    project_root = context.project_root

    try:
        # Check if this is the git repository ROOT (not just a subdirectory)
        check_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=project_root,
            capture_output=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )

        if check_result.returncode != 0:
            logger.debug("Not a git repository: %s", project_root)
            return ""

        # Verify we're at the git root, not a subdirectory of another repo
        git_root = Path(check_result.stdout.strip()).resolve()
        if git_root != project_root.resolve():
            logger.debug(
                "Project %s is inside git repo %s but not at root",
                project_root,
                git_root,
            )
            return ""

        # Use the new validated diff capture with P0/P1 fixes:
        # - P0: Path filtering (excludes cache, metadata, node_modules, etc.)
        # - P0: Merge-base detection (handles merge commits correctly)
        # - P1: Quality validation (warns if garbage ratio too high)
        diff_content, validation = get_validated_diff(
            project_root,
            max_garbage_ratio=0.3,
            raise_on_invalid=False,  # Warn but don't block
        )

        # Log validation results for debugging
        if validation.total_files > 0:
            logger.debug(
                "Diff validation: %d source files, %d garbage files (%.0f%% garbage)",
                validation.source_files,
                validation.garbage_files,
                validation.garbage_ratio * 100,
            )

        if not validation.is_valid:
            logger.warning(
                "Diff quality warning: %s - review may have false positives",
                "; ".join(validation.issues),
            )

        return diff_content

    except FileNotFoundError:
        logger.warning("git command not found")
        return ""
    except subprocess.TimeoutExpired:
        logger.error("git command timed out after %ds", _GIT_TIMEOUT)
        return ""
    except OSError as e:
        logger.warning("git command failed: %s", e)
        return ""


def _extract_modified_files_from_stat(
    stat_output: str,
    skip_docs: bool = True,
    skip_generated: bool = True,
) -> list[tuple[str, int]]:
    """Extract modified file paths and change counts from git diff --stat output.

    Parses output like:
        src/file.py | 42 ++++++----
        tests/test.py | 25 ++++
        old.py => new.py | 5 +++--

    Args:
        stat_output: Raw output from git diff --stat.
        skip_docs: If True, skip files in docs/ directory.
        skip_generated: If True, skip BMAD-generated files (_bmad-output, .bmad-assist).

    Returns:
        List of (path, change_count) tuples sorted by changes desc, path asc.

    """
    # Extract only the stat section (before patch content starts)
    # The stat section ends with a summary line like:
    #   "3 files changed, 25 insertions(+), 10 deletions(-)"
    # After that comes the actual patch content which can contain | characters
    stat_end_pattern = re.compile(r"^\s*\d+\s+files?\s+changed", re.MULTILINE | re.IGNORECASE)
    stat_end_match = stat_end_pattern.search(stat_output)
    if stat_end_match:
        # Only process content up to and including the summary line
        stat_output = stat_output[: stat_end_match.end()]

    # Find all binary files to skip
    binary_matches = set()
    for match in _BINARY_PATTERN.finditer(stat_output):
        # Extract path from binary pattern
        line = match.group(0)
        path_match = re.match(r"^\s*([^\s|]+)", line)
        if path_match:
            binary_matches.add(path_match.group(1))

    result: list[tuple[str, int]] = []
    seen_paths: set[str] = set()

    # First, process renamed files (they have => in the line)
    for match in _RENAME_WITH_CHANGES_PATTERN.finditer(stat_output):
        # Group 2 is new path, group 3 is change count
        new_path = match.group(2).strip()
        try:
            changes = int(match.group(3))
        except ValueError:
            continue

        # Skip pure renames with zero content changes (AC4 requirement)
        if changes == 0:
            continue

        # Skip docs/ files if requested
        if skip_docs and new_path.startswith("docs/"):
            continue

        # Skip BMAD-generated files (synthesis artifacts, cache, etc.)
        if skip_generated and (
            new_path.startswith("_bmad-output/") or
            new_path.startswith(".bmad-assist/") or
            "/_bmad-output/" in new_path or
            "/.bmad-assist/" in new_path
        ):
            continue

        if new_path not in seen_paths:
            result.append((new_path, changes))
            seen_paths.add(new_path)

    # Then, process regular files (no =>)
    for match in _STAT_PATTERN.finditer(stat_output):
        path = match.group(1)
        try:
            changes = int(match.group(2))
        except ValueError:
            continue

        # Skip binary files
        if path in binary_matches:
            continue

        # Skip files that have => in them (they're the "old" part of renames)
        # These are already captured by the rename pattern above
        if "=>" in path:
            continue

        # Skip docs/ files if requested
        if skip_docs and path.startswith("docs/"):
            continue

        # Skip BMAD-generated files (synthesis artifacts, cache, etc.)
        if skip_generated and (
            path.startswith("_bmad-output/") or
            path.startswith(".bmad-assist/") or
            "/_bmad-output/" in path or
            "/.bmad-assist/" in path
        ):
            continue

        if path not in seen_paths:
            result.append((path, changes))
            seen_paths.add(path)

    # Sort by changes descending, then path ascending for determinism
    result.sort(key=lambda x: (-x[1], x[0]))

    return result


class CodeReviewCompiler:
    """Compiler for the code-review workflow.

    Implements the WorkflowCompiler protocol to compile the code-review
    workflow into a standalone prompt. The code-review workflow is an
    action-workflow (no template output), focused on adversarial review
    of implemented stories with git diff and modified files embedded.

    Context embedding follows recency-bias ordering:
    1. project_context.md (general)
    2. architecture.md (technical constraints)
    3. ux.md (optional UI context)
    4. git_diff section (implementation changes)
    5. modified source files (what to review)
    6. story file (LAST - what was requested)

    """

    @property
    def workflow_name(self) -> str:
        """Unique workflow identifier."""
        return "code-review"

    def get_required_files(self) -> list[str]:
        """Return list of required file glob patterns.

        Returns:
            Glob patterns for files needed by code-review workflow.

        """
        return [
            "**/project_context.md",
            "**/project-context.md",
            "**/architecture*.md",
            "**/ux*.md",
            "**/sprint-status.yaml",
        ]

    def get_variables(self) -> dict[str, Any]:
        """Return workflow-specific variables to resolve.

        Returns:
            Variables needed for code-review compilation.

        """
        # NOTE: git_diff is NOT included here - it's embedded as a context file
        # to avoid HTML-escaped duplication in the <variables> section
        return {
            "epic_num": None,
            "story_num": None,
            "story_key": None,
            "story_id": None,
            "story_file": None,
            "story_title": None,
            "date": None,
        }

    def get_workflow_dir(self, context: CompilerContext) -> Path:
        """Return the workflow directory for this compiler.

        Args:
            context: The compilation context with project paths.

        Returns:
            Path to the workflow directory containing workflow.yaml.

        Raises:
            CompilerError: If workflow directory not found.

        """
        from bmad_assist.compiler.workflow_discovery import (
            discover_workflow_dir,
            get_workflow_not_found_message,
        )

        workflow_dir = discover_workflow_dir(self.workflow_name, context.project_root)
        if workflow_dir is None:
            raise CompilerError(
                get_workflow_not_found_message(self.workflow_name, context.project_root)
            )
        return workflow_dir

    def validate_context(self, context: CompilerContext) -> None:
        """Validate context before compilation.

        Args:
            context: The compilation context to validate.

        Raises:
            CompilerError: If required context is missing.

        """
        if context.project_root is None:
            raise CompilerError("project_root is required in context")
        if context.output_folder is None:
            raise CompilerError("output_folder is required in context")

        epic_num = context.resolved_variables.get("epic_num")
        story_num = context.resolved_variables.get("story_num")

        if epic_num is None:
            raise CompilerError(
                "epic_num is required for code-review compilation.\n"
                "  Suggestion: Provide epic_num via invocation params or ensure "
                "sprint-status.yaml has a story in review status"
            )
        if story_num is None:
            raise CompilerError(
                "story_num is required for code-review compilation.\n"
                "  Suggestion: Provide story_num via invocation params or ensure "
                "sprint-status.yaml has a story in review status"
            )

        # Workflow directory is validated by get_workflow_dir via discovery
        workflow_dir = self.get_workflow_dir(context)
        if not workflow_dir.exists():
            raise CompilerError(
                f"Workflow directory not found: {workflow_dir}\n"
                f"  Why it's needed: Contains workflow.yaml and instructions.xml\n"
                f"  How to fix: Reinstall bmad-assist or ensure BMAD is properly installed"
            )

        story_path, _, _ = resolve_story_file(context, epic_num, story_num)
        if story_path is None:
            raise CompilerError(
                f"Story file not found for {epic_num}-{story_num}-*.md\n"
                f"  Expected pattern: docs/sprint-artifacts/{epic_num}-{story_num}-*.md\n"
                f"  Suggestion: Ensure the story exists and is in review status"
            )

    def compile(self, context: CompilerContext) -> CompiledWorkflow:
        """Compile code-review workflow with given context.

        Executes the full compilation pipeline:
        1. Use pre-loaded workflow_ir from context
        2. Resolve variables with sprint-status lookup
        3. Capture git diff
        4. Build context files with recency-bias ordering (story LAST)
        5. Filter instructions
        6. Generate XML output

        Args:
            context: The compilation context with:
                - workflow_ir: Pre-loaded WorkflowIR
                - patch_path: Path to patch file (for post_process)

        Returns:
            CompiledWorkflow ready for output.

        Raises:
            CompilerError: If compilation fails at any stage.

        """
        workflow_ir = context.workflow_ir
        if workflow_ir is None:
            raise CompilerError(
                "workflow_ir not set in context. This is a bug - core.py should have loaded it."
            )

        workflow_dir = self.get_workflow_dir(context)

        with context_snapshot(context):
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Using workflow from %s", workflow_dir)

            invocation_params = {
                k: v
                for k, v in context.resolved_variables.items()
                if k in ("epic_num", "story_num", "story_title", "date")
            }

            sprint_status_path = find_sprint_status_file(context)

            resolved = resolve_variables(context, invocation_params, sprint_status_path, None)

            story_path, story_key, _ = resolve_story_file(
                context,
                resolved.get("epic_num"),
                resolved.get("story_num"),
            )
            if story_path:
                resolved["story_file"] = str(story_path)
            if story_key:
                resolved["story_key"] = story_key

            # Capture git diff (stored in context_files only, not in variables
            # to avoid HTML-escaped duplication in <variables> section)
            git_diff = _capture_git_diff(context)

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Resolved %d variables", len(resolved))

            context_files = self._build_context_files(context, resolved, git_diff)

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Built context with %d files", len(context_files))

            filtered_instructions = filter_instructions(workflow_ir)
            filtered_instructions = substitute_variables(filtered_instructions, resolved)

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Filtered instructions: %d bytes", len(filtered_instructions))

            mission = self._build_mission(workflow_ir, resolved)

            compiled = CompiledWorkflow(
                workflow_name=self.workflow_name,
                mission=mission,
                context="",
                variables=resolved,
                instructions=filtered_instructions,
                output_template="",  # action-workflow, no template
                token_estimate=0,
            )

            result = generate_output(
                compiled,
                project_root=context.project_root,
                context_files=context_files,
                links_only=context.links_only,
            )

            # 2d: Unconditional total compiled size logging + budget enforcement
            logger.info(
                "CODE_REVIEW prompt: ~%d tokens (%d bytes)",
                result.token_estimate,
                result.size_bytes,
            )
            from bmad_assist.compiler.budget import PromptBudgetEnforcer

            enforcer = PromptBudgetEnforcer.from_config("code_review")
            if enforcer.cap > 0 and result.token_estimate > enforcer.cap:
                logger.warning(
                    "CODE_REVIEW prompt exceeds cap (%d tokens > %d). "
                    "Staged budget enforcement active for strategic + TEA sections.",
                    result.token_estimate,
                    enforcer.cap,
                )

            final_xml = apply_post_process(result.xml, context)

            return CompiledWorkflow(
                workflow_name=self.workflow_name,
                mission=mission,
                context=final_xml,
                variables=resolved,
                instructions=filtered_instructions,
                output_template="",
                token_estimate=result.token_estimate,
            )

    def _build_context_files(
        self,
        context: CompilerContext,
        resolved: dict[str, Any],
        git_diff: str = "",
    ) -> dict[str, str]:
        """Build context files dict with recency-bias ordering.

        Files are ordered from general (early) to specific (late):
        1. Strategic docs via StrategicContextService (project-context only by default)
        2. git_diff section (embedded as virtual file)
        3. modified source files (what to review)
        4. story file (LAST - what was requested)

        Args:
            context: Compilation context with paths.
            resolved: Resolved variables containing epic_num, story_num.
            git_diff: Git diff content (passed separately to avoid variable duplication).

        Returns:
            Dictionary mapping file paths to content, ordered by recency-bias.

        """
        files: dict[str, str] = {}
        project_root = context.project_root
        budgets = _get_budgets_config()

        # 2a: Hard line cap for git diff (hunk-aware, applied before embedding)
        git_diff = _truncate_git_diff(git_diff, budgets.max_diff_lines)

        # 1. Strategic docs (project-context only by default - 0% PRD citation in benchmarks)
        strategic_service = StrategicContextService(context, "code_review")
        strategic_files = strategic_service.collect()
        files.update(strategic_files)

        # 1b. Include code antipatterns - reviewers should know what mistakes to look for
        from bmad_assist.compiler.strategic_context import load_antipatterns

        files.update(load_antipatterns(context, "code", budget_tokens=1500))

        # 1c. TEA Context (test-design) for reviewing against test plan
        # 2b: Enforce token budget over TEA artifacts
        tea_files = collect_tea_context(context, "code_review", resolved)
        tea_files = _apply_tea_budget(tea_files, budgets.tea_context_tokens)
        files.update(tea_files)

        # 2. Git diff (embedded as virtual file, not in variables)
        if git_diff:
            files["[git-diff]"] = git_diff

        # 3. Source files using SourceContextService (File List + git diff)
        # Get File List from story file
        story_path_str = resolved.get("story_file")
        file_list_paths: list[str] = []
        if story_path_str:
            story_path = Path(story_path_str)
            story_content = safe_read_file(story_path, project_root)
            if story_content:
                file_list_paths = extract_file_paths_from_story(story_content)
                if file_list_paths:
                    logger.debug("Extracted %d files from File List", len(file_list_paths))

        # Get git diff files with hunk info; also compute 2c overlap-trim exclusions
        git_diff_files = None
        skip_paths: frozenset[str] = frozenset()
        if git_diff:
            modified_files = _extract_modified_files_from_stat(git_diff, skip_docs=True)
            if modified_files:
                git_diff_files = get_git_diff_files(project_root, git_diff)

                # 2c: Exclude files already well-covered by the diff (≥80% line coverage)
                skip_set: set[str] = set()
                for path, changed_lines in modified_files:
                    abs_path = project_root / path
                    content = safe_read_file(abs_path, project_root)
                    if content is None:
                        continue
                    total_file_lines = content.count("\n") + 1
                    if total_file_lines > 0 and changed_lines / total_file_lines >= 0.8:
                        logger.debug(
                            "Skipping %s in source context — %.0f%% covered by diff",
                            path,
                            (changed_lines / total_file_lines) * 100,
                        )
                        skip_set.add(path)
                skip_paths = frozenset(skip_set)

        # Collect source files using service
        service = SourceContextService(context, "code_review")
        source_files = service.collect_files(file_list_paths, git_diff_files, skip_paths)
        files.update(source_files)

        # 4. Story file (LAST - closest to instructions per recency-bias)
        story_path_str = resolved.get("story_file")
        if story_path_str:
            story_path = Path(story_path_str)
            content = safe_read_file(story_path, project_root)
            if content:
                files[str(story_path)] = content

        # 5. Staged prompt budget enforcement
        from bmad_assist.compiler.budget import ContextSection, PromptBudgetEnforcer

        enforcer = PromptBudgetEnforcer.from_config("code_review")
        if enforcer.cap > 0:
            # Partition files into sections by key patterns for trimming
            strategic_keys = {
                k for k in files
                if k.startswith("[project-context") or k.startswith("[antipattern")
            }
            tea_keys = {k for k in files if k.startswith("[tea-")}
            other_keys = [k for k in files if k not in strategic_keys and k not in tea_keys]

            sections = [
                ContextSection(
                    "strategic",
                    {k: files[k] for k in files if k in strategic_keys},
                    priority=1,
                    trimmable=True,
                ),
                ContextSection(
                    "tea",
                    {k: files[k] for k in files if k in tea_keys},
                    priority=2,
                    trimmable=True,
                ),
                ContextSection(
                    "other",
                    {k: files[k] for k in other_keys},
                    priority=99,
                    trimmable=False,
                ),
            ]

            result = enforcer.enforce(sections)
            if result.trimmed_sections:
                # Rebuild files dict preserving original insertion order
                trimmed_files: dict[str, str] = {}
                rebuilt = {**result.sections["strategic"], **result.sections["tea"], **result.sections["other"]}
                for key in files:
                    if key in rebuilt:
                        trimmed_files[key] = rebuilt[key]
                files = trimmed_files

        return files

    def _build_mission(
        self,
        workflow_ir: WorkflowIR,
        resolved: dict[str, Any],
    ) -> str:
        """Build mission description for compiled workflow.

        Args:
            workflow_ir: Workflow IR with description.
            resolved: Resolved variables.

        Returns:
            Mission description string emphasizing adversarial review.

        """
        base_description = workflow_ir.raw_config.get(
            "description", "Perform an ADVERSARIAL Senior Developer code review"
        )

        epic_num = resolved.get("epic_num", "?")
        story_num = resolved.get("story_num", "?")
        story_title = resolved.get("story_title", "")

        if story_title:
            mission = (
                f"{base_description}\n\n"
                f"Target: Story {epic_num}.{story_num} - {story_title}\n"
                f"Find 3-10 specific issues. Challenge every claim."
            )
        else:
            mission = (
                f"{base_description}\n\n"
                f"Target: Story {epic_num}.{story_num}\n"
                f"Find 3-10 specific issues. Challenge every claim."
            )

        return mission
