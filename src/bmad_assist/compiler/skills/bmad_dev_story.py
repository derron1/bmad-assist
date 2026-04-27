"""Skill-layout compiler for the ``bmad-dev-story`` workflow.

Phase 3.1 second consumer of
:class:`bmad_assist.compiler.skills._base.SkillLayoutCompilerBase`.
Same shape as :class:`bmad_assist.compiler.skills.bmad_create_story`:
the base class handles the compile pipeline (find/parse/resolve/
substitute/transform/validate/cache/delegate-to-legacy); this module
only encodes the workflow-specific contract.

The ``apply_llm_transforms`` import is preserved at module top-level
so tests can monkeypatch
``bmad_assist.compiler.skills.bmad_dev_story.apply_llm_transforms``
to stub LLM calls — the same convention used by ``bmad_create_story``.
"""

from __future__ import annotations

import logging
from typing import Any

# Re-exported so tests/conftest can patch this module attribute. The
# base class resolves it dynamically per call (see _base.py docstring).
from bmad_assist.compiler.patching import apply_llm_transforms  # noqa: F401
from bmad_assist.compiler.shared_utils import resolve_story_file
from bmad_assist.compiler.skills._base import SkillLayoutCompilerBase
from bmad_assist.compiler.types import CompilerContext
from bmad_assist.compiler.workflows.dev_story import DevStoryCompiler
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)


# Stable skill id used by all references (config, file paths, dispatch
# keys). Mirrors ``bmad-create-story`` for consistency.
SKILL_ID = "bmad-dev-story"


class BmadDevStoryCompiler(SkillLayoutCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-dev-story`` skill."""

    skill_id = SKILL_ID
    legacy_workflow_name = "dev-story"
    legacy_compiler_class = DevStoryCompiler

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """No workflow-specific extras for dev-story.

        The legacy compiler resolves ``{epic_num}`` / ``{story_num}`` /
        ``{story_file}`` / etc. downstream. The dev-story SKILL.md
        references many runtime tokens (``{communication_language}``,
        ``{user_skill_level}``, ``{installed_path}``,
        ``{project_context}``, ...) but the patch either rewrites them
        (``{installed_path}`` → ``embedded context``) or leaves them
        for the runtime master agent to resolve from the embedded
        context section. None of them require compile-time injection
        from the skill compiler.
        """
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the legacy compiler's contract + skill-source check.

        The legacy ``DevStoryCompiler.validate_context`` already checks
        for ``epic_num``/``story_num``, the story file's existence, and
        the workflow directory's existence. We add the skill-source
        check on top so a missing SKILL.md fails early with a
        skill-layout-specific message.
        """
        if context.project_root is None:
            raise CompilerError("project_root is required in context")
        if context.output_folder is None:
            raise CompilerError("output_folder is required in context")

        epic_num = context.resolved_variables.get("epic_num")
        story_num = context.resolved_variables.get("story_num")
        if epic_num is None:
            raise CompilerError(
                "epic_num is required for dev-story compilation.\n"
                "  Suggestion: Provide epic_num via invocation params or ensure "
                "sprint-status.yaml has a ready-for-dev story"
            )
        if story_num is None:
            raise CompilerError(
                "story_num is required for dev-story compilation.\n"
                "  Suggestion: Provide story_num via invocation params or ensure "
                "sprint-status.yaml has a ready-for-dev story"
            )

        skill_md = self._locate_skill_md(context)
        if not skill_md.is_file():  # pragma: no cover — _locate_skill_md raises first
            raise CompilerError(
                f"SKILL.md not found: {skill_md}\n"
                f"  Why it's needed: Source for the bmad-dev-story skill\n"
                f"  How to fix: Install BMAD v6.4+ in the project, or rely on "
                f"the bmad-assist bundled fallback"
            )

        story_path, _, _ = resolve_story_file(context, epic_num, story_num)
        if story_path is None:
            raise CompilerError(
                f"Story file not found for {epic_num}-{story_num}-*.md\n"
                f"  Expected pattern: docs/sprint-artifacts/{epic_num}-{story_num}-*.md\n"
                f"  Suggestion: Run 'create-story' workflow first to create the story"
            )


__all__ = ["SKILL_ID", "BmadDevStoryCompiler", "apply_llm_transforms"]
