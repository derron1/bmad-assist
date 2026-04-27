"""Skill-layout compiler for the ``bmad-testarch-ci`` workflow.

Phase 3.2-B consumer of
:class:`bmad_assist.compiler.skills._base.SkillLayoutCompilerBase`.
Same shape as :class:`bmad_assist.compiler.skills.bmad_testarch_atdd`:
the base class handles the compile pipeline (find/parse/resolve/
substitute/transform/validate/cache/delegate-to-legacy); this module
only encodes the workflow-specific contract.

bmad-testarch-ci uses BMAD v6.4+ step-file architecture (no inline
``<workflow>`` envelope; step files live under ``steps-c/``,
``steps-v/``, and ``steps-e/``). The CI workflow scaffolds CI/CD
quality pipelines with test execution, burn-in loops, and artifact
collection.

The ``apply_llm_transforms`` import is preserved at module top-level so
tests can monkeypatch
``bmad_assist.compiler.skills.bmad_testarch_ci.apply_llm_transforms``
to stub LLM calls.
"""

from __future__ import annotations

import logging
from typing import Any

# Re-exported so tests/conftest can patch this module attribute. The
# base class resolves it dynamically per call (see _base.py docstring).
from bmad_assist.compiler.patching import apply_llm_transforms  # noqa: F401
from bmad_assist.compiler.skills._base import SkillLayoutCompilerBase
from bmad_assist.compiler.types import CompilerContext
from bmad_assist.compiler.workflows.testarch_ci import TestarchCiCompiler
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)


SKILL_ID = "bmad-testarch-ci"


class BmadTestarchCiCompiler(SkillLayoutCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-testarch-ci`` skill."""

    skill_id = SKILL_ID
    legacy_workflow_name = "testarch-ci"
    legacy_compiler_class = TestarchCiCompiler

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """No workflow-specific extras for testarch-ci.

        The legacy compiler resolves ``{epic_num}`` / optional
        ``{story_num}`` / ``{date}`` downstream. The SKILL.md body
        references runtime tokens (``{user_name}``,
        ``{communication_language}``, ``{workflow.*}`` blocks,
        ``{skill-root}`` / ``{skill-name}`` / ``{project-root}``) that
        the variable engine resolves before the LLM transform layer.
        ``{ci_platform}`` (if present in the post-LLM body) is filled
        from ``_bmad/tea/config.yaml`` at runtime.
        """
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the legacy compiler's contract + skill-source check.

        The legacy ``TestarchCiCompiler`` (via the tri-modal base)
        checks ``epic_num`` and the workflow directory's existence.
        ``story_num`` is optional — CI scaffolding is typically a
        once-per-project or once-per-epic operation.
        """
        if context.project_root is None:
            raise CompilerError("project_root is required in context")
        if context.output_folder is None:
            raise CompilerError("output_folder is required in context")

        epic_num = context.resolved_variables.get("epic_num")
        if epic_num is None:
            raise CompilerError(
                "epic_num is required for testarch-ci compilation.\n"
                "  Suggestion: Provide epic_num via invocation params"
            )

        skill_md = self._locate_skill_md(context)
        if not skill_md.is_file():  # pragma: no cover — _locate_skill_md raises first
            raise CompilerError(
                f"SKILL.md not found: {skill_md}\n"
                f"  Why it's needed: Source for the bmad-testarch-ci skill\n"
                f"  How to fix: Install BMAD v6.4+ in the project, or rely on "
                f"the bmad-assist bundled fallback"
            )


__all__ = ["SKILL_ID", "BmadTestarchCiCompiler", "apply_llm_transforms"]
