"""VALIDATE_STORY_SYNTHESIS phase handler.

Master LLM synthesizes Multi-LLM validation reports.

Story 11.7: Validation Phase Loop Integration
Story 13.6: Synthesizer Schema Integration

This handler:
1. Loads anonymized validations from previous phase (via file cache)
2. Compiles synthesis workflow with validations injected
3. Invokes Master LLM to synthesize findings
4. Master LLM applies changes directly to story file
5. Extracts metrics and saves synthesizer evaluation record (Story 13.6)

The synthesis phase receives anonymized validator outputs and
has write permission to modify the story file.

"""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.types import CompilerContext
from bmad_assist.core.exceptions import ConfigError
from bmad_assist.core.io import get_original_cwd
from bmad_assist.core.loop.handlers.base import BaseHandler, check_for_edit_failures
from bmad_assist.core.loop.synthesis_contract import (
    RESOLUTION_COUNT_FIELDS,
    VALID_RESOLUTIONS,
    ExtractionQuality,
    FailureClass,
    SynthesisDecision,
    extract_story_patches,
    make_synthesis_decision,
    parse_resolution_block,
)
from bmad_assist.core.loop.types import PhaseResult
from bmad_assist.core.paths import get_paths
from bmad_assist.core.state import State
from bmad_assist.core.types import EpicId
from bmad_assist.validation.orchestrator import (
    ValidationError,
    load_validations_for_synthesis,
)
from bmad_assist.validation.reports import (
    extract_synthesis_report,
    save_synthesis_report,
)
from bmad_assist.validation.synthesis_parser import SynthesisMetrics, extract_synthesis_metrics
from bmad_assist.validation.validation_metrics import (
    calculate_aggregate_metrics,
    extract_validator_metrics,
    format_deterministic_metrics_header,
)

logger = logging.getLogger(__name__)

# Markers for structured contract block in validation synthesis output
_CONTRACT_START = "<!-- VALIDATION_CONTRACT_START -->"
_CONTRACT_END = "<!-- VALIDATION_CONTRACT_END -->"

# Bare keywords for detecting partial/malformed contract attempts.
# Either keyword appearing means the LLM attempted a contract block.
_CONTRACT_KW_START = "VALIDATION_CONTRACT_START"
_CONTRACT_KW_END = "VALIDATION_CONTRACT_END"


def _contract_marker_state(text: str) -> Literal["complete", "partial", "none"]:
    """Classify contract marker presence in synthesis output.

    Returns:
        "complete" – both exact start and end markers found
        "partial"  – at least one bare keyword found, but not a complete pair
        "none"     – no contract-related markers at all
    """
    has_exact_start = _CONTRACT_START in text
    has_exact_end = _CONTRACT_END in text
    if has_exact_start and has_exact_end:
        return "complete"
    if _CONTRACT_KW_START in text or _CONTRACT_KW_END in text:
        return "partial"
    return "none"


def _most_repair_worthy_marker_state(
    *states: Literal["complete", "partial", "none"],
) -> Literal["complete", "partial", "none"]:
    """Return the marker state that most strongly indicates repair is needed.

    Repair priority: partial > complete > none.
    - "partial" = malformed markers detected, definitely needs repair.
    - "complete" = markers present but content may be invalid.
    - "none" = no contract attempt detected at all.
    """
    if "partial" in states:
        return "partial"
    if "complete" in states:
        return "complete"
    return "none"


# Regex patterns for layered resolution extraction in validate_story_synthesis.
# (Same logic as code_review_synthesis but applied to validation synthesis output.)
_HEADER_RESOLUTION_RE = re.compile(
    r"^\s*resolution\s*:\s*(resolved|rework|halt)\s*$", re.IGNORECASE | re.MULTILINE
)
_SEMANTIC_RESOLVED_RE = re.compile(
    r"no remaining critical|all critical issues (have been )?fixed|"
    r"all issues (have been )?addressed|validation passed",
    re.IGNORECASE,
)
_SEMANTIC_REWORK_RE = re.compile(
    r"remaining critical issue|recommend rework|requires? rework|needs? rework|"
    r"critical issues? remain|validation failed",
    re.IGNORECASE,
)
_CRITICAL_SECTION_RE = re.compile(
    r"###\s+Critical\s*\n(.*?)(?=\n###|\n##|\Z)", re.DOTALL | re.IGNORECASE
)
_CHANGES_APPLIED_RE = re.compile(
    r"##\s+Changes Applied\s*\n(.*?)(?=\n##|\Z)", re.DOTALL | re.IGNORECASE
)
_ISSUE_LINE_RE = re.compile(r"^\s*-\s+\*\*", re.MULTILINE)

# For repair pass: extract Synthesis Summary section
_SUMMARY_SECTION_RE = re.compile(
    r"(## Synthesis Summary\s*\n.*?)(?=\n##|\Z)", re.DOTALL | re.IGNORECASE
)

_REPAIR_PROMPT_TEMPLATE = """\
You previously produced a validation synthesis report. The structured contract \
and/or metrics blocks were missing or malformed. Re-emit ONLY these two blocks \
based on your analysis below. Do not include any prose, patches, or other content.

<!-- VALIDATION_CONTRACT_START -->
resolution: {{resolved|rework|halt}}
verified_critical: {{N}}
verified_high: {{N}}
fixed_critical: {{N}}
fixed_high: {{N}}
remaining_critical: {{N}}
remaining_high: {{N}}
<!-- VALIDATION_CONTRACT_END -->

<!-- METRICS_JSON_START -->
{{"quality": {{"actionable_ratio": 0.0, "specificity_score": 0.0, "evidence_quality": 0.0, "follows_template": true, "internal_consistency": 0.0}}, "consensus": {{"agreed_findings": 0, "unique_findings": 0, "disputed_findings": 0, "missed_findings": 0, "agreement_score": 0.0, "false_positive_count": 0}}}}
<!-- METRICS_JSON_END -->

Here is the relevant context from your synthesis report:

{context}
"""


