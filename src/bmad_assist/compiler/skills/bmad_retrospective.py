"""Skill-layout compiler for the ``bmad-retrospective`` workflow.

Phase 3.2 consumer of
:class:`bmad_assist.compiler.skills._base.SkillLayoutCompilerBase`.
Same shape as :class:`bmad_assist.compiler.skills.bmad_dev_story`:
the base class handles the compile pipeline (find/parse/resolve/
substitute/transform/validate/cache/delegate-to-legacy); this module
only encodes the workflow-specific contract.

The ``apply_llm_transforms`` import is preserved at module top-level
so tests can monkeypatch
``bmad_assist.compiler.skills.bmad_retrospective.apply_llm_transforms``
to stub LLM calls — the same convention used by ``bmad_dev_story``.
"""

from __future__ import annotations

import logging
from typing import Any

# Re-exported so tests/conftest can patch this module attribute. The
# base class resolves it dynamically per call (see _base.py docstring).
from bmad_assist.compiler.patching import apply_llm_transforms  # noqa: F401
from bmad_assist.compiler.skills._base import SkillLayoutCompilerBase
from bmad_assist.compiler.types import CompilerContext
from bmad_assist.compiler.workflows.retrospective import RetrospectiveCompiler
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)


# Stable skill id used by all references (config, file paths, dispatch
# keys). Mirrors ``bmad-create-story`` for consistency.
SKILL_ID = "bmad-retrospective"


class BmadRetrospectiveCompiler(SkillLayoutCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-retrospective`` skill."""

    skill_id = SKILL_ID
    legacy_workflow_name = "retrospective"
    legacy_compiler_class = RetrospectiveCompiler

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """No workflow-specific extras for retrospective.

        The legacy compiler resolves ``{epic_num}`` and computes
        ``{prev_epic_num}`` / ``{next_epic_num}`` from it during its
        own ``compile()`` call. The retrospective SKILL.md references
        runtime tokens (``{communication_language}``,
        ``{user_skill_level}``, ``{implementation_artifacts}``,
        ``{planning_artifacts}``, ``{user_name}``, ...) but the patch
        either strips party-mode dialogue blocks that reference them
        or leaves them for the runtime master agent to resolve from
        the embedded context section. None require compile-time
        injection from the skill compiler.
        """
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the legacy compiler's contract + skill-source check.

        The legacy ``RetrospectiveCompiler.validate_context`` already
        checks for ``epic_num`` and the workflow directory's
        existence. Unlike create-story / dev-story / code-review, the
        retrospective workflow does NOT require a story file (it
        operates over an entire epic). We add the skill-source check
        on top so a missing SKILL.md fails early with a
        skill-layout-specific message.
        """
        if context.project_root is None:
            raise CompilerError("project_root is required in context")
        if context.output_folder is None:
            raise CompilerError("output_folder is required in context")

        epic_num = context.resolved_variables.get("epic_num")
        if epic_num is None:
            raise CompilerError(
                "epic_num is required for retrospective compilation.\n"
                "  Suggestion: Provide epic_num via invocation params"
            )

        skill_md = self._locate_skill_md(context)
        if not skill_md.is_file():  # pragma: no cover — _locate_skill_md raises first
            raise CompilerError(
                f"SKILL.md not found: {skill_md}\n"
                f"  Why it's needed: Source for the bmad-retrospective skill\n"
                f"  How to fix: Install BMAD v6.4+ in the project, or rely on "
                f"the bmad-assist bundled fallback"
            )


__all__ = ["SKILL_ID", "BmadRetrospectiveCompiler", "apply_llm_transforms"]
