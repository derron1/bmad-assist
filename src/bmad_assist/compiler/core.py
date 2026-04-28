"""Core compiler module with WorkflowCompiler protocol and dynamic loading.

This module provides:
- WorkflowCompiler: Protocol defining the interface for workflow-specific compilers
- get_workflow_compiler: Dynamic loader for workflow compiler modules
- compile_workflow: High-level function to compile a workflow by name

Every supported workflow routes through a v6.4+ skill-layout compiler
under :mod:`bmad_assist.compiler.skills`. :data:`WORKFLOW_REGISTRY` is
the sole dispatch table — only canonical ``bmad-`` prefixed ids are
accepted (legacy short aliases were dropped in 0.6.0).
"""

import logging
import re
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext
from bmad_assist.core.exceptions import CompilerError

# Single source of truth for skill-layout dispatch + discovery. Only
# canonical ``bmad-`` prefixed ids are accepted; legacy short aliases
# (``"create-story"``, ``"testarch-atdd"``, ...) were dropped in 0.6.0.
# Each entry is a self-mapping kept so callers that historically did
# ``WORKFLOW_REGISTRY[name]`` continue to work as a registered-id check.
WORKFLOW_REGISTRY: dict[str, str] = {
    "bmad-code-review": "bmad-code-review",
    "bmad-code-review-synthesis": "bmad-code-review-synthesis",
    "bmad-create-story": "bmad-create-story",
    "bmad-dev-story": "bmad-dev-story",
    "bmad-qa-plan-execute": "bmad-qa-plan-execute",
    "bmad-qa-plan-generate": "bmad-qa-plan-generate",
    "bmad-retrospective": "bmad-retrospective",
    "bmad-security-review": "bmad-security-review",
    "bmad-testarch-atdd": "bmad-testarch-atdd",
    "bmad-testarch-automate": "bmad-testarch-automate",
    "bmad-testarch-ci": "bmad-testarch-ci",
    "bmad-testarch-framework": "bmad-testarch-framework",
    "bmad-testarch-nfr": "bmad-testarch-nfr",
    "bmad-testarch-test-design": "bmad-testarch-test-design",
    "bmad-testarch-test-review": "bmad-testarch-test-review",
    "bmad-testarch-trace": "bmad-testarch-trace",
    "bmad-validate-story": "bmad-validate-story",
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


logger = logging.getLogger(__name__)


@runtime_checkable
class WorkflowCompiler(Protocol):
    """Protocol for workflow-specific compilers.

    Each workflow (bmad-create-story, bmad-validate-story, etc.)
    implements this protocol to provide workflow-specific compilation
    logic.

    Attributes:
        workflow_name: Unique workflow identifier (e.g. ``'bmad-create-story'``).

    """

    @property
    def workflow_name(self) -> str:
        """Unique workflow identifier (e.g. ``'bmad-create-story'``)."""
        ...

    def get_workflow_dir(self, context: CompilerContext) -> Path:
        """Return the workflow directory for this compiler.

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

        """
        ...

    def get_variables(self) -> dict[str, Any]:
        """Return workflow-specific variables to resolve.

        Returns:
            Dictionary of variable names to their values or resolution hints.

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
    project_root: Path | None = None,
    **_legacy_kwargs: Any,
) -> WorkflowCompiler:
    """Load workflow compiler by name.

    Every accepted name in :data:`WORKFLOW_REGISTRY` resolves to a
    v6.4+ skill-layout compiler under :mod:`bmad_assist.compiler.skills`.

    Args:
        workflow_name: Canonical ``bmad-`` prefixed workflow id
            (e.g. ``'bmad-create-story'``).
        project_root: Accepted for backwards compatibility. Not consumed
            (routing is layout-independent).
        **_legacy_kwargs: Swallows deprecated keyword arguments
            (``skill_layout``) that pre-0.6.0 callers may still pass.

    Returns:
        WorkflowCompiler instance for the workflow.

    Raises:
        CompilerError: If workflow name invalid or no compiler is
            registered for it.

    """
    del project_root, _legacy_kwargs  # kept for backwards-compat signature.

    if not workflow_name or not workflow_name.strip():
        raise CompilerError(
            "Workflow name cannot be empty\n"
            "  Why it's needed: Workflow name is required to load the compiler module\n"
            "  How to fix: Provide a valid workflow name (e.g., 'bmad-create-story')"
        )

    normalized_name = workflow_name.strip()

    if not _WORKFLOW_NAME_PATTERN.fullmatch(normalized_name):
        raise CompilerError(
            f"Invalid workflow name: '{workflow_name}'\n"
            f"  Why it's needed: Valid Python identifiers needed for module loading\n"
            f"  How to fix: Use lowercase letters, digits, hyphens, underscores only"
        )

    # Normalize underscores → hyphens so module-style names
    # (``bmad_create_story``) and BMAD canonical hyphen names
    # (``bmad-create-story``) both resolve through the registry.
    lookup_name = normalized_name.replace("_", "-")

    # Internal callers (Phase enum → phase name → workflow name pipeline)
    # still pass un-prefixed names like ``"create-story"``. Auto-prepend
    # the canonical ``bmad-`` prefix when the lookup misses, so existing
    # handlers / orchestrators don't need to be retrofitted with the
    # canonical id at every call site. The 0.6.0 break only affects
    # *external* surfaces (CLI args, user configs, public docs) that
    # callers control; internal dispatch stays seamless.
    skill_id = WORKFLOW_REGISTRY.get(lookup_name)
    if skill_id is None and not lookup_name.startswith("bmad-"):
        skill_id = WORKFLOW_REGISTRY.get(f"bmad-{lookup_name}")

    if skill_id is None:
        raise CompilerError(
            f"Workflow not found: '{normalized_name}'\n"
            f"  Suggestion: register the workflow in `WORKFLOW_REGISTRY` "
            f"in compiler/core.py, or use one of the supported names "
            f"({', '.join(sorted(WORKFLOW_REGISTRY))})."
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
    **_legacy_kwargs: Any,
) -> CompiledWorkflow:
    """Compile a workflow by name with given context.

    All workflows route through the v6.4+ skill-layout compilers. The
    skill-layout compiler manages its own IR / cache lifecycle.

    Args:
        workflow_name: Canonical ``bmad-`` prefixed workflow id.
        context: The compilation context with project paths.
        **_legacy_kwargs: Swallows deprecated keyword arguments
            (``skill_layout``) that pre-0.6.0 callers may still pass.

    Returns:
        CompiledWorkflow: The compiled workflow ready for output.

    Raises:
        CompilerError: If workflow invalid or compilation fails.

    """
    del _legacy_kwargs  # kept for backwards-compat signature.

    compiler = get_workflow_compiler(
        workflow_name,
        project_root=context.project_root,
    )

    compiler.validate_context(context)

    # Skill-layout compilers manage their own IR / cache lifecycle
    # internally; no upfront IR loading is required here.
    return compiler.compile(context)
