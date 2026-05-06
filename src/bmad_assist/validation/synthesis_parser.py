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
    "SynthesisMetrics",
    "extract_metrics_via_llm",
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
                "Layer 1.5: fenced JSON after contract end rejected — "
                "unexpected keys: %s",
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
                    "LLM response did not recover any missing section "
                    f"(needed: {sections_needed})"
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
            logger.warning(
                "LLM metrics extraction attempt %d failed: %s", attempt + 1, last_error
            )
        except ValidationError as e:
            last_error = f"Schema validation: {e}"
            logger.warning(
                "LLM metrics extraction attempt %d failed: %s", attempt + 1, last_error
            )
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
        raw_output.find(_METRICS_END, start_idx + len(_METRICS_START))
        if start_idx != -1
        else -1
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
            "Metrics extraction failed — no JSON found and all fallbacks exhausted "
            "(len=%d): %s...",
            len(raw_output),
            excerpt,
        )
    else:
        logger.warning(
            "Both quality and consensus schema validation failed for synthesis output"
        )
    return None
