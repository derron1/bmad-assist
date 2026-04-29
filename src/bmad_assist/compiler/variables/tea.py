"""TEA (Test Engineer Architect) specific variable resolution.

This module provides variable resolution for TEA Enterprise tri-modal workflows,
including knowledge index resolution, TEA config flags, step-specific variables,
and knowledge base fragment loading.

Public API:
    resolve_tea_variables: Resolve TEA-specific variables for step content
    resolve_knowledge_index: Resolve knowledgeIndex to actual file path
    resolve_knowledge_base: Load workflow-specific knowledge fragments
    load_tea_module_config: Load TEA module config (test_artifacts + siblings)
"""

import logging
import tomllib
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Keys we extract from TEA module config for the `<tea-paths>` block.
# Order is meaningful: it controls emit order in the compiled prompt.
# Source of truth: BMAD v6.4 module.yaml for the TEA module.
# - test_artifacts: actively used by step files (target of this fix).
# - test_design_output / test_review_output / trace_output / test_dir:
#   declared by BMAD upstream but currently FUTURE-marked (unused by
#   step files). Included defensively so future BMAD step revisions
#   don't trigger a token-resolution gap.
TEA_PATH_KEYS: tuple[str, ...] = (
    "test_artifacts",
    "test_design_output",
    "test_review_output",
    "trace_output",
    "test_dir",
)

# Legacy constants retained for backwards-compat imports. New callers
# should use :func:`resolve_tea_index_path` from
# :mod:`bmad_assist.testarch.knowledge.loader` (re-exported below).
DEFAULT_KNOWLEDGE_INDEX_PATH = "_bmad/tea/testarch/tea-index.csv"
FALLBACK_KNOWLEDGE_INDEX_PATH = "_bmad/bmm/testarch/tea-index.csv"


def resolve_knowledge_index(
    project_root: Path,
    explicit_path: str | None = None,
) -> str | None:
    """Resolve knowledgeIndex to actual file path.

    Searches for the TEA knowledge index file. When ``explicit_path`` is
    provided it is honoured (subject to security validation). Otherwise
    delegates to :func:`resolve_tea_index_path` which probes both the
    v6.4+ skill layout (``.claude/skills/bmad-tea/resources/...``) and
    the legacy ``_bmad/tea/...`` / ``_bmad/bmm/...`` locations before
    falling back to the bundled copy.

    Args:
        project_root: Project root directory.
        explicit_path: Optional explicit path from step frontmatter.

    Returns:
        Absolute path to knowledge index file as string, or None if not found.

    """
    from bmad_assist.testarch.knowledge.loader import resolve_tea_index_path

    # If explicit path provided, resolve it (preserving the existing
    # security checks — these are stricter than what the shared
    # resolver does because explicit paths come from step frontmatter).
    if explicit_path:
        # Security: Reject absolute paths
        if Path(explicit_path).is_absolute():
            logger.warning(
                "Absolute knowledge index path rejected: %s (security)",
                explicit_path,
            )
            return None

        # Security: Reject path traversal
        if ".." in explicit_path:
            logger.warning(
                "Knowledge index path with traversal rejected: %s (security)",
                explicit_path,
            )
            return None

        candidate = (project_root / explicit_path).resolve()

        # Security: Validate resolved path is within project root
        try:
            candidate.relative_to(project_root.resolve())
        except ValueError:
            logger.warning(
                "Knowledge index path escapes project root: %s",
                explicit_path,
            )
            return None

        if candidate.exists():
            logger.debug("Using explicit knowledge index: %s", candidate)
            return str(candidate)
        else:
            logger.warning(
                "Explicit knowledge index not found: %s (continuing without it)",
                candidate,
            )
            return None

    resolved = resolve_tea_index_path(project_root)
    if resolved is None:
        logger.debug("No knowledge index found (not a blocker)")
        return None
    return str(resolved)


