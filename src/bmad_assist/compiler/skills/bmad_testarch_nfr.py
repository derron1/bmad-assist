"""Skill-layout compiler for the ``bmad-testarch-nfr`` workflow.

Phase 3.2-B consumer of
:class:`bmad_assist.compiler.skills._base.SkillLayoutCompilerBase`.
Same shape as :class:`bmad_assist.compiler.skills.bmad_testarch_atdd`:
the base class handles the compile pipeline (find/parse/resolve/
substitute/transform/validate/cache/delegate-to-legacy); this module
only encodes the workflow-specific contract.

Naming note (Phase 3.2-B rename)
--------------------------------
The legacy workflow was named ``testarch-nfr-assess``. The v6.4+
canonical skill drops the ``-assess`` suffix and is published as
``bmad-testarch-nfr``. This compiler honours both names:

* ``skill_id = "bmad-testarch-nfr"`` — canonical, used for the SKILL.md
  lookup, cache file naming, and dispatch key.
* ``legacy_workflow_name = "testarch-nfr-assess"`` — un-prefixed legacy
  name still used by the patch file
  (``.bmad-assist/patches/testarch-nfr-assess.patch.yaml``) and the
  routing alias in ``compiler/core.py::_SKILL_LAYOUT_COMPILERS``.

bmad-testarch-nfr uses BMAD v6.4+ step-file architecture (no inline
``<workflow>`` envelope; step files live under ``steps-c/``,
``steps-v/``, and ``steps-e/``). The NFR workflow assesses
non-functional requirements (performance, security, reliability,
maintainability) before release with evidence-based validation.

The ``apply_llm_transforms`` import is preserved at module top-level so
tests can monkeypatch
``bmad_assist.compiler.skills.bmad_testarch_nfr.apply_llm_transforms``
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
from bmad_assist.compiler.workflows.testarch_nfr_assess import TestarchNfrAssessCompiler
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)


SKILL_ID = "bmad-testarch-nfr"


class BmadTestarchNfrCompiler(SkillLayoutCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-testarch-nfr`` skill.

    Backed by the legacy ``TestarchNfrAssessCompiler`` (un-prefixed name
    ``testarch-nfr-assess``); see module docstring for the rename
    rationale.
    """

    skill_id = SKILL_ID
    # Drives patch lookup (testarch-nfr-assess.patch.yaml) and the
    # routing alias on _SKILL_LAYOUT_COMPILERS. Do NOT change to
    # "testarch-nfr" without also renaming the patch file.
    legacy_workflow_name = "testarch-nfr-assess"
    legacy_compiler_class = TestarchNfrAssessCompiler

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """No workflow-specific extras for testarch-nfr.

        The legacy compiler resolves ``{epic_num}`` / optional
        ``{story_num}`` / ``{date}`` downstream. The SKILL.md body
        references runtime tokens (``{user_name}``,
        ``{communication_language}``, ``{workflow.*}`` blocks,
        ``{skill-root}`` / ``{skill-name}`` / ``{project-root}``) that
        the variable engine resolves before the LLM transform layer.
        ``{category}`` (NFR focus area) and ``{risk_threshold}`` are
        filled from ``_bmad/tea/config.yaml`` at runtime; the patch's
        post_process replaces unresolved ``{category}`` with ``"all"``.
        """
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the legacy compiler's contract + skill-source check.

        The legacy compiler (via the tri-modal base) checks
        ``epic_num`` and the workflow directory's existence.
        ``story_num`` is optional — NFR assessment is typically an
        epic- or release-level operation.
        """
        if context.project_root is None:
            raise CompilerError("project_root is required in context")
        if context.output_folder is None:
            raise CompilerError("output_folder is required in context")

        epic_num = context.resolved_variables.get("epic_num")
        if epic_num is None:
            raise CompilerError(
                "epic_num is required for testarch-nfr compilation.\n"
                "  Suggestion: Provide epic_num via invocation params"
            )

        skill_md = self._locate_skill_md(context)
        if not skill_md.is_file():  # pragma: no cover — _locate_skill_md raises first
            raise CompilerError(
                f"SKILL.md not found: {skill_md}\n"
                f"  Why it's needed: Source for the bmad-testarch-nfr skill\n"
                f"  How to fix: Install BMAD v6.4+ in the project, or rely on "
                f"the bmad-assist bundled fallback"
            )


__all__ = ["SKILL_ID", "BmadTestarchNfrCompiler", "apply_llm_transforms"]
