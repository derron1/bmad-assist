"""Synthesis metrics parser for extracting structured output.

Story 13.6: Synthesizer Schema Integration

This module provides:
- SynthesisMetrics dataclass for parsed metrics
- extract_synthesis_metrics() for marker-based extraction

The synthesis workflow outputs structured JSON between markers:
<!-- METRICS_JSON_START --> and <!-- METRICS_JSON_END -->

Extraction is graceful - failures log warnings and return None rather than
raising exceptions, to avoid blocking synthesis phase completion.
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
    "extract_synthesis_metrics",
]

logger = logging.getLogger(__name__)

# Marker strings for JSON extraction
_METRICS_START = "<!-- METRICS_JSON_START -->"
_METRICS_END = "<!-- METRICS_JSON_END -->"

# Maximum JSON object candidates to try during backward scan fallback.
_MAX_BACKWARD_CANDIDATES = 5
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

# Maximum distance (chars) after contract end marker to search for fenced JSON
_POST_CONTRACT_MAX_DISTANCE = 200


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
    - Fenced JSON appears within 200 chars of a contract end marker
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


def _try_json_backward_extraction(raw_output: str) -> dict | None:
    """Attempt to extract metrics JSON by scanning backward from end of output.

    Fallback for when ``METRICS_JSON_START``/``END`` markers are missing.
    Prefers fenced JSON blocks (````json ... ````) over bare brace-balanced
    objects, since fenced output is the lower-risk parse target.

    Stops after checking ``_MAX_BACKWARD_CANDIDATES`` objects to bound runtime.

    Returns:
        Parsed dict containing ``"quality"`` or ``"consensus"`` key, or None.
    """
    import re

    candidates: list[str] = []

    # Pass 1: fenced JSON blocks (prefer these)
    fence_pattern = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
    for m in reversed(list(fence_pattern.finditer(raw_output))):
        candidates.append(m.group(1))
        if len(candidates) >= _MAX_BACKWARD_CANDIDATES:
            break

    # Pass 2: bare brace-balanced objects (scan backward)
    if len(candidates) < _MAX_BACKWARD_CANDIDATES:
        brace_positions: list[tuple[int, int]] = []
        depth = 0
        obj_end = -1
        for i in range(len(raw_output) - 1, -1, -1):
            ch = raw_output[i]
            if ch == "}":
                if depth == 0:
                    obj_end = i
                depth += 1
            elif ch == "{":
                depth -= 1
                if depth == 0 and obj_end != -1:
                    brace_positions.append((i, obj_end))
                    if len(candidates) + len(brace_positions) >= _MAX_BACKWARD_CANDIDATES:
                        break
        for start, end in brace_positions:
            candidates.append(raw_output[start : end + 1])

    for candidate in candidates[:_MAX_BACKWARD_CANDIDATES]:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict) and ("quality" in data or "consensus" in data):
                logger.info(
                    "Metrics JSON extracted via backward scan fallback (no markers). "
                    "Consider adding METRICS_JSON markers to synthesis prompt."
                )
                return data
        except (json.JSONDecodeError, TypeError):
            continue

    return None


def _try_markdown_fallback(raw_output: str) -> SynthesisMetrics | None:
    """Recover minimal synthesis metrics from heading-based markdown output."""
    headings = {match.group(1).strip() for match in _HEADING_PATTERN.finditer(raw_output)}
    heading_matches = sum(1 for heading in _EXPECTED_HEADINGS if heading in headings)

    quality: QualitySignals | None = None
    if heading_matches >= 3:
        quality = QualitySignals(
            actionable_ratio=0.0,
            specificity_score=0.0,
            evidence_quality=0.0,
            follows_template=True,
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


@dataclass(frozen=True)
class SynthesisMetrics:
    """Metrics extracted from synthesis output.

    Both fields may be None if extraction failed for that section.
    Partial extraction is allowed - quality may succeed while consensus fails.
    """

    quality: QualitySignals | None
    consensus: ConsensusData | None


def extract_synthesis_metrics(raw_output: str) -> SynthesisMetrics | None:
    """Extract structured metrics from synthesis output.

    Uses marker-based extraction to find JSON between:
    <!-- METRICS_JSON_START --> and <!-- METRICS_JSON_END -->

    Extraction is graceful:
    - Missing markers -> log warning, return None
    - Invalid JSON -> log warning, return None
    - Schema validation failure for one section -> log warning, return partial
    - Both sections fail -> return None

    Args:
        raw_output: Raw synthesis LLM output.

    Returns:
        SynthesisMetrics with quality and/or consensus, or None if extraction
        completely fails (no valid sections).

    """
    # Layer 1: Marker-based extraction
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

    # Layer 1.5: Post-contract fenced JSON (no METRICS markers)
    if data is None and _METRICS_START not in raw_output:
        data = _try_post_contract_fenced_json(raw_output)
        if data is not None:
            logger.info(
                "Metrics extracted via Layer 1.5 (fenced JSON after contract end, no markers)"
            )

    # Layer 2: Backward scan fallback
    if data is None:
        data = _try_json_backward_extraction(raw_output)

    # Layer 3: Heading-based markdown fallback
    if data is None:
        markdown_result = _try_markdown_fallback(raw_output)
        if markdown_result is not None:
            return markdown_result

    if data is None:
        excerpt = raw_output[:500]
        logger.warning(
            "Metrics extraction failed — no markers and backward scan fallback "
            "failed (len=%d): %s...",
            len(raw_output),
            excerpt,
        )
        return None

    # Validate quality section
    quality: QualitySignals | None = None
    excerpt = raw_output[:500]
    if "quality" in data:
        try:
            quality = QualitySignals.model_validate(data["quality"])
        except ValidationError as e:
            logger.warning(
                "Quality schema validation failed: %s (output len=%d): %s...",
                e,
                len(raw_output),
                excerpt,
            )

    # Validate consensus section
    consensus: ConsensusData | None = None
    if "consensus" in data:
        try:
            consensus = ConsensusData.model_validate(data["consensus"])
        except ValidationError as e:
            logger.warning(
                "Consensus schema validation failed: %s (output len=%d): %s...",
                e,
                len(raw_output),
                excerpt,
            )

    # If both failed, return None
    if quality is None and consensus is None:
        logger.warning("Both quality and consensus schema validation failed for synthesis output")
        return None

    return SynthesisMetrics(quality=quality, consensus=consensus)
