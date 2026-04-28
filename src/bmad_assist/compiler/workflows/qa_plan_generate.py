"""Compiler for the qa-plan-generate workflow.

This module implements the WorkflowCompiler protocol for the qa-plan-generate
workflow, producing standalone prompts for QA plan generation with context
files embedded directly in the prompt.

Key benefits over embedded prompt in generator.py:
- Patchable via .bmad-assist/patches/qa-plan-generate.patch.yaml
- Consistent with other BMAD workflows
- Transparent - prompt visible in instructions.md
- Standalone usage via Claude Code slash commands

Context embedding:
1. Epic definition file
2. Story files (up to 10, truncated)
3. Traceability file (if exists)
4. UX elements documentation (CRITICAL for Category B tests)
5. PRD and architecture (optional)

Public API:
    QaPlanGenerateCompiler: Workflow compiler class implementing WorkflowCompiler protocol
"""

import logging
from pathlib import Path
from typing import Any

from bmad_assist.compiler.filtering import filter_instructions
from bmad_assist.compiler.output import generate_output
from bmad_assist.compiler.shared_utils import (
    apply_post_process,
    context_snapshot,
    get_stories_dir,
    load_workflow_template,
    safe_read_file,
)
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext, WorkflowIR
from bmad_assist.compiler.variable_utils import substitute_variables
from bmad_assist.compiler.variables import resolve_variables
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)

# Workflow path relative to project root
_WORKFLOW_RELATIVE_PATH = "_bmad/bmm/workflows/4-implementation/qa-plan-generate"

# QA artifacts path relative to output folder
_QA_ARTIFACTS_RELATIVE = "qa-artifacts"

# Maximum content sizes to prevent context overflow
_MAX_EPIC_CONTENT = 8000
_MAX_STORY_CONTENT = 4000
_MAX_STORIES = 10
_MAX_TRACE_CONTENT = 5000
_MAX_UX_ELEMENTS_CONTENT = 15000
_MAX_PRD_CONTENT = 5000
_MAX_ARCH_CONTENT = 3000


