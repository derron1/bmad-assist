"""Skill-layout compiler for the ``bmad-dev-story`` workflow.

Phase 7.2 inlines the workhorse compile logic that previously lived in
:mod:`bmad_assist.compiler.workflows.dev_story` so this compiler is
self-contained — no delegation to a legacy compiler. The base class
handles locate / parse / customize / substitute / cache; the inlined
:meth:`_run_workflow_compile` builds the dev-story context files,
mission, filtered instructions, XML envelope, and post-process tail.

The ``apply_llm_transforms`` import is preserved at module top-level
so tests can monkeypatch
``bmad_assist.compiler.skills.bmad_dev_story.apply_llm_transforms``
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
    find_epic_file,
    find_file_in_output_folder,
    find_sprint_status_file,
    resolve_story_file,
    safe_read_file,
)
from bmad_assist.compiler.skills._base import SkillLayoutCompilerBase
from bmad_assist.compiler.source_context import (
    SourceContextService,
    extract_file_paths_from_story,
)
from bmad_assist.compiler.strategic_context import StrategicContextService
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext, WorkflowIR
from bmad_assist.compiler.variable_utils import substitute_variables
from bmad_assist.compiler.variables import resolve_variables
from bmad_assist.core.exceptions import CompilerError
from bmad_assist.testarch.context import collect_tea_context, is_tea_context_enabled

logger = logging.getLogger(__name__)


SKILL_ID = "bmad-dev-story"


class BmadDevStoryCompiler(SkillLayoutCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-dev-story`` skill.

    Phase 7.2 inlined: no legacy compiler delegation. The base class
    handles the compile pipeline tail; this subclass owns the
    dev-story-specific build of context files (recency-bias ordering),
    mission, and instructions.
    """

    skill_id = SKILL_ID
    legacy_workflow_name = "dev-story"
    # Phase 7.2: inlined — no legacy compiler delegation.
    legacy_compiler_class = None

    def get_required_files(self) -> list[str]:
        """Glob patterns the dev-story workflow expects."""
        return [
            "**/project_context.md",
            "**/project-context.md",
            "**/architecture*.md",
            "**/prd*.md",
            "**/ux*.md",
            "**/sprint-status.yaml",
            "**/epic*.md",
        ]

    def get_variables(self) -> dict[str, Any]:
        """Variables resolved by the dev-story compiler."""
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
        """No SKILL.md-level extras for dev-story."""
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the dev-story contract + skill-source check + story-file check."""
        if context.project_root is None:
            raise CompilerError("project_root is required in context")
        if context.output_folder is None:
            raise CompilerError("output_folder is required in context")

        epic_num = context.resolved_variables.get("epic_num")
        story_num = context.resolved_variables.get("story_num")
        if epic_num is None:
            raise CompilerError(
                "epic_num is required for dev-story compilation.\n"
                "  Suggestion: Provide epic_num via invocation params or ensure "
                "sprint-status.yaml has a ready-for-dev story"
            )
        if story_num is None:
            raise CompilerError(
                "story_num is required for dev-story compilation.\n"
                "  Suggestion: Provide story_num via invocation params or ensure "
                "sprint-status.yaml has a ready-for-dev story"
            )

        skill_md = self._locate_skill_md(context)
        if not skill_md.is_file():  # pragma: no cover — _locate_skill_md raises first
            raise CompilerError(
                f"SKILL.md not found: {skill_md}\n"
                f"  Why it's needed: Source for the bmad-dev-story skill\n"
                f"  How to fix: Install BMAD v6.4+ in the project, or rely on "
                f"the bmad-assist bundled fallback"
            )

        story_path, _, _ = resolve_story_file(context, epic_num, story_num)
        if story_path is None:
            raise CompilerError(
                f"Story file not found for {epic_num}-{story_num}-*.md\n"
                f"  Expected pattern: docs/sprint-artifacts/{epic_num}-{story_num}-*.md\n"
                f"  Suggestion: Run 'create-story' workflow first to create the story"
            )

    # --- Inlined workhorse compile ------------------------------------- #

    def _run_workflow_compile(self, context: CompilerContext) -> CompiledWorkflow:
        """Build the dev-story :class:`CompiledWorkflow`.

        Mirrors the pre-Phase-7 ``DevStoryCompiler.compile`` flow
        byte-for-byte. The base class has already set
        ``context.workflow_ir`` to a synthetic IR built around the
        patched SKILL.md body.
        """
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

            epic_num = invocation_params.get("epic_num")
            epics_path = find_epic_file(context, epic_num) if epic_num else None

            resolved = resolve_variables(context, invocation_params, sprint_status_path, epics_path)

            story_path, story_key, _ = resolve_story_file(
                context,
                resolved.get("epic_num"),
                resolved.get("story_num"),
            )
            if story_path:
                resolved["story_file"] = str(story_path)
            if story_key:
                resolved["story_key"] = story_key

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Resolved %d variables", len(resolved))

            context_files = self._build_context_files(context, resolved)

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
                output_template="",  # action-workflow, no template
                token_estimate=0,
            )

            result = generate_output(
                compiled,
                project_root=context.project_root,
                context_files=context_files,
                links_only=context.links_only,
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
    ) -> dict[str, str]:
        """Build context files dict with recency-bias ordering.

        1. Strategic docs (project-context only by default).
        1b. Code antipatterns from previous reviews.
        2. Epic file (current epic).
        3. TEA Context (test-design + ATDD checklist).
        4. Source files from story's File List.
        5. Story file (LAST — closest to instructions).

        Closes with staged prompt-budget enforcement.
        """
        files: dict[str, str] = {}
        project_root = context.project_root

        # 1. Strategic docs.
        strategic_service = StrategicContextService(context, "dev_story")
        strategic_files = strategic_service.collect()
        files.update(strategic_files)

        # 1b. Code antipatterns.
        from bmad_assist.compiler.strategic_context import load_antipatterns

        files.update(load_antipatterns(context, "code", budget_tokens=1500))

        # 2. Epic file (current epic).
        epic_num = resolved.get("epic_num")
        if epic_num:
            epic_path = find_epic_file(context, epic_num)
            if epic_path:
                content = safe_read_file(epic_path, project_root)
                if content:
                    files[str(epic_path)] = content

        # 3. TEA Context (test-design, ATDD checklists).
        # Backward compatible: uses TEA config when available, legacy ATDD
        # discovery otherwise.
        story_id = resolved.get("story_id")

        if is_tea_context_enabled(context):
            files.update(collect_tea_context(context, "dev_story", resolved))
        elif story_id:
            atdd_pattern = f"*atdd-checklist*{story_id}*.md"
            atdd_path = find_file_in_output_folder(context, atdd_pattern)
            if atdd_path:
                content = safe_read_file(atdd_path, project_root)
                if content:
                    files[str(atdd_path)] = content
                    logger.info("ATDD checklist loaded (legacy mode): %s", atdd_path)

        # 4. Source files from story's File List.
        story_path_str = resolved.get("story_file")
        if story_path_str:
            story_path = Path(story_path_str)
            story_content = safe_read_file(story_path, project_root)
            file_list_paths: list[str] = []
            if story_content:
                file_list_paths = extract_file_paths_from_story(story_content)

            service = SourceContextService(context, "dev_story")
            source_files = service.collect_files(file_list_paths, None)
            files.update(source_files)

        # 5. Story file (LAST).
        if story_path_str:
            story_path = Path(story_path_str)
            content = safe_read_file(story_path, project_root)
            if content:
                files[str(story_path)] = content

        # 6. Staged prompt budget enforcement.
        from bmad_assist.compiler.budget import ContextSection, PromptBudgetEnforcer

        enforcer = PromptBudgetEnforcer.from_config("dev_story")
        if enforcer.cap > 0:
            strategic_keys = {
                k
                for k in files
                if k.startswith("[project-context") or k.startswith("[antipattern")
            }
            tea_keys = {k for k in files if k.startswith("[tea-") or "atdd" in k.lower()}
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
        """Build mission description for the dev-story workflow."""
        base_description = workflow_ir.raw_config.get(
            "description", "Execute a story by implementing tasks/subtasks, writing tests"
        )

        epic_num = resolved.get("epic_num", "?")
        story_num = resolved.get("story_num", "?")
        story_title = resolved.get("story_title", "")

        if story_title:
            mission = (
                f"{base_description}\n\n"
                f"Target: Story {epic_num}.{story_num} - {story_title}\n"
                f"Implement all tasks and subtasks following TDD methodology."
            )
        else:
            mission = (
                f"{base_description}\n\n"
                f"Target: Story {epic_num}.{story_num}\n"
                f"Implement all tasks and subtasks following TDD methodology."
            )

        return mission


__all__ = ["SKILL_ID", "BmadDevStoryCompiler", "apply_llm_transforms"]
