"""Skill-layout compiler for the ``bmad-security-review`` workflow.

Phase 6-prep consumer of
:class:`bmad_assist.compiler.skills._base.SkillLayoutCompilerBase` for
the bmad-assist-authored security review (CWE-based vulnerability
scanner). No upstream BMAD equivalent exists — the SKILL.md was
authored outcome-based directly by bmad-assist.

Notable nuances:

* No patch file exists. The base class's no-patch path (Phase 3.5)
  handles that gracefully — the substituted SKILL.md body is the
  final body (no LLM transforms, no regex post-process, no
  ``must_contain`` assertions to enforce).
* The CWE pattern catalogue, modified source files, and git diff are
  embedded as virtual context files by the LEGACY compiler's
  :meth:`SecurityReviewCompiler.compile`. We delegate to it via the
  standard base-class pipeline; nothing about pattern loading or the
  diff-capture mechanic changes here.
* Pattern files are **bundled twice** during the migration window:
  byte-identical under both
  ``src/bmad_assist/workflows/security-review/patterns/`` (legacy
  loader path — :func:`bmad_assist.security.patterns.get_pattern_dir`
  still reads from there) and
  ``src/bmad_assist/skills/bmad-security-review/patterns/`` (new
  bundled skill source). Phase 6 collapses to the new location once
  the legacy tree is removed.

The ``apply_llm_transforms`` import is preserved at module top-level
so tests can monkeypatch
``bmad_assist.compiler.skills.bmad_security_review.apply_llm_transforms``
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
from bmad_assist.compiler.workflows.security_review import SecurityReviewCompiler
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)


# Stable skill id used by all references (config, file paths, dispatch keys).
SKILL_ID = "bmad-security-review"


class BmadSecurityReviewCompiler(SkillLayoutCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-security-review`` skill."""

    skill_id = SKILL_ID
    legacy_workflow_name = "security-review"
    legacy_compiler_class = SecurityReviewCompiler

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """No SKILL.md-level extras for security-review.

        The legacy compiler resolves ``detected_languages`` /
        ``security_patterns`` / ``patterns_loaded_count`` /
        ``detected_languages_list`` downstream from the diff capture
        and tech-stack detection it runs in :meth:`compile`. The
        SKILL.md references runtime tokens
        (``{communication_language}``, ``{document_output_language}``,
        ``{user_name}``, ``{project_name}``) but those are either
        resolved by the legacy variable engine or filled by the runtime
        master agent from the embedded context section. None require
        compile-time injection from the skill compiler.

        We deliberately do NOT inject the CWE patterns or the diff
        here: they are sized against the model's context budget by the
        legacy compiler and embedded as virtual ``security-patterns``
        / ``[git-diff]`` files in the CONTEXT section. Substituting
        them into SKILL.md would defeat that budgeting and balloon the
        compiled output.
        """
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the legacy compiler's contract + skill-source check.

        The legacy ``SecurityReviewCompiler.validate_context`` only
        requires a valid ``project_root`` — security review has no
        per-story arguments. We mirror that requirement here and add
        the skill-source check on top so a missing SKILL.md fails
        early with a skill-layout-specific message.
        """
        if context.project_root is None or not context.project_root.is_dir():
            raise CompilerError(
                "project_root must be a valid directory for security review.\n"
                "  Suggestion: pass a real project directory via CompilerContext"
            )

        skill_md = self._locate_skill_md(context)
        if not skill_md.is_file():  # pragma: no cover — _locate_skill_md raises first
            raise CompilerError(
                f"SKILL.md not found: {skill_md}\n"
                f"  Why it's needed: Source for the bmad-security-review skill\n"
                f"  How to fix: Install bmad-assist v0.5.1+ or rely on the bundled "
                f"fallback under src/bmad_assist/skills/bmad-security-review/"
            )


__all__ = [
    "SKILL_ID",
    "BmadSecurityReviewCompiler",
    "apply_llm_transforms",
]
