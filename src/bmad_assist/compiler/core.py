"""Core compiler module with WorkflowCompiler protocol and dynamic loading.

This module provides:
- WorkflowCompiler: Protocol defining the interface for workflow-specific compilers
- get_workflow_compiler: Dynamic loader for workflow compiler modules
- compile_workflow: High-level function to compile a workflow by name

Phase 6: the legacy routing path (``workflow_name → bmad_assist.compiler.workflows``)
was removed. Every supported workflow now routes through a v6.4+
skill-layout compiler under :mod:`bmad_assist.compiler.skills`.
:data:`WORKFLOW_REGISTRY` is the sole dispatch table — it maps every
accepted name (legacy alias *and* canonical ``bmad-`` prefixed id) to
the canonical skill id used to instantiate the compiler.
"""

import logging
import re
import warnings
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext
from bmad_assist.core.exceptions import CompilerError

# Per-process dedup set for the ``--skill-layout`` deprecation warning.
# We emit at most one warning per process so chatty loops don't spam.
_SKILL_LAYOUT_FLAG_WARNING_EMITTED: bool = False


def _emit_skill_layout_flag_deprecation() -> None:
    """Warn (once per process) that the ``--skill-layout`` flag is deprecated."""
    global _SKILL_LAYOUT_FLAG_WARNING_EMITTED
    if _SKILL_LAYOUT_FLAG_WARNING_EMITTED:
        return
    _SKILL_LAYOUT_FLAG_WARNING_EMITTED = True
    warnings.warn(
        "--skill-layout / config.skill_layout is a no-op since Phase 6 "
        "(legacy layout removed). All workflows route through the v6.4+ "
        "skill-layout compilers. The flag will be removed in the next "
        "major release.",
        DeprecationWarning,
        stacklevel=2,
    )


# TODO: drop legacy aliases in next major. Phase 6 keeps the un-prefixed
# names (``"create-story"``, ``"testarch-atdd"``, etc.) as aliases for
# one more release so existing CLI invocations and configs keep working.
WORKFLOW_REGISTRY: dict[str, str] = {
    # Maps any accepted name (legacy alias or canonical bmad-prefixed
    # id) to the canonical bmad-prefixed skill id. Single source of
    # truth for skill-layout dispatch + discovery + deprecation hints.
    # Entries grouped by canonical skill id, alphabetical.
    "code-review": "bmad-code-review",
    "bmad-code-review": "bmad-code-review",
    "code-review-synthesis": "bmad-code-review-synthesis",
    "bmad-code-review-synthesis": "bmad-code-review-synthesis",
    "create-story": "bmad-create-story",
    "bmad-create-story": "bmad-create-story",
    "dev-story": "bmad-dev-story",
    "bmad-dev-story": "bmad-dev-story",
    "qa-plan-execute": "bmad-qa-plan-execute",
    "bmad-qa-plan-execute": "bmad-qa-plan-execute",
    "qa-plan-generate": "bmad-qa-plan-generate",
    "bmad-qa-plan-generate": "bmad-qa-plan-generate",
    "retrospective": "bmad-retrospective",
    "bmad-retrospective": "bmad-retrospective",
    "security-review": "bmad-security-review",
    "bmad-security-review": "bmad-security-review",
    "testarch-atdd": "bmad-testarch-atdd",
    "bmad-testarch-atdd": "bmad-testarch-atdd",
    "testarch-automate": "bmad-testarch-automate",
    "bmad-testarch-automate": "bmad-testarch-automate",
    "testarch-ci": "bmad-testarch-ci",
    "bmad-testarch-ci": "bmad-testarch-ci",
    "testarch-framework": "bmad-testarch-framework",
    "bmad-testarch-framework": "bmad-testarch-framework",
    # Phase 3.2-B rename: legacy ``testarch-nfr-assess`` collapses into
    # the canonical ``bmad-testarch-nfr`` skill id.
    "testarch-nfr-assess": "bmad-testarch-nfr",
    "bmad-testarch-nfr": "bmad-testarch-nfr",
    "testarch-test-design": "bmad-testarch-test-design",
    "bmad-testarch-test-design": "bmad-testarch-test-design",
    "testarch-test-review": "bmad-testarch-test-review",
    "bmad-testarch-test-review": "bmad-testarch-test-review",
    "testarch-trace": "bmad-testarch-trace",
    "bmad-testarch-trace": "bmad-testarch-trace",
    "validate-story": "bmad-validate-story",
    "bmad-validate-story": "bmad-validate-story",
    "validate-story-synthesis": "bmad-validate-story-synthesis",
    "bmad-validate-story-synthesis": "bmad-validate-story-synthesis",
}


