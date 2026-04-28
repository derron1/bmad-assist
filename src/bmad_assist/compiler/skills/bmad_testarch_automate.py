"""Skill-layout compiler for the ``bmad-testarch-automate`` workflow.

Phase 7.2 inlines the workhorse compile logic via
:class:`bmad_assist.compiler.skills._testarch_base.TestarchSkillCompilerBase`.
The shared tri-modal pipeline (find/parse/resolve/substitute/transform/
validate/cache + the inlined compile body) lives on the base class;
this module only encodes the workflow-specific contract.

bmad-testarch-automate uses BMAD v6.4+ step-file architecture (no
inline ``<workflow>`` envelope; step files live under ``steps-c/``,
``steps-v/``, and ``steps-e/``). The automate workflow expands test
automation coverage after implementation or analyzes existing
codebases to generate comprehensive test suites.

The ``apply_llm_transforms`` import is preserved at module top-level so
tests can monkeypatch
``bmad_assist.compiler.skills.bmad_testarch_automate.apply_llm_transforms``
to stub LLM calls.
"""

from __future__ import annotations

import logging

# Re-exported so tests/conftest can patch this module attribute. The
# base class resolves it dynamically per call (see _base.py docstring).
from bmad_assist.compiler.patching import apply_llm_transforms  # noqa: F401
from bmad_assist.compiler.skills._testarch_base import TestarchSkillCompilerBase

logger = logging.getLogger(__name__)


SKILL_ID = "bmad-testarch-automate"


class BmadTestarchAutomateCompiler(TestarchSkillCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-testarch-automate`` skill."""

    skill_id = SKILL_ID
    legacy_workflow_name = "testarch-automate"
    # Phase 7.2: inlined — no legacy compiler delegation.
    legacy_compiler_class = None


__all__ = ["SKILL_ID", "BmadTestarchAutomateCompiler", "apply_llm_transforms"]