def _build_repair_context(extracted_synthesis: str, raw_stdout: str = "") -> str:
    """Build targeted context excerpt for the repair prompt.

    Extracts:
    1. Raw contract block if markers present (from raw_stdout preferentially,
       since extract_synthesis_report() may strip malformed markers)
    2. Synthesis Summary section if identifiable
    3. First ~1500 chars of prose as general context
    """
    parts: list[str] = []

    # 1. Raw contract block if present (exact or partial markers).
    # Prefer raw_stdout as the marker source: extract_synthesis_report()
    # can strip malformed markers that are still visible in raw output.
    marker_source = raw_stdout if raw_stdout else extracted_synthesis
    marker_state = _contract_marker_state(marker_source)
    if marker_state == "complete":
        start_idx = marker_source.find(_CONTRACT_START)
        end_idx = marker_source.find(_CONTRACT_END, start_idx)
        block = marker_source[start_idx : end_idx + len(_CONTRACT_END)]
        parts.append(f"[Original contract block]\n{block}")
    elif marker_state == "partial":
        # Find the first occurrence of either bare keyword
        kw_start = marker_source.find(_CONTRACT_KW_START)
        kw_end = marker_source.find(_CONTRACT_KW_END)
        # Use whichever appears first (or only one present)
        candidates = [i for i in (kw_start, kw_end) if i != -1]
        kw_idx = min(candidates)
        partial_block = marker_source[kw_idx : kw_idx + 500]
        # Label based on which keyword was found at kw_idx
        label = "partial start" if kw_idx == kw_start else "partial end"
        parts.append(f"[Malformed contract block ({label})]\n{partial_block}")

    # 2. Synthesis Summary section
    summary_match = _SUMMARY_SECTION_RE.search(extracted_synthesis)
    if summary_match:
        parts.append(f"[Summary]\n{summary_match.group(1).strip()}")

    # 3. General prose context
    parts.append(f"[Prose excerpt]\n{extracted_synthesis[:1500]}")

    return "\n\n".join(parts)


def _infer_resolution_from_structure(stdout: str) -> str | None:
    """Infer resolution from heading-based synthesis structure."""
    if not stdout.strip():
        return None

    critical_match = _CRITICAL_SECTION_RE.search(stdout)
    changes_match = _CHANGES_APPLIED_RE.search(stdout)

    if not critical_match and not changes_match:
        return None

    if not critical_match:
        return "resolved"

    critical_body = critical_match.group(1).strip()
    has_critical_issues = bool(_ISSUE_LINE_RE.search(critical_body))
    if not has_critical_issues:
        return "resolved"

    if not changes_match:
        return "rework"

    changes_body = changes_match.group(1).strip()
    if not changes_body:
        return "rework"

    if re.fullmatch(r"\[.*?\]", changes_body, re.DOTALL):
        return None

    return "resolved"


def _extract_validation_resolution(
    stdout: str,
) -> tuple[dict[str, Any] | None, ExtractionQuality]:
    """Extract resolution from validation synthesis output using layered strategy.

    Layer 0: VALIDATION_CONTRACT_START/END markers (STRICT).
             If markers found but block is invalid, return FAILED (fail-closed).
    Layer 1: bare "resolution: X" key-value line (DEGRADED).
    Layer 2: semantic keyword signals (DEGRADED).
    Layer 3: structural inference from heading-based synthesis sections (DEGRADED).

    Returns:
        (parsed_dict_or_None, ExtractionQuality)
    """
    # Layer 0: VALIDATION_CONTRACT_START/END markers (STRICT)
    start_idx = stdout.find(_CONTRACT_START)
    if start_idx != -1:
        end_idx = stdout.find(_CONTRACT_END, start_idx + len(_CONTRACT_START))
        if end_idx != -1:
            block = stdout[start_idx + len(_CONTRACT_START) : end_idx].strip()
            if block:
                parsed = parse_resolution_block(block)
                if parsed is not None:
                    logger.info(
                        "Validation synthesis resolution extracted via contract markers: %s (STRICT)",
                        parsed.get("resolution"),
                    )
                    return parsed, ExtractionQuality.STRICT
            # Markers found but block is empty or invalid — fail-closed.
            # Do NOT fall through to weaker layers when markers are present.
            if not block:
                logger.warning(
                    "VALIDATION_CONTRACT markers found but block is EMPTY; "
                    "treating as FAILED (not falling through to header fallback)"
                )
            else:
                # Diagnose what went wrong: parse raw key:value pairs for logging
                expected_keys = {"resolution"} | set(RESOLUTION_COUNT_FIELDS)
                raw_keys: dict[str, str] = {}
                for line in block.splitlines():
                    line = line.strip()
                    if not line or ":" not in line:
                        continue
                    k, _, v = line.partition(":")
                    raw_keys[k.strip()] = v.strip()
                actual_keys = set(raw_keys.keys())
                extra_keys = sorted(actual_keys - expected_keys)
                missing_keys = sorted(expected_keys - actual_keys)
                raw_resolution = raw_keys.get("resolution")
                resolution_valid = raw_resolution in VALID_RESOLUTIONS
                logger.warning(
                    "VALIDATION_CONTRACT markers found but block is INVALID; "
                    "treating as FAILED. Diagnostics: resolution=%r (valid=%s), "
                    "missing_keys=%s, extra_keys=%s, raw_block=%.300s",
                    raw_resolution,
                    resolution_valid,
                    missing_keys or "none",
                    extra_keys or "none",
                    block[:300],
                )
            return None, ExtractionQuality.FAILED

    # Layer 1: bare "resolution: X" key-value line (DEGRADED)
    res_match = _HEADER_RESOLUTION_RE.search(stdout)
    if res_match:
        resolution_str = res_match.group(1).lower()
        logger.info(
            "Validation synthesis resolution extracted via header: %s", resolution_str
        )
        return {"resolution": resolution_str}, ExtractionQuality.DEGRADED

    if _SEMANTIC_REWORK_RE.search(stdout):
        logger.info("Validation synthesis resolution inferred via semantic fallback: rework")
        return {"resolution": "rework"}, ExtractionQuality.DEGRADED

    if _SEMANTIC_RESOLVED_RE.search(stdout):
        logger.info("Validation synthesis resolution inferred via semantic fallback: resolved")
        return {"resolution": "resolved"}, ExtractionQuality.DEGRADED

    structural = _infer_resolution_from_structure(stdout)
    if structural:
        logger.info(
            "Validation synthesis resolution inferred via structural fallback: %s",
            structural,
        )
        return {"resolution": structural}, ExtractionQuality.DEGRADED

    logger.warning(
        "Validation synthesis: all extraction layers failed "
        "(stdout_len=%d, preview=%.200s)",
        len(stdout),
        stdout[:200] if stdout else "(empty)",
    )
    return None, ExtractionQuality.FAILED


