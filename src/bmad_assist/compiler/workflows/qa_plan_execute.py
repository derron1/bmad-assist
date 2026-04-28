"""Compiler for the qa-plan-execute workflow.

This module implements the WorkflowCompiler protocol for the qa-plan-execute
workflow, producing standalone prompts for E2E test execution with the test
plan file embedded directly in the prompt.

Key benefits over standalone workflow:
- Test plan file embedded in prompt (saves LLM Read API call)
- Previous run results embedded for rerun-failed mode
- Compile-time validation of test plan existence
- Variable resolution before LLM invocation

Public API:
    QaPlanExecuteCompiler: Workflow compiler class implementing WorkflowCompiler protocol
"""

import logging
from pathlib import Path
from typing import Any

from bmad_assist.compiler.filtering import filter_instructions
from bmad_assist.compiler.output import generate_output
from bmad_assist.compiler.shared_utils import (
    apply_post_process,
    context_snapshot,
    safe_read_file,
)
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext, WorkflowIR
from bmad_assist.compiler.variable_utils import substitute_variables
from bmad_assist.compiler.variables import resolve_variables
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)

# Workflow path relative to project root
_WORKFLOW_RELATIVE_PATH = "_bmad/bmm/workflows/4-implementation/qa-plan-execute"

# QA artifacts path relative to output folder
_QA_ARTIFACTS_RELATIVE = "qa-artifacts"


