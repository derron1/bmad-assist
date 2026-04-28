"""Skill-layout compiler for the ``bmad-testarch-nfr`` workflow.

Phase 7.2 inlines the workhorse compile logic via
:class:`bmad_assist.compiler.skills._testarch_base.TestarchSkillCompilerBase`.
The shared tri-modal pipeline lives on the base class; this module
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

# Re-exported so tests/conftest can patch this module attribute. The
# base class resolves it dynamically per call (see _base.py docstring).
from bmad_assist.compiler.patching import apply_llm_transforms  # noqa: F401
from bmad_assist.compiler.skills._testarch_base import TestarchSkillCompilerBase

logger = logging.getLogger(__name__)


SKILL_ID = "bmad-testarch-nfr"


class BmadTestarchNfrCompiler(TestarchSkillCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-testarch-nfr`` skill.

    Inlined from the legacy ``TestarchNfrAssessCompiler`` (un-prefixed
    name ``testarch-nfr-assess``); see module docstring for the rename
    rationale.
    """

    skill_id = SKILL_ID
    # Drives patch lookup (testarch-nfr-assess.patch.yaml) and the
    # routing alias on _SKILL_LAYOUT_COMPILERS. Do NOT change to
    # "testarch-nfr" without also renaming the patch file.
    legacy_workflow_name = "testarch-nfr-assess"
    # Phase 7.2: inlined — no legacy compiler delegation.
    legacy_compiler_class = None


__all__ = ["SKILL_ID", "BmadTestarchNfrCompiler", "apply_llm_transforms"]
