"""Skill-layout compiler for the ``bmad-create-story`` workflow.

Phase 3.1 reduced this module to its workflow-specific parts; the
shared compile pipeline (find/parse/resolve/substitute/transform/
validate/cache/delegate-to-legacy) lives on
:class:`bmad_assist.compiler.skills._base.SkillLayoutCompilerBase`.

For background on the routing and cache behaviour, see the base
class. The ``apply_llm_transforms`` import is preserved at module
top-level so existing tests can monkeypatch
``bmad_assist.compiler.skills.bmad_create_story.apply_llm_transforms``
to stub LLM calls.
"""

from __future__ import annotations

import logging
from typing import Any

# Re-exported so tests/conftest can patch this module attribute. The
# base class resolves it dynamically per call (see _base.py docstring).
from bmad_assist.compiler.patching import apply_llm_transforms  # noqa: F401
from bmad_assist.compiler.shared_utils import find_project_context_file
from bmad_assist.compiler.skills._base import SkillLayoutCompilerBase
from bmad_assist.compiler.types import CompilerContext
from bmad_assist.compiler.workflows.create_story import CreateStoryCompiler
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)


# Stable skill id used by all references (config, file paths, dispatch
# keys). Phase 2 keeps the ``bmad-`` prefix for parity with upstream
# BMAD's manifest.
SKILL_ID = "bmad-create-story"


class BmadCreateStoryCompiler(SkillLayoutCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-create-story`` skill."""

    skill_id = SKILL_ID
    legacy_workflow_name = "create-story"
    legacy_compiler_class = CreateStoryCompiler

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """No workflow-specific extras for create-story.

        The variable engine downstream resolves ``{epic_num}`` /
        ``{story_num}`` / etc. against the legacy compiler's variable
        surface, so we don't need to inject anything at SKILL.md
        substitution time.
        """
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the legacy compiler's contract + skill-source check.

        Skill-layout adds two requirements on top of the legacy checks:

        * SKILL.md must be locatable (via the project install or the
          bundled fallback).
        * ``project_context.md`` must exist — the prompt's quality
          depends on it being embedded.
        """
        if context.project_root is None:
            raise CompilerError("project_root is required in context")
        if context.output_folder is None:
            raise CompilerError("output_folder is required in context")

        epic_num = context.resolved_variables.get("epic_num")
        story_num = context.resolved_variables.get("story_num")
        if epic_num is None:
            raise CompilerError(
                "epic_num is required for create-story compilation.\n"
                "  Suggestion: Provide epic_num via invocation params or ensure "
                "sprint-status.yaml has a backlog story"
            )
        if story_num is None:
            raise CompilerError(
                "story_num is required for create-story compilation.\n"
                "  Suggestion: Provide story_num via invocation params or ensure "
                "sprint-status.yaml has a backlog story"
            )

        skill_md = self._locate_skill_md(context)
        if not skill_md.is_file():  # pragma: no cover — _locate_skill_md raises first
            raise CompilerError(
                f"SKILL.md not found: {skill_md}\n"
                f"  Why it's needed: Source for the bmad-create-story skill\n"
                f"  How to fix: Install BMAD v6.4+ in the project, or rely on "
                f"the bmad-assist bundled fallback"
            )

        project_context_path = find_project_context_file(context)
        if project_context_path is None:
            raise CompilerError(
                f"project_context.md not found: {context.output_folder / 'project_context.md'}\n"
                f"  Why it's needed: Contains critical implementation rules for AI agents\n"
                f"  How to fix: Run 'generate-project-context' workflow"
            )


__all__ = ["SKILL_ID", "BmadCreateStoryCompiler", "apply_llm_transforms"]
