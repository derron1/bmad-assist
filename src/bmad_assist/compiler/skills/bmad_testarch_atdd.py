"""Skill-layout compiler for the ``bmad-testarch-atdd`` workflow.

Phase 7.2 inlines the workhorse compile logic via
:class:`bmad_assist.compiler.skills._testarch_base.TestarchSkillCompilerBase`.
The shared tri-modal pipeline lives on the base class; this module
only encodes the workflow-specific contract: story_num requirement,
``test_dir`` variable, and ``test-design-epic`` context augmentation.

bmad-testarch-atdd uses BMAD v6.4+ step-file architecture (no inline
``<workflow>`` envelope; step files live under ``steps-c/``,
``steps-v/``, and ``steps-e/``). The ATDD workflow generates failing
acceptance tests before implementation using the TDD red-green-refactor
cycle.

The ``apply_llm_transforms`` import is preserved at module top-level so
tests can monkeypatch
``bmad_assist.compiler.skills.bmad_testarch_atdd.apply_llm_transforms``
to stub LLM calls.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

# Re-exported so tests/conftest can patch this module attribute. The
# base class resolves it dynamically per call (see _base.py docstring).
from bmad_assist.compiler.patching import apply_llm_transforms  # noqa: F401
from bmad_assist.compiler.shared_utils import safe_read_file
from bmad_assist.compiler.skills._testarch_base import TestarchSkillCompilerBase
from bmad_assist.compiler.types import CompilerContext, WorkflowIR
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)


SKILL_ID = "bmad-testarch-atdd"


class BmadTestarchAtddCompiler(TestarchSkillCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-testarch-atdd`` skill."""

    skill_id = SKILL_ID
    legacy_workflow_name = "testarch-atdd"
    # Phase 7.2: inlined — no legacy compiler delegation.
    legacy_compiler_class = None

    def get_variables(self) -> dict[str, Any]:
        """Return ATDD-specific variables (adds ``test_dir``)."""
        base_vars = super().get_variables()
        base_vars["test_dir"] = None  # From workflow.yaml variables
        return base_vars

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the shared TEA contract + story-level requirement.

        ATDD is a story-level workflow, so ``story_num`` is required in
        addition to the base ``epic_num`` requirement.
        """
        super().validate_context(context)

        story_num = context.resolved_variables.get("story_num")
        if story_num is None:
            raise CompilerError(
                "story_num is required for testarch-atdd compilation.\n"
                "  Suggestion: Provide story_num via invocation params"
            )

    def _get_workflow_specific_variables(
        self,
        resolved: dict[str, Any],
        context: CompilerContext,
        workflow_ir: WorkflowIR,
    ) -> None:
        """Resolve ``test_dir`` from workflow.yaml variables."""
        workflow_vars = workflow_ir.raw_config.get("variables", {})
        test_dir = workflow_vars.get("test_dir", "{project-root}/tests")
        test_dir = test_dir.replace("{project-root}", str(context.project_root))
        resolved["test_dir"] = test_dir

    def _build_context_files(
        self,
        context: CompilerContext,
        resolved: dict[str, Any],
    ) -> dict[str, str]:
        """Add test-design-epic file to base context for P0/P1/P2 priorities."""
        files = super()._build_context_files(context, resolved)

        epic_num = resolved.get("epic_num")
        if epic_num:
            test_design_pattern = f"*test-design-epic*{epic_num}*.md"
            test_design_path = self._find_test_design_file(context, test_design_pattern)
            if test_design_path:
                content = safe_read_file(test_design_path, context.project_root)
                if content:
                    files[str(test_design_path)] = content
                    logger.debug("Embedded test-design-epic: %s", test_design_path)

        return files

    def _find_test_design_file(
        self,
        context: CompilerContext,
        pattern: str,
    ) -> Path | None:
        """Find test-design file in output folder.

        Searches ``implementation-artifacts/`` first, then
        ``planning-artifacts/``, then the output folder root.
        """
        output_folder = context.output_folder
        if not output_folder or not output_folder.exists():
            return None

        for subdir in ["implementation-artifacts", "planning-artifacts", ""]:
            search_dir = output_folder / subdir if subdir else output_folder
            if search_dir.exists():
                matches = sorted(search_dir.glob(pattern))
                if matches:
                    return matches[0]

        return None


__all__ = ["SKILL_ID", "BmadTestarchAtddCompiler", "apply_llm_transforms"]
