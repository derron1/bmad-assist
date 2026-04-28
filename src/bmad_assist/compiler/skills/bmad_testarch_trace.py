"""Skill-layout compiler for the ``bmad-testarch-trace`` workflow.

Phase 7.2 inlines the workhorse compile logic via
:class:`bmad_assist.compiler.skills._testarch_base.TestarchSkillCompilerBase`.
The shared tri-modal pipeline lives on the base class; this module
encodes the trace-specific contract: ``test_dir`` / ``source_dir`` /
``gate_type`` variables, custom context (project_context + epic file
+ all stories in epic), and a custom mission that surfaces the gate
type.

bmad-testarch-trace uses BMAD v6.4+ step-file architecture (no inline
``<workflow>`` envelope; step files live under ``steps-c/``,
``steps-v/``, and ``steps-e/``). The trace workflow generates a
requirements-to-tests traceability matrix and makes a quality gate
decision (PASS/CONCERNS/FAIL/WAIVED) on epic completion.

The ``apply_llm_transforms`` import is preserved at module top-level so
tests can monkeypatch
``bmad_assist.compiler.skills.bmad_testarch_trace.apply_llm_transforms``
to stub LLM calls.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

# Re-exported so tests/conftest can patch this module attribute. The
# base class resolves it dynamically per call (see _base.py docstring).
from bmad_assist.compiler.patching import apply_llm_transforms  # noqa: F401
from bmad_assist.compiler.shared_utils import (
    find_project_context_file,
    get_epics_dir,
    get_stories_dir,
    safe_read_file,
)
from bmad_assist.compiler.skills._testarch_base import TestarchSkillCompilerBase
from bmad_assist.compiler.types import CompilerContext, WorkflowIR

logger = logging.getLogger(__name__)


SKILL_ID = "bmad-testarch-trace"


class BmadTestarchTraceCompiler(TestarchSkillCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-testarch-trace`` skill."""

    skill_id = SKILL_ID
    legacy_workflow_name = "testarch-trace"
    # Phase 7.2: inlined — no legacy compiler delegation.
    legacy_compiler_class = None

    def get_required_files(self) -> list[str]:
        """Trace also needs epic files for context."""
        return [
            "**/project_context.md",
            "**/project-context.md",
            "**/epic*.md",
        ]

    def get_variables(self) -> dict[str, Any]:
        """Return trace-specific variables."""
        base_vars = super().get_variables()
        base_vars.update(
            {
                "test_dir": None,
                "source_dir": None,
                "gate_type": None,
            }
        )
        return base_vars

    def _get_workflow_specific_variables(
        self,
        resolved: dict[str, Any],
        context: CompilerContext,
        workflow_ir: WorkflowIR,
    ) -> None:
        """Resolve trace-specific variables.

        Reads ``test_dir`` / ``source_dir`` / ``gate_type`` from
        ``workflow.yaml`` variables (with sensible defaults).
        """
        workflow_vars = workflow_ir.raw_config.get("variables", {})

        test_dir = workflow_vars.get("test_dir", "{project-root}/tests")
        test_dir = test_dir.replace("{project-root}", str(context.project_root))
        resolved["test_dir"] = test_dir

        source_dir = workflow_vars.get("source_dir", "{project-root}/src")
        source_dir = source_dir.replace("{project-root}", str(context.project_root))
        resolved["source_dir"] = source_dir

        gate_type = workflow_vars.get("gate_type", "epic")
        resolved["gate_type"] = gate_type

    def _build_context_files(
        self,
        context: CompilerContext,
        resolved: dict[str, Any],
    ) -> dict[str, str]:
        """Build trace-specific context files.

        Departs from the default tri-modal context: trace embeds the
        epic file and ALL stories in the epic for complete
        traceability, not just the (optional) story file.
        """
        files: dict[str, str] = {}
        project_root = context.project_root

        # 1. Project context (general).
        project_context_path = find_project_context_file(context)
        if project_context_path:
            content = safe_read_file(project_context_path, project_root)
            if content:
                files[str(project_context_path)] = content

        # 2. Epic file (overview).
        epic_num = resolved.get("epic_num")
        if epic_num:
            epic_path = self._find_epic_file(context, epic_num)
            if epic_path:
                content = safe_read_file(epic_path, project_root)
                if content:
                    files[str(epic_path)] = content

            # 3. All stories in epic for complete traceability.
            stories_dir = get_stories_dir(context)
            if stories_dir.exists():
                pattern = f"{epic_num}-*-*.md"
                story_files = sorted(stories_dir.glob(pattern))
                for story_path in story_files:
                    content = safe_read_file(story_path, project_root)
                    if content:
                        files[str(story_path)] = content
                if story_files:
                    logger.debug(
                        "Loaded %d story files for epic %s",
                        len(story_files),
                        epic_num,
                    )

        return files

    def _find_epic_file(
        self,
        context: CompilerContext,
        epic_num: Any,
    ) -> Path | None:
        """Find epic file by epic number."""
        epics_dir = get_epics_dir(context)
        if not epics_dir.exists():
            return None

        pattern = f"epic-{epic_num}*.md"
        matches = sorted(epics_dir.glob(pattern))

        if not matches:
            logger.debug("No epic file found matching %s in %s", pattern, epics_dir)
            return None

        return matches[0]

    def _build_mission(
        self,
        workflow_ir: WorkflowIR,
        resolved: dict[str, Any],
    ) -> str:
        """Build trace-specific mission (surfaces gate type)."""
        base_description = workflow_ir.raw_config.get(
            "description", "Generate requirements-to-tests traceability matrix"
        )

        mode = resolved.get("workflow_mode", "c")
        mode_name = {"c": "Create", "v": "Validate", "e": "Edit"}.get(mode, mode)

        epic_num = resolved.get("epic_num", "?")
        gate_type = resolved.get("gate_type", "epic")

        mission = (
            f"{base_description}\n\n"
            f"Mode: {mode_name}\n"
            f"Target: Epic {epic_num}\n"
            f"Gate Type: {gate_type}\n"
            f"Analyze test coverage and make quality gate decision."
        )

        return mission


__all__ = ["SKILL_ID", "BmadTestarchTraceCompiler", "apply_llm_transforms"]
