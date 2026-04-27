"""Core compiler module with WorkflowCompiler protocol and dynamic loading.

This module provides:
- WorkflowCompiler: Protocol defining the interface for workflow-specific compilers
- get_workflow_compiler: Dynamic loader for workflow compiler modules
- compile_workflow: High-level function to compile a workflow by name

The compilation flow integrates patch/template discovery:
1. Load workflow compiler
2. Get workflow directory from compiler
3. Load WorkflowIR (from cache if patch exists, or original files)
4. Set context.workflow_ir and context.patch_path
5. Call compiler.compile() with prepared context

Phase 2 of the skill-layout refactor adds parallel routing: when the
caller (or the loaded config) requests ``skill_layout="new"`` and the
workflow has a skill-layout port, the factory returns the new
:mod:`bmad_assist.compiler.skills` compiler instead of the legacy
:mod:`bmad_assist.compiler.workflows` one. Both paths coexist; the
flag selects between them.
"""

import importlib
import logging
import re
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext
from bmad_assist.core.exceptions import CompilerError

# Skill-layout-port registry. Maps the *legacy* workflow name and the
# new bmad-prefixed skill id to the canonical skill id. Phase 2 shipped
# bmad-create-story; Phase 3.1 added bmad-dev-story. Phase 3.2 fans
# out to the remaining workflows.
_SKILL_LAYOUT_COMPILERS: dict[str, str] = {
    # legacy_name → bmad-prefixed skill id (canonical)
    # Entries grouped by canonical skill id, alphabetical.
    "code-review": "bmad-code-review",
    "bmad-code-review": "bmad-code-review",
    "create-story": "bmad-create-story",
    "bmad-create-story": "bmad-create-story",
    "dev-story": "bmad-dev-story",
    "bmad-dev-story": "bmad-dev-story",
    "retrospective": "bmad-retrospective",
    "bmad-retrospective": "bmad-retrospective",
}


