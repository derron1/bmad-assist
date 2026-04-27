"""Skill-layout compiler for the ``bmad-qa-plan-execute`` workflow.

Phase 3.5 consumer of
:class:`bmad_assist.compiler.skills._base.SkillLayoutCompilerBase`.
Same shape as the other Phase 3.5 orphans — the base class drives the
compile pipeline; this module only encodes the workflow-specific
contract for the orphan ``qa-plan-execute`` workflow.

Notable nuance: the legacy compiler reads ``instructions.md`` and
embeds the test plan + previous-run YAML directly into the prompt.
``QaPlanExecuteCompiler.validate_context`` raises if the test plan
file does not exist on disk; we delegate to it via ``validate_context``
below so the new path enforces the same precondition.
"""

from __future__ import annotations

import logging
from typing import Any

# Re-exported so tests/conftest can patch this module attribute. The
# base class resolves it dynamically per call (see _base.py docstring).
from bmad_assist.compiler.patching import apply_llm_transforms  # noqa: F401
from bmad_assist.compiler.skills._base import SkillLayoutCompilerBase
from bmad_assist.compiler.types import CompilerContext
from bmad_assist.compiler.workflows.qa_plan_execute import QaPlanExecuteCompiler
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)


# Stable skill id used by all references (config, file paths, dispatch keys).
SKILL_ID = "bmad-qa-plan-execute"


class BmadQaPlanExecuteCompiler(SkillLayoutCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-qa-plan-execute`` skill."""

    skill_id = SKILL_ID
    legacy_workflow_name = "qa-plan-execute"
    legacy_compiler_class = QaPlanExecuteCompiler

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """No workflow-specific extras for qa-plan-execute.

        The legacy compiler resolves ``{epic_num}`` / ``{category}`` /
        ``{test_id}`` / ``{rerun_failed}`` / ``{dry_run}`` / ``{verbose}``
        / ``{fail_fast}`` / ``{timeout_seconds}`` / ``{playwright_*}``
        downstream and pre-pins ``non_interactive=True`` /
        ``auto_continue_on_fail=True`` for the compiled (headless)
        path. The SKILL.md references runtime tokens
        (``{communication_language}``, ``{document_output_language}``,
        ``{user_name}``, ``{output_folder}``, ``{timestamp}``) but these
        are either resolved by the legacy variable engine or filled by
        the runtime master agent. None require compile-time injection
        from the skill compiler.
        """
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the legacy compiler's contract + skill-source check.

        The legacy ``QaPlanExecuteCompiler.validate_context`` already
        enforces that ``epic_num`` is set, the workflow directory
        exists, and the test plan file exists on disk. We delegate to
        that, then add the skill-source check so a missing SKILL.md
        fails early with a skill-layout-specific message.
        """
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

        # Test-plan precondition (output_folder + qa-artifacts/test-plans/...)
        # is enforced by the legacy compiler. Defer to it for the
        # exact error message and "How to fix" suggestion.
        if context.output_folder is not None:
            self._instantiate_legacy().validate_context(context)


__all__ = ["SKILL_ID", "BmadQaPlanExecuteCompiler", "apply_llm_transforms"]
