"""Skill-layout compiler for the ``bmad-validate-story`` workflow.

Phase 7.2 inlines the workhorse compile logic that previously lived in
:mod:`bmad_assist.compiler.workflows.validate_story` so this compiler
is self-contained — no delegation to a legacy compiler. The base class
handles locate / parse / customize / substitute / cache; the inlined
:meth:`_run_workflow_compile` builds the validate-story context (epic,
story-context, checklist, previous stories, source files, story file
last), mission, filtered instructions, XML envelope, and post-process
tail.

The ``apply_llm_transforms`` import is preserved at module top-level
so tests can monkeypatch
``bmad_assist.compiler.skills.bmad_validate_story.apply_llm_transforms``
to stub LLM calls.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from bmad_assist.compiler.filtering import filter_instructions
from bmad_assist.compiler.output import generate_output

# Re-exported so tests/conftest can patch this module attribute. The
# base class resolves it dynamically per call (see _base.py docstring).
from bmad_assist.compiler.patching import (  # noqa: F401
    apply_llm_transforms,
    load_patch,
    validate_output,
)
from bmad_assist.compiler.shared_utils import (
    apply_post_process,
    context_snapshot,
    find_epic_file,
    find_previous_stories,
    find_sprint_status_file,
    find_story_context_file,
    get_stories_dir,
    load_workflow_template,
    normalize_model_name,
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
from bmad_assist.compiler.variable_utils import (
    filter_garbage_variables,
    substitute_variables,
)
from bmad_assist.compiler.variables import resolve_variables
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)


SKILL_ID = "bmad-validate-story"

# Hardcoded validation focus — not configurable via YAML.
_VALIDATION_FOCUS = "story_quality"


class BmadValidateStoryCompiler(SkillLayoutCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-validate-story`` skill.

    Phase 7.2 inlined: no legacy compiler delegation. The base class
    handles the compile pipeline tail; this subclass owns the
    validate-story-specific context build, mission, and instructions.
    """

    skill_id = SKILL_ID
    legacy_workflow_name = "validate-story"
    # Phase 7.2: inlined — no legacy compiler delegation.
    legacy_compiler_class = None

    def get_required_files(self) -> list[str]:
        """Glob patterns the validate-story workflow expects."""
        return [
            "**/project_context.md",
            "**/architecture*.md",
            "**/prd*.md",
            "**/sprint-status.yaml",
            "**/epic*.md",
            "**/sprint-artifacts/*.md",
        ]

    def get_variables(self) -> dict[str, Any]:
        """Variables resolved by the validate-story compiler."""
        return {
            "epic_num": None,
            "story_num": None,
            "story_key": None,
            "story_id": None,
            "story_file": None,
            "story_title": None,
            "validation_focus": _VALIDATION_FOCUS,
            "date": None,
        }

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """No SKILL.md-level extras for validate-story."""
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the validate-story contract + skill-source check."""
        if context.project_root is None:
            raise CompilerError("project_root is required in context")
        if context.output_folder is None:
            raise CompilerError("output_folder is required in context")

        epic_num = context.resolved_variables.get("epic_num")
        story_num = context.resolved_variables.get("story_num")
        if epic_num is None:
            raise CompilerError(
                "epic_num is required for validate-story compilation.\n"
                "  Suggestion: Provide epic_num via invocation params or ensure "
                "sprint-status.yaml has a story to validate"
            )
        if story_num is None:
            raise CompilerError(
                "story_num is required for validate-story compilation.\n"
                "  Suggestion: Provide story_num via invocation params or ensure "
                "sprint-status.yaml has a story to validate"
            )

        skill_md = self._locate_skill_md(context)
        if not skill_md.is_file():  # pragma: no cover — _locate_skill_md raises first
            raise CompilerError(
                f"SKILL.md not found: {skill_md}\n"
                f"  Why it's needed: Source for the bmad-validate-story skill\n"
                f"  How to fix: Install bmad-assist v0.5.1+ or rely on the "
                f"bundled fallback under src/bmad_assist/skills/bmad-validate-story/"
            )

    # --- Inlined workhorse compile ------------------------------------- #

    def _run_workflow_compile(self, context: CompilerContext) -> CompiledWorkflow:
        """Build the validate-story :class:`CompiledWorkflow`.

        Mirrors the pre-Phase-7 ``ValidateStoryCompiler.compile`` flow
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

            invocation_params = {
                k: v
                for k, v in context.resolved_variables.items()
                if k in ("epic_num", "story_num", "story_title", "date")
            }

            sprint_status_path = find_sprint_status_file(context)
            resolved = resolve_variables(context, invocation_params, sprint_status_path, None)

            # Resolve story file and extract metadata.
            epic_num = resolved.get("epic_num")
            story_num = resolved.get("story_num")
            story_file, story_key, story_title = resolve_story_file(context, epic_num, story_num)

            resolved["story_file"] = str(story_file) if story_file else None
            if story_key:
                resolved["story_key"] = story_key
            if story_title:
                # Override fallback "story-N" pattern with extracted title.
                # Only override the exact "story-{number}" sentinel — preserve
                # legitimate slugs like "story-service-api".
                current_title = resolved.get("story_title", "")
                if not current_title or re.match(r"^story-\d+$", current_title):
                    logger.debug("Extracted story_title from header: %s", story_title)
                    resolved["story_title"] = story_title

            resolved["story_id"] = f"{epic_num}.{story_num}"
            resolved["validation_focus"] = _VALIDATION_FOCUS

            # Compile-time timestamp for output filename.
            resolved["timestamp"] = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Default model name placeholder; overridden at runtime when known.
            if "model" not in resolved:
                resolved["model"] = "validator"

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Resolved %d variables", len(resolved))

            # Build context (raises if story file missing/empty).
            context_files = self._build_context_files(context, resolved)

            template_content = load_workflow_template(workflow_ir, context)

            filtered_instructions = filter_instructions(workflow_ir)
            filtered_instructions = substitute_variables(filtered_instructions, resolved)

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Filtered instructions: %d bytes", len(filtered_instructions))

            mission = self._build_mission(workflow_ir, resolved)

            filtered_vars = filter_garbage_variables(resolved)
            if "model" in filtered_vars:
                filtered_vars["model"] = normalize_model_name(str(filtered_vars["model"]))
            else:
                filtered_vars["model"] = "validator"

            logger.debug(
                "Filtered vars keys (%d): %s", len(filtered_vars), list(filtered_vars.keys())
            )

            compiled = CompiledWorkflow(
                workflow_name=self.legacy_workflow_name,
                mission=mission,
                context="",
                variables=filtered_vars,
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

            final_xml = apply_post_process(result.xml, context)

            # Patch validation tail.
            if context.patch_path and context.patch_path.exists():
                try:
                    patch = load_patch(context.patch_path)
                    if patch.validation:
                        errors = validate_output(final_xml, patch.validation)
                        if errors:
                            logger.warning(
                                "Validation warnings for %s: %s",
                                self.legacy_workflow_name,
                                "; ".join(errors),
                            )
                except Exception as e:
                    logger.warning("Failed to validate output: %s", e)

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

        1. Strategic docs (project-context + architecture by default).
        2. Epic file (related — optional).
        3. Story Context File (BMM internal — optional).
        4. Checklist from skill folder (variable-substituted).
        5. Previous stories (up to 3, oldest first).
        6. Source files from story's File List.
        6b. STORY FILE (LAST — REQUIRED).
        """
        files: dict[str, str] = {}
        project_root = context.project_root
        epic_num = resolved.get("epic_num")
        story_num = resolved.get("story_num")

        # 1. Strategic docs.
        strategic_service = StrategicContextService(context, "validate_story")
        strategic_files = strategic_service.collect()
        files.update(strategic_files)
        logger.debug("Added %d strategic docs via service", len(strategic_files))

        # 2. Epic file (optional).
        epic_path = find_epic_file(context, epic_num)
        if epic_path:
            content = safe_read_file(epic_path, project_root)
            if content:
                files[str(epic_path)] = content
                resolved["epics_file"] = str(epic_path)
                logger.debug("Added epic file to context: %s", epic_path)
        else:
            logger.debug("File not found, skipping: epic file for epic %s", epic_num)

        # 3. Story Context File (optional).
        story_context_path = find_story_context_file(context, epic_num, story_num)
        if story_context_path:
            content = safe_read_file(story_context_path, project_root)
            if content:
                files[str(story_context_path)] = content
                resolved["story_context_file"] = str(story_context_path)
                logger.debug("Added story context file to context: %s", story_context_path)
        else:
            logger.debug(
                "File not found, skipping: story context file for epic %s, story %s",
                epic_num,
                story_num,
            )

        # 4. Checklist (validation checklist from skill folder).
        # In skill-layout, the checklist lives next to SKILL.md, not in
        # the legacy workflow_dir. The base class's get_workflow_dir
        # already returns the SKILL.md parent.
        workflow_dir = self.get_workflow_dir(context)
        checklist_path = workflow_dir / "checklist.md"
        if checklist_path.exists():
            content = safe_read_file(checklist_path)
            if content:
                content = substitute_variables(content, resolved)
                files[str(checklist_path)] = content
                resolved["checklist_file"] = str(checklist_path)
                logger.debug("Added checklist to context: %s", checklist_path)
        else:
            logger.debug("File not found, skipping: checklist.md")

        # 5. Previous stories (up to 3, oldest first).
        prev_stories = find_previous_stories(context, resolved)
        for prev_story in prev_stories:
            content = safe_read_file(prev_story, project_root)
            if content:
                files[str(prev_story)] = content
                logger.debug("Added previous story to context: %s", prev_story)

        # 6. STORY FILE (REQUIRED).
        stories_dir = get_stories_dir(context)
        pattern = f"{epic_num}-{story_num}-*.md"
        story_matches = sorted(stories_dir.glob(pattern)) if stories_dir.exists() else []

        if not story_matches:
            raise CompilerError(
                f"Story file not found: {stories_dir}/{pattern}\n\n"
                f"Expected pattern: {stories_dir}/{epic_num}-{story_num}-*.md\n"
                f"Found: 0 matching files\n\n"
                f"Suggestion: Run 'bmad-assist compile -w create-story -e {epic_num} "
                f"-s {story_num}' first"
            )

        story_path = story_matches[0]

        try:
            if story_path.stat().st_size == 0:
                raise CompilerError(
                    f"Story file is empty: {story_path}\n\n"
                    f"The story file exists but contains no content (0 bytes).\n\n"
                    f"Suggestion: Regenerate the story using create-story workflow"
                )
        except OSError as e:
            raise CompilerError(
                f"Cannot read story file: {story_path}\n\n"
                f"Error: {e}\n\n"
                f"Suggestion: Check file permissions"
            ) from e

        story_content = safe_read_file(story_path, project_root)

        if not story_content:
            raise CompilerError(
                f"Story file unreadable: {story_path}\n\n"
                f"The story file exists but could not be read.\n"
                f"Possible causes: permission denied, encoding error, or path outside project.\n\n"
                f"Suggestion: Check file permissions and encoding (UTF-8 required)"
            )

        # 6a. Source files from story's File List (before story for recency-bias).
        file_list_paths = extract_file_paths_from_story(story_content)
        if file_list_paths:
            try:
                service = SourceContextService(context, "validate_story")
                source_files = service.collect_files(file_list_paths, None)
                files.update(source_files)
                if source_files:
                    logger.debug(
                        "Added %d source files to context for validate_story",
                        len(source_files),
                    )
                else:
                    logger.warning(
                        "No source files collected for validate_story (budget=%d, candidates=%d)",
                        service.budget,
                        len(file_list_paths),
                    )
            except Exception as e:
                logger.warning("Failed to collect source files for validate_story: %s", e)

        # 6b. Insert story LAST.
        files[str(story_path)] = story_content
        resolved["story_file"] = str(story_path)
        logger.debug("Added story file to context (LAST): %s", story_path)

        logger.info(
            "Built context with %d files for validation of story %s.%s",
            len(files),
            epic_num,
            story_num,
        )

        return files

    def _build_mission(
        self,
        workflow_ir: WorkflowIR,
        resolved: dict[str, Any],
    ) -> str:
        """Build adversarial validation mission description."""
        epic_num = resolved.get("epic_num", "?")
        story_num = resolved.get("story_num", "?")
        story_title = resolved.get("story_title", "")

        title_part = f" - {story_title}" if story_title else ""

        return f"""Adversarial Story Validation

Target: Story {epic_num}.{story_num}{title_part}

Your mission is to FIND ISSUES in the story file:
- Identify missing requirements or acceptance criteria
- Find ambiguous or unclear specifications
- Detect gaps in technical context
- Suggest improvements for developer clarity

CRITICAL: You are a VALIDATOR, not a developer.
- Read-only: You cannot modify any files
- Adversarial: Assume the story has problems
- Thorough: Check all sections systematically

Focus on STORY QUALITY, not code implementation."""


__all__ = ["SKILL_ID", "BmadValidateStoryCompiler", "apply_llm_transforms"]
