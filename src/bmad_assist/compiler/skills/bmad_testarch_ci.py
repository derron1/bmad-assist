"""Skill-layout compiler for the ``bmad-testarch-ci`` workflow.

Phase 7.2 inlines the workhorse compile logic via
:class:`bmad_assist.compiler.skills._testarch_base.TestarchSkillCompilerBase`.
The shared tri-modal pipeline lives on the base class; this module
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

# Re-exported so tests/conftest can patch this module attribute. The
# base class resolves it dynamically per call (see _base.py docstring).
from bmad_assist.compiler.patching import apply_llm_transforms  # noqa: F401
from bmad_assist.compiler.skills._testarch_base import TestarchSkillCompilerBase

logger = logging.getLogger(__name__)


SKILL_ID = "bmad-testarch-ci"


class BmadTestarchCiCompiler(TestarchSkillCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-testarch-ci`` skill."""

    skill_id = SKILL_ID
    legacy_workflow_name = "testarch-ci"
    # Phase 7.2: inlined — no legacy compiler delegation.
    legacy_compiler_class = None


__all__ = ["SKILL_ID", "BmadTestarchCiCompiler", "apply_llm_transforms"]