class QaPlanExecuteCompiler:
    """Compiler for the qa-plan-execute workflow.

    Implements the WorkflowCompiler protocol to compile the qa-plan-execute
    workflow into a standalone prompt. The key value is embedding the test
    plan file directly in the prompt, saving LLM API calls for file reading.

    Context embedding:
    1. Test plan file (MAIN - the tests to execute)
    2. Previous run results (for rerun-failed mode)

    """

    @property
    def workflow_name(self) -> str:
        """Unique workflow identifier."""
        return "qa-plan-execute"

    def get_required_files(self) -> list[str]:
        """Return list of required file glob patterns.

        Returns:
            Glob patterns for files needed by qa-plan-execute workflow.

        """
        return [
            "**/qa-artifacts/test-plans/epic-*-e2e-plan.md",
            "**/qa-artifacts/test-results/epic-*-run-*.yaml",
        ]

    def get_variables(self) -> dict[str, Any]:
        """Return workflow-specific variables to resolve.

        Returns:
            Variables needed for qa-plan-execute compilation.

        """
        return {
            "epic_num": None,
            "category": "A",
            "test_id": None,
            "dry_run": False,
            "verbose": False,
            "rerun_failed": False,
            "timeout_seconds": 60,
            "fail_fast": False,
            "playwright_enabled": False,
            "playwright_headless": True,
            "playwright_screenshot_on_fail": True,
            "generate_bug_reports": True,
            "non_interactive": True,  # Compiled mode is always non-interactive
            "auto_continue_on_fail": True,
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
                "epic_num is required for qa-plan-execute compilation.\n"
                "  Suggestion: Provide epic_num via invocation params"
            )

        # Workflow directory is validated by get_workflow_dir via discovery
        workflow_dir = self.get_workflow_dir(context)
        if not workflow_dir.exists():
            raise CompilerError(
                f"Workflow directory not found: {workflow_dir}\n"
                f"  Why it's needed: Contains workflow.yaml and instructions.md\n"
                f"  How to fix: Reinstall bmad-assist or ensure BMAD is properly installed"
            )

        # Validate test plan exists
        test_plan_path = self._get_test_plan_path(context, epic_num)
        if not test_plan_path.exists():
            raise CompilerError(
                f"Test plan not found: {test_plan_path}\n"
                f"  Why it's needed: Contains E2E tests to execute\n"
                f"  How to fix: Run /qa-plan-generate {epic_num} first"
            )

    def compile(self, context: CompilerContext) -> CompiledWorkflow:
        """Compile qa-plan-execute workflow with given context.

        Executes the full compilation pipeline:
        1. Use pre-loaded workflow_ir from context
        2. Resolve variables
        3. Build context files (test plan + previous run)
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

            # Ensure non_interactive is set for compiled mode
            resolved["non_interactive"] = True
            resolved["auto_continue_on_fail"] = True

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
                workflow_name=self.workflow_name,
                mission=mission,
                context="",
                variables=resolved,
                instructions=filtered_instructions,
                output_template="",  # Uses result-template.yaml via instructions
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
                output_template="",
                token_estimate=result.token_estimate,
            )

    def _build_context_files(
        self,
        context: CompilerContext,
        resolved: dict[str, Any],
    ) -> dict[str, str]:
        """Build context files dict with test plan and previous run.

        Files are ordered:
        1. Test plan file (MAIN - tests to execute)
        2. Previous run results (for rerun-failed mode)

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

        # 1. Test plan file (MAIN)
        test_plan_path = self._get_test_plan_path(context, epic_num)
        logger.info("Looking for test plan: %s", test_plan_path)
        logger.info("  output_folder: %s", context.output_folder)
        logger.info("  project_root: %s", project_root)

        if test_plan_path.exists():
            logger.info("Test plan exists, reading...")
            content = safe_read_file(test_plan_path, project_root)
            if content:
                files[str(test_plan_path)] = content
                logger.info("Embedded test plan: %s (%d bytes)", test_plan_path.name, len(content))
            else:
                logger.warning("safe_read_file returned empty for: %s", test_plan_path)
        else:
            logger.warning("Test plan not found: %s", test_plan_path)

        # 2. Previous run (for rerun-failed mode)
        if resolved.get("rerun_failed"):
            prev_run_path = self._find_latest_run(context, epic_num)
            if prev_run_path:
                content = safe_read_file(prev_run_path, project_root)
                if content:
                    files[str(prev_run_path)] = content
                    logger.debug("Embedded previous run: %s", prev_run_path.name)

        return files

    def _get_test_plan_path(self, context: CompilerContext, epic_num: Any) -> Path:
        """Get path to test plan file for epic.

        Args:
            context: Compilation context.
            epic_num: Epic number.

        Returns:
            Path to test plan file (may not exist).

        """
        qa_artifacts = context.output_folder / _QA_ARTIFACTS_RELATIVE
        return qa_artifacts / "test-plans" / f"epic-{epic_num}-e2e-plan.md"

    def _find_latest_run(self, context: CompilerContext, epic_num: Any) -> Path | None:
        """Find most recent test run results for epic.

        Args:
            context: Compilation context.
            epic_num: Epic number.

        Returns:
            Path to latest run YAML, or None if not found.

        """
        qa_artifacts = context.output_folder / _QA_ARTIFACTS_RELATIVE
        results_dir = qa_artifacts / "test-results"

        if not results_dir.exists():
            return None

        # Find all runs for this epic, sorted by name (timestamp) descending
        pattern = f"epic-{epic_num}-run-*.yaml"
        runs = sorted(results_dir.glob(pattern), reverse=True)

        if runs:
            logger.debug("Found %d previous runs for epic %s", len(runs), epic_num)
            return runs[0]

        return None

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
            Mission description string for qa-plan-execute.

        """
        base_description = workflow_ir.raw_config.get(
            "description", "Execute E2E tests from generated test plans"
        )

        epic_num = resolved.get("epic_num", "?")
        category = resolved.get("category", "A")
        test_id = resolved.get("test_id")

        mission_parts = [
            base_description,
            "",
            f"Target: Epic {epic_num}",
            f"Category: {category}",
        ]

        if test_id:
            mission_parts.append(f"Specific test: {test_id}")

        if resolved.get("dry_run"):
            mission_parts.append("Mode: DRY RUN (parse only, no execution)")

        if resolved.get("rerun_failed"):
            mission_parts.append("Mode: RERUN FAILED (only failed tests from last run)")

        mission_parts.append("")
        mission_parts.append("Execute tests and generate results YAML + summary report.")

        return "\n".join(mission_parts)