def _apply_story_patches(story_path: Path, stdout: str) -> int:
    """Apply STORY_PATCH blocks from LLM stdout to the story file in a single write.

    Extraction uses extract_story_patches() from synthesis_contract.
    Replacement uses heading-boundary scan (finds heading → next same/higher heading).
    Fails closed on ambiguity: if any heading is missing or duplicated, no patches
    are applied and 0 is returned (caller treats this as extraction failure).

    Args:
        story_path: Absolute path to the story Markdown file.
        stdout: Raw LLM output containing STORY_PATCH_START/END blocks.

    Returns:
        Number of patches applied (0 = nothing written).
    """
    patches = extract_story_patches(stdout)
    if not patches:
        return 0

    try:
        content = story_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.error("Cannot read story file for patch application: %s", e)
        return 0

    lines = content.splitlines(keepends=True)

    def _heading_level(line: str) -> int:
        """Return Markdown heading level (1-6) or 0 if not a heading."""
        stripped = line.lstrip()
        if not stripped.startswith("#"):
            return 0
        count = len(stripped) - len(stripped.lstrip("#"))
        if count > 6:
            return 0
        if len(stripped) > count and stripped[count] not in (" ", "\t"):
            return 0
        return count

    def _find_heading_bounds(target_heading: str) -> tuple[int, int] | None:
        """Find (start_line_idx, end_line_idx_exclusive) for a heading section.

        Returns None if heading not found or is ambiguous (found more than once).
        """
        target_normalized = target_heading.lower().strip()
        found_indices: list[int] = []
        for idx, line in enumerate(lines):
            stripped = line.strip().lower()
            if stripped == target_normalized:
                found_indices.append(idx)

        if len(found_indices) == 0:
            logger.warning(
                "Patch heading not found in story file: %r", target_heading
            )
            return None
        if len(found_indices) > 1:
            logger.warning(
                "Patch heading is ambiguous (appears %d times): %r",
                len(found_indices),
                target_heading,
            )
            return None

        start_idx = found_indices[0]
        start_level = _heading_level(lines[start_idx])
        if start_level == 0:
            logger.warning(
                "Found heading text but line is not a Markdown heading: %r",
                lines[start_idx].rstrip(),
            )
            return None

        # Find end: next line with heading level <= start_level, or EOF
        end_idx = len(lines)
        for idx in range(start_idx + 1, len(lines)):
            lvl = _heading_level(lines[idx])
            if lvl > 0 and lvl <= start_level:
                end_idx = idx
                break

        return start_idx, end_idx

    # Validate ALL patches before touching the file (fail-closed)
    bounds: list[tuple[int, int]] = []
    for patch in patches:
        result = _find_heading_bounds(patch.heading)
        if result is None:
            logger.error(
                "Patch application aborted: cannot resolve heading %r — "
                "no patches applied to preserve story integrity",
                patch.heading,
            )
            return 0
        bounds.append(result)

    # Apply patches in reverse order so earlier line numbers stay valid
    working_lines = list(lines)
    patch_replacement_pairs = list(zip(patches, bounds))
    patch_replacement_pairs.sort(key=lambda x: x[1][0], reverse=True)

    for patch, (start_idx, end_idx) in patch_replacement_pairs:
        replacement_lines = [
            line if line.endswith("\n") else line + "\n"
            for line in patch.content.splitlines()
        ]
        # Ensure trailing newline separation
        if replacement_lines and not replacement_lines[-1].endswith("\n"):
            replacement_lines[-1] += "\n"
        working_lines[start_idx:end_idx] = replacement_lines

    merged = "".join(working_lines)
    story_path.write_text(merged, encoding="utf-8")
    logger.info(
        "Applied %d story patch(es) to %s in a single write",
        len(patches),
        story_path.name,
    )
    return len(patches)


