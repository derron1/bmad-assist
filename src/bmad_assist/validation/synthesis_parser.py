"""Synthesis metrics parser for extracting structured output.

Story 13.6: Synthesizer Schema Integration

This module provides:
- SynthesisMetrics dataclass for parsed metrics
- extract_synthesis_metrics() for marker-based extraction
- extract_metrics_via_llm() for LLM-based fallback extraction

The synthesis workflow outputs structured JSON between markers:
<!-- METRICS_JSON_START --> and <!-- METRICS_JSON_END -->

Extraction is graceful - failures log warnings and return None rather than
raising exceptions, to avoid blocking synthesis phase completion.

Layer order:
  1.  Marker-based (STRICT)
  1.5 Post-contract fenced JSON
  2.  Haiku LLM extraction (when llm_fallback=True)
  3.  Heading-based markdown fallback (additive — fills gaps only)

Fallback layers are additive: they may fill missing sections but never
replace higher-confidence data from earlier layers.

follows_template semantics:
  - True only when both sections came from strict JSON extraction and the
    original synthesizer JSON contained follows_template=true.
  - False whenever any fallback layer (LLM or markdown) was used or attempted
    to rescue missing sections, even if only one section needed help.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from pydantic import ValidationError

from bmad_assist.benchmarking.schema import ConsensusData, QualitySignals

__all__ = [
    "ReviewFindings",
    "SynthesisMetrics",
    "count_review_followups_by_severity",
    "cross_check_defer_counts",
    "extract_metrics_via_llm",
    "extract_review_findings",
    "extract_synthesis_metrics",
]

logger = logging.getLogger(__name__)

# Marker strings for JSON extraction
_METRICS_START = "<!-- METRICS_JSON_START -->"
_METRICS_END = "<!-- METRICS_JSON_END -->"

_HEADING_PATTERN = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_SUMMARY_COUNTS_RE = re.compile(
    r"(?P<verified>\d+)\s+issues?\s+verified.*?(?P<dismissed>\d+)\s+false positives?\s+dismissed",
    re.IGNORECASE | re.DOTALL,
)
_EXPECTED_HEADINGS = (
    "Synthesis Summary",
    "Issues Verified",
    "Issues Dismissed",
    "Changes Applied",
)


# Marker for detecting post-contract fenced JSON (Layer 1.5)
_CONTRACT_END_MARKER = "<!-- VALIDATION_CONTRACT_END -->"
_ALLOWED_METRICS_KEYS = frozenset({"quality", "consensus"})
# Also detect code-review synthesis contract end for reuse
_SYNTHESIS_RESOLUTION_END = "<!-- SYNTHESIS_RESOLUTION_END -->"

_POST_CONTRACT_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

# Maximum distance (chars) after contract end marker to search for fenced JSON.
# Must accommodate the full metrics JSON block (~300-400 chars formatted).
_POST_CONTRACT_MAX_DISTANCE = 500

# LLM extraction constants
_LLM_HEAD_CHARS = 1500
_LLM_TAIL_CHARS = 2500
_LLM_TRUNCATION_MARKER = "\n[...truncated...]\n"

# Cache for loaded prompt template
_metrics_prompt_cache: str | None = None


def _load_metrics_extraction_prompt() -> str:
    """Load metrics extraction prompt template from package resource."""
    global _metrics_prompt_cache

    if _metrics_prompt_cache is not None:
        return _metrics_prompt_cache

    from importlib import resources

    try:
        prompt_file = resources.files("bmad_assist.validation.prompts").joinpath(
            "metrics_extraction.xml"
        )
        _metrics_prompt_cache = prompt_file.read_text(encoding="utf-8")
    except (TypeError, AttributeError):
        import importlib.resources as pkg_resources

        with pkg_resources.open_text(
            "bmad_assist.validation.prompts", "metrics_extraction.xml"
        ) as f:
            _metrics_prompt_cache = f.read()

    logger.debug("Loaded metrics extraction prompt template from package resource")
    return _metrics_prompt_cache


def _truncate_for_llm(raw_output: str) -> str:
    """Truncate synthesis output for LLM extraction using head + tail strategy.

    Keeps the first ~1.5K chars (contract/summary) and last ~2.5K chars
    (where malformed metrics typically appear).
    """
    max_len = _LLM_HEAD_CHARS + _LLM_TAIL_CHARS
    if len(raw_output) <= max_len:
        return raw_output
    head = raw_output[:_LLM_HEAD_CHARS]
    tail = raw_output[-_LLM_TAIL_CHARS:]
    return head + _LLM_TRUNCATION_MARKER + tail


def _try_post_contract_fenced_json(raw_output: str) -> dict | None:
    """Layer 1.5: Extract metrics from fenced JSON immediately after contract end marker.

    Handles the specific bad pattern where the LLM outputs:
        <!-- VALIDATION_CONTRACT_END -->
        ```json
        {"quality": {...}, "consensus": {...}}
        ```
    without METRICS_JSON markers.

    Only accepts if:
    - METRICS_JSON markers are NOT present (caller must check)
    - Fenced JSON appears within 500 chars of a contract end marker
    - Top-level keys are EXACTLY {"quality", "consensus"} (no extras)

    Returns parsed dict or None.
    """
    # Try both validation and code-review contract end markers
    for marker in (_CONTRACT_END_MARKER, _SYNTHESIS_RESOLUTION_END):
        end_idx = raw_output.find(marker)
        if end_idx == -1:
            continue
        search_start = end_idx + len(marker)
        search_region = raw_output[search_start : search_start + _POST_CONTRACT_MAX_DISTANCE]
        fence_match = _POST_CONTRACT_FENCE_RE.search(search_region)
        if fence_match is None:
            continue
        try:
            data = json.loads(fence_match.group(1))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        # Strict key check: exactly {"quality", "consensus"}
        if set(data.keys()) != _ALLOWED_METRICS_KEYS:
            logger.info(
                "Layer 1.5: fenced JSON after contract end rejected — unexpected keys: %s",
                sorted(set(data.keys()) - _ALLOWED_METRICS_KEYS),
            )
            continue
        return data

    return None


def _try_markdown_fallback(raw_output: str) -> SynthesisMetrics | None:
    """Recover minimal synthesis metrics from heading-based markdown output.

    Any QualitySignals produced here use ``follows_template=False`` because
    markdown fallback is a non-strict recovery path — the original synthesizer
    output did not provide valid structured metrics.
    """
    headings = {match.group(1).strip() for match in _HEADING_PATTERN.finditer(raw_output)}
    heading_matches = sum(1 for heading in _EXPECTED_HEADINGS if heading in headings)

    quality: QualitySignals | None = None
    if heading_matches >= 3:
        quality = QualitySignals(
            actionable_ratio=0.0,
            specificity_score=0.0,
            evidence_quality=0.0,
            follows_template=False,
            internal_consistency=0.0,
        )

    consensus: ConsensusData | None = None
    summary_match = _SUMMARY_COUNTS_RE.search(raw_output)
    if summary_match:
        agreed_findings = int(summary_match.group("verified"))
        false_positive_count = int(summary_match.group("dismissed"))
        denominator = agreed_findings + false_positive_count
        consensus = ConsensusData(
            agreed_findings=agreed_findings,
            unique_findings=0,
            disputed_findings=0,
            missed_findings=0,
            agreement_score=(agreed_findings / denominator) if denominator else 0.0,
            false_positive_count=false_positive_count,
        )

    if quality is None and consensus is None:
        return None

    logger.info(
        "Metrics recovered via markdown fallback (headings=%d, consensus=%s)",
        heading_matches,
        "yes" if consensus is not None else "no",
    )
    return SynthesisMetrics(quality=quality, consensus=consensus)


def _force_follows_template_false(
    quality: QualitySignals | None,
) -> QualitySignals | None:
    """Return a copy of *quality* with ``follows_template=False``, or None."""
    if quality is None:
        return None
    if not quality.follows_template:
        return quality
    return quality.model_copy(update={"follows_template": False})


def _finalize_result(
    quality: QualitySignals | None,
    consensus: ConsensusData | None,
    used_fallback: bool,
) -> SynthesisMetrics | None:
    """Combine sections, enforce ``follows_template`` invariant, return result.

    Args:
        quality: Best-available quality section (strict > LLM > markdown).
        consensus: Best-available consensus section (strict > LLM > markdown).
        used_fallback: True if any non-strict layer was used or attempted to
            rescue a missing section.

    Returns:
        SynthesisMetrics or None if both sections are still None.

    """
    if quality is None and consensus is None:
        return None

    if used_fallback:
        quality = _force_follows_template_false(quality)

    return SynthesisMetrics(quality=quality, consensus=consensus)


@dataclass(frozen=True)
class SynthesisMetrics:
    """Metrics extracted from synthesis output.

    Both fields may be None if extraction failed for that section.
    Partial extraction is allowed - quality may succeed while consensus fails.
    """

    quality: QualitySignals | None
    consensus: ConsensusData | None


def extract_metrics_via_llm(
    raw_output: str,
    provider_name: str,
    model: str,
    timeout: int = 120,
    max_retries: int = 2,
    existing_quality: QualitySignals | None = None,
    existing_consensus: ConsensusData | None = None,
) -> SynthesisMetrics | None:
    """Extract metrics from synthesis output using a focused LLM call.

    Uses a lightweight model (e.g. haiku) with a short, schema-strict prompt
    to extract QualitySignals and ConsensusData from free-form synthesis text.

    If ``existing_quality`` or ``existing_consensus`` is provided, only the
    missing section is requested from the LLM. If both are already valid,
    returns immediately without making an LLM call.

    Sets ``follows_template=False`` on any returned QualitySignals because
    the original synthesizer output did not comply with the prescribed schema.

    Success criteria: the LLM must recover at least one section that was
    *missing* when the call started.  If ``existing_quality`` was provided
    and ``consensus`` is still None after parsing, that attempt is a failure.

    Args:
        raw_output: Raw synthesis LLM output.
        provider_name: Provider to use (e.g. "claude").
        model: Model to use (e.g. "haiku").
        timeout: Timeout in seconds for LLM invocation.
        max_retries: Maximum retry attempts on parse/validation failure.
        existing_quality: Already-validated QualitySignals (skip LLM for this section).
        existing_consensus: Already-validated ConsensusData (skip LLM for this section).

    Returns:
        SynthesisMetrics or None if extraction fails after retries.

    """
    # If both sections already validated, no LLM call needed
    if existing_quality is not None and existing_consensus is not None:
        return SynthesisMetrics(quality=existing_quality, consensus=existing_consensus)

    # Determine which sections we need
    need_quality = existing_quality is None
    need_consensus = existing_consensus is None
    if need_quality and need_consensus:
        sections_needed = "both"
    elif need_quality:
        sections_needed = "quality"
    else:
        sections_needed = "consensus"

    from bmad_assist.providers.registry import get_provider

    try:
        provider = get_provider(provider_name)
    except Exception as e:
        logger.warning("LLM metrics extraction: failed to get provider %r: %s", provider_name, e)
        return None

    template = _load_metrics_extraction_prompt()
    truncated = _truncate_for_llm(raw_output)
    prompt = template.format(synthesis_output=truncated, sections_needed=sections_needed)

    last_error: str | None = None
    for attempt in range(max_retries):
        try:
            retry_prompt = prompt
            if attempt > 0 and last_error:
                retry_hint = (
                    f"Previous attempt failed: {last_error}. "
                    "Please output ONLY valid JSON matching the schema exactly."
                )
                retry_prompt = f"{prompt}\n\n<error>{retry_hint}</error>"

            result = provider.invoke(
                retry_prompt,
                model=model,
                timeout=timeout,
                allowed_tools=[],
            )

            if result.exit_code != 0:
                last_error = f"Provider exit code {result.exit_code}"
                logger.warning(
                    "LLM metrics extraction attempt %d failed: %s", attempt + 1, last_error
                )
                continue

            raw_json = result.stdout.strip()

            # Strip markdown code block wrappers if present
            if raw_json.startswith("```"):
                lines = raw_json.split("\n")
                end_idx = len(lines) - 1
                for i in range(len(lines) - 1, 0, -1):
                    if lines[i].strip() == "```":
                        end_idx = i
                        break
                raw_json = "\n".join(lines[1:end_idx]).strip()

            data = json.loads(raw_json)
            if not isinstance(data, dict):
                last_error = f"Expected dict, got {type(data).__name__}"
                continue

            # Validate and extract quality section
            quality = existing_quality
            if need_quality and "quality" in data:
                quality_data = dict(data["quality"])
                # Set follows_template=False — original output didn't comply
                quality_data["follows_template"] = False
                quality = QualitySignals.model_validate(quality_data)

            # Validate and extract consensus section
            consensus = existing_consensus
            if need_consensus and "consensus" in data:
                consensus = ConsensusData.model_validate(data["consensus"])

            # Success criteria: must recover at least one section that was
            # missing when the call started.
            recovered_quality = need_quality and quality is not None
            recovered_consensus = need_consensus and consensus is not None
            if not recovered_quality and not recovered_consensus:
                last_error = (
                    f"LLM response did not recover any missing section (needed: {sections_needed})"
                )
                continue

            # Ensure follows_template=False on any quality we return
            if quality is not None:
                quality = _force_follows_template_false(quality)

            logger.info(
                "LLM metrics extraction succeeded (attempt %d, sections=%s, "
                "quality=%s, consensus=%s)",
                attempt + 1,
                sections_needed,
                "recovered" if recovered_quality else ("existing" if quality else "absent"),
                "recovered" if recovered_consensus else ("existing" if consensus else "absent"),
            )
            return SynthesisMetrics(quality=quality, consensus=consensus)

        except json.JSONDecodeError as e:
            last_error = f"Invalid JSON: {e}"
            logger.warning("LLM metrics extraction attempt %d failed: %s", attempt + 1, last_error)
        except ValidationError as e:
            last_error = f"Schema validation: {e}"
            logger.warning("LLM metrics extraction attempt %d failed: %s", attempt + 1, last_error)
        except Exception as e:
            last_error = f"Unexpected error: {e}"
            logger.warning(
                "LLM metrics extraction attempt %d failed: %s",
                attempt + 1,
                last_error,
                exc_info=True,
            )

    logger.warning(
        "LLM metrics extraction failed after %d attempts (last error: %s)",
        max_retries,
        last_error,
    )
    return None


def extract_synthesis_metrics(
    raw_output: str,
    llm_fallback: bool = False,
    provider_name: str | None = None,
    model: str | None = None,
) -> SynthesisMetrics | None:
    """Extract structured metrics from synthesis output.

    Uses a layered extraction strategy:
      Layer 1:   Marker-based (STRICT)
      Layer 1.5: Post-contract fenced JSON
      Layer 2:   Haiku LLM extraction (when llm_fallback=True)
      Layer 3:   Heading-based markdown fallback (additive — fills gaps only)

    Fallback layers are additive: they fill missing sections but never replace
    higher-confidence data from earlier layers.

    ``follows_template`` is True only when both sections came from strict JSON
    and the original value was true.  Any fallback involvement forces it False.

    Args:
        raw_output: Raw synthesis LLM output.
        llm_fallback: If True, attempt LLM-based extraction when JSON
            extraction or Pydantic validation fails.
        provider_name: Provider for LLM fallback (required if llm_fallback=True).
        model: Model for LLM fallback (required if llm_fallback=True).

    Returns:
        SynthesisMetrics with quality and/or consensus, or None if extraction
        completely fails (no valid sections).

    """
    # ── Layer 1: Marker-based extraction ──────────────────────────────
    start_idx = raw_output.find(_METRICS_START)
    end_idx = (
        raw_output.find(_METRICS_END, start_idx + len(_METRICS_START)) if start_idx != -1 else -1
    )

    data: dict | None = None

    if start_idx != -1 and end_idx != -1:
        json_str = raw_output[start_idx + len(_METRICS_START) : end_idx].strip()
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(
                "Invalid JSON in synthesis metrics markers: %s (output len=%d)",
                e,
                len(raw_output),
            )

    # ── Layer 1.5: Post-contract fenced JSON (no METRICS markers) ─────
    if data is None and _METRICS_START not in raw_output:
        data = _try_post_contract_fenced_json(raw_output)
        if data is not None:
            logger.info(
                "Metrics extracted via Layer 1.5 (fenced JSON after contract end, no markers)"
            )

    # ── Validate strict JSON sections ─────────────────────────────────
    strict_quality: QualitySignals | None = None
    strict_consensus: ConsensusData | None = None
    excerpt = raw_output[:500]

    if data is not None:
        if "quality" in data:
            try:
                strict_quality = QualitySignals.model_validate(data["quality"])
            except ValidationError as e:
                logger.warning(
                    "Quality schema validation failed: %s (output len=%d): %s...",
                    e,
                    len(raw_output),
                    excerpt,
                )

        if "consensus" in data:
            try:
                strict_consensus = ConsensusData.model_validate(data["consensus"])
            except ValidationError as e:
                logger.warning(
                    "Consensus schema validation failed: %s (output len=%d): %s...",
                    e,
                    len(raw_output),
                    excerpt,
                )

    # If both sections valid from strict extraction, return immediately —
    # preserving the original follows_template value from the JSON.
    if strict_quality is not None and strict_consensus is not None:
        return SynthesisMetrics(quality=strict_quality, consensus=strict_consensus)

    # Track the best-known sections so far (strict wins over everything).
    best_quality = strict_quality
    best_consensus = strict_consensus
    used_fallback = False

    # ── Layer 2: LLM extraction fallback ──────────────────────────────
    if llm_fallback and len(raw_output.strip()) >= 200:
        if provider_name is None or model is None:
            logger.warning(
                "LLM metrics fallback requested but provider_name=%r / model=%r not set; "
                "skipping LLM extraction",
                provider_name,
                model,
            )
            # Even though we couldn't call the LLM, fallback was *attempted*
            # (config was missing). Mark it so follows_template is forced False.
            used_fallback = True
        else:
            logger.info(
                "Attempting LLM metrics extraction (quality=%s, consensus=%s)",
                "valid" if best_quality else "missing",
                "valid" if best_consensus else "missing",
            )
            used_fallback = True
            llm_result = extract_metrics_via_llm(
                raw_output,
                provider_name=provider_name,
                model=model,
                existing_quality=best_quality,
                existing_consensus=best_consensus,
            )
            if llm_result is not None:
                # LLM recovered at least one missing section. Merge additively.
                if best_quality is None and llm_result.quality is not None:
                    best_quality = llm_result.quality
                if best_consensus is None and llm_result.consensus is not None:
                    best_consensus = llm_result.consensus

    # ── Layer 3: Heading-based markdown fallback (additive) ───────────
    # Only fill sections that are *still* missing after strict + LLM.
    if best_quality is None or best_consensus is None:
        markdown_result = _try_markdown_fallback(raw_output)
        if markdown_result is not None:
            if best_quality is None and markdown_result.quality is not None:
                best_quality = markdown_result.quality
                used_fallback = True
            if best_consensus is None and markdown_result.consensus is not None:
                best_consensus = markdown_result.consensus
                used_fallback = True

    # ── Finalize ──────────────────────────────────────────────────────
    result = _finalize_result(best_quality, best_consensus, used_fallback)
    if result is not None:
        return result

    if data is None:
        logger.warning(
            "Metrics extraction failed — no JSON found and all fallbacks exhausted (len=%d): %s...",
            len(raw_output),
            excerpt,
        )
    else:
        logger.warning("Both quality and consensus schema validation failed for synthesis output")
    return None


# ---------------------------------------------------------------------------
# Review Findings extraction + defer-aware cross-check (D.3, 2026-05)
# ---------------------------------------------------------------------------
#
# The code-review-synthesis SKILL (step 6.5) appends a `### Review Findings`
# section to the story file with defer-aware accounting:
#
#     ### Review Findings
#     - **Date:** 2026-05-12
#     - **Reviewer:** AI Code Review Synthesis
#     - **Outcome:** Approved with Reservations
#     - **Issues Found:** 5
#     - **Issues Fixed:** 3
#     - **Deferred (Critical):** 1
#     - **Deferred (High):** 0
#     - **Remaining Critical (non-deferred):** 0
#     - **Remaining High (non-deferred):** 0
#     - **Action Items Created:** 2
#
# These counts pair with the machine-readable SYNTHESIS_RESOLUTION block, but
# also stand alone for downstream consumers that read the story file directly.
# Pre-D.3 outputs lack the four `Deferred (...)` / `Remaining ... (non-deferred)`
# bullets entirely; ``extract_review_findings`` reports them as ``None`` so
# callers can detect the legacy schema and fall through to the old code path.

# Each bullet is a Markdown list item with a bolded label.  We accept any of:
#   - **Label:** value     (colon INSIDE the bold — the synthesizer's format)
#   - **Label**: value     (colon outside the bold)
#   - **Label** value      (no colon at all — defensive)
# Trailing colon on the label is stripped before normalisation.  The label
# pattern is non-greedy and stops at the closing ``**`` regardless of any
# colons inside.
_REVIEW_FINDINGS_HEADING_RE = re.compile(
    r"^#{2,4}\s+Review\s+Findings\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_REVIEW_FIELD_RE = re.compile(
    r"^\s*[-*]\s*\*\*\s*(?P<label>[^*\n]+?)\s*\*\*\s*:?\s*(?P<value>.+?)\s*$",
    re.MULTILINE,
)
# Stop scanning when we hit the next heading (any level).
_NEXT_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)

# Unchecked Review Follow-up tasks. `[ ]` only (checked `[x]` is closed).
_UNCHECKED_REVIEW_TASK_RE = re.compile(
    r"^[-*]\s*\[\s\]\s*"
    r"\[Review\]\[(?P<marker>Patch|Decision)\]\s+"
    r"(?P<body>[^\n]+)$",
    re.MULTILINE,
)
# Inline severity prefix in the post-em-dash detail (e.g. "HIGH: ..."
# or "CRITICAL: ..."). We also accept HIGH→CRITICAL alias mapping.
_TASK_INLINE_SEVERITY_RE = re.compile(
    r"[—–-]+\s*(?P<sev>CRITICAL|HIGH|MEDIUM|LOW|IMPORTANT|MINOR)\s*:",
    re.IGNORECASE,
)

# Map free-form severity tokens to (critical|high|medium|low) buckets used by
# the resolution accounting.  Synthesis SKILL.md emits HIGH for the IMPORTANT
# bucket and CRITICAL for the top bucket — keep both alive.
_SEVERITY_BUCKET: dict[str, str] = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "IMPORTANT": "high",
    "MEDIUM": "medium",
    "LOW": "low",
    "MINOR": "low",
}


@dataclass(frozen=True)
class ReviewFindings:
    """Counts extracted from the `### Review Findings` markdown block.

    All four defer-related fields are ``int | None``: ``None`` indicates the
    field was absent from the report (pre-D.3 schema) and the caller should
    fall back to the legacy code path.  ``issues_found``, ``issues_fixed``,
    and ``action_items_created`` are informational only.

    Attributes:
        outcome: Free-form Outcome string (e.g. ``"Approved"``,
            ``"Changes Requested"``).
        issues_found: Total verified issues across reviewers (informational).
        issues_fixed: Fixes applied this round (informational).
        deferred_critical: CRITICAL findings tagged ``[Review][Defer]``.
            ``None`` when the field is missing.
        deferred_high: HIGH findings tagged ``[Review][Defer]``.
        remaining_critical: CRITICAL findings still open and NOT deferred.
        remaining_high: HIGH findings still open and NOT deferred.
        action_items_created: Total open follow-up items written to the story
            (informational; includes deferred entries since dev_story reads
            the same list).

    """

    outcome: str | None
    issues_found: int | None
    issues_fixed: int | None
    deferred_critical: int | None
    deferred_high: int | None
    remaining_critical: int | None
    remaining_high: int | None
    action_items_created: int | None


def _parse_int(value: str) -> int | None:
    """Return ``int(value)`` or None when the token is not a non-negative int."""
    value = value.strip()
    # Strip surrounding bold/emphasis if the synthesizer included `**N**`.
    value = value.strip("*").strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    if parsed < 0:
        return None
    return parsed


def extract_review_findings(report: str) -> ReviewFindings | None:
    """Extract the `### Review Findings` block from a synthesis report.

    Scans for the most recent ``### Review Findings`` heading (rework rounds
    append new blocks — the last one is authoritative).  Reads bold-labelled
    bullets until the next heading and returns a ``ReviewFindings`` with the
    parsed values.  Missing fields are returned as ``None`` so callers can
    distinguish a legacy report (no defer fields) from an explicit zero.

    Returns ``None`` only when no ``### Review Findings`` heading is present at
    all.

    Args:
        report: Full synthesis-report text (markdown).

    Returns:
        ReviewFindings or None.

    """
    headings = list(_REVIEW_FINDINGS_HEADING_RE.finditer(report))
    if not headings:
        return None

    # Last heading wins — synthesis appends a new block per rework round.
    last = headings[-1]
    block_start = last.end()
    next_heading = _NEXT_HEADING_RE.search(report, block_start)
    block_end = next_heading.start() if next_heading else len(report)
    block = report[block_start:block_end]

    fields: dict[str, str] = {}
    for m in _REVIEW_FIELD_RE.finditer(block):
        label = m.group("label").strip().rstrip(":").strip().lower()
        fields[label] = m.group("value").strip()

    def _get(*labels: str) -> str | None:
        for label in labels:
            if label in fields:
                return fields[label]
        return None

    def _get_int(*labels: str) -> int | None:
        raw = _get(*labels)
        if raw is None:
            return None
        return _parse_int(raw)

    outcome = _get("outcome")

    return ReviewFindings(
        outcome=outcome,
        issues_found=_get_int("issues found"),
        issues_fixed=_get_int("issues fixed"),
        deferred_critical=_get_int("deferred (critical)", "deferred critical"),
        deferred_high=_get_int("deferred (high)", "deferred high"),
        remaining_critical=_get_int("remaining critical (non-deferred)", "remaining critical"),
        remaining_high=_get_int("remaining high (non-deferred)", "remaining high"),
        action_items_created=_get_int("action items created"),
    )


def count_review_followups_by_severity(report: str) -> dict[str, int]:
    """Count unchecked ``[Review][Patch]``/``[Review][Decision]`` task lines.

    Buckets each unchecked item by the severity prefix in its post-em-dash
    detail (``CRITICAL`` / ``HIGH``-or-``IMPORTANT`` / ``MEDIUM`` / ``LOW``).
    Items without an inline severity hint fall into ``"unknown"`` so callers
    can detect missing-severity bullets without silently miscounting.

    ``[Review][Defer]`` is excluded by construction — deferred items are
    closed for the purposes of this story's loop, regardless of checkbox
    state.

    Args:
        report: Full synthesis-report text (markdown).

    Returns:
        Dict with non-negative counts under keys ``critical``, ``high``,
        ``medium``, ``low``, ``unknown``.  Always returns all five keys.

    """
    counts: dict[str, int] = {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "unknown": 0,
    }
    for match in _UNCHECKED_REVIEW_TASK_RE.finditer(report):
        body = match.group("body")
        sev_match = _TASK_INLINE_SEVERITY_RE.search(body)
        if sev_match is None:
            counts["unknown"] += 1
            continue
        bucket = _SEVERITY_BUCKET.get(sev_match.group("sev").upper())
        if bucket is None:
            counts["unknown"] += 1
        else:
            counts[bucket] += 1
    return counts


def cross_check_defer_counts(
    findings: ReviewFindings | None,
    report: str,
) -> tuple[int | None, int | None]:
    """Reconcile LLM-reported remaining_* counts against parser-observed tasks.

    The synthesizer emits ``Remaining Critical (non-deferred)`` and
    ``Remaining High (non-deferred)`` in the Review Findings block.  This
    function compares those numbers against the parser's own count of unchecked
    ``[Review][Patch]`` / ``[Review][Decision]`` bullets in the
    ``#### Review Follow-ups (AI)`` subsection.

    When the LLM-reported counts match the parser-observed counts we trust the
    LLM.  When they disagree we log a warning and return the parser's counts
    (the safer choice — a misclaimed "0 remaining" would otherwise let a
    real CRITICAL slip through).

    When the LLM did not emit the new fields (``findings`` is None or its
    ``remaining_*`` are None), the function returns ``(None, None)`` — caller
    falls back to the legacy code path.

    Args:
        findings: Output of :func:`extract_review_findings` (may be None).
        report: Full synthesis-report text (used for parser-observed counts).

    Returns:
        Tuple ``(remaining_critical, remaining_high)``.  Each element is an
        ``int`` when defer-aware accounting is active, or ``None`` when the
        legacy fallback should be used.

    """
    if findings is None:
        return None, None
    if findings.remaining_critical is None or findings.remaining_high is None:
        return None, None

    observed = count_review_followups_by_severity(report)
    obs_critical = observed["critical"]
    # HIGH-equivalent: HIGH and IMPORTANT share a bucket in synthesis output.
    obs_high = observed["high"]

    llm_critical = findings.remaining_critical
    llm_high = findings.remaining_high

    if obs_critical == llm_critical and obs_high == llm_high:
        return llm_critical, llm_high

    logger.warning(
        "Review-findings cross-check mismatch: LLM reported remaining "
        "critical=%d high=%d but parser observed critical=%d high=%d "
        "(unknown=%d). Using parser-observed counts.",
        llm_critical,
        llm_high,
        obs_critical,
        obs_high,
        observed["unknown"],
    )
    return obs_critical, obs_high