def _build_skill_layout_compiler(skill_id: str) -> "WorkflowCompiler":
    """Instantiate the skill-layout compiler for ``skill_id``.

    Imports lazily to avoid pulling skill-layout deps into the import
    graph for callers that never invoke a particular workflow.
    """
    if skill_id == "bmad-code-review":
        from bmad_assist.compiler.skills.bmad_code_review import (
            BmadCodeReviewCompiler,
        )

        return BmadCodeReviewCompiler()
    if skill_id == "bmad-code-review-synthesis":
        from bmad_assist.compiler.skills.bmad_code_review_synthesis import (
            BmadCodeReviewSynthesisCompiler,
        )

        return BmadCodeReviewSynthesisCompiler()
    if skill_id == "bmad-create-story":
        from bmad_assist.compiler.skills.bmad_create_story import (
            BmadCreateStoryCompiler,
        )

        return BmadCreateStoryCompiler()
    if skill_id == "bmad-dev-story":
        from bmad_assist.compiler.skills.bmad_dev_story import (
            BmadDevStoryCompiler,
        )

        return BmadDevStoryCompiler()
    if skill_id == "bmad-qa-plan-execute":
        from bmad_assist.compiler.skills.bmad_qa_plan_execute import (
            BmadQaPlanExecuteCompiler,
        )

        return BmadQaPlanExecuteCompiler()
    if skill_id == "bmad-qa-plan-generate":
        from bmad_assist.compiler.skills.bmad_qa_plan_generate import (
            BmadQaPlanGenerateCompiler,
        )

        return BmadQaPlanGenerateCompiler()
    if skill_id == "bmad-retrospective":
        from bmad_assist.compiler.skills.bmad_retrospective import (
            BmadRetrospectiveCompiler,
        )

        return BmadRetrospectiveCompiler()
    if skill_id == "bmad-security-review":
        from bmad_assist.compiler.skills.bmad_security_review import (
            BmadSecurityReviewCompiler,
        )

        return BmadSecurityReviewCompiler()
    if skill_id == "bmad-testarch-atdd":
        from bmad_assist.compiler.skills.bmad_testarch_atdd import (
            BmadTestarchAtddCompiler,
        )

        return BmadTestarchAtddCompiler()
    if skill_id == "bmad-testarch-automate":
        from bmad_assist.compiler.skills.bmad_testarch_automate import (
            BmadTestarchAutomateCompiler,
        )

        return BmadTestarchAutomateCompiler()
    if skill_id == "bmad-testarch-ci":
        from bmad_assist.compiler.skills.bmad_testarch_ci import (
            BmadTestarchCiCompiler,
        )

        return BmadTestarchCiCompiler()
    if skill_id == "bmad-testarch-framework":
        from bmad_assist.compiler.skills.bmad_testarch_framework import (
            BmadTestarchFrameworkCompiler,
        )

        return BmadTestarchFrameworkCompiler()
    if skill_id == "bmad-testarch-nfr":
        from bmad_assist.compiler.skills.bmad_testarch_nfr import (
            BmadTestarchNfrCompiler,
        )

        return BmadTestarchNfrCompiler()
    if skill_id == "bmad-testarch-test-design":
        from bmad_assist.compiler.skills.bmad_testarch_test_design import (
            BmadTestarchTestDesignCompiler,
        )

        return BmadTestarchTestDesignCompiler()
    if skill_id == "bmad-testarch-test-review":
        from bmad_assist.compiler.skills.bmad_testarch_test_review import (
            BmadTestarchTestReviewCompiler,
        )

        return BmadTestarchTestReviewCompiler()
    if skill_id == "bmad-testarch-trace":
        from bmad_assist.compiler.skills.bmad_testarch_trace import (
            BmadTestarchTraceCompiler,
        )

        return BmadTestarchTraceCompiler()
    if skill_id == "bmad-validate-story":
        from bmad_assist.compiler.skills.bmad_validate_story import (
            BmadValidateStoryCompiler,
        )

        return BmadValidateStoryCompiler()
    if skill_id == "bmad-validate-story-synthesis":
        from bmad_assist.compiler.skills.bmad_validate_story_synthesis import (
            BmadValidateStorySynthesisCompiler,
        )

        return BmadValidateStorySynthesisCompiler()
    raise CompilerError(
        f"No skill-layout compiler registered for '{skill_id}'.\n"
        f"  Suggestion: register the compiler in `WORKFLOW_REGISTRY` "
        f"and `_build_skill_layout_compiler` in compiler/core.py."
    )


