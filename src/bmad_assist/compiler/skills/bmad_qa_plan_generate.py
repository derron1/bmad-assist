"""Skill-layout compiler for the ``bmad-qa-plan-generate`` workflow.

Phase 7.2 inlines the workhorse compile logic that previously lived in
:mod:`bmad_assist.compiler.workflows.qa_plan_generate` so this compiler
is self-contained — no delegation to a legacy compiler. The base class
handles locate / parse / customize / substitute / cache; the inlined
:meth:`_run_workflow_compile` builds the qa-plan-generate context (UX
elements + epic + stories + trace + truncated PRD/architecture),
mission, filtered instructions, XML envelope, and post-process tail.

The ``apply_llm_transforms`` import is preserved at module top-level
so tests can monkeypatch
``bmad_assist.compiler.skills.bmad_qa_plan_generate.apply_llm_transforms``
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
    get_stories_dir,
    load_workflow_template,
    safe_read_file,
)
from bmad_assist.compiler.skills._base import SkillLayoutCompilerBase
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext, WorkflowIR
from bmad_assist.compiler.variable_utils import substitute_variables
from bmad_assist.compiler.variables import resolve_variables
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)


SKILL_ID = "bmad-qa-plan-generate"

# QA artifacts path relative to output folder.
_QA_ARTIFACTS_RELATIVE = "qa-artifacts"

# Maximum content sizes to prevent context overflow.
_MAX_EPIC_CONTENT = 8000
_MAX_STORY_CONTENT = 4000
_MAX_STORIES = 10
_MAX_TRACE_CONTENT = 5000
_MAX_UX_ELEMENTS_CONTENT = 15000
_MAX_PRD_CONTENT = 5000
_MAX_ARCH_CONTENT = 3000


class BmadQaPlanGenerateCompiler(SkillLayoutCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-qa-plan-generate`` skill.

    Phase 7.2 inlined: no legacy compiler delegation. The base class
    handles the compile pipeline tail; this subclass owns the
    qa-plan-generate-specific context build (UX elements first +
    epic + stories + trace), mission, and instructions.
    """

    skill_id = SKILL_ID
    legacy_workflow_name = "qa-plan-generate"
    # Phase 7.2: inlined — no legacy compiler delegation.
    legacy_compiler_class = None

    def get_required_files(self) -> list[str]:
        """Glob patterns the qa-plan-generate workflow expects."""
        return [
            "**/docs/epics/epic-*.md",
            "**/implementation-artifacts/stories/*.md",
            "**/qa-artifacts/traceability/epic-*-trace.md",
            "**/docs/modules/*/ux-elements.md",
            "**/docs/ux-elements.md",
        ]

    def get_variables(self) -> dict[str, Any]:
        """Variables resolved by the qa-plan-generate compiler."""
        return {
            "epic_num": None,
            "force": False,
        }

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """No SKILL.md-level extras for qa-plan-generate."""
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the qa-plan-generate contract + skill-source check."""
        if context.project_root is None:
            raise CompilerError("project_root is required in context")
        if context.output_folder is None:
            raise CompilerError("output_folder is required in context")

        epic_num = context.resolved_variables.get("epic_num")
        if epic_num is None:
            raise CompilerError(
                "epic_num is required for qa-plan-generate compilation.\n"
                "  Suggestion: Provide epic_num via -e/--epic option"
            )

        skill_md = self._locate_skill_md(context)
        if not skill_md.is_file():  # pragma: no cover — _locate_skill_md raises first
            raise CompilerError(
                f"SKILL.md not found: {skill_md}\n"
                f"  Why it's needed: Source for the bmad-qa-plan-generate skill\n"
                f"  How to fix: Install bmad-assist v0.5.1+ or rely on the "
                f"bundled fallback under src/bmad_assist/skills/bmad-qa-plan-generate/"
            )

    # --- Inlined workhorse compile ------------------------------------- #

    def _run_workflow_compile(self, context: CompilerContext) -> CompiledWorkflow:
        """Build the qa-plan-generate :class:`CompiledWorkflow`."""
        workflow_ir = context.workflow_ir
        if workflow_ir is None:  # pragma: no cover — base guarantees this
            raise CompilerError(
                "workflow_ir not set in context. This is a bug - core.py should have loaded it."
            )

        with context_snapshot(context):
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Compiling %s", self.skill_id)

            invocation_params = {
                k: v for k, v in context.resolved_variables.items() if k in self.get_variables()
            }

            resolved = resolve_variables(context, invocation_params, None, None)

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

            template_content = load_workflow_template(workflow_ir, context)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Loaded template: %d bytes", len(template_content or ""))

            compiled = CompiledWorkflow(
                workflow_name=self.legacy_workflow_name,
                mission=mission,
                context="",
                variables=resolved,
                instructions=filtered_instructions,
                output_template=template_content or "",
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
                output_template=template_content or "",
                token_estimate=result.token_estimate,
            )

    # --- Helpers --------------------------------------------------------- #

    def _build_context_files(
        self,
        context: CompilerContext,
        resolved: dict[str, Any],
    ) -> dict[str, str]:
        """Build context files dict.

        1. UX elements (CRITICAL — first for Category B test selectors).
        2. Epic definition.
        3. Stories (capped at 10).
        4. Traceability file.
        5. PRD (optional, truncated).
        6. Architecture (optional, truncated).
        """
        files: dict[str, str] = {}
        project_root = context.project_root
        epic_num = resolved.get("epic_num")

        if epic_num is None:
            logger.warning("epic_num is None, skipping context files")
            return files

        # 1. UX Elements (CRITICAL).
        ux_content = self._load_ux_elements(project_root, epic_num)
        if ux_content:
            files["ux-elements.md"] = ux_content
            logger.info("Embedded UX elements: %d bytes", len(ux_content))

        # 2. Epic definition.
        epic_content = self._load_epic(project_root, epic_num)
        if epic_content:
            files[f"epic-{epic_num}.md"] = epic_content
            logger.debug("Embedded epic: %d bytes", len(epic_content))

        # 3. Stories.
        stories = self._load_stories(context, epic_num)
        for story_name, story_content in stories.items():
            files[story_name] = story_content
        if stories:
            logger.debug("Embedded %d stories", len(stories))

        # 4. Traceability file.
        trace_content = self._load_trace(context, epic_num)
        if trace_content:
            files[f"epic-{epic_num}-trace.md"] = trace_content
            logger.debug("Embedded trace: %d bytes", len(trace_content))

        # 5. PRD (optional, truncated).
        prd_path = project_root / "docs" / "prd.md"
        if prd_path.exists():
            content = safe_read_file(prd_path, project_root)
            if content:
                files["prd.md"] = content[:_MAX_PRD_CONTENT]
                logger.debug("Embedded PRD: %d bytes", len(files["prd.md"]))

        # 6. Architecture (optional, truncated).
        arch_path = project_root / "docs" / "architecture.md"
        if arch_path.exists():
            content = safe_read_file(arch_path, project_root)
            if content:
                files["architecture.md"] = content[:_MAX_ARCH_CONTENT]
                logger.debug("Embedded architecture: %d bytes", len(files["architecture.md"]))

        return files

    def _load_ux_elements(self, project_root: Path, epic_num: Any) -> str | None:
        """Load UX elements docs for Category B Playwright tests."""
        ux_paths = [
            # Module-specific.
            project_root / "docs" / "modules" / "dashboard" / "ux-elements.md",
            project_root / "docs" / "modules" / "experiments" / "ux-elements.md",
            # Generic project-wide.
            project_root / "docs" / "ux-elements.md",
            # Epic-specific.
            project_root / "docs" / "epics" / f"epic-{epic_num}-ux.md",
        ]

        all_content: list[str] = []

        for ux_path in ux_paths:
            if ux_path.exists():
                content = safe_read_file(ux_path, project_root)
                if content:
                    all_content.append(f"# From: {ux_path.name}\n\n{content}")
                    logger.debug("Found UX elements: %s", ux_path)

        if not all_content:
            logger.warning(
                "No ux-elements.md found. Category B tests will need manual selector discovery."
            )
            return None

        combined = "\n\n---\n\n".join(all_content)
        return combined[:_MAX_UX_ELEMENTS_CONTENT]

    def _load_epic(self, project_root: Path, epic_num: Any) -> str | None:
        """Load epic definition file."""
        epic_patterns = [
            project_root / "docs" / "epics" / f"epic-{epic_num}.md",
            project_root / "docs" / "epics" / f"epic-{epic_num}-*.md",
        ]

        for pattern in epic_patterns:
            if pattern.exists():
                content = safe_read_file(pattern, project_root)
                if content:
                    return content[:_MAX_EPIC_CONTENT]
            # Glob fallback for wildcard patterns.
            if "*" in str(pattern):
                matches = list(pattern.parent.glob(pattern.name))
                if matches:
                    content = safe_read_file(matches[0], project_root)
                    if content:
                        return content[:_MAX_EPIC_CONTENT]

        logger.debug("No epic file found for epic %s", epic_num)
        return None

    def _load_stories(self, context: CompilerContext, epic_num: Any) -> dict[str, str]:
        """Load story files for this epic (capped at ``_MAX_STORIES``)."""
        stories: dict[str, str] = {}
        stories_dir = get_stories_dir(context)

        if not stories_dir.exists():
            logger.debug("Stories directory not found: %s", stories_dir)
            return stories

        pattern = f"{epic_num}-*.md"
        story_files = sorted(stories_dir.glob(pattern))[:_MAX_STORIES]

        for story_file in story_files:
            content = safe_read_file(story_file, context.project_root)
            if content:
                stories[story_file.name] = content[:_MAX_STORY_CONTENT]

        return stories

    def _load_trace(self, context: CompilerContext, epic_num: Any) -> str | None:
        """Load traceability file if present."""
        qa_artifacts = context.output_folder / _QA_ARTIFACTS_RELATIVE
        trace_path = qa_artifacts / "traceability" / f"epic-{epic_num}-trace.md"

        if trace_path.exists():
            content = safe_read_file(trace_path, context.project_root)
            if content:
                return content[:_MAX_TRACE_CONTENT]

        return None

    def _build_mission(
        self,
        workflow_ir: WorkflowIR,
        resolved: dict[str, Any],
    ) -> str:
        """Build mission description for the qa-plan-generate workflow."""
        base_description = workflow_ir.raw_config.get(
            "description", "Generate comprehensive E2E test plans for completed epics"
        )

        epic_num = resolved.get("epic_num", "?")

        mission_parts = [
            base_description,
            "",
            f"Target: Epic {epic_num}",
            "",
            "Generate E2E test plan with categories:",
            "- Category A: CLI/API/File tests (100% automatable)",
            "- Category B: Playwright UI tests (use ONLY selectors from ux-elements.md)",
            "- Category C: Human verification (manual tests)",
            "",
            "CRITICAL: For Category B tests, use ONLY data-testid selectors from the",
            "embedded ux-elements.md. NEVER invent or guess selector names.",
        ]

        return "\n".join(mission_parts)


__all__ = ["SKILL_ID", "BmadQaPlanGenerateCompiler", "apply_llm_transforms"]
