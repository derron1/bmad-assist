"""Skill-layout compiler for the ``bmad-validate-story-synthesis`` workflow.

Phase 7.1 inlines the workhorse compile logic that previously lived in
:mod:`bmad_assist.compiler.workflows.validate_story_synthesis` so this
compiler is self-contained — no delegation to a legacy compiler. The
shared synthesis contract surface (input-list validation, SKILL.md
locator, ``build_extra_vars``) lives on
:class:`bmad_assist.compiler.skills._synthesis_base.SynthesisCompilerBase`;
brief 7.2 will refactor :mod:`bmad_assist.compiler.skills.bmad_code_review_synthesis`
to also inherit from that base.

The ``apply_llm_transforms`` import is preserved at module top-level
so tests can monkeypatch
``bmad_assist.compiler.skills.bmad_validate_story_synthesis.apply_llm_transforms``
to stub LLM calls — same convention every other skill compiler uses.
The synthesis workflow ships without a patch, so transforms are
skipped in practice (the base class's no-patch path), but the import
is kept for parity with sibling modules and forward compatibility.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

# Re-exported so tests/conftest can patch this module attribute. The
# base class resolves it dynamically per call (see _base.py docstring).
from bmad_assist.compiler.filtering import filter_instructions
from bmad_assist.compiler.output import generate_output
from bmad_assist.compiler.patching import (  # noqa: F401
    apply_llm_transforms,
    load_patch,
    validate_output,
)
from bmad_assist.compiler.shared_utils import (
    apply_post_process,
    context_snapshot,
    find_sprint_status_file,
    format_dv_findings_for_prompt,
    get_stories_dir,
    load_workflow_template,
    resolve_story_file,
    safe_read_file,
)
from bmad_assist.compiler.skills._synthesis_base import SynthesisCompilerBase
from bmad_assist.compiler.source_context import (
    SourceContextService,
    extract_file_paths_from_story,
)
from bmad_assist.compiler.strategic_context import StrategicContextService
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext
from bmad_assist.compiler.variable_utils import (
    filter_garbage_variables,
    substitute_variables,
)
from bmad_assist.compiler.variables import resolve_variables
from bmad_assist.core.exceptions import CompilerError
from bmad_assist.validation.anonymizer import AnonymizedValidation

logger = logging.getLogger(__name__)


# Stable skill id used by all references (config, file paths, dispatch keys).
SKILL_ID = "bmad-validate-story-synthesis"


class BmadValidateStorySynthesisCompiler(SynthesisCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-validate-story-synthesis`` skill.

    Phase 7.1 reference inlining: this compiler does NOT delegate to
    ``ValidateStorySynthesisCompiler``. The base class handles
    locate / parse / customize / substitute / cache; the inlined
    :meth:`_run_workflow_compile` builds the synthesis context, mission,
    filtered instructions, XML envelope, and post-process tail.
    """

    skill_id = SKILL_ID
    legacy_workflow_name = "validate-story-synthesis"
    # Phase 7.1: inlined — no legacy compiler delegation.
    legacy_compiler_class = None

    # --- Synthesis subclass contract ----------------------------------- #

    _input_variable_name = "anonymized_validations"
    _input_label = "validations"
    _input_kind = "validate-story"

    # --- Inlined workhorse compile ------------------------------------- #

    def _run_workflow_compile(self, context: CompilerContext) -> CompiledWorkflow:
        """Build the synthesis :class:`CompiledWorkflow` for this skill.

        Mirrors the pre-Phase-7 ``ValidateStorySynthesisCompiler.compile``
        flow byte-for-byte. The base class has already set
        ``context.workflow_ir`` to a synthetic IR built around the
        patched SKILL.md body and wired ``context.patch_path``.
        """
        workflow_ir = context.workflow_ir
        if workflow_ir is None:  # pragma: no cover — base class guarantees this
            raise CompilerError(
                "workflow_ir not set in context.\n"
                f"  Workflow: {self.skill_id}\n"
                "  Reason: Expected SkillLayoutCompilerBase.compile() to set it\n"
                "  Suggestion: Do not call _run_workflow_compile() directly"
            )

        with context_snapshot(context):
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "Using workflow_ir from context (patch: %s)",
                    context.patch_path.name if context.patch_path else "none",
                )

            # 1. Re-validate (cheap, defensive) and snapshot caller-supplied vars.
            self.validate_context(context)

            epic_num = context.resolved_variables.get("epic_num")
            story_num = context.resolved_variables.get("story_num")
            session_id = context.resolved_variables.get("session_id")
            validations: list[AnonymizedValidation] = context.resolved_variables.get(
                "anonymized_validations", []
            )
            dv_findings = context.resolved_variables.get("deep_verify_findings")
            skip_source_files = context.resolved_variables.get("skip_source_files", False)

            # 2. Resolve workflow variables (communication_language, etc.).
            invocation_params = {
                k: v
                for k, v in context.resolved_variables.items()
                if k in ("epic_num", "story_num", "date")
            }
            sprint_status_path = find_sprint_status_file(context)
            resolved = resolve_variables(context, invocation_params, sprint_status_path, None)

            story_file, story_key, story_title = resolve_story_file(context, epic_num, story_num)

            resolved.update(
                {
                    "epic_num": epic_num,
                    "story_num": story_num,
                    "story_id": f"{epic_num}.{story_num}",
                    "story_key": story_key or f"{epic_num}-{story_num}",
                    "story_file": str(story_file) if story_file else None,
                    "story_title": story_title,
                    "session_id": session_id,
                    "validator_count": len(validations),
                    "date": date.today().isoformat(),
                }
            )

            if dv_findings:
                resolved["deep_verify_findings"] = dv_findings
            if skip_source_files:
                resolved["skip_source_files"] = skip_source_files

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Resolved %d variables", len(resolved))

            # 3. Build synthesis context (story + validations + optional DV findings).
            context_files = self._build_synthesis_context(context, resolved, validations)

            # 4. Mission, template, filtered instructions.
            mission = self._build_synthesis_mission(resolved)
            template_content = load_workflow_template(workflow_ir, context)

            filtered_instructions = filter_instructions(workflow_ir)
            filtered_instructions = substitute_variables(filtered_instructions, resolved)

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Filtered instructions: %d bytes", len(filtered_instructions))

            # 5. Variables surface in the XML envelope; strip duplicates already
            # embedded as virtual context files to avoid double-encoding.
            filtered_vars = filter_garbage_variables(resolved)
            if dv_findings and "deep_verify_findings" in filtered_vars:
                del filtered_vars["deep_verify_findings"]
                logger.debug(
                    "Removed deep_verify_findings from variables (already embedded as file)"
                )

            # 6. Generate the XML output.
            compiled = CompiledWorkflow(
                workflow_name=self.legacy_workflow_name,
                mission=mission,
                context="",
                variables=filtered_vars,
                instructions=filtered_instructions,
                output_template=template_content,
                token_estimate=0,
            )
            result = generate_output(
                compiled,
                project_root=context.project_root,
                context_files=context_files,
                links_only=context.links_only,
            )

            # 7. Patch post_process + validation (no-op when synthesis ships without a patch).
            final_xml = apply_post_process(result.xml, context)
            if context.patch_path and context.patch_path.exists():
                try:
                    patch = load_patch(context.patch_path)
                    if patch.validation:
                        errors = validate_output(final_xml, patch.validation)
                        if errors:
                            logger.warning(
                                "Validation warnings for %s: %s",
                                self.legacy_workflow_name,
                                "; ".join(errors),
                            )
                except Exception as e:
                    logger.warning("Failed to validate output: %s", e)

            return CompiledWorkflow(
                workflow_name=self.legacy_workflow_name,
                mission=mission,
                context=final_xml,
                variables=resolved,
                instructions=filtered_instructions,
                output_template=template_content,
                token_estimate=result.token_estimate,
            )

    # --- Synthesis context construction -------------------------------- #

    def _build_synthesis_context(
        self,
        context: CompilerContext,
        resolved: dict[str, Any],
        validations: list[AnonymizedValidation],
    ) -> dict[str, str]:
        """Build focused context files dict for synthesis.

        Includes (in order):
        1. Strategic docs (project-context-only by default).
        2. Source files referenced from the story's File List (capped).
        3. Story file (REQUIRED).
        4. Anonymized validations as ``[Validator X]`` virtual files.
        5. Optional Deep Verify findings as ``[Deep Verify Findings]``.

        Closes with prompt-budget enforcement that may trim trimmable
        sections to fit the configured cap.
        """
        files: dict[str, str] = {}
        source_file_keys: set[str] = set()
        story_key: str = ""
        project_root = context.project_root
        epic_num = resolved.get("epic_num")
        story_num = resolved.get("story_num")

        # 1. Strategic docs.
        strategic_service = StrategicContextService(context, "validate_story_synthesis")
        strategic_files = strategic_service.collect()
        files.update(strategic_files)
        logger.debug("Added %d strategic docs to synthesis context", len(strategic_files))

        # 2. Story file (REQUIRED).
        stories_dir = get_stories_dir(context)
        pattern = f"{epic_num}-{story_num}-*.md"
        story_matches = sorted(stories_dir.glob(pattern)) if stories_dir.exists() else []

        if not story_matches:
            raise CompilerError(
                f"Story file not found: {stories_dir}/{pattern}\n\n"
                f"Expected pattern: {stories_dir}/{epic_num}-{story_num}-*.md\n"
                f"Found: 0 matching files\n\n"
                f"Suggestion: Run 'bmad-assist compile -w create-story -e {epic_num} "
                f"-s {story_num}' first"
            )

        story_path = story_matches[0]

        try:
            stat = story_path.stat()
            if stat.st_size == 0:
                raise CompilerError(
                    f"Story file is empty: {story_path}\n\n"
                    f"The story file exists but contains no content (0 bytes).\n\n"
                    f"Suggestion: Regenerate the story using create-story workflow"
                )
        except OSError as e:
            raise CompilerError(
                f"Cannot read story file: {story_path}\n\n"
                f"Error: {e}\n\n"
                f"Suggestion: Check file permissions"
            ) from e

        story_content = safe_read_file(story_path, project_root)

        if not story_content:
            raise CompilerError(
                f"Story file unreadable: {story_path}\n\n"
                f"The story file exists but could not be read.\n"
                f"Possible causes: permission denied, encoding error, or path outside project.\n\n"
                f"Suggestion: Check file permissions and encoding (UTF-8 required)"
            )

        # 2a. Source files from File List (before story for recency-bias).
        skip_source_files = context.resolved_variables.get("skip_source_files", False)
        if not skip_source_files:
            file_list_paths = extract_file_paths_from_story(story_content)
            if file_list_paths:
                try:
                    service = SourceContextService(context, "validate_story_synthesis")
                    source_files = service.collect_files(file_list_paths, None)

                    max_synthesis_files = 3
                    if len(source_files) > max_synthesis_files:
                        sorted_files = sorted(source_files.items(), key=lambda x: x[0])
                        limited_files = dict(sorted_files[:max_synthesis_files])
                        logger.warning(
                            "Synthesis source files limited: %d → %d (token budget protection)",
                            len(source_files),
                            max_synthesis_files,
                        )
                        source_files = limited_files

                    source_file_keys.update(source_files.keys())
                    files.update(source_files)
                    if source_files:
                        logger.debug(
                            "Added %d source files to context for validate_story_synthesis",
                            len(source_files),
                        )
                    else:
                        logger.warning(
                            "No source files collected for validate_story_synthesis "
                            "(budget=%d, candidates=%d)",
                            service.budget,
                            len(file_list_paths),
                        )
                except Exception as e:
                    logger.warning(
                        "Failed to collect source files for validate_story_synthesis: %s", e
                    )
        else:
            logger.info("Step 0 compression: skipping source files (base context exceeds limit)")

        # 2b. Story file.
        story_key = str(story_path)
        files[story_key] = story_content
        logger.debug(
            "Added story file to synthesis context: %s (mtime=%d, size=%d bytes)",
            story_path,
            int(stat.st_mtime),
            stat.st_size,
        )

        # 4. Validations (each as a separate file for clean CDATA handling).
        sorted_validations = sorted(validations, key=lambda v: v.validator_id)
        for v in sorted_validations:
            validation_path = f"[{v.validator_id}]"
            files[validation_path] = v.content
            logger.debug("Added validation to synthesis context: %s", validation_path)

        # 5. Deep Verify findings (if available).
        dv_findings = resolved.get("deep_verify_findings")
        logger.debug(
            "DV findings check: dv_findings=%s, type=%s",
            dv_findings is not None,
            type(dv_findings).__name__ if dv_findings else None,
        )
        if dv_findings:
            dv_content = format_dv_findings_for_prompt(dv_findings)
            files["[Deep Verify Findings]"] = dv_content
            logger.info(
                "Added Deep Verify findings to synthesis context: verdict=%s, findings=%d",
                dv_findings.get("verdict", "?"),
                len(dv_findings.get("findings", [])),
            )
        else:
            logger.debug("No Deep Verify findings in resolved_variables for synthesis")

        file_count = len([k for k in files if not k.startswith("[")])
        logger.info(
            "Built synthesis context with %d files, %d validations%s for story %s.%s",
            file_count,
            len(validations),
            ", and DV findings" if dv_findings else "",
            epic_num,
            story_num,
        )

        # 6. Staged prompt budget enforcement.
        from bmad_assist.compiler.budget import ContextSection, PromptBudgetEnforcer

        enforcer = PromptBudgetEnforcer.from_config("validate_story_synthesis")
        if enforcer.cap > 0:
            strategic_keys = {k for k in files if k.startswith("[project-context")}
            validation_keys = {
                k for k in files if k.startswith("[Validator ") or k.startswith("[RAW]")
            }
            dv_keys = {k for k in files if k == "[Deep Verify Findings]"}

            sections = [
                ContextSection(
                    "strategic",
                    {k: files[k] for k in files if k in strategic_keys},
                    priority=1,
                    trimmable=True,
                ),
                ContextSection(
                    "source",
                    {k: files[k] for k in files if k in source_file_keys},
                    priority=2,
                    trimmable=True,
                ),
                ContextSection(
                    "deep_verify",
                    {k: files[k] for k in files if k in dv_keys},
                    priority=3,
                    trimmable=True,
                ),
                ContextSection(
                    "validations",
                    {k: files[k] for k in files if k in validation_keys},
                    priority=4,
                    trimmable=True,
                ),
                ContextSection(
                    "story",
                    {story_key: files[story_key]} if story_key in files else {},
                    priority=99,
                    trimmable=False,
                ),
            ]

            result = enforcer.enforce(sections)
            if result.trimmed_sections:
                trimmed_files: dict[str, str] = {}
                for section_key in result.sections:
                    trimmed_files.update(result.sections[section_key])
                files = {k: trimmed_files[k] for k in files if k in trimmed_files}

        return files

    @staticmethod
    def _build_synthesis_mission(resolved: dict[str, Any]) -> str:
        """Build the synthesis mission description Master receives."""
        epic_num = resolved.get("epic_num", "?")
        story_num = resolved.get("story_num", "?")
        validator_count = resolved.get("validator_count", 0)

        return f"""Master Synthesis: Story {epic_num}.{story_num}

You are synthesizing {validator_count} independent validator reviews.

Your mission:
1. VERIFY each issue raised by validators
   - Cross-reference with story content
   - Identify false positives (issues that aren't real problems)
   - Confirm valid issues with evidence

2. PRIORITIZE real issues by severity
   - Critical: Blocks implementation or causes major problems
   - High: Significant gaps or ambiguities
   - Medium: Improvements that would help
   - Low: Nice-to-have suggestions

3. SYNTHESIZE findings
   - Merge duplicate issues from different validators
   - Note validator consensus (if 3+ agree, high confidence)
   - Highlight unique insights from individual validators

4. APPLY changes to story file
   - You have WRITE PERMISSION to modify the story
   - CRITICAL: Before using Edit tool, ALWAYS Read the target file first
   - Use EXACT content from Read tool output as old_string, NOT content from this prompt
   - If Read output is truncated, use offset/limit parameters to locate the target section
   - Apply fixes for verified issues
   - Document what you changed and why

Output format:
## Synthesis Summary
## Issues Verified (by severity)
## Issues Dismissed (false positives with reasoning)
## Changes Applied"""


__all__ = [
    "SKILL_ID",
    "BmadValidateStorySynthesisCompiler",
    "apply_llm_transforms",
]
