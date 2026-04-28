"""Skill-layout compiler for the ``bmad-security-review`` workflow.

Phase 7.2 inlines the workhorse compile logic that previously lived in
:mod:`bmad_assist.compiler.workflows.security_review` so this compiler
is self-contained — no delegation to a legacy compiler. The base class
handles locate / parse / customize / substitute / cache; the inlined
:meth:`_run_workflow_compile` builds the security-review context (CWE
patterns + git diff + modified source files), mission, filtered
instructions, XML envelope, and post-process tail.

Cross-imports — Phase 7.2 extracts the shared git-diff helpers into
:mod:`bmad_assist.compiler.skills._git_helpers` so this inlined
compiler doesn't reach into the legacy ``compiler/workflows`` package
(Brief 7.3 deletes it wholesale).

The ``apply_llm_transforms`` import is preserved at module top-level
so tests can monkeypatch
``bmad_assist.compiler.skills.bmad_security_review.apply_llm_transforms``
to stub LLM calls.
"""

from __future__ import annotations

import logging
from typing import Any

import yaml

from bmad_assist.compiler.filtering import filter_instructions
from bmad_assist.compiler.output import generate_output

# Re-exported so tests/conftest can patch this module attribute. The
# base class resolves it dynamically per call (see _base.py docstring).
from bmad_assist.compiler.patching import apply_llm_transforms  # noqa: F401
from bmad_assist.compiler.shared_utils import apply_post_process
from bmad_assist.compiler.skills._base import SkillLayoutCompilerBase
from bmad_assist.compiler.skills._git_helpers import capture_git_diff
from bmad_assist.compiler.source_context import (
    SourceContextService,
    get_git_diff_files,
)
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext
from bmad_assist.core.exceptions import CompilerError
from bmad_assist.security.patterns import load_security_patterns
from bmad_assist.security.tech_stack import detect_tech_stack

logger = logging.getLogger(__name__)


SKILL_ID = "bmad-security-review"

# Default token budget for CWE patterns.
DEFAULT_PATTERN_BUDGET = 12000

# Approximate tokens per character (conservative).
_CHARS_PER_TOKEN = 4