# ``SkillLayoutMode`` retained for backwards-compatible signatures.
# Phase 6 makes the value a no-op-with-warning; all routing goes through
# the skill-layout compilers. Will be deleted in the next major release.
SkillLayoutMode = Literal["auto", "new", "old"]

logger = logging.getLogger(__name__)


@runtime_checkable
class WorkflowCompiler(Protocol):
    """Protocol for workflow-specific compilers.

    Each workflow (create-story, validate-story, etc.) implements this
    protocol to provide workflow-specific compilation logic.

    Attributes:
        workflow_name: Unique workflow identifier (e.g., 'create-story').

    """

    @property
    def workflow_name(self) -> str:
        """Unique workflow identifier (e.g., 'create-story')."""
        ...

    def get_workflow_dir(self, context: CompilerContext) -> Path:
        """Return the workflow directory for this compiler.

        The workflow directory contains workflow.yaml and instructions.xml.
        This is typically a path relative to project_root like:
        .bmad/bmm/workflows/4-implementation/create-story

        Args:
            context: The compilation context with project paths.

        Returns:
            Path to the workflow directory.

        """
        ...

    def get_required_files(self) -> list[str]:
        """Return list of required file glob patterns.

        Returns:
            List of glob patterns for files this workflow needs.
            Example: ['**/epics*.md', '**/prd*.md']

        """
        ...

    def get_variables(self) -> dict[str, Any]:
        """Return workflow-specific variables to resolve.

        Returns:
            Dictionary of variable names to their values or resolution hints.
            Values can be str, Path, int, or other types as needed.

        """
        ...

    def validate_context(self, context: CompilerContext) -> None:
        """Validate context before compilation.

        Args:
            context: The compilation context to validate.

        Raises:
            CompilerError: If context is invalid or missing required data.

        """
        ...

    def compile(self, context: CompilerContext) -> CompiledWorkflow:
        """Compile workflow with given context.

        Args:
            context: The compilation context with resolved variables and files.
                context.workflow_ir: Pre-loaded WorkflowIR (from cache or original).
                context.patch_path: Path to patch file (for post_process).

        Returns:
            CompiledWorkflow: The compiled workflow ready for output.

        """
        ...


# Regex for valid workflow names: lowercase letters, digits, hyphens, underscores
# Must start with a letter, no dots allowed (prevents import path injection)
_WORKFLOW_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


