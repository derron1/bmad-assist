"""Skill-layout compiler for the ``bmad-qa-plan-execute`` workflow.

Phase 7.2 inlines the workhorse compile logic that previously lived in
:mod:`bmad_assist.compiler.workflows.qa_plan_execute` so this compiler
is self-contained — no delegation to a legacy compiler. The base class
handles locate / parse / customize / substitute / cache; the inlined
:meth:`_run_workflow_compile` builds the qa-plan-execute context (test
plan + optional previous run results), mission, filtered instructions,
XML envelope, and post-process tail.

The legacy compiler enforced ``non_interactive=True`` /
``auto_continue_on_fail=True`` for the compiled (headless) path; the
inlined version preserves that behaviour.

The ``apply_llm_transforms`` import is preserved at module top-level
so tests can monkeypatch
``bmad_assist.compiler.skills.bmad_qa_plan_execute.apply_llm_transforms``
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
    safe_read_file,
)
from bmad_assist.compiler.skills._base import SkillLayoutCompilerBase
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext, WorkflowIR
from bmad_assist.compiler.variable_utils import substitute_variables
from bmad_assist.compiler.variables import resolve_variables
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)


SKILL_ID = "bmad-qa-plan-execute"

# QA artifacts path relative to output folder.
_QA_ARTIFACTS_RELATIVE = "qa-artifacts"


class BmadQaPlanExecuteCompiler(SkillLayoutCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-qa-plan-execute`` skill.

    Phase 7.2 inlined: no legacy compiler delegation. The base class
    handles the compile pipeline tail; this subclass owns the
    qa-plan-execute-specific test-plan + previous-run embedding,
    mission, and instructions.
    """

    skill_id = SKILL_ID
    legacy_workflow_name = "qa-plan-execute"
    # Phase 7.2: inlined — no legacy compiler delegation.
    legacy_compiler_class = None

    def get_required_files(self) -> list[str]:
        """Glob patterns the qa-plan-execute workflow expects."""
        return [
            "**/qa-artifacts/test-plans/epic-*-e2e-plan.md",
            "**/qa-artifacts/test-results/epic-*-run-*.yaml",
        ]

    def get_variables(self) -> dict[str, Any]:
        """Variables resolved by the qa-plan-execute compiler."""
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
            "non_interactive": True,  # Compiled mode is always non-interactive.
            "auto_continue_on_fail": True,
        }

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """No SKILL.md-level extras for qa-plan-execute."""
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the qa-plan-execute contract + skill-source + test-plan checks."""
        if context.project_root is None:
            raise CompilerError("project_root is required in context")

        epic_num = context.resolved_variables.get("epic_num")
        if epic_num is None:
            raise CompilerError(
                "epic_num is required for qa-plan-execute compilation.\n"
                "  Suggestion: Provide epic_num via invocation params"
            )

        skill_md = self._locate_skill_md(context)
        if not skill_md.is_file():  # pragma: no cover — _locate_skill_md raises first
            raise CompilerError(
                f"SKILL.md not found: {skill_md}\n"
                f"  Why it's needed: Source for the bmad-qa-plan-execute skill\n"
                f"  How to fix: Install bmad-assist v0.5.1+ or rely on the "
                f"bundled fallback under src/bmad_assist/skills/bmad-qa-plan-execute/"
            )

        # Test-plan existence check. Skip when output_folder isn't set
        # (this matches the pre-Phase-7 deferred-validation behaviour).
        if context.output_folder is not None:
            test_plan_path = self._get_test_plan_path(context, epic_num)
            if not test_plan_path.exists():
                raise CompilerError(
                    f"Test plan not found: {test_plan_path}\n"
                    f"  Why it's needed: Contains E2E tests to execute\n"
                    f"  How to fix: Run /qa-plan-generate {epic_num} first"
                )

    # --- Inlined workhorse compile ------------------------------------- #

    def _run_workflow_compile(self, context: CompilerContext) -> CompiledWorkflow:
        """Build the qa-plan-execute :class:`CompiledWorkflow`."""
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

            # Compiled mode is always non-interactive.
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
                workflow_name=self.legacy_workflow_name,
                mission=mission,
                context="",
                variables=resolved,
                instructions=filtered_instructions,
                output_template="",  # Uses result-template.yaml via instructions.
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
        """Build context files dict.

        1. Test plan file (MAIN — tests to execute).
        2. Previous run results (optional, when ``rerun_failed`` set).
        """
        files: dict[str, str] = {}
        project_root = context.project_root
        epic_num = resolved.get("epic_num")

        if epic_num is None:
            logger.warning("epic_num is None, skipping context files")
            return files

        # 1. Test plan file (MAIN).
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

        # 2. Previous run (for rerun-failed mode).
        if resolved.get("rerun_failed"):
            prev_run_path = self._find_latest_run(context, epic_num)
            if prev_run_path:
                content = safe_read_file(prev_run_path, project_root)
                if content:
                    files[str(prev_run_path)] = content
                    logger.debug("Embedded previous run: %s", prev_run_path.name)

        return files

    def _get_test_plan_path(self, context: CompilerContext, epic_num: Any) -> Path:
        """Get path to test plan file for epic (may not exist)."""
        qa_artifacts = context.output_folder / _QA_ARTIFACTS_RELATIVE
        return qa_artifacts / "test-plans" / f"epic-{epic_num}-e2e-plan.md"

    def _find_latest_run(self, context: CompilerContext, epic_num: Any) -> Path | None:
        """Find most recent test run results YAML for epic."""
        qa_artifacts = context.output_folder / _QA_ARTIFACTS_RELATIVE
        results_dir = qa_artifacts / "test-results"

        if not results_dir.exists():
            return None

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
        """Build mission description for the qa-plan-execute workflow."""
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


__all__ = ["SKILL_ID", "BmadQaPlanExecuteCompiler", "apply_llm_transforms"]