class QaPlanGenerateCompiler:
    """Compiler for the qa-plan-generate workflow.

    Implements the WorkflowCompiler protocol to compile the qa-plan-generate
    workflow into a standalone prompt. The key value is embedding context
    files (epic, stories, ux-elements) directly in the prompt.

    """

    @property
    def workflow_name(self) -> str:
        """Unique workflow identifier."""
        return "qa-plan-generate"

    def get_required_files(self) -> list[str]:
        """Return list of required file glob patterns.

        Returns:
            Glob patterns for files that may be needed.

        """
        return [
            "**/docs/epics/epic-*.md",
            "**/implementation-artifacts/stories/*.md",
            "**/qa-artifacts/traceability/epic-*-trace.md",
            "**/docs/modules/*/ux-elements.md",
            "**/docs/ux-elements.md",
        ]

    def get_variables(self) -> dict[str, Any]:
        """Return workflow-specific variables to resolve.

        Returns:
            Variables needed for qa-plan-generate compilation.

        """
        return {
            "epic_num": None,
            "force": False,
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
            get_workflow_not_found_message,
        )
        from bmad_assist.compiler.workflows import resolve_legacy_workflow_dir

        workflow_dir = resolve_legacy_workflow_dir(self.workflow_name, context.project_root)
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
        epic_num = context.resolved_variables.get("epic_num")

        if epic_num is None:
            raise CompilerError(
                "epic_num is required for qa-plan-generate compilation.\n"
                "  Suggestion: Provide epic_num via -e/--epic option"
            )

        # Workflow directory is validated by get_workflow_dir via discovery
        workflow_dir = self.get_workflow_dir(context)
        if not workflow_dir.exists():
            raise CompilerError(
                f"Workflow directory not found: {workflow_dir}\n"
                f"  Why it's needed: Contains workflow.yaml and instructions.md\n"
                f"  How to fix: Reinstall bmad-assist or ensure BMAD is properly installed"
            )

    def compile(self, context: CompilerContext) -> CompiledWorkflow:
        """Compile qa-plan-generate workflow with given context.

        Executes the full compilation pipeline:
        1. Use pre-loaded workflow_ir from context
        2. Resolve variables
        3. Build context files (epic, stories, trace, ux-elements)
        4. Filter instructions
        5. Generate XML output

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

            # Extract invocation params
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

            # Load output template from workflow
            template_content = load_workflow_template(workflow_ir, context)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Loaded template: %d bytes", len(template_content or ""))

            compiled = CompiledWorkflow(
                workflow_name=self.workflow_name,
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
                workflow_name=self.workflow_name,
                mission=mission,
                context=final_xml,
                variables=resolved,
                instructions=filtered_instructions,
                output_template=template_content or "",
                token_estimate=result.token_estimate,
            )

    def _build_context_files(
        self,
        context: CompilerContext,
        resolved: dict[str, Any],
    ) -> dict[str, str]:
        """Build context files dict with epic, stories, trace, ux-elements.

        Files are ordered by importance:
        1. UX elements (CRITICAL for Category B - loaded first)
        2. Epic definition
        3. Stories (up to 10)
        4. Traceability file
        5. PRD (optional)
        6. Architecture (optional)

        Args:
            context: Compilation context with paths.
            resolved: Resolved variables containing epic_num.

        Returns:
            Dictionary mapping file paths to content.

        """
        files: dict[str, str] = {}
        project_root = context.project_root
        epic_num = resolved.get("epic_num")

        if epic_num is None:
            logger.warning("epic_num is None, skipping context files")
            return files

        # 1. UX Elements (CRITICAL - must be first for Category B tests)
        ux_content = self._load_ux_elements(project_root, epic_num)
        if ux_content:
            files["ux-elements.md"] = ux_content
            logger.info("Embedded UX elements: %d bytes", len(ux_content))

        # 2. Epic definition
        epic_content = self._load_epic(project_root, epic_num)
        if epic_content:
            files[f"epic-{epic_num}.md"] = epic_content
            logger.debug("Embedded epic: %d bytes", len(epic_content))

        # 3. Stories
        stories = self._load_stories(context, epic_num)
        for story_name, story_content in stories.items():
            files[story_name] = story_content
        if stories:
            logger.debug("Embedded %d stories", len(stories))

        # 4. Traceability file
        trace_content = self._load_trace(context, epic_num)
        if trace_content:
            files[f"epic-{epic_num}-trace.md"] = trace_content
            logger.debug("Embedded trace: %d bytes", len(trace_content))

        # 5. PRD (optional, truncated)
        prd_path = project_root / "docs" / "prd.md"
        if prd_path.exists():
            content = safe_read_file(prd_path, project_root)
            if content:
                files["prd.md"] = content[:_MAX_PRD_CONTENT]
                logger.debug("Embedded PRD: %d bytes", len(files["prd.md"]))

        # 6. Architecture (optional, truncated)
        arch_path = project_root / "docs" / "architecture.md"
        if arch_path.exists():
            content = safe_read_file(arch_path, project_root)
            if content:
                files["architecture.md"] = content[:_MAX_ARCH_CONTENT]
                logger.debug("Embedded architecture: %d bytes", len(files["architecture.md"]))

        return files

    def _load_ux_elements(self, project_root: Path, epic_num: Any) -> str | None:
        """Load UX elements documentation for Category B tests.

        Searches multiple locations for ux-elements.md files.
        This is CRITICAL for generating correct Playwright selectors.

        Args:
            project_root: Project root directory.
            epic_num: Epic number (for epic-specific ux file).

        Returns:
            UX elements content or None if not found.

        """
        ux_paths = [
            # Module-specific
            project_root / "docs" / "modules" / "dashboard" / "ux-elements.md",
            project_root / "docs" / "modules" / "experiments" / "ux-elements.md",
            # Generic project-wide
            project_root / "docs" / "ux-elements.md",
            # Epic-specific
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
        """Load epic definition file.

        Searches multiple locations for epic file.

        Args:
            project_root: Project root directory.
            epic_num: Epic number.

        Returns:
            Epic content or None if not found.

        """
        epic_patterns = [
            project_root / "docs" / "epics" / f"epic-{epic_num}.md",
            project_root / "docs" / "epics" / f"epic-{epic_num}-*.md",
        ]

        for pattern in epic_patterns:
            if pattern.exists():
                content = safe_read_file(pattern, project_root)
                if content:
                    return content[:_MAX_EPIC_CONTENT]
            # Try glob for wildcard patterns
            if "*" in str(pattern):
                matches = list(pattern.parent.glob(pattern.name))
                if matches:
                    content = safe_read_file(matches[0], project_root)
                    if content:
                        return content[:_MAX_EPIC_CONTENT]

        logger.debug("No epic file found for epic %s", epic_num)
        return None

    def _load_stories(self, context: CompilerContext, epic_num: Any) -> dict[str, str]:
        """Load story files for this epic.

        Args:
            context: Compilation context.
            epic_num: Epic number.

        Returns:
            Dictionary mapping story filename to content.

        """
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
        """Load traceability file if exists.

        Args:
            context: Compilation context.
            epic_num: Epic number.

        Returns:
            Trace content or None if not found.

        """
        qa_artifacts = context.output_folder / _QA_ARTIFACTS_RELATIVE
        trace_path = qa_artifacts / "traceability" / f"epic-{epic_num}-trace.md"

        if trace_path.exists():
            content = safe_read_file(trace_path, context.project_root)
            if content:
                return content[:_MAX_TRACE_CONTENT]

        return None

    def _get_output_path(self, context: CompilerContext, epic_num: Any) -> Path:
        """Get path where QA plan should be written.

        Args:
            context: Compilation context.
            epic_num: Epic number.

        Returns:
            Path to output QA plan file.

        """
        qa_artifacts = context.output_folder / _QA_ARTIFACTS_RELATIVE
        return qa_artifacts / "test-plans" / f"epic-{epic_num}-e2e-plan.md"

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
            Mission description string for qa-plan-generate.

        """
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