class BmadSecurityReviewCompiler(SkillLayoutCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-security-review`` skill.

    Phase 7.2 inlined: no legacy compiler delegation. The base class
    handles the compile pipeline tail; this subclass owns the
    security-review-specific tech-stack detection, CWE pattern loading,
    git-diff capture, and source-file embedding.
    """

    skill_id = SKILL_ID
    legacy_workflow_name = "security-review"
    # Phase 7.2: inlined — no legacy compiler delegation.
    legacy_compiler_class = None

    def get_required_files(self) -> list[str]:
        """Security review embeds the diff and source files directly."""
        return []

    def get_variables(self) -> dict[str, Any]:
        """Variables resolved by the security-review compiler."""
        return {
            "detected_languages": "",
            "security_patterns": "",
        }

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """No SKILL.md-level extras for security-review.

        We deliberately do NOT inject the CWE patterns or the diff into
        SKILL.md: they are sized against the model's context budget by
        the inlined compile body and embedded as virtual
        ``security-patterns`` / ``[git-diff]`` files in the CONTEXT
        section. Substituting them into SKILL.md would defeat that
        budgeting and balloon the compiled output.
        """
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the security-review contract + skill-source check.

        Security review has no per-story arguments — only a valid
        ``project_root`` is required.
        """
        if context.project_root is None or not context.project_root.is_dir():
            raise CompilerError(
                "project_root must be a valid directory for security review.\n"
                "  Suggestion: pass a real project directory via CompilerContext"
            )

        skill_md = self._locate_skill_md(context)
        if not skill_md.is_file():  # pragma: no cover — _locate_skill_md raises first
            raise CompilerError(
                f"SKILL.md not found: {skill_md}\n"
                f"  Why it's needed: Source for the bmad-security-review skill\n"
                f"  How to fix: Install bmad-assist v0.5.1+ or rely on the bundled "
                f"fallback under src/bmad_assist/skills/bmad-security-review/"
            )

    # --- Inlined workhorse compile ------------------------------------- #

    def _run_workflow_compile(self, context: CompilerContext) -> CompiledWorkflow:
        """Build the security-review :class:`CompiledWorkflow`.

        Pipeline:

        1. Capture git diff.
        2. Detect tech stack from diff + project markers.
        3. Estimate token budget; load CWE patterns.
        4. Embed patterns + diff + source files as context files.
        5. Substitute variables in instructions.
        6. Generate output.
        """
        if context.workflow_ir is None:  # pragma: no cover — base guarantees this
            raise CompilerError("workflow_ir not set in context")

        workflow_ir = context.workflow_ir

        # 1. Capture git diff.
        diff_content = capture_git_diff(context)

        # 2. Detect tech stack.
        languages = detect_tech_stack(context.project_root, diff_content or None)

        # 3. Calculate token budget for patterns.
        diff_tokens = len(diff_content) // _CHARS_PER_TOKEN if diff_content else 0
        available_pattern_budget = max(
            4000,  # Enough for all Tier 1 + most Tier 2.
            DEFAULT_PATTERN_BUDGET - max(0, diff_tokens - 6000),
        )

        # 4. Load CWE patterns.
        patterns = load_security_patterns(languages, available_pattern_budget)

        # Format patterns as YAML for embedding.
        patterns_yaml = (
            yaml.dump(
                {"patterns": patterns},
                default_flow_style=False,
                allow_unicode=True,
            )
            if patterns
            else "# No patterns loaded"
        )

        # 5. Build context files dict.
        context_files: dict[str, str] = {}

        # Security patterns (first — background context).
        context_files["security-patterns"] = patterns_yaml

        # Source files from diff (full file context for inter-procedural analysis).
        if diff_content:
            try:
                git_diff_files = get_git_diff_files(context.project_root, diff_content)
                if git_diff_files:
                    service = SourceContextService(context, "security-review")
                    source_files = service.collect_files([], git_diff_files)
                    context_files.update(source_files)
            except (OSError, ValueError) as e:
                logger.warning("Failed to collect source files: %s", e)

        # Git diff (primary analysis target — positioned after source for recency-bias).
        if diff_content:
            context_files["[git-diff]"] = diff_content

        # 6. Resolve variables.
        resolved_variables: dict[str, Any] = {
            "detected_languages": ", ".join(languages) if languages else "unknown",
            "detected_languages_list": languages,
            "patterns_loaded_count": len(patterns),
            "security_patterns": patterns_yaml,
        }

        # 7. Filter instructions (variable substitution handled by generate_output).
        filtered_instructions = filter_instructions(workflow_ir)

        # 8. Build mission.
        mission = (
            "Perform CWE-based security vulnerability analysis on the provided "
            f"code diff. Languages detected: {', '.join(languages) if languages else 'unknown'}. "
            f"Patterns loaded: {len(patterns)}."
        )

        compiled = CompiledWorkflow(
            workflow_name=self.legacy_workflow_name,
            mission=mission,
            context="",
            variables=resolved_variables,
            instructions=filtered_instructions,
            output_template="",
        )

        # 9. Generate output.
        output = generate_output(
            compiled,
            project_root=context.project_root,
            context_files=context_files,
        )

        # 10. Apply post-process if patch exists.
        result = output.xml
        if context.patch_path:
            result = apply_post_process(result, context)

        return CompiledWorkflow(
            workflow_name=self.legacy_workflow_name,
            mission=mission,
            context=result,
            variables=resolved_variables,
            instructions=filtered_instructions,
            output_template="",
            token_estimate=len(result) // _CHARS_PER_TOKEN,
        )


__all__ = [
    "SKILL_ID",
    "BmadSecurityReviewCompiler",
    "apply_llm_transforms",
]
