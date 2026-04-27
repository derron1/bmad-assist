"""BMAD Workflow Patching submodule.

This module provides functionality for defining and applying patches
to BMAD workflows, transforming them into optimized templates via LLM.

Public API:
    PatchConfig: Configuration metadata for a patch
    Compatibility: Version compatibility requirements
    TransformResult: Result of applying transforms
    Validation: Output validation rules
    WorkflowPatch: Complete patch definition
    PatcherConfig: Patcher runtime configuration
    discover_patch: Find patch file for a workflow
    load_patch: Load and parse a patch file
    format_transform_prompt: Format instructions for LLM
    PatchSession: Orchestrates LLM calls to apply transforms
    extract_workflow_from_response: Extract workflow content from LLM response
    compile_patch: Compile a workflow patch into a template
    ensure_template_compiled: Ensure cached template exists for a workflow
    load_workflow_ir: Load workflow IR from cache or original files
"""

from bmad_assist.compiler.patching.cache import (
    CacheMeta,
    TemplateCache,
    compute_file_hash,
)
from bmad_assist.compiler.patching.compiler import (
    apply_llm_transforms,
    compile_patch,
    ensure_template_compiled,
    load_workflow_ir,
)
from bmad_assist.compiler.patching.config import (
    PatcherConfig,
    get_patcher_config,
    load_patcher_config,
    reset_patcher_config,
)
from bmad_assist.compiler.patching.discovery import (
    determine_patch_source_level,
    discover_patch,
    load_defaults,
    load_patch,
)
from bmad_assist.compiler.patching.git_intelligence import (
    extract_git_intelligence,
    is_git_repo,
    run_git_command,
)
from bmad_assist.compiler.patching.output import (
    TemplateMetadata,
    generate_template,
)
from bmad_assist.compiler.patching.session import (
    PatchSession,
    extract_workflow_from_response,
)
from bmad_assist.compiler.patching.transforms import (
    format_transform_prompt,
    post_process_compiled,
)
from bmad_assist.compiler.patching.types import (
    Compatibility,
    GitCommand,
    GitIntelligence,
    PatchConfig,
    PostProcessRule,
    TransformResult,
    Validation,
    WorkflowPatch,
)
from bmad_assist.compiler.patching.validation import (
    check_threshold,
    is_regex,
    parse_regex,
    validate_output,
)

__all__ = [
    "CacheMeta",
    "Compatibility",
    "GitCommand",
    "GitIntelligence",
    "PatchConfig",
    "PatchSession",
    "PatcherConfig",
    "PostProcessRule",
    "TemplateCache",
    "TemplateMetadata",
    "TransformResult",
    "Validation",
    "WorkflowPatch",
    "apply_llm_transforms",
    "check_threshold",
    "compile_patch",
    "compute_file_hash",
    "determine_patch_source_level",
    "discover_patch",
    "ensure_template_compiled",
    "extract_git_intelligence",
    "extract_workflow_from_response",
    "format_transform_prompt",
    "generate_template",
    "get_patcher_config",
    "is_git_repo",
    "is_regex",
    "load_defaults",
    "load_patch",
    "load_patcher_config",
    "load_workflow_ir",
    "parse_regex",
    "post_process_compiled",
    "reset_patcher_config",
    "run_git_command",
    "validate_output",
]
