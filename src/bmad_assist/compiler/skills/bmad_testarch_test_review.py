"""Skill-layout compiler for the ``bmad-testarch-test-review`` workflow.

Phase 7.2 inlines the workhorse compile logic via
:class:`bmad_assist.compiler.skills._testarch_base.TestarchSkillCompilerBase`.
The shared tri-modal pipeline lives on the base class; this module
only encodes the workflow-specific contract: story_num requirement,
``test_dir`` / ``project_path`` variables, dash-separated ``story_id``,
and test-file discovery for context.

bmad-testarch-test-review uses BMAD v6.4+ step-file architecture (no
inline ``<workflow>`` envelope; step files live under ``steps-c/``,
``steps-v/``, and ``steps-e/``). The test-review workflow validates
tests against best practices for maintainability, determinism,
isolation, and flakiness prevention.

The ``apply_llm_transforms`` import is preserved at module top-level so
tests can monkeypatch
``bmad_assist.compiler.skills.bmad_testarch_test_review.apply_llm_transforms``
to stub LLM calls.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

# Re-exported so tests/conftest can patch this module attribute. The
# base class resolves it dynamically per call (see _base.py docstring).
from bmad_assist.compiler.patching import apply_llm_transforms  # noqa: F401
from bmad_assist.compiler.shared_utils import safe_read_file
from bmad_assist.compiler.skills._testarch_base import TestarchSkillCompilerBase
from bmad_assist.compiler.types import CompilerContext, WorkflowIR
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)

# Maximum number of test files to include in context.
_MAX_TEST_FILES = 20


SKILL_ID = "bmad-testarch-test-review"


class BmadTestarchTestReviewCompiler(TestarchSkillCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-testarch-test-review`` skill."""

    skill_id = SKILL_ID
    legacy_workflow_name = "testarch-test-review"
    # Phase 7.2: inlined — no legacy compiler delegation.
    legacy_compiler_class = None

    def get_variables(self) -> dict[str, Any]:
        """Return test-review-specific variables."""
        base_vars = super().get_variables()
        base_vars.update(
            {
                "test_dir": None,
                "project_path": None,
            }
        )
        return base_vars

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the shared TEA contract + story-level requirement."""
        super().validate_context(context)

        story_num = context.resolved_variables.get("story_num")
        if story_num is None:
            raise CompilerError(
                "story_num is required for testarch-test-review compilation.\n"
                "  Suggestion: Provide story_num via invocation params"
            )

    def _get_workflow_specific_variables(
        self,
        resolved: dict[str, Any],
        context: CompilerContext,
        workflow_ir: WorkflowIR,
    ) -> None:
        """Resolve test-review-specific variables.

        Resolves ``test_dir`` / ``project_path`` from workflow.yaml and
        overrides ``story_id`` to use the dash-separated form
        (``epic-story``).
        """
        workflow_vars = workflow_ir.raw_config.get("variables", {})
        test_dir = workflow_vars.get("test_dir", "{project-root}/tests")
        test_dir = test_dir.replace("{project-root}", str(context.project_root))
        resolved["test_dir"] = test_dir
        resolved["project_path"] = str(context.project_root)

        # Override story_id format for test review (uses dash, not dot).
        epic_num = resolved.get("epic_num")
        story_num = resolved.get("story_num")
        if epic_num is not None and story_num is not None:
            resolved["story_id"] = f"{epic_num}-{story_num}"

    def _build_context_files(
        self,
        context: CompilerContext,
        resolved: dict[str, Any],
    ) -> dict[str, str]:
        """Add discovered test files to base context."""
        files = super()._build_context_files(context, resolved)

        epic_num = resolved.get("epic_num")
        story_num = resolved.get("story_num")
        test_files = self._discover_test_files(context, epic_num, story_num)

        for test_file in test_files:
            content = safe_read_file(test_file, context.project_root)
            if content:
                files[str(test_file)] = content

        return files

    def _discover_test_files(
        self,
        context: CompilerContext,
        epic_num: Any,
        story_num: Any,
    ) -> list[Path]:
        """Discover test files relevant to the story.

        Tries ``tests/**/*{epic}-{story}*.py``, then
        ``tests/**/*{epic}_{story}*.py``, then falls back to the 20 most
        recently modified ``tests/**/*.py`` files.
        """
        tests_dir = context.project_root / "tests"
        if not tests_dir.exists():
            logger.warning("Tests directory not found: %s", tests_dir)
            return []

        story_patterns = [
            f"**/*{epic_num}-{story_num}*.py",
            f"**/*{epic_num}_{story_num}*.py",
        ]

        for pattern in story_patterns:
            matches = list(tests_dir.glob(pattern))
            if matches:
                logger.debug("Found %d test files matching pattern %s", len(matches), pattern)
                return sorted(matches)[:_MAX_TEST_FILES]

        all_test_files = list(tests_dir.glob("**/*.py"))
        if not all_test_files:
            logger.warning("No test files found in %s (non-blocking)", tests_dir)
            return []

        all_test_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        selected = all_test_files[:_MAX_TEST_FILES]
        logger.debug("Using fallback: %d most recently modified test files", len(selected))
        return selected


__all__ = ["SKILL_ID", "BmadTestarchTestReviewCompiler", "apply_llm_transforms"]
