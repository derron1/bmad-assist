"""Skill-layout compiler for the ``bmad-code-review-synthesis`` workflow.

Phase 7.2 inlines the workhorse compile logic that previously lived in
:mod:`bmad_assist.compiler.workflows.code_review_synthesis` so this
compiler is self-contained — no delegation to a legacy compiler. The
shared synthesis contract surface (input-list validation, SKILL.md
locator, ``build_extra_vars``) lives on
:class:`bmad_assist.compiler.skills._synthesis_base.SynthesisCompilerBase`;
this module supplies the ``_input_*`` knobs and the inlined compile
body (mission + per-reviewer context construction + git diff capture +
modified-source-file collection + prompt-budget enforcement).

Notable nuances:

* No patch file exists for this synthesis workflow. The base class's
  no-patch path handles that gracefully — substituted SKILL.md is the
  final body.
* Cross-file imports — Phase 7.2 extracts the shared git-diff helpers
  into :mod:`bmad_assist.compiler.skills._git_helpers` so this inlined
  compiler doesn't reach into the legacy ``compiler/workflows``
  package (Brief 7.3 deletes it wholesale).

The ``apply_llm_transforms`` import is preserved at module top-level
so tests can monkeypatch
``bmad_assist.compiler.skills.bmad_code_review_synthesis.apply_llm_transforms``
to stub LLM calls — same convention every other skill compiler uses.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

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
from bmad_assist.compiler.skills._git_helpers import (
    capture_git_diff,
    extract_modified_files_from_stat,
)
from bmad_assist.compiler.skills._synthesis_base import SynthesisCompilerBase
from bmad_assist.compiler.source_context import (
    SourceContextService,
    extract_file_paths_from_story,
    get_git_diff_files,
)
from bmad_assist.compiler.strategic_context import StrategicContextService
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext
from bmad_assist.compiler.variable_utils import (
    filter_garbage_variables,
    substitute_variables,
)
from bmad_assist.compiler.variables import resolve_variables
from bmad_assist.core.exceptions import CompilerError
from bmad_assist.testarch.context import collect_tea_context
from bmad_assist.validation.anonymizer import AnonymizedValidation

logger = logging.getLogger(__name__)


SKILL_ID = "bmad-code-review-synthesis"


class BmadCodeReviewSynthesisCompiler(SynthesisCompilerBase):
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-code-review-synthesis`` skill.

    Phase 7.2 inlined: no legacy compiler delegation. The base class
    handles locate / parse / customize / substitute / cache; the inlined
    :meth:`_run_workflow_compile` builds the synthesis context, mission,
    filtered instructions, XML envelope, and post-process tail.
    """

    skill_id = SKILL_ID
    legacy_workflow_name = "code-review-synthesis"
    # Phase 7.2: inlined — no legacy compiler delegation.
    legacy_compiler_class = None

    # --- Synthesis subclass contract ----------------------------------- #

    _input_variable_name = "anonymized_reviews"
    _input_label = "reviews"
    _input_kind = "code-review"

    # --- Inlined workhorse compile ------------------------------------- #

    def _run_workflow_compile(self, context: CompilerContext) -> CompiledWorkflow:
        """Build the synthesis :class:`CompiledWorkflow` for this skill.

        Mirrors the pre-Phase-7 ``CodeReviewSynthesisCompiler.compile``
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
            reviews: list[AnonymizedValidation] = context.resolved_variables.get(
                "anonymized_reviews", []
            )
            dv_findings = context.resolved_variables.get("deep_verify_findings")
            security_findings = context.resolved_variables.get("security_findings")
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

            # 3. Capture git diff (used by both the prompt and source-file scoring).
            git_diff = capture_git_diff(context)

            resolved.update(
                {
                    "epic_num": epic_num,
                    "story_num": story_num,
                    "story_id": f"{epic_num}.{story_num}",
                    "story_key": story_key or f"{epic_num}-{story_num}",
                    "story_file": str(story_file) if story_file else None,
                    "story_title": story_title,
                    "session_id": session_id,
                    "reviewer_count": len(reviews),
                    "git_diff": git_diff,  # Temporarily added for context build.
                    "date": date.today().isoformat(),
                }
            )

            if dv_findings:
                resolved["deep_verify_findings"] = dv_findings
            if security_findings:
                resolved["security_findings"] = security_findings
            if skip_source_files:
                resolved["skip_source_files"] = skip_source_files

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Resolved %d variables", len(resolved))

            # 4. Build synthesis context (reviews + git diff + source + story).
            context_files = self._build_synthesis_context(context, resolved, reviews)

            # 5. Strip pieces already embedded as virtual files so the
            # variables block in the XML envelope doesn't duplicate them.
            resolved.pop("git_diff", None)
            resolved.pop("deep_verify_findings", None)
            resolved.pop("security_findings", None)

            # 6. Mission + template + filtered instructions.
            mission = self._build_synthesis_mission(resolved)
            template_content = load_workflow_template(workflow_ir, context)

            filtered_instructions = filter_instructions(workflow_ir)
            filtered_instructions = substitute_variables(filtered_instructions, resolved)

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Filtered instructions: %d bytes", len(filtered_instructions))

            filtered_vars = filter_garbage_variables(resolved)

            # 7. Generate the XML output.
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

            # 8. Patch post_process + validation (no-op for synthesis without a patch).
            final_xml = apply_post_process(result.xml, context)
            if context.patch_path:
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
                    logger.warning("Failed to load patch for validation: %s", e)

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
        reviews: list[AnonymizedValidation],
    ) -> dict[str, str]:
        """Build context files dict for synthesis.

        Includes (in recency-bias order):

        1. Strategic docs (project-context only by default).
        2. Code antipatterns.
        3. TEA test-review findings.
        4. Optional Deep Verify findings.
        5. Optional Security agent findings.
        6. Anonymized reviews as ``[Reviewer X]`` virtual files.
        7. Git diff (truncated to 20k chars).
        8. Modified source files (capped at 3 for token budget).
        9. Story file (REQUIRED; embedded LAST — closest to instructions).

        Closes with prompt-budget enforcement that may trim trimmable
        sections to fit the configured cap.
        """
        files: dict[str, str] = {}
        source_file_keys: set[str] = set()
        story_key = ""
        project_root = context.project_root
        epic_num = resolved.get("epic_num")
        story_num = resolved.get("story_num")
        git_diff = resolved.get("git_diff", "")

        # 1. Strategic docs (project-context only by default).
        strategic_service = StrategicContextService(context, "code_review_synthesis")
        strategic_files = strategic_service.collect()
        files.update(strategic_files)
        strategic_keys = set(strategic_files)
        logger.debug("Added %d strategic docs to synthesis context", len(strategic_files))

        # 1b. Code antipatterns.
        from bmad_assist.compiler.strategic_context import load_antipatterns

        antipattern_files = load_antipatterns(context, "code", budget_tokens=1000)
        files.update(antipattern_files)
        antipattern_keys = set(antipattern_files)

        # 1c. TEA Context (test-review findings).
        tea_files = collect_tea_context(context, "code_review_synthesis", resolved)
        files.update(tea_files)
        tea_keys = set(tea_files)

        # 1d. Deep Verify findings (if available).
        dv_findings = context.resolved_variables.get("deep_verify_findings")
        if dv_findings:
            dv_content = format_dv_findings_for_prompt(dv_findings)
            files["[Deep Verify Findings]"] = dv_content
            logger.debug("Added Deep Verify findings to synthesis context")

        # 1e. Security agent findings (if available).
        security_findings = context.resolved_variables.get("security_findings")
        if security_findings and isinstance(security_findings, dict):
            sec_parts: list[str] = []
            if security_findings.get("timed_out"):
                sec_parts.append(
                    "SECURITY REVIEW NOT COMPLETED — security analysis timed out, "
                    "manual security review recommended\n"
                )
            findings_list = security_findings.get("findings", [])
            if findings_list:
                for f in findings_list:
                    sec_parts.append(
                        f"- [{f.get('severity', 'UNKNOWN')}] {f.get('cwe_id', 'CWE-?')}: "
                        f"{f.get('title', 'Untitled')} "
                        f"({f.get('file_path', '?')}:{f.get('line_number', '?')}) "
                        f"[confidence={f.get('confidence', '?')}]\n"
                        f"  {f.get('description', '')}"
                    )
                    if f.get("remediation"):
                        sec_parts.append(f"  Remediation: {f['remediation']}")
            if sec_parts:
                files["[Security Findings]"] = "\n".join(sec_parts)
                logger.debug(
                    "Added security findings to synthesis context: %d findings",
                    len(findings_list),
                )

        # 2. Reviews (each as a separate file for clean CDATA handling).
        sorted_reviews = sorted(reviews, key=lambda r: r.validator_id)
        for r in sorted_reviews:
            review_path = f"[{r.validator_id}]"
            files[review_path] = r.content
            logger.debug("Added review to synthesis context: %s", review_path)

        # 3. Git diff (embedded as section). Truncate aggressively.
        max_diff_chars = 20000  # ~5k tokens max for diff
        if git_diff:
            if len(git_diff) > max_diff_chars:
                truncated = git_diff[:max_diff_chars]
                last_nl = truncated.rfind("\n")
                if last_nl > 0:
                    truncated = truncated[:last_nl]
                files["[git-diff]"] = (
                    truncated
                    + "\n\n[... Git diff truncated due to size - see full diff with git command ...]"
                )
                logger.warning(
                    "Git diff truncated for synthesis: %d → %d chars (%.0f%%)",
                    len(git_diff),
                    len(truncated),
                    100 * len(truncated) / len(git_diff),
                )
            else:
                files["[git-diff]"] = git_diff
                logger.debug("Added git diff to synthesis context: %d chars", len(git_diff))

        # 4. Source files (File List + git diff).
        stories_dir = get_stories_dir(context)
        pattern = f"{epic_num}-{story_num}-*.md"
        story_matches = sorted(stories_dir.glob(pattern)) if stories_dir.exists() else []

        file_list_paths: list[str] = []
        if story_matches:
            story_content_for_list = safe_read_file(story_matches[0], project_root)
            if story_content_for_list:
                file_list_paths = extract_file_paths_from_story(story_content_for_list)

        git_diff_files = None
        if git_diff:
            modified_files = extract_modified_files_from_stat(git_diff, skip_docs=True)
            if modified_files:
                git_diff_files = get_git_diff_files(project_root, git_diff)

        skip_source_files = context.resolved_variables.get("skip_source_files", False)
        if not skip_source_files:
            service = SourceContextService(context, "code_review_synthesis")
            source_files = service.collect_files(file_list_paths, git_diff_files)

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

            files.update(source_files)
            source_file_keys.update(source_files)
            if source_files:
                logger.debug("Added %d source files to synthesis context", len(source_files))
        else:
            logger.info("Step 0 compression: skipping source files (base context exceeds limit)")

        # 5. Story file (LAST - REQUIRED).
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

        story_key = str(story_path)
        files[story_key] = story_content
        logger.debug(
            "Added story file to synthesis context: %s (mtime=%d, size=%d bytes)",
            story_path,
            int(stat.st_mtime),
            stat.st_size,
        )

        file_count = len([k for k in files if not k.startswith("[")])
        logger.info(
            "Built synthesis context with %d files, %d reviews%s for story %s.%s",
            file_count,
            len(reviews),
            ", and DV findings" if "[Deep Verify Findings]" in files else "",
            epic_num,
            story_num,
        )

        # 6. Staged prompt budget enforcement.
        from bmad_assist.compiler.budget import ContextSection, PromptBudgetEnforcer

        enforcer = PromptBudgetEnforcer.from_config("code_review_synthesis")
        if enforcer.cap > 0:
            review_keys = {
                key for key in files if key.startswith("[Reviewer ") or key.startswith("[RAW]")
            }
            dv_keys = {key for key in files if key == "[Deep Verify Findings]"}
            security_keys = {key for key in files if key == "[Security Findings]"}
            diff_keys = {key for key in files if key == "[git-diff]"}

            sections = [
                ContextSection(
                    "strategic",
                    {key: files[key] for key in files if key in strategic_keys},
                    priority=1,
                    trimmable=True,
                ),
                ContextSection(
                    "antipatterns",
                    {key: files[key] for key in files if key in antipattern_keys},
                    priority=2,
                    trimmable=True,
                ),
                ContextSection(
                    "tea",
                    {key: files[key] for key in files if key in tea_keys},
                    priority=3,
                    trimmable=True,
                ),
                ContextSection(
                    "deep_verify",
                    {key: files[key] for key in files if key in dv_keys},
                    priority=4,
                    trimmable=True,
                ),
                ContextSection(
                    "security",
                    {key: files[key] for key in files if key in security_keys},
                    priority=5,
                    trimmable=True,
                ),
                ContextSection(
                    "git_diff",
                    {key: files[key] for key in files if key in diff_keys},
                    priority=6,
                    trimmable=True,
                ),
                ContextSection(
                    "source",
                    {key: files[key] for key in files if key in source_file_keys},
                    priority=7,
                    trimmable=True,
                ),
                ContextSection(
                    "reviews",
                    {key: files[key] for key in files if key in review_keys},
                    priority=8,
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
                files = {key: trimmed_files[key] for key in files if key in trimmed_files}

        return files

    @staticmethod
    def _build_synthesis_mission(resolved: dict[str, Any]) -> str:
        """Build the synthesis mission description for code review.

        Emphasizes SOURCE CODE modifications (not story file edits).
        """
        epic_num = resolved.get("epic_num", "?")
        story_num = resolved.get("story_num", "?")
        reviewer_count = resolved.get("reviewer_count", 0)

        return f"""Master Code Review Synthesis: Story {epic_num}.{story_num}

