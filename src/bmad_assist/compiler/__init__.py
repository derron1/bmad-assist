"""BMAD Workflow Compiler module.

This module provides the public API for compiling BMAD workflows into
standalone prompts suitable for LLM consumption.

Public API:
    compile_workflow: Compile a workflow by name
    get_workflow_compiler: Load a workflow compiler dynamically
    parse_workflow: Parse workflow directory into WorkflowIR
    resolve_variables: Resolve all variable placeholders in workflow config
    discover_files: Discover files based on input_file_patterns
    load_file_contents: Load discovered file contents into context
    extract_section: Extract a section from markdown by header
    filter_instructions: Filter workflow instructions to keep only executable elements
    generate_output: Generate XML output from compiled workflow
    LoadStrategy: Enum for file loading strategies
    WorkflowCompiler: Protocol for workflow-specific compilers
    CompilerError: Exception for compilation errors
    ParserError: Exception for parsing errors
    VariableError: Exception for variable resolution errors
    AmbiguousFileError: Exception for multiple file matches
    CompilerContext: Context passed to compilers
    CompiledWorkflow: Final compiled output
    WorkflowIR: Intermediate representation
    GeneratedOutput: Return type for generate_output
"""

from bmad_assist.compiler.core import (
    WorkflowCompiler,
    compile_workflow,
    get_workflow_compiler,
)
from bmad_assist.compiler.discovery import (
    LoadStrategy,
    discover_files,
    extract_section,
    load_file_contents,
)
from bmad_assist.compiler.filtering import filter_instructions
from bmad_assist.compiler.output import (
    DEFAULT_HARD_LIMIT_TOKENS,
    DEFAULT_SOFT_LIMIT_TOKENS,
    SOFT_LIMIT_RATIO,
    GeneratedOutput,
    generate_output,
    validate_token_budget,
)
from bmad_assist.compiler.parser import parse_workflow
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext, WorkflowIR
from bmad_assist.compiler.variables import resolve_variables
from bmad_assist.core.exceptions import (
    AmbiguousFileError,
    CompilerError,
    ParserError,
    VariableError,
)

__all__ = [
    "compile_workflow",
    "get_workflow_compiler",
    "parse_workflow",
    "resolve_variables",
    "discover_files",
    "load_file_contents",
    "extract_section",
    "filter_instructions",
    "generate_output",
    "validate_token_budget",
    "LoadStrategy",
    "WorkflowCompiler",
    "CompilerError",
    "ParserError",
    "VariableError",
    "AmbiguousFileError",
    "CompilerContext",
    "CompiledWorkflow",
    "WorkflowIR",
    "GeneratedOutput",
    "DEFAULT_SOFT_LIMIT_TOKENS",
    "DEFAULT_HARD_LIMIT_TOKENS",
    "SOFT_LIMIT_RATIO",
]
