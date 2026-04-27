"""Skill-layout compiler for the ``bmad-qa-plan-generate`` workflow.

Phase 3.5 consumer of
:class:`bmad_assist.compiler.skills._base.SkillLayoutCompilerBase`.
Same shape as :class:`bmad_assist.compiler.skills.bmad_validate_story` —
the base class drives the compile pipeline; this module only encodes
the workflow-specific contract for the orphan ``qa-plan-generate``
workflow.

Notable nuance: the legacy compiler reads ``instructions.md`` (not
``.xml``). The base class doesn't care about the source extension —
it operates on the SKILL.md body, which we author as XML-wrapped
outcome-based markdown. The patch's ``must_contain`` rules check for
section headers like ``"## Test Categories Summary"`` that are emitted
by the runtime master agent into the OUTPUT file, not the prompt
itself, so the patch validation runs cleanly against either source
shape.
"""

from __future__ import annotations

import logging
from typing import Any

# Re-exported so tests/conftest can patch this module attribute. The
# base class resolves it dynamically per call (see _base.py docstring).
from bmad_assist.compiler.patching import apply_llm_transforms  # noqa: F401
from bmad_assist.compiler.skills._base import SkillLayoutCompilerBase
from bmad_assist.compiler.types import CompilerContext
from bmad_assist.compiler.workflows.qa_plan_generate import QaPlanGenerateCompiler
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)


# Stable skill id used by all references (config, file paths, dispatch keys).
SKILL_ID = "bmad-qa-plan-generate"


class BmadQaPlanGenerateCompiler(SkillLayoutCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-qa-plan-generate`` skill."""

    skill_id = SKILL_ID
    legacy_workflow_name = "qa-plan-generate"
    legacy_compiler_class = QaPlanGenerateCompiler

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """No workflow-specific extras for qa-plan-generate.

        The legacy compiler resolves ``{epic_num}`` / ``{output_file}``
        / ``{qa_artifacts}`` / ``{date}`` downstream. The SKILL.md
        references runtime tokens (``{communication_language}``,
        ``{document_output_language}``, ``{user_name}``,
        ``{output_folder}``) but these are either resolved by the
        legacy variable engine or left for the runtime master agent to
        fill from the embedded context section. None require
        compile-time injection from the skill compiler.
        """
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the legacy compiler's contract + skill-source check."""
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


__all__ = ["SKILL_ID", "BmadQaPlanGenerateCompiler", "apply_llm_transforms"]
