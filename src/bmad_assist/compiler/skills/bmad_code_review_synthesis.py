"""Skill-layout compiler for the ``bmad-code-review-synthesis`` workflow.

Phase 3.5 consumer of
:class:`bmad_assist.compiler.skills._base.SkillLayoutCompilerBase`
for the multi-LLM code-review aggregation orphan. Same shape as
:mod:`bmad_assist.compiler.skills.bmad_validate_story_synthesis`
(its sibling) — the base class drives the compile pipeline; this
module only encodes the workflow-specific contract.

Notable nuances:

* No patch file exists. The base class's no-patch path (Phase 3.5)
  handles that gracefully — substituted SKILL.md is the final body.
* The synthesis input shape (reviewer outputs as virtual
  ``[Reviewer X]`` files, plus git diff, modified source files, and
  optional Deep Verify / Security / TEA findings) is built by the
  LEGACY compiler's ``_build_synthesis_context``. We delegate to it
  via the standard base-class pipeline; the multi-LLM aggregation
  mechanic does not change.

The ``apply_llm_transforms`` import is preserved at module top-level
so tests can monkeypatch
``bmad_assist.compiler.skills.bmad_code_review_synthesis.apply_llm_transforms``
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
from bmad_assist.compiler.workflows.code_review_synthesis import (
    CodeReviewSynthesisCompiler,
)
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)


# Stable skill id used by all references (config, file paths, dispatch keys).
SKILL_ID = "bmad-code-review-synthesis"

# Minimum reviewers required for meaningful synthesis. Mirrors the
# legacy compiler's constant — kept here so skill-layout callers can
# error early before delegating into the legacy compile.
_MIN_REVIEWERS = 2


class BmadCodeReviewSynthesisCompiler(SkillLayoutCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-code-review-synthesis`` skill."""

    skill_id = SKILL_ID
    legacy_workflow_name = "code-review-synthesis"
    legacy_compiler_class = CodeReviewSynthesisCompiler

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """No SKILL.md-level extras for code-review synthesis.

        Reviewer outputs (``anonymized_reviews``), session id, story
        metadata, git diff, Deep Verify findings, security findings,
        and TEA test-review findings flow through
        ``context.resolved_variables`` and are read by the legacy
        compiler's ``_build_synthesis_context`` when it builds the
        ``[Reviewer X]`` / ``[git-diff]`` / ``[Deep Verify Findings]``
        / ``[Security Findings]`` virtual files. SKILL.md substitution
        does not need to inject them.

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

        The legacy ``CodeReviewSynthesisCompiler.validate_context``
        already enforces ``epic_num`` / ``story_num`` and the
        ``anonymized_reviews`` minimum. We mirror those checks here so
        the skill-layout path errors early with the right suggestion,
        then add the skill-source check.
        """
        if context.project_root is None:
            raise CompilerError("project_root is required in context")
        if context.output_folder is None:
            raise CompilerError("output_folder is required in context")

        epic_num = context.resolved_variables.get("epic_num")
        story_num = context.resolved_variables.get("story_num")
        if epic_num is None:
            raise CompilerError(
                "epic_num is required for code-review-synthesis compilation.\n"
                "  Suggestion: Provide epic_num via invocation params"
            )
        if story_num is None:
            raise CompilerError(
                "story_num is required for code-review-synthesis compilation.\n"
                "  Suggestion: Provide story_num via invocation params"
            )

        reviews = context.resolved_variables.get("anonymized_reviews", [])
        if not reviews:
            raise CompilerError(
                "No anonymized reviews provided for synthesis.\n"
                "  Why it's needed: Synthesis requires reviewer outputs to synthesize.\n"
                "  Suggestion: Run code-review workflow first with multiple LLMs"
            )
        if len(reviews) < _MIN_REVIEWERS:
            raise CompilerError(
                f"Synthesis requires at least {_MIN_REVIEWERS} reviews, "
                f"but only {len(reviews)} provided.\n"
                "  Why: Single review doesn't need synthesis - use it directly.\n"
                "  Suggestion: Run code-review workflow with multiple LLM providers"
            )

        skill_md = self._locate_skill_md(context)
        if not skill_md.is_file():  # pragma: no cover — _locate_skill_md raises first
            raise CompilerError(
                f"SKILL.md not found: {skill_md}\n"
                f"  Why it's needed: Source for the bmad-code-review-synthesis skill\n"
                f"  How to fix: Install bmad-assist v0.5.1+ or rely on the bundled "
                f"fallback under src/bmad_assist/skills/bmad-code-review-synthesis/"
            )


__all__ = [
    "SKILL_ID",
    "BmadCodeReviewSynthesisCompiler",
    "apply_llm_transforms",
]