def resolve_tea_config_flags(
    project_root: Path,
) -> dict[str, Any]:
    """Resolve TEA config flags from module.yaml.

    Loads TEA module configuration and extracts feature flags.
    Falls back to defaults if module.yaml not found.

    Args:
        project_root: Project root directory.

    Returns:
        Dictionary with TEA config flags.

    """
    # Default values if config not found
    flags: dict[str, Any] = {
        "tea_use_playwright_utils": True,
        "tea_use_mcp_enhancements": True,
    }

    # Try to load TEA module config
    module_yaml_path = project_root / "_bmad/tea/module.yaml"
    if not module_yaml_path.exists():
        logger.debug("TEA module.yaml not found, using defaults")
        return flags

    try:
        import yaml

        with open(module_yaml_path, encoding="utf-8") as f:
            module_config = yaml.safe_load(f) or {}

        # Extract feature flags (handle both dict format and simple values)
        for flag_name in ["tea_use_playwright_utils", "tea_use_mcp_enhancements"]:
            if flag_name in module_config:
                value = module_config[flag_name]
                # Handle dict format with 'default' key
                if isinstance(value, dict):
                    flags[flag_name] = value.get("default", True)
                elif isinstance(value, bool):
                    flags[flag_name] = value
                else:
                    logger.warning(
                        "Invalid type for %s: expected bool, got %s. Using default.",
                        flag_name,
                        type(value).__name__,
                    )
                    # Keep default value

        logger.debug("Loaded TEA config flags: %s", flags)

    except Exception as e:
        logger.warning("Failed to load TEA module.yaml: %s (using defaults)", e)

    return flags


def resolve_next_step_file(
    next_step_ref: str | None,
    current_step_path: Path,
) -> str | None:
    """Resolve nextStepFile reference to absolute path.

    Args:
        next_step_ref: Relative path from step frontmatter (e.g., './step-02.md').
        current_step_path: Absolute path to current step file.

    Returns:
        Absolute path to next step file as string, or None if not provided.

    """
    if not next_step_ref:
        return None

    # Resolve relative to current step's directory
    step_dir = current_step_path.parent
    next_path = (step_dir / next_step_ref).resolve()

    return str(next_path)


def resolve_knowledge_base(
    project_root: Path,
    workflow_id: str,
    tea_flags: dict[str, Any] | None = None,
) -> str:
    """Load workflow-specific knowledge fragments.

    Loads relevant knowledge fragments for the workflow and returns
    concatenated markdown content with headers.

    Args:
        project_root: Project root directory.
        workflow_id: Workflow identifier (e.g., "atdd", "test-review").
        tea_flags: Optional TEA config flags for conditional loading.

    Returns:
        Concatenated markdown content with <!-- KNOWLEDGE: name --> headers.
        Empty string if index missing or no fragments found.

    """
    try:
        from bmad_assist.testarch.knowledge import get_knowledge_loader

        loader = get_knowledge_loader(project_root)
        content = loader.load_for_workflow(workflow_id, tea_flags)
        if content:
            logger.debug("Loaded knowledge base for workflow %s", workflow_id)
        return content
    except (
        ImportError,
        ModuleNotFoundError,
        OSError,
        ValueError,
    ) as e:
        logger.warning("Failed to load knowledge base: %s (continuing)", e)
        return ""


