"""Compiler for the security-review workflow.

This module implements the WorkflowCompiler protocol for the security
review workflow, producing standalone prompts for CWE-based security
analysis with embedded git diff, source files, and CWE patterns.

Reuses _capture_git_diff() from code_review compiler and
SourceContextService for full source file context.

Public API:
    SecurityReviewCompiler: Workflow compiler implementing WorkflowCompiler protocol
"""

import logging
from pathlib import Path
from typing import Any

import yaml

from bmad_assist.compiler.filtering import filter_instructions
from bmad_assist.compiler.output import generate_output
from bmad_assist.compiler.shared_utils import apply_post_process
from bmad_assist.compiler.source_context import (
    SourceContextService,
    get_git_diff_files,
)
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext
from bmad_assist.compiler.workflows import resolve_legacy_workflow_dir
from bmad_assist.compiler.workflows.code_review import _capture_git_diff
from bmad_assist.core.exceptions import CompilerError
from bmad_assist.security.patterns import load_security_patterns
from bmad_assist.security.tech_stack import detect_tech_stack

logger = logging.getLogger(__name__)

# Default token budget for CWE patterns
DEFAULT_PATTERN_BUDGET = 12000

# Approximate tokens per character (conservative)
_CHARS_PER_TOKEN = 4


class SecurityReviewCompiler:
    """Compiler for the security-review workflow.

    Implements the WorkflowCompiler protocol. Produces a standalone prompt
    with git diff, source files, and CWE security patterns embedded.
    """

    @property
    def workflow_name(self) -> str:
        """Unique workflow identifier."""
        return "security-review"

    def get_workflow_dir(self, context: CompilerContext) -> Path:
        """Return a real on-disk directory for legacy bookkeeping.

        Phase 6 removed the bundled ``workflows/security-review/`` tree;
        the skill-layout compiler injects the IR before invoking
        ``compile()``. The path returned here is only used for
        existence checks and debug logging — it points at the
        ``bmad-security-review`` skill bundle (which always exists in
        a healthy install).
        """
        workflow_dir = resolve_legacy_workflow_dir("security-review", context.project_root)
        if workflow_dir is None:
            raise CompilerError(
                "Security review skill directory not found.\n  Reinstall: pip install -e ."
            )
        return workflow_dir

    def get_required_files(self) -> list[str]:
        """Return list of required file glob patterns.

        Security review only needs the diff and source files (embedded directly).
        """
        return []

    def get_variables(self) -> dict[str, Any]:
        """Return workflow-specific variables."""
        return {
            "detected_languages": "",
            "security_patterns": "",
        }

    def validate_context(self, context: CompilerContext) -> None:
        """Validate context before compilation."""
        if not context.project_root or not context.project_root.is_dir():
            raise CompilerError("project_root must be a valid directory for security review")

    def compile(self, context: CompilerContext) -> CompiledWorkflow:
        """Compile security-review workflow.

        Pipeline:
        1. Capture git diff via _capture_git_diff()
        2. Detect tech stack from diff + project markers
        3. Estimate token budget, load CWE patterns
        4. Embed patterns + diff + source files as context files
        5. Substitute variables in instructions
        6. Generate output

        Args:
            context: CompilerContext with workflow_ir set.

        Returns:
            CompiledWorkflow ready for LLM invocation.

        """
        if context.workflow_ir is None:
            raise CompilerError("workflow_ir not set in context")

        workflow_ir = context.workflow_ir

        # Step 1: Capture git diff
        diff_content = _capture_git_diff(context)

        # Step 2: Detect tech stack
        languages = detect_tech_stack(context.project_root, diff_content or None)

        # Step 3: Calculate token budget for patterns
        diff_tokens = len(diff_content) // _CHARS_PER_TOKEN if diff_content else 0
        available_pattern_budget = max(
            4000,  # enough for all Tier 1 + most Tier 2
            DEFAULT_PATTERN_BUDGET - max(0, diff_tokens - 6000),
        )

        # Step 4: Load CWE patterns
        patterns = load_security_patterns(languages, available_pattern_budget)

        # Format patterns as YAML for embedding
        patterns_yaml = (
            yaml.dump(
                {"patterns": patterns},
                default_flow_style=False,
                allow_unicode=True,
            )
            if patterns
            else "# No patterns loaded"
        )

        # Step 5: Build context files dict
        context_files: dict[str, str] = {}

        # Security patterns (first — background context)
        context_files["security-patterns"] = patterns_yaml

        # Source files from diff (full file context for inter-procedural analysis)
        if diff_content:
            try:
                git_diff_files = get_git_diff_files(context.project_root, diff_content)
                if git_diff_files:
                    service = SourceContextService(context, "security-review")
                    source_files = service.collect_files([], git_diff_files)
                    context_files.update(source_files)
            except (OSError, ValueError) as e:
                logger.warning("Failed to collect source files: %s", e)

        # Git diff (primary analysis target — positioned after source for recency-bias)
        if diff_content:
            context_files["[git-diff]"] = diff_content

        # Step 6: Resolve variables
        resolved_variables: dict[str, Any] = {
            "detected_languages": ", ".join(languages) if languages else "unknown",
            "detected_languages_list": languages,
            "patterns_loaded_count": len(patterns),
            "security_patterns": patterns_yaml,
        }

        # Step 7: Filter instructions (variable substitution handled by generate_output)
        filtered_instructions = filter_instructions(workflow_ir)

        # Step 9: Build mission
        mission = (
            "Perform CWE-based security vulnerability analysis on the provided "
            f"code diff. Languages detected: {', '.join(languages) if languages else 'unknown'}. "
            f"Patterns loaded: {len(patterns)}."
        )

        # Step 10: Build compiled workflow
        compiled = CompiledWorkflow(
            workflow_name="security-review",
            mission=mission,
            context="",  # context_files dict handles this
            variables=resolved_variables,
            instructions=filtered_instructions,
            output_template="",  # No template — structured JSON output
        )

        # Step 11: Generate output
        output = generate_output(
            compiled,
            project_root=context.project_root,
            context_files=context_files,
        )

        # Step 12: Apply post-process if patch exists
        result = output.xml
        if context.patch_path:
            result = apply_post_process(result, context)

        return CompiledWorkflow(
            workflow_name="security-review",
            mission=mission,
            context=result,
            variables=resolved_variables,
            instructions=filtered_instructions,
            output_template="",
            token_estimate=len(result) // _CHARS_PER_TOKEN,
        )
