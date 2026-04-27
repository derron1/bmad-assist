"""Skill-layout compiler for the ``bmad-testarch-atdd`` workflow.

Phase 3.2-B consumer of
:class:`bmad_assist.compiler.skills._base.SkillLayoutCompilerBase`.
Same shape as :class:`bmad_assist.compiler.skills.bmad_code_review`:
the base class handles the compile pipeline (find/parse/resolve/
substitute/transform/validate/cache/delegate-to-legacy); this module
only encodes the workflow-specific contract.

bmad-testarch-atdd uses BMAD v6.4+ step-file architecture (no inline
``<workflow>`` envelope; step files live under ``steps-c/``,
``steps-v/``, and ``steps-e/``). The ATDD workflow generates failing
acceptance tests before implementation using the TDD red-green-refactor
cycle.

The ``apply_llm_transforms`` import is preserved at module top-level so
tests can monkeypatch
``bmad_assist.compiler.skills.bmad_testarch_atdd.apply_llm_transforms``
to stub LLM calls — the same convention used by every other skill
compiler.
"""

from __future__ import annotations

import logging
from typing import Any

# Re-exported so tests/conftest can patch this module attribute. The
# base class resolves it dynamically per call (see _base.py docstring).
from bmad_assist.compiler.patching import apply_llm_transforms  # noqa: F401
from bmad_assist.compiler.skills._base import SkillLayoutCompilerBase
from bmad_assist.compiler.types import CompilerContext
from bmad_assist.compiler.workflows.testarch_atdd import TestarchAtddCompiler
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)


# Stable skill id used by all references (config, file paths, dispatch
# keys). Mirrors ``bmad-code-review`` for consistency.
SKILL_ID = "bmad-testarch-atdd"


class BmadTestarchAtddCompiler(SkillLayoutCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-testarch-atdd`` skill."""

    skill_id = SKILL_ID
    legacy_workflow_name = "testarch-atdd"
    legacy_compiler_class = TestarchAtddCompiler

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """No workflow-specific extras for testarch-atdd.

        The legacy compiler resolves ``{epic_num}`` / ``{story_num}`` /
        ``{story_id}`` / ``{story_file}`` / ``{test_dir}`` / ``{date}``
        downstream during its own ``compile()`` call. The SKILL.md
        body references runtime tokens (``{user_name}``,
        ``{communication_language}``, ``{workflow.*}`` blocks,
        ``{skill-root}`` / ``{skill-name}`` / ``{project-root}``) that
        the variable engine resolves before the LLM transform layer.
        Nothing requires compile-time injection from the skill compiler.
        """
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the legacy compiler's contract + skill-source check.

        The legacy ``TestarchAtddCompiler.validate_context`` already
        checks for ``epic_num``, ``story_num`` (ATDD is a story-level
        workflow), and the workflow directory's existence. We add the
        skill-source check on top so a missing SKILL.md fails early
        with a skill-layout-specific message.
        """
        if context.project_root is None:
            raise CompilerError("project_root is required in context")
        if context.output_folder is None:
            raise CompilerError("output_folder is required in context")

        epic_num = context.resolved_variables.get("epic_num")
        story_num = context.resolved_variables.get("story_num")
        if epic_num is None:
            raise CompilerError(
                "epic_num is required for testarch-atdd compilation.\n"
                "  Suggestion: Provide epic_num via invocation params"
            )
        if story_num is None:
            raise CompilerError(
                "story_num is required for testarch-atdd compilation.\n"
                "  Suggestion: Provide story_num via invocation params"
            )

        skill_md = self._locate_skill_md(context)
        if not skill_md.is_file():  # pragma: no cover — _locate_skill_md raises first
            raise CompilerError(
                f"SKILL.md not found: {skill_md}\n"
                f"  Why it's needed: Source for the bmad-testarch-atdd skill\n"
                f"  How to fix: Install BMAD v6.4+ in the project, or rely on "
                f"the bmad-assist bundled fallback"
            )


__all__ = ["SKILL_ID", "BmadTestarchAtddCompiler", "apply_llm_transforms"]