def get_workflow_compiler(
    workflow_name: str,
    *,
    skill_layout: SkillLayoutMode = "auto",
    project_root: Path | None = None,
) -> WorkflowCompiler:
    """Load workflow compiler by name.

    Phase 6: every accepted name in :data:`WORKFLOW_REGISTRY` resolves
    to a v6.4+ skill-layout compiler. The legacy
    ``bmad_assist.compiler.workflows`` modules remain as private
    delegation targets only.

    Args:
        workflow_name: Workflow identifier (e.g., 'create-story').
        skill_layout: Accepted for backwards compatibility but a no-op
            since Phase 6. Passing any value emits a
            :class:`DeprecationWarning` (once per process).
        project_root: Accepted for backwards compatibility. Phase 6
            does not consume the value (routing is layout-independent).

    Returns:
        WorkflowCompiler instance for the workflow.

    Raises:
        CompilerError: If workflow name invalid or no compiler is
            registered for it.

    """
    del project_root  # No longer consulted; kept for signature compat.

    if not workflow_name or not workflow_name.strip():
        raise CompilerError(
            "Workflow name cannot be empty\n"
            "  Why it's needed: Workflow name is required to load the compiler module\n"
            "  How to fix: Provide a valid workflow name (e.g., 'create-story')"
        )

    # Phase 6: ``skill_layout`` is no longer functional. Emit a
    # DeprecationWarning the first time a non-default value is passed,
    # then continue routing through the skill-layout compiler regardless.
    if skill_layout != "auto":
        _emit_skill_layout_flag_deprecation()

    normalized_name = workflow_name.strip()

    if not _WORKFLOW_NAME_PATTERN.fullmatch(normalized_name):
        raise CompilerError(
            f"Invalid workflow name: '{workflow_name}'\n"
            f"  Why it's needed: Valid Python identifiers needed for module loading\n"
            f"  How to fix: Use lowercase letters, digits, hyphens, underscores only"
        )

    # Normalize underscores → hyphens so legacy module-style names
    # (``create_story``) and BMAD canonical hyphen names
    # (``create-story``) both resolve through the registry.
    lookup_name = normalized_name.replace("_", "-")
    skill_id = WORKFLOW_REGISTRY.get(lookup_name)
    if skill_id is None:
        raise CompilerError(
            f"Workflow not found: '{normalized_name}'\n"
            f"  Suggestion: register the workflow in `WORKFLOW_REGISTRY` "
            f"in compiler/core.py, or use one of the supported names "
            f"({', '.join(sorted(set(WORKFLOW_REGISTRY)))})."
        )

    logger.debug("Routing '%s' through skill-layout compiler '%s'", normalized_name, skill_id)
    return _build_skill_layout_compiler(skill_id)


# Pattern to detect interactive <ask> elements in workflow instructions
_ASK_PATTERN = re.compile(r"<ask[\s>]", re.IGNORECASE)


def _check_interactive_elements(
    workflow_name: str,
    raw_instructions: str | None,
    patch_path: Path | None,
) -> None:
    """Check for interactive elements in workflow without patch.

    Workflows with <ask> elements require user input and will hang in
    non-interactive mode (subprocess/automation). This logs a CRITICAL
    warning if such elements are found and no patch was applied.

    Args:
        workflow_name: Name of the workflow being compiled.
        raw_instructions: Raw XML instructions from workflow.
        patch_path: Path to applied patch, or None if no patch.

    """
    if patch_path is not None:
        return  # Patch applied, assume it handles interactive elements

    if not raw_instructions:
        return  # No instructions to check

    if _ASK_PATTERN.search(raw_instructions):
        logger.critical(
            "Workflow '%s' contains <ask> elements but no patch was applied. "
            "Interactive prompts will hang in non-interactive mode (subprocess/automation). "
            "Either: (1) add a patch to remove <ask> tags, or (2) remove them from workflow.",
            workflow_name,
        )


def compile_workflow(
    workflow_name: str,
    context: CompilerContext,
    *,
    skill_layout: SkillLayoutMode = "auto",
) -> CompiledWorkflow:
    """Compile a workflow by name with given context.

    Phase 6: all workflows route through the v6.4+ skill-layout
    compilers. The skill-layout compiler manages its own IR / cache
    lifecycle, so :func:`load_workflow_ir` is no longer invoked from
    this function.

    Args:
        workflow_name: Workflow identifier (e.g., 'create-story').
        context: The compilation context with project paths.
        skill_layout: Accepted for backwards compatibility but a no-op
            since Phase 6. Passing any non-default value emits a
            :class:`DeprecationWarning` (once per process).

    Returns:
        CompiledWorkflow: The compiled workflow ready for output.

    Raises:
        CompilerError: If workflow invalid or compilation fails.

    """
    compiler = get_workflow_compiler(
        workflow_name,
        skill_layout=skill_layout,
        project_root=context.project_root,
    )

    compiler.validate_context(context)

    # Skill-layout compilers manage their own IR / cache lifecycle
    # internally; no upfront IR loading is required here.
    return compiler.compile(context)