def _build_skill_layout_compiler(skill_id: str) -> "WorkflowCompiler":
    """Instantiate the skill-layout compiler for ``skill_id``.

    Imports lazily to avoid pulling skill-layout deps into the import
    graph for callers that never opt into the new path.
    """
    if skill_id == "bmad-code-review":
        from bmad_assist.compiler.skills.bmad_code_review import (
            BmadCodeReviewCompiler,
        )

        return BmadCodeReviewCompiler()
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
    if skill_id == "bmad-retrospective":
        from bmad_assist.compiler.skills.bmad_retrospective import (
            BmadRetrospectiveCompiler,
        )

        return BmadRetrospectiveCompiler()
    raise CompilerError(
        f"No skill-layout compiler registered for '{skill_id}'.\n"
        f"  Suggestion: register the compiler in "
        f"`_SKILL_LAYOUT_COMPILERS` and `_build_skill_layout_compiler` "
        f"in compiler/core.py."
    )


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

    Dynamically loads the appropriate workflow compiler module based on
    the workflow name. Workflow names with hyphens are normalized to
    underscores for Python module naming (e.g., 'create-story' becomes
    'create_story').

    Args:
        workflow_name: Workflow identifier (e.g., 'create-story').
        skill_layout: Routing mode. ``"auto"`` resolves the layout via
            :func:`bmad_assist.skill_layout.detect_layout` against
            ``project_root`` (or the loaded config's value when
            ``project_root`` is None). ``"new"`` forces the v6.4+
            skill-layout compiler when the workflow has one; ``"old"``
            forces the legacy path. Defaults to ``"auto"`` for full
            backwards compatibility with pre-Phase-2 callers.
        project_root: Project root used by ``skill_layout="auto"`` to
            run the layout probe. When ``None``, layout detection is
            skipped and the routing falls back to the legacy path.

    Returns:
        WorkflowCompiler instance for the workflow.

    Raises:
        CompilerError: If workflow name invalid or module can't be loaded.

    """
    if not workflow_name or not workflow_name.strip():
        raise CompilerError(
            "Workflow name cannot be empty\n"
            "  Why it's needed: Workflow name is required to load the compiler module\n"
            "  How to fix: Provide a valid workflow name (e.g., 'create-story')"
        )

    # Normalize workflow name: strip whitespace
    normalized_name = workflow_name.strip()

    # Validate workflow name format (security: prevents import path manipulation)
    if not _WORKFLOW_NAME_PATTERN.fullmatch(normalized_name):
        raise CompilerError(
            f"Invalid workflow name: '{workflow_name}'\n"
            f"  Why it's needed: Valid Python identifiers needed for module loading\n"
            f"  How to fix: Use lowercase letters, digits, hyphens, underscores only"
        )

    # Phase 2 routing: skill-layout port takes priority when the caller
    # opted in (or auto-detected) AND the workflow has a port.
    use_new_path = _resolve_skill_layout(skill_layout, project_root) == "new"
    if use_new_path and normalized_name in _SKILL_LAYOUT_COMPILERS:
        skill_id = _SKILL_LAYOUT_COMPILERS[normalized_name]
        logger.debug("Routing '%s' through skill-layout compiler '%s'", normalized_name, skill_id)
        return _build_skill_layout_compiler(skill_id)

    # Convert to Python module naming: hyphens to underscores
    module_name = normalized_name.replace("-", "_")
    module_path = f"bmad_assist.compiler.workflows.{module_name}"

    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as e:
        # Distinguish "workflow module missing" vs "workflow has missing dependency"
        # ModuleNotFoundError.name contains the actual missing module
        missing_module = getattr(e, "name", None)
        if missing_module in (module_path, module_name):
            raise CompilerError(
                f"Workflow not found: '{normalized_name}'\n"
                f"  Expected module: {module_path}\n"
                f"  Suggestion: Check workflow name or ensure the compiler module exists"
            ) from e
        # The workflow module exists but imports a missing dependency
        raise CompilerError(
            f"Workflow '{normalized_name}' has import errors\n"
            f"  Missing dependency: {missing_module!r}\n"
            f"  Error: {e}\n"
            f"  Suggestion: Install the missing dependency"
        ) from e
    except SyntaxError as e:
        raise CompilerError(
            f"Workflow '{normalized_name}' has syntax errors\n"
            f"  Error: {e}\n"
            f"  Suggestion: Check the workflow module for syntax issues"
        ) from e
    except ImportError as e:
        raise CompilerError(
            f"Workflow '{normalized_name}' has import errors\n"
            f"  Error: {e}\n"
            f"  Suggestion: Check the workflow module imports"
        ) from e

    # Get compiler class (convention: CamelCase of module name + "Compiler")
    # e.g., create_story -> CreateStoryCompiler
    class_name = "".join(word.capitalize() for word in module_name.split("_")) + "Compiler"
    compiler_class: type[WorkflowCompiler] | None = getattr(module, class_name, None)

    if compiler_class is None:
        raise CompilerError(
            f"Workflow module missing compiler class\n"
            f"  Module: {module_path}\n"
            f"  Expected class: {class_name}\n"
            f"  How to fix: Define class {class_name} implementing WorkflowCompiler protocol"
        )

    # Wrap instantiation to catch constructor errors
    try:
        instance: WorkflowCompiler = compiler_class()
    except Exception as e:
        raise CompilerError(
            f"Workflow failed to instantiate: '{normalized_name}'\n"
            f"  Class: {class_name}\n"
            f"  Error: {e}\n"
            f"  Suggestion: Check the compiler class constructor"
        ) from e

    return instance


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


def _resolve_skill_layout(
    mode: SkillLayoutMode,
    project_root: Path | None,
) -> Literal["new", "old"]:
    """Resolve a layout request to the concrete ``"new"`` or ``"old"`` flavor.

    ``"auto"`` walks: explicit ``project_root`` → loaded config's
    ``skill_layout`` → :func:`detect_layout` against ``project_root``.
    Any failure to resolve falls back to ``"old"`` so legacy callers
    that don't pass the new arguments keep their existing behaviour.
    """
    if mode == "new":
        return "new"
    if mode == "old":
        return "old"

    # mode == "auto" — first ask the loaded config (if any).
    try:
        from bmad_assist.core.config import get_config

        cfg = get_config()
        cfg_mode = getattr(cfg, "skill_layout", "auto")
        if cfg_mode == "new":
            return "new"
        if cfg_mode == "old":
            return "old"
    except Exception:
        # No config loaded yet (e.g. test contexts) — fall through.
        pass

    if project_root is None:
        return "old"

    try:
        from bmad_assist.skill_layout import detect_layout

        return detect_layout(project_root)
    except Exception:
        return "old"


def compile_workflow(
    workflow_name: str,
    context: CompilerContext,
    *,
    skill_layout: SkillLayoutMode = "auto",
) -> CompiledWorkflow:
    """Compile a workflow by name with given context.

    High-level function that orchestrates the full compilation pipeline:
    1. Load the appropriate workflow compiler (legacy or skill-layout)
    2. Get workflow directory from compiler
    3. Load WorkflowIR (from cached template if patch exists, or original files)
    4. Set context.workflow_ir and context.patch_path
    5. Call compiler.compile() with prepared context

    This centralizes patch/template discovery so individual workflow
    compilers don't need to handle it.

    Args:
        workflow_name: Workflow identifier (e.g., 'create-story').
        context: The compilation context with project paths.
        skill_layout: Routing mode. ``"auto"`` (default) consults the
            loaded config and the project layout probe; ``"new"`` forces
            the v6.4+ skill compiler when one exists for the workflow;
            ``"old"`` always uses the legacy path. Backwards-compatible
            with pre-Phase-2 callers that don't pass the argument.

    Returns:
        CompiledWorkflow: The compiled workflow ready for output.

    Raises:
        CompilerError: If workflow invalid or compilation fails.

    """
    from bmad_assist.compiler.patching import load_workflow_ir

    # Step 1: Load workflow compiler
    compiler = get_workflow_compiler(
        workflow_name,
        skill_layout=skill_layout,
        project_root=context.project_root,
    )

    # Step 2: Validate context (basic validation before loading)
    compiler.validate_context(context)

    # Step 3: Get workflow directory from compiler
    workflow_dir = compiler.get_workflow_dir(context)

    # Skill-layout compilers manage their own IR / cache lifecycle.
    # Skip the legacy load_workflow_ir step for them — that pipeline
    # assumes workflow.yaml + instructions.xml on disk, which the
    # new path doesn't have.
    is_skill_layout = type(compiler).__module__.startswith("bmad_assist.compiler.skills.")

    if not is_skill_layout:
        # Step 4: Load WorkflowIR (auto-compiles patch if needed)
        # This is the centralized logic that handles:
        # - Checking for cached template (project → CWD → global)
        # - Auto-compiling patch if exists but no cache
        # - Falling back to original workflow files
        workflow_ir, patch_path = load_workflow_ir(
            workflow_name,
            context.project_root,
            cwd=context.cwd,
            workflow_dir=workflow_dir,
        )

        # Step 5: Set context for compiler
        context.workflow_ir = workflow_ir
        context.patch_path = patch_path

        # Step 5.1: Check for interactive elements without patch
        _check_interactive_elements(workflow_name, workflow_ir.raw_instructions, patch_path)

        logger.debug(
            "Prepared workflow %s: ir=%s, patch=%s",
            workflow_name,
            "cached" if patch_path else "original",
            patch_path.name if patch_path else None,
        )

    # Step 6: Compile with prepared context
    return compiler.compile(context)