def resolve_tea_variables(
    resolved: dict[str, Any],
    project_root: Path,
    knowledge_index_path: str | None = None,
    workflow_id: str | None = None,
    context_files: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve all TEA-specific variables.

    Adds TEA variables to the resolved variables dict:
    - knowledgeIndex: Path to TEA knowledge index CSV
    - tea_use_playwright_utils: Playwright utils feature flag
    - tea_use_mcp_enhancements: MCP enhancements feature flag
    - knowledge_base: Workflow-specific knowledge fragments (if workflow_id provided)

    Args:
        resolved: Existing resolved variables dict (modified in place).
        project_root: Project root directory.
        knowledge_index_path: Optional explicit knowledge index path.
        workflow_id: Optional workflow identifier for knowledge base loading.
        context_files: Optional dict to add knowledge fragments as context.

    Returns:
        Updated resolved variables dict with TEA variables.

    """
    # Resolve knowledge index. Don't clobber a value the caller has
    # already set (e.g. step_chain pre-resolves the per-step explicit
    # path before this function runs); previously the implicit None
    # call would only overwrite if no project install existed, but
    # Phase 4's bundled fallback means the resolver always returns a
    # path. Preserve an existing value to keep that contract.
    if "knowledgeIndex" not in resolved:
        ki_path = resolve_knowledge_index(project_root, knowledge_index_path)
        if ki_path:
            resolved["knowledgeIndex"] = ki_path
            logger.debug("Set knowledgeIndex: %s", ki_path)

    # Resolve TEA config flags
    tea_flags = resolve_tea_config_flags(project_root)
    for key, value in tea_flags.items():
        # Only set if not already set (allow overrides)
        if key not in resolved:
            resolved[key] = value

    # Load knowledge base for workflow (AC8)
    if workflow_id:
        knowledge_content = resolve_knowledge_base(project_root, workflow_id, tea_flags)
        if knowledge_content:
            resolved["knowledge_base"] = knowledge_content
            # Also add to context_files if provided
            if context_files is not None:
                context_files["knowledge_base"] = knowledge_content
            logger.debug("Set knowledge_base variable for workflow %s", workflow_id)
        else:
            logger.debug("No knowledge base content for workflow %s", workflow_id)

    return resolved


def _resolve_tea_path_token(value: str, project_root: Path, output_folder: str | None) -> str:
    """Substitute `{project-root}` and `{output_folder}` tokens in a path value.

    BMAD config files express paths with templated tokens (e.g.
    ``{project-root}/_bmad-output/test-artifacts``). We pre-resolve them
    so the LLM sees concrete paths and never has to guess.

    Substitution order:
    1. ``{project-root}`` -> canonical absolute path string of
       ``project_root`` (symlinks followed via :meth:`Path.resolve`).
       Matches the form other compile-time substitutions emit, so
       all paths in the compiled prompt agree on the same prefix.
    2. ``{output_folder}`` -> ``output_folder`` (which itself may have
       had ``{project-root}`` substituted upstream — pass the already-
       resolved value here).

    Args:
        value: Raw path value from config (may contain tokens).
        project_root: Project root absolute path.
        output_folder: Resolved output folder path string, or None.

    Returns:
        Path string with tokens substituted (idempotent if already resolved).

    """
    result = value
    if "{project-root}" in result:
        result = result.replace("{project-root}", str(project_root.resolve()))
    if output_folder is not None and "{output_folder}" in result:
        result = result.replace("{output_folder}", output_folder)
    return result


def _read_yaml_tea_config(project_root: Path) -> tuple[dict[str, Any], str | None] | None:
    """Read `_bmad/tea/config.yaml`. Returns (data, output_folder) or None."""
    yaml_path = project_root / "_bmad" / "tea" / "config.yaml"
    if not yaml_path.is_file():
        return None
    try:
        import yaml

        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("Failed to parse %s: %s", yaml_path, e)
        return None
    if not isinstance(data, dict):
        return None
    output_folder = data.get("output_folder")
    if isinstance(output_folder, str):
        output_folder = _resolve_tea_path_token(output_folder, project_root, None)
    else:
        output_folder = None
    return data, output_folder


def _read_toml_tea_config(project_root: Path) -> tuple[dict[str, Any], str | None] | None:
    """Read `_bmad/config.toml [modules.tea]`. Returns (data, output_folder) or None."""
    toml_path = project_root / "_bmad" / "config.toml"
    if not toml_path.is_file():
        return None
    try:
        with open(toml_path, "rb") as f:
            parsed = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.warning("Failed to parse %s: %s", toml_path, e)
        return None
    modules = parsed.get("modules") or {}
    tea = modules.get("tea") if isinstance(modules, dict) else None
    if not isinstance(tea, dict):
        return None
    core = parsed.get("core") if isinstance(parsed.get("core"), dict) else {}
    output_folder = core.get("output_folder") if isinstance(core, dict) else None
    if isinstance(output_folder, str):
        output_folder = _resolve_tea_path_token(output_folder, project_root, None)
    else:
        output_folder = None
    return tea, output_folder


def load_tea_module_config(project_root: Path) -> dict[str, str]:
    """Load TEA module path config with `{project-root}` / `{output_folder}` resolved.

    Read order:
    1. ``_bmad/tea/config.yaml`` (BMAD v6.4+ flat YAML format).
    2. ``_bmad/config.toml`` ``[modules.tea]`` section (TOML fallback).
    3. Returns ``{}`` if neither file exists or parses cleanly.

    Only the keys in :data:`TEA_PATH_KEYS` are extracted. Each value
    has ``{project-root}`` substituted with the absolute project root,
    and ``{output_folder}`` substituted with the resolved
    ``[core].output_folder`` (when present in the same source).

    The returned dict preserves the order from :data:`TEA_PATH_KEYS`,
    and only includes keys whose source value is a non-empty string —
    callers can iterate it directly to build the ``<tea-paths>`` block.

    Args:
        project_root: Project root absolute path.

    Returns:
        Mapping of TEA path key -> resolved path string. Empty if no
        config source is found or no path keys are present.

    """
    source = _read_yaml_tea_config(project_root)
    if source is None:
        source = _read_toml_tea_config(project_root)
    if source is None:
        return {}

    raw, output_folder = source

    resolved: dict[str, str] = {}
    for key in TEA_PATH_KEYS:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        resolved[key] = _resolve_tea_path_token(value, project_root, output_folder)
    return resolved