You are synthesizing {reviewer_count} independent code review findings.

Your mission:
1. VERIFY each issue raised by reviewers
   - Cross-reference with project_context.md (ground truth)
   - Cross-reference with git diff and source files
   - Identify false positives (issues that aren't real problems)
   - Confirm valid issues with evidence

2. PRIORITIZE real issues by severity
   - Critical: Security vulnerabilities, data corruption risks
   - High: Bugs, logic errors, missing error handling
   - Medium: Code quality issues, performance concerns
   - Low: Style issues, minor improvements

3. SYNTHESIZE findings
   - Merge duplicate issues from different reviewers
   - Note reviewer consensus (if 3+ agree, high confidence)
   - Highlight unique insights from individual reviewers

4. APPLY source code fixes
   - You have WRITE PERMISSION to modify SOURCE CODE files
   - CRITICAL: Before using Edit tool, ALWAYS Read the target file first
   - Use EXACT content from Read tool output as old_string, NOT content from this prompt
   - If Read output is truncated, use offset/limit parameters to locate the target section
   - Apply fixes for verified issues
   - Do NOT modify the story file (only Dev Agent Record if needed)
   - Document what you changed and why

Output format:
<!-- CODE_REVIEW_SYNTHESIS_START -->
## Synthesis Summary
## Issues Verified (by severity)
## Issues Dismissed (false positives with reasoning)
## Source Code Fixes Applied
<!-- METRICS_JSON_START -->
[valid JSON with "quality" and "consensus" objects]
<!-- METRICS_JSON_END -->
<!-- SYNTHESIS_RESOLUTION_START -->
resolution: [resolved|rework|halt]
verified_critical: [N]
verified_high: [N]
fixed_critical: [N]
fixed_high: [N]
remaining_critical: [N]
remaining_high: [N]
<!-- SYNTHESIS_RESOLUTION_END -->
<!-- CODE_REVIEW_SYNTHESIS_END -->"""


__all__ = [
    "SKILL_ID",
    "BmadCodeReviewSynthesisCompiler",
    "apply_llm_transforms",
]
