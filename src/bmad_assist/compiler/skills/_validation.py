"""Shared validation helpers for skill-layout workflow compilers.

These helpers were extracted from
:mod:`bmad_assist.compiler.skills.bmad_create_story` in Phase 3.1 so
they can be reused by every skill-layout compiler. Both helpers carry
the calling skill's ``skill_id`` only for error-message provenance —
their behaviour is otherwise skill-agnostic.

Public API:
    validate_workflow_xml(content, *, skill_id) -> None
    validate_patch_assertions(content, patch, *, skill_id) -> None
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from bmad_assist.compiler.patching import validate_output
from bmad_assist.compiler.patching.types import WorkflowPatch
from bmad_assist.core.exceptions import CompilerError

_WORKFLOW_RE = re.compile(r"<workflow\b[^>]*>.*?</workflow>", re.DOTALL)


def validate_workflow_xml(content: str, *, skill_id: str) -> None:
    """Extract ``<workflow>...</workflow>`` and assert it parses.

    SKILL.md doesn't carry an ``<instructions-xml>`` envelope, so the
    legacy ``_validate_instructions_xml`` doesn't apply. We instead
    extract the first ``<workflow>`` block and parse it with stdlib
    ``xml.etree.ElementTree``. Compile fails when the block is missing
    or malformed.

    Args:
        content: Compiled workflow body to validate.
        skill_id: Skill identifier (e.g. ``"bmad-create-story"``); used
            only to enrich error messages.

    Raises:
        CompilerError: If the ``<workflow>`` block is missing or
            malformed.

    """
    match = _WORKFLOW_RE.search(content)
    if not match:
        raise CompilerError(
            f"Compiled {skill_id} body does not contain a <workflow>"
            "...</workflow> block.\n"
            "  Why this matters: the runtime compiler relies on the "
            "<workflow> envelope to drive step iteration.\n"
            "  How to fix: ensure SKILL.md contains a single "
            "<workflow>...</workflow> section and that patch "
            "transforms preserve it."
        )

    try:
        ET.fromstring(match.group(0))
    except ET.ParseError as exc:
        raise CompilerError(
            f"Compiled {skill_id} body has malformed <workflow> XML: {exc}.\n"
            "  Why this matters: filter_instructions() and the loop "
            "runtime parse this XML; mismatched tags break the "
            "workflow at runtime.\n"
            "  How to fix: re-run with a master provider that "
            "honours XML well-formedness, or correct the source "
            "SKILL.md if regex post-process produced the breakage."
        ) from exc


def validate_patch_assertions(
    content: str,
    patch: WorkflowPatch,
    *,
    skill_id: str,
) -> None:
    """Apply ``patch.validation`` ``must_contain`` / ``must_not_contain`` rules.

    Mirrors what :func:`compile_patch` does on the legacy path —
    running the rules against the post-processed output gives us the
    same content guarantees regardless of which compiler produced it.

    Args:
        content: Compiled workflow body to validate.
        patch: Loaded patch with optional ``validation`` block.
        skill_id: Skill identifier used to enrich error messages.

    Raises:
        CompilerError: When the patch validation rules report errors.

    """
    if patch.validation is None:
        return
    errors = validate_output(content, patch.validation)
    if errors:
        raise CompilerError(
            f"Compiled {skill_id} failed patch validation: {errors}.\n"
            "  Why this matters: the patch declares content "
            "invariants that the compiled body must satisfy.\n"
            "  How to fix: configure a master provider so LLM "
            "transforms can run, or update the patch's "
            "must_contain / must_not_contain rules to match the "
            "regex-only output."
        )


__all__ = ["validate_patch_assertions", "validate_workflow_xml"]