class ValidateStorySynthesisHandler(BaseHandler):
    """Handler for VALIDATE_STORY_SYNTHESIS phase.

    Invokes Master LLM to synthesize validation reports from
    multiple validators. Uses the validate-story-synthesis
    workflow compiler.

    """

    @property
    def phase_name(self) -> str:
        """Returns the name of the phase."""
        return "validate_story_synthesis"

    def build_context(self, state: State) -> dict[str, Any]:
        """Build context for validate_story_synthesis prompt template.

        Available variables: epic_num, story_num, story_id, project_path

        """
        return self._build_common_context(state)

    def _get_session_id_from_state(self, state: State) -> str | None:
        """Retrieve session_id from state.

        The session_id is saved to state file after validation phase.
        Falls back to reading from cache file if not in state.

        Args:
            state: Current loop state.

        Returns:
            Session ID string or None if not found.

        """
        # TODO: In future, session_id could be stored in state file
        # For now, we search for the most recent validations cache file
        cache_dir = self.project_path / ".bmad-assist" / "cache"
        if not cache_dir.exists():
            return None

        # Find most recent validations file
        # Use safe_mtime to handle TOCTOU race (file may be deleted between glob and stat)
        def safe_mtime(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except (OSError, FileNotFoundError):
                return 0.0  # Treat missing files as oldest

        validation_files = sorted(
            cache_dir.glob("validations-*.json"),
            key=safe_mtime,
            reverse=True,
        )

        if not validation_files:
            return None

        # Extract session_id from filename
        latest_file = validation_files[0]
        # Filename format: validations-{session_id}.json
        # Use removeprefix for safer string manipulation
        session_id = latest_file.stem.removeprefix("validations-")

        logger.debug("Found latest validation session: %s", session_id)
        return session_id

    def render_prompt(self, state: State) -> str:
        """Render synthesis prompt with validation data.

        Overrides base render_prompt to use synthesis compiler
        with validations injected.

        Args:
            state: Current loop state.

        Returns:
            Compiled prompt XML with validations.

        """
        # Get story info
        epic_num = state.current_epic
        story_num_str = self._extract_story_num(state.current_story)

        if epic_num is None or story_num_str is None:
            raise ConfigError("Cannot synthesize: missing epic_num or story_num in state")

        story_num = int(story_num_str)

        # Get session_id for loading validations
        session_id = self._get_session_id_from_state(state)
        if session_id is None:
            raise ConfigError(
                "Cannot synthesize: no validation session found. Run VALIDATE_STORY phase first."
            )

        # Load anonymized validations from cache
        # Story 22.8 AC#4: Unpack tuple with failed_validators
        # TIER 2: Also loads pre-calculated evidence_score
        # Story 26.16: Also loads Deep Verify result
        try:
            anonymized_validations, _, _evidence_score, dv_data = load_validations_for_synthesis(
                session_id,
                self.project_path,
            )
        except ValidationError as e:
            raise ConfigError(f"Cannot load validations: {e}") from e

        if not anonymized_validations:
            raise ConfigError("No validations found for synthesis. Run VALIDATE_STORY phase first.")

        logger.info(
            "Compiling synthesis for story %s.%s with %d validations",
            epic_num,
            story_num,
            len(anonymized_validations),
        )

        # === Adaptive Synthesis Prompt Compression Pipeline ===
        import math
        import time

        from bmad_assist.core.loop.handlers.synthesis_utils import (
            decide_compression_steps,
            estimate_base_context_tokens,
            estimate_synthesis_tokens,
            pre_extract_reviews,
            progressive_synthesize,
            resolve_synthesis_budget_limits,
        )
        from bmad_assist.core.retry import invoke_with_timeout_retry
        from bmad_assist.providers.registry import get_provider

        synthesis_config = self.config.compiler.synthesis
        budget_limits = resolve_synthesis_budget_limits(
            self.config, "validate_story_synthesis"
        )
        effective_budget = budget_limits.effective_budget
        base_tokens = estimate_base_context_tokens(
            self.project_path, self.config, "validate_story_synthesis"
        )
        total_tokens = estimate_synthesis_tokens(
            anonymized_validations, base_tokens, synthesis_config.safety_factor
        )
        steps = decide_compression_steps(
            total_tokens,
            base_tokens,
            effective_budget,
            synthesis_config.base_context_limit,
        )

        skip_source_files = False
        compression_start = time.monotonic()
        original_token_estimate = total_tokens
        extraction_llm_calls = 0
        validations_to_use = anonymized_validations

        if steps:
            logger.info(
                "Compression pipeline: steps=%s, total=%d, budget=%d (synthesis=%d, prompt_cap=%d), base=%d",
                steps,
                total_tokens,
                effective_budget,
                budget_limits.synthesis_budget,
                budget_limits.prompt_cap,
                base_tokens,
            )

            if "step0" in steps:
                skip_source_files = True
                base_tokens = max(base_tokens - 5000, 0)
                total_tokens = estimate_synthesis_tokens(
                    anonymized_validations, base_tokens, synthesis_config.safety_factor
                )
                logger.info("Step 0: skip_source_files, revised total=%d", total_tokens)

            if "step1" in steps:
                # Provider resolution: extraction_provider > helper > master
                if synthesis_config.extraction_provider:
                    ext_provider = get_provider(synthesis_config.extraction_provider)
                    ext_model = synthesis_config.extraction_model or (
                        self.config.providers.helper.model
                        if self.config.providers.helper
                        else self.config.providers.master.model
                    )
                elif self.config.providers.helper:
                    ext_provider = get_provider(self.config.providers.helper.provider)
                    ext_model = (
                        synthesis_config.extraction_model or self.config.providers.helper.model
                    )
                else:
                    ext_provider = get_provider(self.config.providers.master.provider)
                    ext_model = (
                        synthesis_config.extraction_model or self.config.providers.master.model
                    )

                expected_calls = (
                    math.ceil(len(anonymized_validations) / synthesis_config.extraction_batch_size)
                    + 2
                )
                per_call_timeout = max(
                    synthesis_config.max_compression_timeout // max(expected_calls, 1),
                    30,
                )

                def invoke_fn(prompt: str) -> str:
                    res = invoke_with_timeout_retry(
                        ext_provider.invoke,
                        timeout_retries=1,
                        phase_name=f"{self.phase_name}_extraction",
                        prompt=prompt,
                        model=ext_model,
                        timeout=per_call_timeout,
                        disable_tools=True,
                        cwd=self.project_path,
                    )
                    if res.exit_code != 0:
                        raise RuntimeError(
                            f"Extraction failed: {res.stderr[:200] if res.stderr else 'unknown'}"
                        )
                    return res.stdout

                cache_dir = self.project_path / ".bmad-assist" / "cache"
                validations_to_use = pre_extract_reviews(
                    reviews=anonymized_validations,
                    batch_size=synthesis_config.extraction_batch_size,
                    base_context_summary=f"Project at {self.project_path.name}",
                    invoke_fn=invoke_fn,
                    log=logger,
                    cache_dir=cache_dir,
                    session_id=session_id,
                )
                extraction_llm_calls = math.ceil(
                    len(anonymized_validations) / synthesis_config.extraction_batch_size
                )

                total_tokens = estimate_synthesis_tokens(
                    validations_to_use, base_tokens, synthesis_config.safety_factor
                )
                logger.info(
                    "Step 1: %d validations in %d batches, revised total=%d",
                    len(validations_to_use),
                    extraction_llm_calls,
                    total_tokens,
                )

                elapsed = time.monotonic() - compression_start
                if elapsed > synthesis_config.max_compression_timeout:
                    logger.warning(
                        "Compression timeout after Step 1 (%.1fs > %ds)",
                        elapsed,
                        synthesis_config.max_compression_timeout,
                    )
                elif total_tokens > effective_budget:
                    validations_to_use = progressive_synthesize(
                        extracted_reviews=validations_to_use,
                        batch_size=synthesis_config.progressive_batch_size,
                        base_context_summary=f"Project at {self.project_path.name}",
                        token_budget=effective_budget,
                        invoke_fn=invoke_fn,
                        log=logger,
                        cache_dir=cache_dir,
                        session_id=session_id,
                    )
                    prog_calls = (
                        math.ceil(
                            len(anonymized_validations) / synthesis_config.progressive_batch_size
                        )
                        + 1
                    )
                    extraction_llm_calls += prog_calls
                    total_tokens = estimate_synthesis_tokens(
                        validations_to_use, base_tokens, synthesis_config.safety_factor
                    )
                    logger.info("Step 2: progressive synthesis, final=%d", total_tokens)
        else:
            logger.info(
                "Compression: passthrough (total=%d <= budget=%d)",
                total_tokens,
                effective_budget,
            )

        compression_end = time.monotonic()
        self._compressed_reviews = validations_to_use
        self._compression_metrics: dict[str, object] = {
            "compression_steps_applied": steps,
            "original_token_estimate": original_token_estimate,
            "compressed_token_estimate": total_tokens,
            "extraction_llm_calls": extraction_llm_calls,
            "extraction_duration_ms": int((compression_end - compression_start) * 1000),
        }

        # Get configured paths
        paths = get_paths()

        # Build compiler context with (possibly compressed) validations
        # Use get_original_cwd() to preserve original CWD when running as subprocess
        # Story 26.16: Include Deep Verify findings in synthesis context
        resolved_vars: dict[str, Any] = {
            "epic_num": epic_num,
            "story_num": story_num,
            "session_id": session_id,
            "anonymized_validations": validations_to_use,
            "skip_source_files": skip_source_files,
        }

        # Add DV findings to synthesis context if available
        if dv_data is not None:
            from bmad_assist.deep_verify.core.types import serialize_validation_result

            resolved_vars["deep_verify_findings"] = serialize_validation_result(dv_data)
            logger.debug(
                "Including Deep Verify findings in synthesis: verdict=%s, findings=%d",
                dv_data.verdict.value,
                len(dv_data.findings),
            )

        context = CompilerContext(
            project_root=self.project_path,
            output_folder=paths.implementation_artifacts,
            project_knowledge=paths.project_knowledge,
            cwd=get_original_cwd(),
            resolved_variables=resolved_vars,
        )

        # Compile synthesis workflow
        compiled = compile_workflow("validate-story-synthesis", context)

        logger.info(
            "Synthesis prompt compiled: ~%d tokens",
            compiled.token_estimate,
        )

        return compiled.context

    def execute(self, state: State) -> PhaseResult:
        """Execute synthesis phase.

        Compiles synthesis workflow with validations and invokes
        Master LLM to synthesize findings and apply changes.

        After successful synthesis, extracts metrics and saves synthesizer
        evaluation record (Story 13.6).

        Args:
            state: Current loop state.

        Returns:
            PhaseResult with synthesis output.

        """
        from bmad_assist.core.io import save_prompt

        try:
            # Get story info for report saving
            epic_num = state.current_epic
            story_num_str = self._extract_story_num(state.current_story)

            if epic_num is None or story_num_str is None:
                raise ConfigError("Cannot synthesize: missing epic_num or story_num in state")

            story_num = int(story_num_str)

            # Get session_id and load validations for report saving
            session_id = self._get_session_id_from_state(state)
            if session_id is None:
                raise ConfigError(
                    "Cannot synthesize: no validation session found. "
                    "Run VALIDATE_STORY phase first."
                )

            # Story 22.8 AC#4: Unpack tuple with failed_validators
            # TIER 2: Also loads pre-calculated evidence_score for synthesis context
            # Story 26.16: Also loads Deep Verify result
            anonymized_validations, failed_validators, evidence_score_data, _dv_data = (
                load_validations_for_synthesis(  # noqa: E501
                    session_id,
                    self.project_path,
                )
            )
            validators_used = [v.validator_id for v in anonymized_validations]

            # Render prompt with validations
            prompt = self.render_prompt(state)

            # Save prompt to .bmad-assist/prompts/ (atomic write, always saved)
            save_prompt(self.project_path, epic_num, story_num, self.phase_name, prompt)

            # Record start time for benchmarking
            start_time = datetime.now(UTC)

            # Invoke Master LLM with Read-only tools (one-write patch model:
            # story changes are expressed as STORY_PATCH blocks in stdout, not
            # direct edits, so Edit/Write are excluded to prevent ToolCallGuard
            # triggering on repeated same-file edits)
            result = self.invoke_provider(prompt, allowed_tools=["Read"])

            # Record end time for benchmarking
            end_time = datetime.now(UTC)

            # Derive evidence verdict for SynthesisDecision fallback
            evidence_verdict: str = (
                evidence_score_data.get("verdict", "UNKNOWN")
                if evidence_score_data
                else "UNKNOWN"
            )

            # Check for errors
            if result.exit_code != 0:
                # Classify ToolCallGuard terminations as RETRYABLE
                is_guard_termination = bool(
                    result.termination_reason
                    and result.termination_reason.startswith("guard:")
                )
                if is_guard_termination:
                    logger.warning(
                        "Validation synthesis terminated by ToolCallGuard: %s — "
                        "classifying as RETRYABLE",
                        result.termination_reason,
                    )
                    return PhaseResult.ok(
                        {
                            "response": result.stdout or "",
                            "model": result.model,
                            "duration_ms": result.duration_ms,
                            "resolution": "halt",
                            "extraction_quality": ExtractionQuality.FAILED.value,
                            "failure_class": FailureClass.RETRYABLE.value,
                        }
                    )
                error_msg = result.stderr or f"Master LLM exited with code {result.exit_code}"
                logger.warning(
                    "Synthesis failed: exit_code=%d, stderr=%s",
                    result.exit_code,
                    result.stderr[:500] if result.stderr else "(empty)",
                )
                phase_result = PhaseResult.fail(error_msg)
            else:
                # Success - save synthesis report
                logger.info(
                    "Synthesis complete: %d chars output",
                    len(result.stdout),
                )

                # Story 22.4 AC5: Check for Edit tool failures (best-effort logging)
                check_for_edit_failures(result.stdout, target_hint="story file")

                # Apply one-write patch model: extract STORY_PATCH blocks from LLM output
                # and apply them in a single write to the story file.
                story_path = self._get_story_path(state)
                if story_path is not None and story_path.exists():
                    patch_count = _apply_story_patches(story_path, result.stdout)
                    if patch_count == 0 and not result.stdout:
                        logger.warning(
                            "No story patches found in synthesis output and output is empty"
                        )
                    elif patch_count == 0:
                        logger.info(
                            "No STORY_PATCH blocks found in synthesis output "
                            "(LLM may have used direct edits or produced no story updates)"
                        )
                    else:
                        logger.info("Applied %d story patch(es) via one-write model", patch_count)

                # Extract synthesis report using priority-based extraction
                # 1. Markers, 2. Summary header, 3. Full content
                extracted_synthesis = extract_synthesis_report(
                    result.stdout, synthesis_type="validation"
                )

                # Guard against silent provider failure: if provider returns
                # exit_code=0 but empty/minimal output, synthesis is useless.
                # A real synthesis has thousands of chars (issues, verdicts, changes).
                min_synthesis_chars = 200
                if len(extracted_synthesis.strip()) < min_synthesis_chars:
                    logger.error(
                        "Synthesis output too short (%d chars, min %d). "
                        "Provider returned exit_code=0 but produced no meaningful synthesis. "
                        "Raw stdout (%d chars): %.500s",
                        len(extracted_synthesis.strip()),
                        min_synthesis_chars,
                        len(result.stdout),
                        result.stdout[:500] if result.stdout else "(empty)",
                    )
                    return PhaseResult.fail(
                        f"Synthesis failed: provider returned empty/minimal output "
                        f"({len(extracted_synthesis.strip())} chars, "
                        f"duration={result.duration_ms}ms). "
                        f"Check provider config and model availability."
                    )

                # Extract deterministic metrics from validation reports
                deterministic_header = self._extract_deterministic_metrics(anonymized_validations)

                # Prepend deterministic metrics to extracted synthesis
                synthesis_content = deterministic_header + extracted_synthesis

                # Save synthesis report with YAML frontmatter
                paths = get_paths()
                validations_dir = paths.validations_dir
                validations_dir.mkdir(parents=True, exist_ok=True)

                master_validator_id = f"master-{self.get_model()}"
                # Story 22.8 AC#2, AC#4: Pass failed_validators to synthesis report
                save_synthesis_report(
                    content=synthesis_content,
                    master_validator_id=master_validator_id,
                    session_id=session_id,
                    validators_used=validators_used,
                    epic=epic_num,
                    story=story_num,
                    duration_ms=result.duration_ms or 0,
                    validations_dir=validations_dir,
                    failed_validators=failed_validators,
                )

                # Extract antipatterns for create-story (best-effort, non-blocking)
                try:
                    from bmad_assist.antipatterns import extract_and_append_antipatterns

                    extract_and_append_antipatterns(
                        synthesis_content=synthesis_content,
                        epic_id=epic_num,
                        story_id=f"{epic_num}-{story_num}",
                        antipattern_type="story",
                        project_path=self.project_path,
                        config=self.config,
                    )
                except Exception as e:
                    logger.warning("Antipatterns extraction failed (non-blocking): %s", e)

                # Extract synthesis resolution via layered strategy
                res_parsed, res_quality = _extract_validation_resolution(extracted_synthesis)
                _fallback_provider, _fallback_model = self._resolve_metrics_fallback_config()
                metrics = extract_synthesis_metrics(
                    result.stdout,
                    llm_fallback=True,
                    provider_name=_fallback_provider,
                    model=_fallback_model,
                )

                # Phase 1.5: Contract repair if main synthesis was substantial
                # but contract/metrics extraction failed
                marker_state_raw = _contract_marker_state(result.stdout)
                marker_state_extracted = _contract_marker_state(extracted_synthesis)
                # Use the signal most likely to trigger needed repair.
                # If raw stdout has partial markers that extraction stripped,
                # we still need repair.
                marker_state = _most_repair_worthy_marker_state(
                    marker_state_raw, marker_state_extracted,
                )
                needs_contract_repair = (
                    # complete markers but extraction was not STRICT (malformed block content)
                    (marker_state == "complete" and res_quality != ExtractionQuality.STRICT)
                    # partial markers (malformed/truncated) — repair proactively
                    or marker_state == "partial"
                    # no markers at all and extraction failed entirely
                    or (marker_state == "none" and res_quality == ExtractionQuality.FAILED and res_parsed is None)
                )
                needs_metrics_repair = metrics is None
                needs_repair = (
                    (needs_contract_repair or needs_metrics_repair)
                    and len(extracted_synthesis.strip()) >= 200
                )

                used_repaired_contract = False
                used_repaired_metrics = False
                if needs_repair:
                    logger.info(
                        "Attempting contract repair pass: "
                        "contract_repair=%s metrics_repair=%s",
                        needs_contract_repair,
                        needs_metrics_repair,
                    )
                    repair_parsed, repair_quality, repair_metrics = (
                        self._attempt_contract_repair(
                            extracted_synthesis, raw_stdout=result.stdout,
                        )
                    )
                    if needs_contract_repair and repair_parsed is not None:
                        res_parsed = repair_parsed
                        res_quality = repair_quality
                        used_repaired_contract = True
                        logger.info(
                            "Contract repair succeeded: resolution=%s quality=%s",
                            res_parsed.get("resolution"),
                            res_quality.value,
                        )
                    if needs_metrics_repair and repair_metrics is not None:
                        metrics = repair_metrics
                        used_repaired_metrics = True
                        logger.info("Metrics repair succeeded")

                synthesis_decision: SynthesisDecision = make_synthesis_decision(
                    res_parsed, res_quality, evidence_verdict, evidence_score_data
                )
                logger.info(
                    "Validation synthesis decision: resolution=%s quality=%s",
                    synthesis_decision.resolution.value,
                    synthesis_decision.extraction_quality.value,
                )

                # Story 13.6: Save synthesizer record AFTER repair + decision
                # so the persisted record reflects post-repair truth.
                estimated_output_tokens = len(result.stdout) // 4 if result.stdout else 0
                synth_record_custom: dict[str, object] = {
                    "final_extraction_quality": res_quality.value,
                    "final_resolution": synthesis_decision.resolution.value,
                }
                if needs_repair:
                    synth_record_custom["repaired_contract"] = used_repaired_contract
                    synth_record_custom["repaired_metrics"] = used_repaired_metrics
                self._save_synthesizer_record(
                    synthesis_output=result.stdout,
                    epic_num=epic_num,
                    story_num=story_num,
                    story_title=state.current_story or "",
                    start_time=start_time,
                    end_time=end_time,
                    input_tokens=0,  # Not available from current provider result
                    output_tokens=estimated_output_tokens,
                    validator_count=len(validators_used),
                    metrics=metrics,
                    record_custom=synth_record_custom,
                )

                phase_result = PhaseResult.ok(
                    {
                        "response": result.stdout,
                        "model": result.model,
                        "duration_ms": result.duration_ms,
                        "resolution": synthesis_decision.resolution.value,
                        "extraction_quality": synthesis_decision.extraction_quality.value,
                        "failure_class": (
                            synthesis_decision.failure_class.value
                            if synthesis_decision.failure_class
                            else None
                        ),
                        **self._timing_outputs(),
                    }
                )

            return phase_result

        except ConfigError as e:
            logger.error("Synthesis config error: %s", e)
            return PhaseResult.fail(str(e))

        except Exception as e:
            logger.error("Synthesis handler failed: %s", e, exc_info=True)
            return PhaseResult.fail(f"Synthesis failed: {e}")

    def _get_story_path(self, state: State) -> Path | None:
        """Return the absolute path to the current story Markdown file, or None."""
        from bmad_assist.core.paths import get_paths

        paths = get_paths()
        stories_dir = paths.implementation_artifacts
        if state.current_story is None:
            return None
        story_slug = state.current_story.replace(".", "-")
        # Convention: story-{epic}-{story}.md
        candidates = list(stories_dir.glob(f"story-{story_slug}.md"))
        if not candidates:
            # Broader search
            candidates = list(stories_dir.glob(f"*{story_slug}*.md"))
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            logger.warning(
                "Multiple story files matched %r: %s — skipping patch application",
                story_slug,
                [str(p) for p in candidates[:3]],
            )
            return None
        logger.warning("Story file not found for %r — skipping patch application", story_slug)
        return None

    def _resolve_metrics_fallback_config(self) -> tuple[str | None, str | None]:
        """Resolve provider name and model for LLM metrics extraction fallback.

        Resolution order: extraction_provider > helper > master.
        Returns (provider_name, model) tuple; either may be None if unconfigured.
        """
        synthesis_config = self.config.compiler.synthesis
        if synthesis_config.extraction_provider:
            provider_name = synthesis_config.extraction_provider
            model = synthesis_config.extraction_model or (
                self.config.providers.helper.model
                if self.config.providers.helper
                else self.config.providers.master.model
            )
        elif self.config.providers.helper:
            provider_name = self.config.providers.helper.provider
            model = synthesis_config.extraction_model or self.config.providers.helper.model
        else:
            provider_name = self.config.providers.master.provider
            model = synthesis_config.extraction_model or self.config.providers.master.model
        return provider_name, model

    def _extract_deterministic_metrics(
        self,
        anonymized_validations: list[Any],
    ) -> str:
        """Extract deterministic metrics from validation reports.

        Parses each validation report to extract scores, issue counts,
        and other metrics that can be calculated without LLM judgment.

        Args:
            anonymized_validations: List of AnonymizedValidation objects.

        Returns:
            Formatted markdown header with deterministic metrics.
            Returns empty string if extraction fails.

        """
        try:
            # Extract metrics from each validation
            validator_metrics = [
                extract_validator_metrics(v.content, v.validator_id) for v in anonymized_validations
            ]

            # Calculate aggregate metrics
            aggregate = calculate_aggregate_metrics(validator_metrics)

            # Format as markdown header
            header = format_deterministic_metrics_header(aggregate)

            logger.info(
                "Extracted deterministic metrics: %d validators, avg evidence score %.1f",
                aggregate.validator_count,
                aggregate.evidence_score_avg or 0,
            )

            return header

        except Exception as e:
            logger.warning(
                "Failed to extract deterministic metrics: %s",
                e,
                exc_info=True,
            )
            return ""

    def _attempt_contract_repair(
        self,
        extracted_synthesis: str,
        raw_stdout: str = "",
    ) -> tuple[dict[str, Any] | None, ExtractionQuality, "SynthesisMetrics | None"]:
        """Attempt a short follow-up LLM call to recover contract + metrics blocks.

        Only called when:
        - Main synthesis produced meaningful output (>= 200 chars)
        - Contract block is invalid OR metrics block is missing/invalid

        Returns:
            (parsed_resolution, extraction_quality, metrics) — any may be None on failure.
        """
        repair_context = _build_repair_context(extracted_synthesis, raw_stdout=raw_stdout)
        repair_prompt = _REPAIR_PROMPT_TEMPLATE.format(context=repair_context)

        logger.info("Contract repair pass: invoking provider (no tools, single attempt)")
        try:
            repair_result = self.invoke_provider(
                repair_prompt,
                retry_timeout_minutes=2,
                retry_delay=10,
                allowed_tools=[],
            )
        except Exception as e:
            logger.warning("Contract repair pass: provider call failed: %s", e)
            return None, ExtractionQuality.FAILED, None

        if repair_result.exit_code != 0 or not repair_result.stdout:
            logger.warning(
                "Contract repair pass: provider returned exit_code=%s, stdout_len=%d",
                repair_result.exit_code,
                len(repair_result.stdout) if repair_result.stdout else 0,
            )
            return None, ExtractionQuality.FAILED, None

        repair_stdout = repair_result.stdout

        # Extract contract from repair output
        repair_parsed, repair_quality = _extract_validation_resolution(repair_stdout)

        # Extract metrics from repair output
        _fallback_provider, _fallback_model = self._resolve_metrics_fallback_config()
        repair_metrics = extract_synthesis_metrics(
            repair_stdout,
            llm_fallback=True,
            provider_name=_fallback_provider,
            model=_fallback_model,
        )

        logger.info(
            "Contract repair pass result: resolution=%s quality=%s metrics=%s",
            repair_parsed.get("resolution") if repair_parsed else None,
            repair_quality.value,
            "present" if repair_metrics else "absent",
        )

        return repair_parsed, repair_quality, repair_metrics

    def _save_synthesizer_record(
        self,
        synthesis_output: str,
        epic_num: EpicId,
        story_num: int,
        story_title: str,
        start_time: datetime,
        end_time: datetime,
        input_tokens: int,
        output_tokens: int,
        validator_count: int,
        metrics: SynthesisMetrics | None = None,
        record_custom: dict[str, object] | None = None,
    ) -> None:
        """Extract metrics and save synthesizer evaluation record.

        Story 13.6: Synthesizer Schema Integration

        Creates and saves an LLMEvaluationRecord for the synthesizer
        with extracted quality and consensus metrics.

        Args:
            synthesis_output: Raw synthesis LLM output.
            epic_num: Epic number.
            story_num: Story number within epic.
            story_title: Story title/key.
            start_time: Synthesis start time (UTC).
            end_time: Synthesis end time (UTC).
            input_tokens: Input token count.
            output_tokens: Output token count.
            validator_count: Number of validators (for sequence_position).
            metrics: Pre-extracted metrics (e.g. post-repair). If None,
                create_synthesizer_record will extract from synthesis_output.
            record_custom: Additional custom fields to persist on the record
                (e.g. repair metadata, final_extraction_quality).

        """
        from bmad_assist.benchmarking import PatchInfo, StoryInfo, WorkflowInfo
        from bmad_assist.benchmarking.storage import get_benchmark_base_dir, save_evaluation_record
        from bmad_assist.validation.benchmarking_integration import (
            create_synthesizer_record,
            should_collect_benchmarking,
        )

        # Check if benchmarking is enabled (use self.config from handler)
        if not should_collect_benchmarking(self.config):
            logger.debug("Benchmarking disabled, skipping synthesizer record")
            return

        try:
            # Create workflow info
            workflow_info = WorkflowInfo(
                id="validate-story-synthesis",
                version="1.0.0",
                variant="default",
                patch=PatchInfo(applied=True),  # Synthesis always uses patch
            )

            # Create story info
            story_info = StoryInfo(
                epic_num=epic_num,
                story_num=story_num,
                title=story_title,
                complexity_flags={},
            )

            # Create synthesizer record
            record = create_synthesizer_record(
                synthesis_output=synthesis_output,
                workflow_info=workflow_info,
                story_info=story_info,
                provider=self.get_provider().provider_name,
                model=self.get_model() or "unknown",
                start_time=start_time,
                end_time=end_time,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                validator_count=validator_count,
                metrics=metrics,
            )

            # Build custom dict: start with caller-provided fields, then
            # layer on compression metrics if available.
            custom: dict[str, object] = {}
            if record_custom:
                custom.update(record_custom)

            compression_metrics = getattr(self, "_compression_metrics", None)
            if compression_metrics:
                custom.setdefault("phase", "validate-story-synthesis")
                custom.setdefault("validator_count", validator_count)
                custom.update(compression_metrics)

            if custom:
                if record.custom is not None:
                    custom = {**record.custom, **custom}
                record = record.model_copy(update={"custom": custom})

            # Get base directory for storage
            # CRITICAL: Use centralized path utility, not get_paths() singleton!
            # get_paths() is initialized for CLI working directory, but records
            # must be saved to the TARGET project directory.
            base_dir = get_benchmark_base_dir(self.project_path)

            # Save record
            record_path = save_evaluation_record(record, base_dir)
            logger.info("Saved synthesizer evaluation record: %s", record_path)

        except Exception as e:
            # Log but don't fail synthesis phase due to benchmarking error
            logger.warning(
                "Failed to save synthesizer evaluation record: %s",
                e,
                exc_info=True,
            )
