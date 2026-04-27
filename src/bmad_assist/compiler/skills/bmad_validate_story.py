"""Skill-layout compiler for the ``bmad-validate-story`` workflow.

Phase 3.5 consumer of
:class:`bmad_assist.compiler.skills._base.SkillLayoutCompilerBase`.
Same shape as :class:`bmad_assist.compiler.skills.bmad_dev_story` —
the base class drives the compile pipeline; this module only encodes
the workflow-specific contract for the orphan ``validate-story``
workflow.

The ``apply_llm_transforms`` import is preserved at module top-level
so tests can monkeypatch
``bmad_assist.compiler.skills.bmad_validate_story.apply_llm_transforms``
to stub LLM calls — the same convention every other skill compiler uses.
"""

from __future__ import annotations

import logging
from typing import Any

# Re-exported so tests/conftest can patch this module attribute. The
# base class resolves it dynamically per call (see _base.py docstring).
from bmad_assist.compiler.patching import apply_llm_transforms  # noqa: F401
from bmad_assist.compiler.skills._base import SkillLayoutCompilerBase
from bmad_assist.compiler.types import CompilerContext
from bmad_assist.compiler.workflows.validate_story import ValidateStoryCompiler
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)


# Stable skill id used by all references (config, file paths, dispatch
# keys). Mirrors ``bmad-create-story`` for consistency.
SKILL_ID = "bmad-validate-story"


class BmadValidateStoryCompiler(SkillLayoutCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-validate-story`` skill."""

    skill_id = SKILL_ID
    legacy_workflow_name = "validate-story"
    legacy_compiler_class = ValidateStoryCompiler

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """No workflow-specific extras for validate-story.

        The legacy compiler resolves ``{epic_num}`` / ``{story_num}`` /
        ``{story_file}`` / ``{story_key}`` / ``{story_title}`` /
        ``{validation_focus}`` / ``{date}`` downstream. The SKILL.md
        references runtime tokens (``{communication_language}``,
        ``{document_output_language}``, ``{user_name}``,
        ``{implementation_artifacts}``, ``{planning_artifacts}``) but
        these are either resolved by the legacy variable engine or left
        for the runtime master agent to fill from the embedded context
        section. None require compile-time injection from the skill
        compiler.
        """
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the legacy compiler's contract + skill-source check.

        The legacy ``ValidateStoryCompiler.validate_context`` already
        checks for ``epic_num``/``story_num`` and the workflow
        directory's existence. We add the skill-source check on top so
        a missing SKILL.md fails early with a skill-layout-specific
        message.
        """
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


__all__ = ["SKILL_ID", "BmadValidateStoryCompiler", "apply_llm_transforms"]
