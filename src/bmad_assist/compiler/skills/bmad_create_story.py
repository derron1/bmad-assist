"""Skill-layout compiler for the ``bmad-create-story`` workflow.

Phase 7.2 inlines the workhorse compile logic that previously lived in
:mod:`bmad_assist.compiler.workflows.create_story` so this compiler is
self-contained — no delegation to a legacy compiler. The base class
handles locate / parse / customize / substitute / cache; the inlined
:meth:`_run_workflow_compile` builds the create-story context files,
mission, and XML envelope.

The ``apply_llm_transforms`` import is preserved at module top-level
so existing tests can monkeypatch
``bmad_assist.compiler.skills.bmad_create_story.apply_llm_transforms``
to stub LLM calls.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from bmad_assist.compiler.context import ContextBuilder
from bmad_assist.compiler.filtering import filter_instructions
from bmad_assist.compiler.output import generate_output

# Re-exported so tests/conftest can patch this module attribute. The
# base class resolves it dynamically per call (see _base.py docstring).
from bmad_assist.compiler.patching import apply_llm_transforms  # noqa: F401
from bmad_assist.compiler.shared_utils import (
    apply_post_process,
    context_snapshot,
    find_previous_stories,
    find_project_context_file,
    find_sprint_status_file,
    get_epics_dir,
    load_workflow_template,
)
from bmad_assist.compiler.skills._base import SkillLayoutCompilerBase
from bmad_assist.compiler.source_context import (
    SourceContextService,
    extract_file_paths_from_story,
)
from bmad_assist.compiler.strategic_context import StrategicContextService
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext, WorkflowIR
from bmad_assist.compiler.variables import resolve_variables
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)


SKILL_ID = "bmad-create-story"

# Patterns for variable substitution. Inlined from the legacy module
# because the helper was module-private and only used here.
_DOUBLE_BRACE_PATTERN = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_-]*)\}\}")
_SINGLE_BRACE_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_-]*)\}")


def _substitute_variables(text: str, variables: dict[str, Any]) -> str:
    """Replace ``{{var}}`` and ``{var}`` placeholders with resolved values.

    Unknown placeholders are left intact.
    """

    def replace_var(match: re.Match[str]) -> str:
        var_name = match.group(1)
        if var_name in variables:
            value = variables[var_name]
            return str(value) if value is not None else ""
        return match.group(0)

    result = _DOUBLE_BRACE_PATTERN.sub(replace_var, text)
    result = _SINGLE_BRACE_PATTERN.sub(replace_var, result)
    return result


class BmadCreateStoryCompiler(SkillLayoutCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-create-story`` skill.

    Phase 7.2 inlined: no legacy compiler delegation. The base class
    handles the compile pipeline tail; this subclass owns the
    create-story-specific build of context files, mission, and
    instructions.
    """

    skill_id = SKILL_ID
    legacy_workflow_name = "create-story"
    # Phase 7.2: inlined — no legacy compiler delegation.
    legacy_compiler_class = None

    def get_required_files(self) -> list[str]:
        """Glob patterns the create-story workflow expects."""
        return [
            "**/project_context.md",  # Required - critical implementation rules
            "**/architecture*.md",
            "**/prd*.md",
            "**/ux*.md",
            "**/sprint-status.yaml",
            "**/epic*.md",
        ]

    def get_variables(self) -> dict[str, Any]:
        """Variables resolved by the create-story compiler."""
        return {
            "epic_num": None,
            "story_num": None,
            "story_key": None,
            "story_id": None,
            "story_title": None,
            "date": None,
        }

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """No SKILL.md-level extras for create-story.

        The variable engine downstream handles workflow tokens during
        the inlined compile body.
        """
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the create-story contract + skill-source check.

        Beyond the legacy checks (epic_num + story_num required), the
        skill-layout path also verifies SKILL.md is locatable and
        ``project_context.md`` exists — quality of the prompt depends
        on it being embedded.
        """
        if context.project_root is None:
            raise CompilerError("project_root is required in context")
        if context.output_folder is None:
            raise CompilerError("output_folder is required in context")

        epic_num = context.resolved_variables.get("epic_num")
        story_num = context.resolved_variables.get("story_num")
        if epic_num is None:
            raise CompilerError(
                "epic_num is required for create-story compilation.\n"
                "  Suggestion: Provide epic_num via invocation params or ensure "
                "sprint-status.yaml has a backlog story"
            )
        if story_num is None:
            raise CompilerError(
                "story_num is required for create-story compilation.\n"
                "  Suggestion: Provide story_num via invocation params or ensure "
                "sprint-status.yaml has a backlog story"
            )

        skill_md = self._locate_skill_md(context)
        if not skill_md.is_file():  # pragma: no cover — _locate_skill_md raises first
            raise CompilerError(
                f"SKILL.md not found: {skill_md}\n"
                f"  Why it's needed: Source for the bmad-create-story skill\n"
                f"  How to fix: Install BMAD v6.4+ in the project, or rely on "
                f"the bmad-assist bundled fallback"
            )

        project_context_path = find_project_context_file(context)
        if project_context_path is None:
            raise CompilerError(
                f"project_context.md not found: {context.output_folder / 'project_context.md'}\n"
                f"  Why it's needed: Contains critical implementation rules for AI agents\n"
                f"  How to fix: Run 'generate-project-context' workflow"
            )

    # --- Inlined workhorse compile ------------------------------------- #

    def _run_workflow_compile(self, context: CompilerContext) -> CompiledWorkflow:
        """Build the create-story :class:`CompiledWorkflow`.

        Mirrors the pre-Phase-7 ``CreateStoryCompiler.compile`` flow
        byte-for-byte. The base class has already set ``context.workflow_ir``
        to a synthetic IR built around the patched SKILL.md body.
        """
        workflow_ir = context.workflow_ir
        if workflow_ir is None:  # pragma: no cover — base guarantees this
            raise CompilerError(
                "workflow_ir not set in context. This is a bug - core.py should have loaded it."
            )

        with context_snapshot(context):
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Compiling %s", self.skill_id)

            # 1. Resolve variables with sprint-status lookup.
            invocation_params = {
                k: v
                for k, v in context.resolved_variables.items()
                if k in ("epic_num", "story_num", "story_title", "date")
            }

            sprint_status_path = find_sprint_status_file(context)

            epic_num = invocation_params.get("epic_num")
            epics_path: Path | None = None
            if epic_num:
                epic_files = self._find_epic_context_files(
                    context,
                    {"epic_num": epic_num, "story_num": invocation_params.get("story_num")},
                )
                # Prefer the actual epic-{num}-*.md file.
                for f in epic_files:
                    if f.name.startswith(f"epic-{epic_num}"):
                        epics_path = f
                        break
                if not epics_path and epic_files:
                    epics_path = epic_files[0]

            resolved = resolve_variables(context, invocation_params, sprint_status_path, epics_path)

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Resolved %d variables", len(resolved))

            # 2. Build context files (recency-bias).
            context_files = self._build_context_files(context, resolved)

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Built context with %d files", len(context_files))

            # 3. Template + filtered instructions.
            template_content = load_workflow_template(workflow_ir, context)
            filtered_instructions = filter_instructions(workflow_ir)
            filtered_instructions = _substitute_variables(filtered_instructions, resolved)

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Filtered instructions: %d bytes", len(filtered_instructions))

            # 4. Mission + XML output.
            mission = self._build_mission(workflow_ir, resolved)
            compiled = CompiledWorkflow(
                workflow_name=self.legacy_workflow_name,
                mission=mission,
                context="",
                variables=resolved,
                instructions=filtered_instructions,
                output_template=template_content,
                token_estimate=0,
            )

            result = generate_output(
                compiled,
                project_root=context.project_root,
                context_files=context_files,
                links_only=context.links_only,
            )

            # 5. Patch post_process tail.
            final_xml = apply_post_process(result.xml, context)

            return CompiledWorkflow(
                workflow_name=self.legacy_workflow_name,
                mission=mission,
                context=final_xml,
                variables=resolved,
                instructions=filtered_instructions,
                output_template=template_content,
                token_estimate=result.token_estimate,
            )

    # --- Helpers --------------------------------------------------------- #

    def _build_context_files(
        self,
        context: CompilerContext,
        resolved: dict[str, Any],
    ) -> dict[str, str]:
        """Build context files dict with recency-bias ordering.

        1. Strategic docs (project-context, prd, architecture, ux).
        2. Story antipatterns (if exists).
        3. Previous stories (oldest first, count=1).
        4. Source files from previous-stories' File List.
        5. Epic files (LAST — most specific).
        """
        files: dict[str, str] = {}
        epic_num = resolved.get("epic_num")

        # Store resolved variables in context for ContextBuilder to use.
        context.resolved_variables = resolved

        # 1. Strategic docs.
        strategic_service = StrategicContextService(context, "create_story")
        strategic_files = strategic_service.collect()
        files.update(strategic_files)

        # 2. Story antipatterns.
        from bmad_assist.compiler.strategic_context import load_antipatterns

        files.update(load_antipatterns(context, "story", budget_tokens=1000))

        # 3. Previous stories via ContextBuilder.
        builder = ContextBuilder(context)
        builder = builder.add_previous_stories(count=1)
        prev_stories_files = builder.build()
        files.update(prev_stories_files)

        # Get previous stories for source-file collection.
        prev_stories = find_previous_stories(context, resolved, max_stories=1)

        file_list_paths: list[str] = []
        for story_path in prev_stories:
            try:
                story_content = story_path.read_text(encoding="utf-8")
                paths = extract_file_paths_from_story(story_content)
                file_list_paths.extend(paths)
            except (OSError, UnicodeDecodeError) as e:
                logger.debug("Could not read story %s: %s", story_path, e)

        # 4. Source files from File List (no git diff for create-story).
        source_service = SourceContextService(context, "create_story")
        source_files = source_service.collect_files(file_list_paths, None)
        files.update(source_files)

        # 5. Epic files (LAST).
        if epic_num is not None:
            epic_builder = ContextBuilder(context)
            epic_builder = epic_builder.add_epic_files(epic_num=epic_num)
            epic_files = epic_builder.build()
            files.update(epic_files)

        return files

    def _find_epic_context_files(
        self,
        context: CompilerContext,
        resolved: dict[str, Any],
    ) -> list[Path]:
        """Find epic context files for compilation.

        Sharded layout: returns supporting files (index/summary/etc.) +
        the matching ``epic-{num}-*.md`` file.

        Single-file layout: returns the matching standalone epic file.
        """
        epic_num = resolved.get("epic_num")
        if epic_num is None:
            return []

        epics_dir = get_epics_dir(context)
        found_files: list[Path] = []

        if epics_dir.exists() and epics_dir.is_dir():
            epic_pattern = re.compile(r"^epic-\d+-")

            for file_path in sorted(epics_dir.glob("*.md")):
                filename = file_path.name
                if epic_pattern.match(filename):
                    if f"epic-{epic_num}-" in filename:
                        found_files.append(file_path)
                        logger.debug("Found current epic file: %s", file_path)
                else:
                    found_files.append(file_path)
                    logger.debug("Found epic support file: %s", file_path)

            if found_files:
                logger.debug("Found %d epic context files for epic %s", len(found_files), epic_num)
                return found_files

        epic_file = self._find_single_epic_file(context, epic_num)
        if epic_file:
            logger.debug("Found single epic file: %s", epic_file)
            return [epic_file]

        logger.warning("No epic files found for epic %s", epic_num)
        return []

    def _find_single_epic_file(self, context: CompilerContext, epic_num: Any) -> Path | None:
        """Find single-file epic (not sharded).

        Falls back through ``output_folder``, then ``project_knowledge``.
        """
        from bmad_assist.core.paths import get_paths

        pattern = f"*epic*{epic_num}*.md"
        matches = sorted(context.output_folder.glob(pattern))
        if matches:
            return matches[0]

        generic_epics = context.output_folder / "epics.md"
        if generic_epics.exists():
            return generic_epics

        try:
            paths = get_paths()
            project_knowledge = paths.project_knowledge

            matches = sorted(project_knowledge.glob(pattern))
            if matches:
                return matches[0]

            generic_epics = project_knowledge / "epics.md"
            if generic_epics.exists():
                return generic_epics
        except RuntimeError:
            pass

        return None

    def _build_mission(
        self,
        workflow_ir: WorkflowIR,
        resolved: dict[str, Any],
    ) -> str:
        """Build mission description for the create-story workflow."""
        base_description = workflow_ir.raw_config.get(
            "description", "Create the next user story from epics"
        )

        epic_num = resolved.get("epic_num", "?")
        story_num = resolved.get("story_num", "?")
        story_title = resolved.get("story_title", "")

        if story_title:
            mission = (
                f"{base_description}\n\n"
                f"Target: Story {epic_num}.{story_num} - {story_title}\n"
                f"Create comprehensive developer context and implementation-ready story."
            )
        else:
            mission = (
                f"{base_description}\n\n"
                f"Target: Story {epic_num}.{story_num}\n"
                f"Create comprehensive developer context and implementation-ready story."
            )

        return mission


__all__ = ["SKILL_ID", "BmadCreateStoryCompiler", "apply_llm_transforms"]
