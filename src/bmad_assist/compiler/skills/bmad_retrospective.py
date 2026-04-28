"""Skill-layout compiler for the ``bmad-retrospective`` workflow.

Phase 7.2 inlines the workhorse compile logic that previously lived in
:mod:`bmad_assist.compiler.workflows.retrospective` so this compiler is
self-contained — no delegation to a legacy compiler. The base class
handles locate / parse / customize / substitute / cache; the inlined
:meth:`_run_workflow_compile` builds the retrospective context (epic
file, sprint status, story files, optional previous retrospective),
mission, filtered instructions, XML envelope, and post-process tail.

The ``apply_llm_transforms`` import is preserved at module top-level
so tests can monkeypatch
``bmad_assist.compiler.skills.bmad_retrospective.apply_llm_transforms``
to stub LLM calls.
"""

from __future__ import annotations

import logging
import re
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
    find_file_in_planning_dir,
    find_project_context_file,
    find_sprint_status_file,
    get_stories_dir,
    safe_read_file,
)
from bmad_assist.compiler.skills._base import SkillLayoutCompilerBase
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext, WorkflowIR
from bmad_assist.compiler.variable_utils import substitute_variables
from bmad_assist.compiler.variables import resolve_variables
from bmad_assist.core.exceptions import CompilerError
from bmad_assist.testarch.context import collect_tea_context

logger = logging.getLogger(__name__)


SKILL_ID = "bmad-retrospective"

# Pattern for story files.
_STORY_FILE_PATTERN = re.compile(r"^(\d+)-(\d+)-.+\.md$")


