"""Skill-layout compiler for the ``bmad-code-review`` workflow.

Phase 7.2 inlines the workhorse compile logic that previously lived in
:mod:`bmad_assist.compiler.workflows.code_review` so this compiler is
self-contained — no delegation to a legacy compiler. The base class
handles locate / parse / customize / substitute / cache; the inlined
:meth:`_run_workflow_compile` builds the code-review context files
(strategic + antipatterns + TEA + git diff + source files + story),
mission, filtered instructions, XML envelope, and post-process tail.

Cross-imports — Phase 7.2 extracts the shared git-diff helpers into
:mod:`bmad_assist.compiler.skills._git_helpers` so this inlined
compiler doesn't reach into the legacy ``compiler/workflows`` package
(Brief 7.3 deletes it wholesale).

The ``apply_llm_transforms`` import is preserved at module top-level
so tests can monkeypatch
``bmad_assist.compiler.skills.bmad_code_review.apply_llm_transforms``
to stub LLM calls.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from bmad_assist.compiler.filtering import filter_instructions
from bmad_assist.compiler.output import generate_output

# Re-exported so tests/conftest can patch this module attribute. The
# base class resolves it dynamically per call (see _base.py docstring).
from bmad_assist.compiler.patching import apply_llm_transforms  # noqa: F401
from bmad_assist.compiler.shared_utils import (
    apply_post_process,
    context_snapshot,
    find_sprint_status_file,
    resolve_story_file,
    safe_read_file,
)
from bmad_assist.compiler.skills._base import SkillLayoutCompilerBase
from bmad_assist.compiler.skills._git_helpers import (
    capture_git_diff,
    extract_modified_files_from_stat,
)
from bmad_assist.compiler.source_context import (
    SourceContextService,
    extract_file_paths_from_story,
    get_git_diff_files,
)
from bmad_assist.compiler.strategic_context import StrategicContextService
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext, WorkflowIR
from bmad_assist.compiler.variable_utils import substitute_variables
from bmad_assist.compiler.variables import resolve_variables
from bmad_assist.core.exceptions import CompilerError
from bmad_assist.testarch.context import collect_tea_context

logger = logging.getLogger(__name__)


SKILL_ID = "bmad-code-review"


def _get_budgets_config():  # type: ignore[return]
    """Return SourceContextBudgetsConfig with a safe fallback to defaults."""
    try:
        from bmad_assist.core.config import get_config

        return get_config().compiler.source_context.budgets
    except Exception:  # noqa: BLE001
        from bmad_assist.core.config.models.source_context import SourceContextBudgetsConfig

        return SourceContextBudgetsConfig()


def _truncate_git_diff(diff: str, max_lines: int) -> str:
    """Truncate git diff to ``max_lines`` using a hunk-aware boundary cut.

    Walks lines and finds the last ``diff --git`` file header before the
    cap, then cuts there so no file hunk is partially included. Falls
    back to a hunk-level (``@@``) cut for single huge files, or a raw
    line cut for binary diffs / header-only diffs.

    A truncation notice is appended in the form:
    ``[DIFF TRUNCATED — N lines omitted. K files shown of T changed.]``
    """
    if max_lines <= 0 or not diff:
        return diff

    lines = diff.splitlines(keepends=True)
    if len(lines) <= max_lines:
        return diff

    total_lines = len(lines)
    total_files = sum(1 for line in lines if line.startswith("diff --git "))

    cut_at = max_lines
    for i in range(min(max_lines, total_lines) - 1, -1, -1):
        if lines[i].startswith("diff --git "):
            cut_at = i
            break

    # Guard: ensure retained slice contains at least one diff --git block.
    if cut_at > 0 and not any(lines[j].startswith("diff --git ") for j in range(cut_at)):
        first_file_idx = next(
            (j for j in range(total_lines) if lines[j].startswith("diff --git ")),
            None,
        )
        if first_file_idx is not None:
            end = min(first_file_idx + max_lines, total_lines)
            second_file_idx = next(
                (
                    j
                    for j in range(first_file_idx + 1, end)
                    if lines[j].startswith("diff --git ")
                ),
                None,
            )
            if second_file_idx is not None:
                cut_at = second_file_idx
            else:
                cut_at = 0

    if cut_at <= 0:
        # Single file exceeds cap — fall back to hunk-level cut.
        first_hunk_start = None
        for i in range(1, min(max_lines, total_lines)):
            if lines[i].startswith("@@ "):
                first_hunk_start = i
                break

        if first_hunk_start is not None:
            hunk_cut = max_lines
            for i in range(min(max_lines, total_lines) - 1, first_hunk_start, -1):
                if lines[i].startswith("@@ "):
                    hunk_cut = i
                    break
            cut_at = hunk_cut
        else:
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
    """Enforce a token budget over TEA context artifacts."""
    from bmad_assist.compiler.budget import apply_section_budget

    return apply_section_budget(tea_files, budget_tokens)


class BmadCodeReviewCompiler(SkillLayoutCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-code-review`` skill.

    Phase 7.2 inlined: no legacy compiler delegation. The base class
    handles the compile pipeline tail; this subclass owns the
    code-review-specific build of context files (recency-bias ordering),
    mission, and instructions — including git-diff capture + truncation
    and the staged prompt-budget enforcement.
    """

    skill_id = SKILL_ID
    legacy_workflow_name = "code-review"
    # Phase 7.2: inlined — no legacy compiler delegation.
    legacy_compiler_class = None

    def get_required_files(self) -> list[str]:
        """Glob patterns the code-review workflow expects."""
        return [
            "**/project_context.md",
            "**/project-context.md",
            "**/architecture*.md",
            "**/ux*.md",
            "**/sprint-status.yaml",
        ]

    def get_variables(self) -> dict[str, Any]:
        """Variables resolved by the code-review compiler.

        ``git_diff`` is intentionally absent — it's embedded as a
        context file, not surfaced in the ``<variables>`` block.
        """
        return {
            "epic_num": None,
            "story_num": None,
            "story_key": None,
            "story_id": None,
            "story_file": None,
            "story_title": None,
            "date": None,
        }

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """No SKILL.md-level extras for code-review."""
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the code-review contract + skill-source + story-file checks."""
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

        skill_md = self._locate_skill_md(context)
        if not skill_md.is_file():  # pragma: no cover — _locate_skill_md raises first
            raise CompilerError(
                f"SKILL.md not found: {skill_md}\n"
                f"  Why it's needed: Source for the bmad-code-review skill\n"
                f"  How to fix: Install BMAD v6.4+ in the project, or rely on "
                f"the bmad-assist bundled fallback"
            )

        story_path, _, _ = resolve_story_file(context, epic_num, story_num)
        if story_path is None:
            raise CompilerError(
                f"Story file not found for {epic_num}-{story_num}-*.md\n"
                f"  Expected pattern: docs/sprint-artifacts/{epic_num}-{story_num}-*.md\n"
                f"  Suggestion: Ensure the story exists and is in review status"
            )

    # --- Inlined workhorse compile ------------------------------------- #

    def _run_workflow_compile(self, context: CompilerContext) -> CompiledWorkflow:
        """Build the code-review :class:`CompiledWorkflow`."""
        workflow_ir = context.workflow_ir
        if workflow_ir is None:  # pragma: no cover — base guarantees this
            raise CompilerError(
                "workflow_ir not set in context. This is a bug - core.py should have loaded it."
            )

        with context_snapshot(context):
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Compiling %s", self.skill_id)

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

            # Capture git diff (stored only in context_files, not in
            # variables — keeps it out of the HTML-escaped <variables>
            # section).
            git_diff = capture_git_diff(context)

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
                workflow_name=self.legacy_workflow_name,
                mission=mission,
                context="",
                variables=resolved,
                instructions=filtered_instructions,
                output_template="",
                token_estimate=0,
            )

            result = generate_output(
                compiled,
                project_root=context.project_root,
                context_files=context_files,
                links_only=context.links_only,
            )

            # Total compiled-size logging + budget warning.
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
                workflow_name=self.legacy_workflow_name,
                mission=mission,
                context=final_xml,
                variables=resolved,
                instructions=filtered_instructions,
                output_template="",
                token_estimate=result.token_estimate,
            )

    # --- Helpers --------------------------------------------------------- #

    def _build_context_files(
        self,
        context: CompilerContext,
        resolved: dict[str, Any],
        git_diff: str = "",
    ) -> dict[str, str]:
        """Build context files dict with recency-bias ordering.

        1. Strategic docs (project-context only by default).
        1b. Code antipatterns.
        1c. TEA Context (test-design) with token-budget enforcement.
        2. Git diff (embedded as virtual file).
        3. Source files (File List + git diff, with overlap-trim).
        4. Story file (LAST — closest to instructions).

        Closes with staged prompt-budget enforcement.
        """
        files: dict[str, str] = {}
        project_root = context.project_root
        budgets = _get_budgets_config()

        # 2a: Hard line cap for git diff (hunk-aware).
        git_diff = _truncate_git_diff(git_diff, budgets.max_diff_lines)

        # 1. Strategic docs.
        strategic_service = StrategicContextService(context, "code_review")
        strategic_files = strategic_service.collect()
        files.update(strategic_files)

        # 1b. Code antipatterns.
        from bmad_assist.compiler.strategic_context import load_antipatterns

        files.update(load_antipatterns(context, "code", budget_tokens=1500))

        # 1c. TEA Context (test-design) with token-budget enforcement.
        tea_files = collect_tea_context(context, "code_review", resolved)
        tea_files = _apply_tea_budget(tea_files, budgets.tea_context_tokens)
        files.update(tea_files)

        # 2. Git diff (embedded as virtual file).
        if git_diff:
            files["[git-diff]"] = git_diff

        # 3. Source files.
        story_path_str = resolved.get("story_file")
        file_list_paths: list[str] = []
        if story_path_str:
            story_path = Path(story_path_str)
            story_content = safe_read_file(story_path, project_root)
            if story_content:
                file_list_paths = extract_file_paths_from_story(story_content)
                if file_list_paths:
                    logger.debug("Extracted %d files from File List", len(file_list_paths))

        # Compute overlap-trim exclusions.
        git_diff_files = None
        skip_paths: frozenset[str] = frozenset()
        if git_diff:
            modified_files = extract_modified_files_from_stat(git_diff, skip_docs=True)
            if modified_files:
                git_diff_files = get_git_diff_files(project_root, git_diff)

                # Exclude files already well-covered by the diff (≥80%).
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

        service = SourceContextService(context, "code_review")
        source_files = service.collect_files(file_list_paths, git_diff_files, skip_paths)
        files.update(source_files)

        # 4. Story file (LAST).
        if story_path_str:
            story_path = Path(story_path_str)
            content = safe_read_file(story_path, project_root)
            if content:
                files[str(story_path)] = content

        # 5. Staged prompt budget enforcement.
        from bmad_assist.compiler.budget import ContextSection, PromptBudgetEnforcer

        enforcer = PromptBudgetEnforcer.from_config("code_review")
        if enforcer.cap > 0:
            strategic_keys = {
                k
                for k in files
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
                trimmed_files: dict[str, str] = {}
                rebuilt = {
                    **result.sections["strategic"],
                    **result.sections["tea"],
                    **result.sections["other"],
                }
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
        """Build mission description for the code-review workflow."""
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


__all__ = ["SKILL_ID", "BmadCodeReviewCompiler", "apply_llm_transforms"]
