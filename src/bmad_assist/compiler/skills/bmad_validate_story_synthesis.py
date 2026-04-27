"""Skill-layout compiler for the ``bmad-validate-story-synthesis`` workflow.

Phase 3.5 consumer of
:class:`bmad_assist.compiler.skills._base.SkillLayoutCompilerBase`
for the multi-LLM-aggregation orphan. Same shape as the standard
Phase 3.5 orphans except:

* No patch file exists. The base class handles that gracefully (Phase
  3.5 enhancement) — it skips LLM transforms, regex post-process, and
  patch-validation when ``discover_patch`` returns ``None``.
* The synthesis input shape (validator outputs as virtual
  ``[Validator X]`` files) is built by the LEGACY compiler's
  ``_build_synthesis_context``. We delegate to it via the standard
  base-class pipeline; nothing about the multi-LLM aggregation
  mechanic changes here.

The ``apply_llm_transforms`` import is preserved at module top-level
so tests can monkeypatch
``bmad_assist.compiler.skills.bmad_validate_story_synthesis.apply_llm_transforms``
to stub LLM calls — same convention every other skill compiler uses.
"""

from __future__ import annotations

import logging
from typing import Any

# Re-exported so tests/conftest can patch this module attribute. The
# base class resolves it dynamically per call (see _base.py docstring).
from bmad_assist.compiler.patching import apply_llm_transforms  # noqa: F401
from bmad_assist.compiler.skills._base import SkillLayoutCompilerBase
from bmad_assist.compiler.types import CompilerContext
from bmad_assist.compiler.workflows.validate_story_synthesis import (
    ValidateStorySynthesisCompiler,
)
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)


# Stable skill id used by all references (config, file paths, dispatch keys).
SKILL_ID = "bmad-validate-story-synthesis"

# Minimum validators required for meaningful synthesis. Mirrors the
# legacy compiler's constant — kept here so skill-layout callers can
# error early before delegating into the legacy compile.
_MIN_VALIDATORS = 2


class BmadValidateStorySynthesisCompiler(SkillLayoutCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-validate-story-synthesis`` skill."""

    skill_id = SKILL_ID
    legacy_workflow_name = "validate-story-synthesis"
    legacy_compiler_class = ValidateStorySynthesisCompiler

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """No SKILL.md-level extras for synthesis.

        The validator outputs (``anonymized_validations``), session id,
        story metadata, and Deep Verify findings flow through
        ``context.resolved_variables`` and are read by the legacy
        compiler's ``_build_synthesis_context`` when it builds
        ``[Validator X]`` / ``[Deep Verify Findings]`` virtual files.
        SKILL.md substitution does not need to inject them.

        The SKILL.md references runtime tokens
        (``{communication_language}``, ``{document_output_language}``,
        ``{epic_num}``, ``{story_num}``, ``{user_name}``,
        ``{implementation_artifacts}``) but these are either resolved
        by the legacy variable engine or filled by the runtime master
        agent. None require compile-time injection from the skill
        compiler.
        """
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the legacy compiler's contract + skill-source check.

        The legacy ``ValidateStorySynthesisCompiler.validate_context``
        already enforces ``epic_num`` / ``story_num`` and the
        ``anonymized_validations`` minimum. We mirror those checks
        here so the skill-layout path errors early with the right
        suggestion, then add the skill-source check.
        """
        if context.project_root is None:
            raise CompilerError("project_root is required in context")
        if context.output_folder is None:
            raise CompilerError("output_folder is required in context")

        epic_num = context.resolved_variables.get("epic_num")
        story_num = context.resolved_variables.get("story_num")
        if epic_num is None:
            raise CompilerError(
                "epic_num is required for validate-story-synthesis compilation.\n"
                "  Suggestion: Provide epic_num via invocation params"
            )
        if story_num is None:
            raise CompilerError(
                "story_num is required for validate-story-synthesis compilation.\n"
                "  Suggestion: Provide story_num via invocation params"
            )

        validations = context.resolved_variables.get("anonymized_validations", [])
        if not validations:
            raise CompilerError(
                "No anonymized validations provided for synthesis.\n"
                "  Why it's needed: Synthesis requires validator outputs to synthesize.\n"
                "  Suggestion: Run validate-story workflow first with multiple LLMs"
            )
        if len(validations) < _MIN_VALIDATORS:
            raise CompilerError(
                f"Synthesis requires at least {_MIN_VALIDATORS} validations, "
                f"but only {len(validations)} provided.\n"
                "  Why: Single validation doesn't need synthesis - use it directly.\n"
                "  Suggestion: Run validation with additional LLM providers"
            )

        skill_md = self._locate_skill_md(context)
        if not skill_md.is_file():  # pragma: no cover — _locate_skill_md raises first
            raise CompilerError(
                f"SKILL.md not found: {skill_md}\n"
                f"  Why it's needed: Source for the bmad-validate-story-synthesis skill\n"
                f"  How to fix: Install bmad-assist v0.5.1+ or rely on the bundled "
                f"fallback under src/bmad_assist/skills/bmad-validate-story-synthesis/"
            )


__all__ = [
    "SKILL_ID",
    "BmadValidateStorySynthesisCompiler",
    "apply_llm_transforms",
]