class BmadRetrospectiveCompiler(SkillLayoutCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-retrospective`` skill.

    Phase 7.2 inlined: no legacy compiler delegation. The base class
    handles the compile pipeline tail; this subclass owns the
    retrospective-specific build of context files, prev/next epic
    computation, mission, and instructions.
    """

    skill_id = SKILL_ID
    legacy_workflow_name = "retrospective"
    # Phase 7.2: inlined — no legacy compiler delegation.
    legacy_compiler_class = None

    def get_required_files(self) -> list[str]:
        """Glob patterns the retrospective workflow expects."""
        return [
            "**/project_context.md",
            "**/project-context.md",
            "**/architecture*.md",
            "**/prd*.md",
            "**/epic*.md",
            "**/sprint-status.yaml",
        ]

    def get_variables(self) -> dict[str, Any]:
        """Variables resolved by the retrospective compiler."""
        return {
            "epic_num": None,
            "prev_epic_num": None,
            "next_epic_num": None,
            "epic_title": None,
            "date": None,
        }

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """No SKILL.md-level extras for retrospective."""
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the retrospective contract + skill-source check.

        Unlike create-story / dev-story / code-review, retrospective
        operates over an entire epic — no story file required.
        """
        if context.project_root is None:
            raise CompilerError("project_root is required in context")
        if context.output_folder is None:
            raise CompilerError("output_folder is required in context")

        epic_num = context.resolved_variables.get("epic_num")
        if epic_num is None:
            raise CompilerError(
                "epic_num is required for retrospective compilation.\n"
                "  Suggestion: Provide epic_num via invocation params"
            )

        skill_md = self._locate_skill_md(context)
        if not skill_md.is_file():  # pragma: no cover — _locate_skill_md raises first
            raise CompilerError(
                f"SKILL.md not found: {skill_md}\n"
                f"  Why it's needed: Source for the bmad-retrospective skill\n"
                f"  How to fix: Install BMAD v6.4+ in the project, or rely on "
                f"the bmad-assist bundled fallback"
            )

    # --- Inlined workhorse compile ------------------------------------- #

    def _run_workflow_compile(self, context: CompilerContext) -> CompiledWorkflow:
        """Build the retrospective :class:`CompiledWorkflow`."""
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
                if k in ("epic_num", "date")
            }

            sprint_status_path = find_sprint_status_file(context)
            resolved = resolve_variables(context, invocation_params, sprint_status_path, None)

            # Compute prev/next epic numbers (when epic_num is numeric).
            epic_num = resolved.get("epic_num")
            if epic_num is not None:
                try:
                    epic_int = int(epic_num)
                    resolved["prev_epic_num"] = epic_int - 1 if epic_int > 1 else None
                    resolved["next_epic_num"] = epic_int + 1
                except (ValueError, TypeError):
                    # Non-numeric epic (e.g. "testarch") — leave both unset.
                    resolved["prev_epic_num"] = None
                    resolved["next_epic_num"] = None

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

        1. Project context.
        2. Architecture.
        3. PRD.
        4. Epic file.
        5. Sprint status.
        6. Story files for this epic.
        6b. TEA Context (trace matrix when present).
        7. Previous retrospective (LAST — closest to instructions).
        """
        files: dict[str, str] = {}
        project_root = context.project_root

        # 1. Project context (general).
        project_context_path = find_project_context_file(context)
        if project_context_path:
            content = safe_read_file(project_context_path, project_root)
            if content:
                files[str(project_context_path)] = content

        # 2. Architecture.
        arch_path = find_file_in_planning_dir(context, "*architecture*.md")
        if arch_path:
            content = safe_read_file(arch_path, project_root)
            if content:
                files[str(arch_path)] = content

        # 3. PRD.
        prd_path = find_file_in_planning_dir(context, "*prd*.md")
        if prd_path:
            content = safe_read_file(prd_path, project_root)
            if content:
                files[str(prd_path)] = content

        # 4. Epic file.
        epic_num = resolved.get("epic_num")
        if epic_num is not None:
            epic_path = self._find_epic_file(context, epic_num)
            if epic_path:
                content = safe_read_file(epic_path, project_root)
                if content:
                    files[str(epic_path)] = content

        # 5. Sprint status.
        sprint_status_path = find_sprint_status_file(context)
        if sprint_status_path:
            content = safe_read_file(sprint_status_path, project_root)
            if content:
                files[str(sprint_status_path)] = content

        # 6. Story files for this epic.
        if epic_num is not None:
            story_files = self._collect_story_files(context, epic_num)
            files.update(story_files)

        # 6b. TEA Context (trace matrix). May not exist on first
        # retrospective (it's CREATED by the trace workflow).
        files.update(collect_tea_context(context, "retrospective", resolved))

        # 7. Previous retrospective (LAST).
        prev_epic_num = resolved.get("prev_epic_num")
        if prev_epic_num is not None:
            prev_retro_path = self._find_previous_retrospective(context, prev_epic_num)
            if prev_retro_path:
                content = safe_read_file(prev_retro_path, project_root)
                if content:
                    files[str(prev_retro_path)] = content

        return files

    def _find_epic_file(self, context: CompilerContext, epic_num: Any) -> Path | None:
        """Find epic file by number (sharded or whole-document)."""
        planning_dir = context.output_folder
        if planning_dir is None:
            return None

        # Sharded layout: docs/epics/epic-N.md or docs/epics/epic-N-*.md.
        docs_dir = context.project_root / "docs"
        if docs_dir.exists():
            sharded_path = docs_dir / "epics" / f"epic-{epic_num}.md"
            if sharded_path.exists():
                return sharded_path

            epics_dir = docs_dir / "epics"
            if epics_dir.exists():
                for f in epics_dir.glob(f"epic-{epic_num}*.md"):
                    return f

        # Whole-document fallback in the planning dir.
        whole_path = find_file_in_planning_dir(context, f"*epic*{epic_num}*.md")
        if whole_path:
            return whole_path

        return None

    def _collect_story_files(self, context: CompilerContext, epic_num: Any) -> dict[str, str]:
        """Collect all story files for an epic."""
        result: dict[str, str] = {}
        project_root = context.project_root

        stories_dir = get_stories_dir(context)
        if not stories_dir.exists():
            # Fallback to legacy location.
            stories_dir = project_root / "docs" / "sprint-artifacts"
            if not stories_dir.exists():
                return result

        for story_file in sorted(stories_dir.glob(f"{epic_num}-*.md")):
            match = _STORY_FILE_PATTERN.match(story_file.name)
            if match and match.group(1) == str(epic_num):
                content = safe_read_file(story_file, project_root)
                if content:
                    result[str(story_file)] = content

        if result:
            logger.debug("Collected %d story files for epic %s", len(result), epic_num)

        return result

    def _find_previous_retrospective(
        self, context: CompilerContext, prev_epic_num: int
    ) -> Path | None:
        """Find previous epic's retrospective file."""
        impl_artifacts = context.output_folder
        if impl_artifacts is None:
            return None

        retro_dir = impl_artifacts / "retrospectives"
        if not retro_dir.exists():
            return None

        pattern = f"epic-{prev_epic_num}-retro-*.md"
        retros = sorted(retro_dir.glob(pattern), reverse=True)

        if retros:
            return retros[0]

        return None

    def _build_mission(
        self,
        workflow_ir: WorkflowIR,
        resolved: dict[str, Any],
    ) -> str:
        """Build mission description for the retrospective workflow."""
        base_description = workflow_ir.raw_config.get(
            "description", "Run epic retrospective to review success and extract lessons learned"
        )

        epic_num = resolved.get("epic_num", "?")
        epic_title = resolved.get("epic_title", "")

        if epic_title:
            mission = (
                f"{base_description}\n\n"
                f"Target: Epic {epic_num} - {epic_title}\n"
                f"Generate retrospective report with extraction markers."
            )
        else:
            mission = (
                f"{base_description}\n\n"
                f"Target: Epic {epic_num}\n"
                f"Generate retrospective report with extraction markers."
            )

        return mission


__all__ = ["SKILL_ID", "BmadRetrospectiveCompiler", "apply_llm_transforms"]
