"""Shared synthesis decision contract for all synthesis phases.

Defines the canonical vocabulary for:
- ExtractionQuality: how reliably was synthesis output parsed
- CanonicalResolution: the machine-derived outcome (resolved/rework/halt)
- FailureClass: how to respond to a bad synthesis outcome
- SynthesisDecision: the combined decision produced by make_synthesis_decision()
- StoryPatch: a single targeted update for one-write mutation model
- extract_story_patches(): parse patch blocks from LLM stdout
- make_synthesis_decision(): compute canonical resolution from parsed data + evidence

Both CODE_REVIEW_SYNTHESIS and VALIDATE_STORY_SYNTHESIS import from here.
No external deps — this module must be importable without side effects.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = [
    "ExtractionQuality",
    "CanonicalResolution",
    "FailureClass",
    "SynthesisDecision",
    "StoryPatch",
    "VALID_RESOLUTIONS",
    "RESOLUTION_COUNT_FIELDS",
    "extract_story_patches",
    "make_synthesis_decision",
    "parse_resolution_block",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ExtractionQuality(str, Enum):
    """How reliably was the synthesis output parsed.

    STRICT: exact HTML-comment markers found and all fields valid.
    DEGRADED: fallback to section headers or semantic keyword scan.
    FAILED: no usable structure found; decision must fall back to evidence only.
    """

    STRICT = "strict"
    DEGRADED = "degraded"
    FAILED = "failed"


class CanonicalResolution(str, Enum):
    """Machine-derived outcome of a synthesis phase.

    RESOLVED: no remaining issues; continue to next phase.
    REWORK: issues remain; loop back (if rework enabled) or log warning.
    HALT: unable to trust output or evidence is contradictory; stop for manual review.
    """

    RESOLVED = "resolved"
    REWORK = "rework"
    HALT = "halt"


class FailureClass(str, Enum):
    """How to respond to a bad synthesis outcome.

    RETRYABLE: ToolCallGuard termination or provider truncation; bounded retry is safe.
    HALT: contradictory evidence, unusable extraction with no fallback, patch ambiguity.
    IGNORE: non-critical failure that does not affect resolution.
    """

    RETRYABLE = "retryable"
    HALT = "halt"
    IGNORE = "ignore"


# ---------------------------------------------------------------------------
# SynthesisDecision dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SynthesisDecision:
    """The combined outcome of a synthesis phase.

    Attributes:
        resolution: Canonical machine-derived outcome.
        extraction_quality: How reliably the output was parsed.
        failure_class: How to respond if something went wrong (None = clean run).
        raw_parsed: Raw parsed fields from extraction, for logging/debugging.
        evidence_summary: Human-readable explanation of how the decision was made.

    """

    resolution: CanonicalResolution
    extraction_quality: ExtractionQuality
    failure_class: FailureClass | None
    raw_parsed: dict[str, Any] | None
    evidence_summary: str


# ---------------------------------------------------------------------------
# Shared resolution block parsing
# ---------------------------------------------------------------------------

# Valid resolution values (shared by code_review_synthesis and validate_story_synthesis)
VALID_RESOLUTIONS = frozenset({"resolved", "rework", "halt"})

# Integer count fields in the resolution block.
# `deferred_*` was added in 2026-05 to support [Review][Defer] classification:
# verified-but-not-remediable findings (research-blocked / out-of-scope / pre-existing)
# are subtracted from `remaining_*` so they don't force a rework loop. Old synthesis
# outputs that pre-date this change simply omit the fields; parse_resolution_block
# tolerates absent keys, so backward-compat is preserved.
RESOLUTION_COUNT_FIELDS = (
    "verified_critical",
    "verified_high",
    "fixed_critical",
    "fixed_high",
    "dismissed_critical",
    "dismissed_high",
    "deferred_critical",
    "deferred_high",
    "remaining_critical",
    "remaining_high",
)


def parse_resolution_block(block: str) -> dict[str, Any] | None:
    """Parse key: value lines from a resolution marker block.

    Shared by code_review_synthesis and validate_story_synthesis handlers.
    Validates resolution value and integer count fields, then cross-validates
    that "resolved" is consistent with remaining counts.

    Returns validated dict or None on validation failure.
    """
    parsed: dict[str, Any] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key and value:
            parsed[key] = value

    resolution = parsed.get("resolution")
    if resolution not in VALID_RESOLUTIONS:
        logger.warning(
            "Invalid or missing resolution value in block: %r (valid: %s)",
            resolution,
            ", ".join(sorted(VALID_RESOLUTIONS)),
        )
        return None

    for field in RESOLUTION_COUNT_FIELDS:
        raw = parsed.get(field)
        if raw is not None:
            try:
                val = int(raw)
                if val < 0:
                    logger.warning("Negative count for %s: %d", field, val)
                    return None
                parsed[field] = val
            except (ValueError, TypeError):
                logger.warning("Non-integer count for %s: %r", field, raw)
                return None

    # Cross-validate: override "resolved" if remaining counts contradict
    if parsed.get("resolution") == "resolved":
        remaining_critical = parsed.get("remaining_critical", 0)
        remaining_high = parsed.get("remaining_high", 0)
        if isinstance(remaining_critical, int) and remaining_critical > 0:
            logger.info(
                "Cross-validation override: resolution 'resolved' but "
                "remaining_critical=%d, overriding to 'rework'",
                remaining_critical,
            )
            parsed["resolution"] = "rework"
        elif isinstance(remaining_high, int) and remaining_high > 0:
            logger.info(
                "Cross-validation override: resolution 'resolved' but "
                "remaining_high=%d, overriding to 'rework'",
                remaining_high,
            )
            parsed["resolution"] = "rework"

    return parsed


# ---------------------------------------------------------------------------
# Evidence sufficiency rule
# ---------------------------------------------------------------------------

# Verdicts that indicate the evidence score found real issues pre-synthesis.
_REWORK_VERDICTS = frozenset({"REJECT", "MAJOR_REWORK"})
# Verdicts where we cannot trust evidence as a standalone signal.
_UNCERTAIN_VERDICTS = frozenset({"UNCERTAIN", "UNKNOWN"})

# Safety caps for the defer-aware resolution override (D.3, 2026-05).
# When the synthesizer reports unusually large deferral counts the story scope
# is almost certainly wrong; halt for human review instead of letting the
# defer-aware override silently accept a defer-everything outcome.
_DEFER_SAFETY_CAP_CRITICAL = 3
_DEFER_SAFETY_CAP_HIGH = 5


def _has_sufficient_evidence(
    evidence_verdict: str,
    evidence_score_data: dict[str, Any] | None,
) -> bool:
    """Return True when pre-synthesis evidence is trustworthy enough to act on alone.

    Sufficient means:
    - Pre-synthesis findings_summary contains CRITICAL > 0 or IMPORTANT > 0, AND
    - Evidence verdict is not UNCERTAIN / UNKNOWN (which would mean the signal itself
      is ambiguous).
    """
    if evidence_verdict in _UNCERTAIN_VERDICTS:
        return False
    if not evidence_score_data:
        return False
    findings = evidence_score_data.get("findings_summary", {})
    pre_critical = findings.get("CRITICAL", 0)
    pre_important = findings.get("IMPORTANT", 0)
    return pre_critical > 0 or pre_important > 0


# ---------------------------------------------------------------------------
# make_synthesis_decision
# ---------------------------------------------------------------------------


def make_synthesis_decision(
    parsed: dict[str, Any] | None,
    quality: ExtractionQuality,
    evidence_verdict: str,
    evidence_score_data: dict[str, Any] | None = None,
) -> SynthesisDecision:
    """Compute the canonical synthesis decision from parsed output + evidence.

    Resolution priority:
    1. If quality == STRICT or DEGRADED and parsed is not None → trust LLM block
       (with cross-validation: resolved+zero_fixes+evidence_issues → halt)
    2. If quality == FAILED:
       - sufficient deterministic evidence (CRITICAL/IMPORTANT > 0, verdict not UNCERTAIN)
         → REWORK (we trust the pre-synthesis counts)
       - otherwise → HALT (cannot determine outcome safely)

    Args:
        parsed: Output of the layered extraction (may be None if FAILED).
        quality: How reliably the output was parsed.
        evidence_verdict: Evidence Score verdict (REJECT, MAJOR_REWORK, PASS, etc.).
        evidence_score_data: Pre-synthesis evidence dict with findings_summary.

    Returns:
        SynthesisDecision with resolution, quality, failure_class, and summary.

    """
    if parsed is not None and quality != ExtractionQuality.FAILED:
        return _decision_from_parsed(parsed, quality, evidence_verdict, evidence_score_data)

    # quality == FAILED (or parsed is None with FAILED quality)
    return _decision_from_evidence_only(evidence_verdict, evidence_score_data, quality)


def _decision_from_parsed(
    parsed: dict[str, Any],
    quality: ExtractionQuality,
    evidence_verdict: str,
    evidence_score_data: dict[str, Any] | None,
) -> SynthesisDecision:
    """Derive decision when extraction succeeded (STRICT or DEGRADED)."""
    resolution_str = parsed.get("resolution", "")

    if quality == ExtractionQuality.DEGRADED:
        logger.warning(
            "Synthesis extraction quality is DEGRADED "
            "(markers absent or drifted; used fallback parsing). "
            "resolution=%r evidence_verdict=%s",
            resolution_str,
            evidence_verdict,
        )

    if resolution_str == "halt":
        return SynthesisDecision(
            resolution=CanonicalResolution.HALT,
            extraction_quality=quality,
            failure_class=FailureClass.HALT,
            raw_parsed=parsed,
            evidence_summary=(
                f"LLM requested halt (quality={quality.value}, verdict={evidence_verdict})"
            ),
        )

    if resolution_str == "rework":
        return SynthesisDecision(
            resolution=CanonicalResolution.REWORK,
            extraction_quality=quality,
            failure_class=None,
            raw_parsed=parsed,
            evidence_summary=(
                f"LLM reported remaining issues (quality={quality.value}, verdict={evidence_verdict})"
            ),
        )

    # resolution_str == "resolved"

    # ── D.3 (2026-05) defer-aware override ────────────────────────────────
    # The synthesizer can classify verified-but-not-remediable findings as
    # `[Review][Defer]` and exclude them from `remaining_*`.  When the new
    # defer fields are present we apply three rules in order:
    #
    #   1. Safety cap → halt when deferred_critical > 3 or deferred_high > 5
    #      (overrides LLM resolved — the volume signals a scoping problem).
    #   2. Defer-aware override → resolved when remaining_* == 0 and the
    #      evidence verdict was REJECT/MAJOR_REWORK driven solely by items
    #      the synthesis classified as deferred.  This overrides the score
    #      layer's CRITICAL hard-block (scoring.py stays pure math; defer
    #      policy lives here — see module docstring).
    #   3. Otherwise fall through to the legacy accounting cross-validation.
    #
    # Backwards compatibility: when deferred_critical and deferred_high are
    # both absent (legacy synthesizer output) the safety cap and the override
    # are skipped — the legacy `fixed + dismissed` accounting runs unchanged.
    defer_critical = parsed.get("deferred_critical")
    defer_high = parsed.get("deferred_high")
    has_defer_fields = isinstance(defer_critical, int) or isinstance(defer_high, int)

    if has_defer_fields:
        dc = defer_critical if isinstance(defer_critical, int) else 0
        dh = defer_high if isinstance(defer_high, int) else 0

        # Rule 1 — safety cap: too many deferrals indicates scope drift.
        if dc > _DEFER_SAFETY_CAP_CRITICAL or dh > _DEFER_SAFETY_CAP_HIGH:
            logger.warning(
                "Defer-aware safety cap tripped: deferred_critical=%d "
                "(cap=%d), deferred_high=%d (cap=%d). Halting for human "
                "review even though LLM reported resolved.",
                dc,
                _DEFER_SAFETY_CAP_CRITICAL,
                dh,
                _DEFER_SAFETY_CAP_HIGH,
            )
            return SynthesisDecision(
                resolution=CanonicalResolution.HALT,
                extraction_quality=quality,
                failure_class=FailureClass.HALT,
                raw_parsed=parsed,
                evidence_summary=(
                    f"Deferred-item safety cap tripped (deferred_critical={dc} "
                    f"> {_DEFER_SAFETY_CAP_CRITICAL} or deferred_high={dh} > "
                    f"{_DEFER_SAFETY_CAP_HIGH}); halting for human review"
                ),
            )

        # Rule 2 — defer-aware override.  Trust LLM-resolved when no real
        # remaining items and the evidence REJECT was attributable to defer.
        rc = parsed.get("remaining_critical", 0)
        rh = parsed.get("remaining_high", 0)
        if (
            isinstance(rc, int)
            and isinstance(rh, int)
            and rc == 0
            and rh == 0
            and (dc > 0 or dh > 0)
            and evidence_verdict in _REWORK_VERDICTS
        ):
            logger.info(
                "Defer-aware override: LLM resolved with remaining_critical=0 "
                "remaining_high=0, deferred_critical=%d deferred_high=%d, "
                "evidence_verdict=%s. Accepting resolved (score-layer REJECT "
                "treated as PASS for defer-only findings).",
                dc,
                dh,
                evidence_verdict,
            )
            return SynthesisDecision(
                resolution=CanonicalResolution.RESOLVED,
                extraction_quality=quality,
                failure_class=None,
                raw_parsed=parsed,
                evidence_summary=(
                    f"Defer-aware override: LLM reported resolved with no "
                    f"non-deferred remaining items (deferred_critical={dc}, "
                    f"deferred_high={dh}); accepted despite "
                    f"evidence_verdict={evidence_verdict}"
                ),
            )
        # Rule 3 falls through to the legacy accounting block below.

    # Cross-validate: if evidence shows pre-synthesis issues existed but the LLM
    # cannot account for their disposition, that is suspicious.
    #
    # Disposition accounting uses fields the LLM already emits:
    #   dismissed = evidence - fixed - remaining   (inferred)
    #   evidence  = fixed + dismissed + remaining   (invariant)
    #
    # A synthesis that correctly identifies a CRITICAL as a false positive will
    # report verified_critical=0, fixed_critical=0, remaining_critical=0.  The
    # inferred dismissed count (evidence - 0 - 0 = evidence) covers the gap.
    # Halt only when fixed + inferred_dismissed == 0, i.e. remaining == evidence
    # (nothing was addressed at all).
    if evidence_score_data:
        findings = evidence_score_data.get("findings_summary", {})
        pre_critical = findings.get("CRITICAL", 0)
        pre_important = findings.get("IMPORTANT", 0)
        if pre_critical > 0 or pre_important > 0:
            fixed_critical = parsed.get("fixed_critical", 0)
            fixed_high = parsed.get("fixed_high", 0)
            # Use explicit dismissed fields if present, otherwise infer from
            # the accounting identity: dismissed = evidence - fixed - remaining
            remaining_critical = parsed.get("remaining_critical", 0)
            remaining_high = parsed.get("remaining_high", 0)
            counts_valid = all(
                isinstance(v, int)
                for v in (fixed_critical, fixed_high, remaining_critical, remaining_high)
            )
            if counts_valid:
                dismissed_critical = parsed.get("dismissed_critical")
                dismissed_high = parsed.get("dismissed_high")
                if isinstance(dismissed_critical, int) and isinstance(dismissed_high, int):
                    # Explicit dismissed fields present — use them directly
                    inferred_dismissed = dismissed_critical + dismissed_high
                else:
                    # Infer from accounting identity
                    inferred_dismissed = max(
                        0,
                        (pre_critical - fixed_critical - remaining_critical)
                        + (pre_important - fixed_high - remaining_high),
                    )
                total_accounted = fixed_critical + fixed_high + inferred_dismissed
                # Halt only when nothing accounts for the evidence
                if total_accounted == 0:
                    logger.warning(
                        "Cross-validation halt: LLM claims resolved but "
                        "fixed+dismissed=0 while evidence shows "
                        "CRITICAL=%d, IMPORTANT=%d (quality=%s)",
                        pre_critical,
                        pre_important,
                        quality.value,
                    )
                    return SynthesisDecision(
                        resolution=CanonicalResolution.HALT,
                        extraction_quality=quality,
                        failure_class=FailureClass.HALT,
                        raw_parsed=parsed,
                        evidence_summary=(
                            f"LLM claims resolved but reports 0 fixes and 0 dismissals "
                            f"despite evidence showing CRITICAL={pre_critical}, "
                            f"IMPORTANT={pre_important}"
                        ),
                    )
                # Log when all findings were dismissed (none fixed) for audit
                if fixed_critical + fixed_high == 0 and inferred_dismissed > 0:
                    logger.info(
                        "Cross-validation note: all findings inferred as dismissed "
                        "(dismissed=%d, pre_critical=%d, pre_important=%d). "
                        "Accepting resolution.",
                        inferred_dismissed,
                        pre_critical,
                        pre_important,
                    )

    return SynthesisDecision(
        resolution=CanonicalResolution.RESOLVED,
        extraction_quality=quality,
        failure_class=None,
        raw_parsed=parsed,
        evidence_summary=(
            f"LLM reported resolved (quality={quality.value}, verdict={evidence_verdict})"
        ),
    )


def _decision_from_evidence_only(
    evidence_verdict: str,
    evidence_score_data: dict[str, Any] | None,
    quality: ExtractionQuality,
) -> SynthesisDecision:
    """Derive decision when extraction fully failed.

    Evidence sufficiency rule:
    - FAILED + sufficient deterministic evidence → REWORK
    - FAILED + insufficient / uncertain evidence → HALT
    """
    if _has_sufficient_evidence(evidence_verdict, evidence_score_data):
        logger.warning(
            "Synthesis extraction FAILED; falling back to evidence verdict=%s "
            "with sufficient pre-synthesis findings → REWORK",
            evidence_verdict,
        )
        return SynthesisDecision(
            resolution=CanonicalResolution.REWORK,
            extraction_quality=quality,
            failure_class=FailureClass.HALT,
            raw_parsed=None,
            evidence_summary=(
                f"Extraction failed; evidence verdict={evidence_verdict} with "
                f"sufficient pre-synthesis findings → rework"
            ),
        )

    logger.warning(
        "Synthesis extraction FAILED and evidence is insufficient "
        "(verdict=%s, no reliable pre-synthesis counts) → HALT",
        evidence_verdict,
    )
    return SynthesisDecision(
        resolution=CanonicalResolution.HALT,
        extraction_quality=quality,
        failure_class=FailureClass.HALT,
        raw_parsed=None,
        evidence_summary=(
            f"Extraction failed and evidence is insufficient "
            f"(verdict={evidence_verdict}) → halt for manual review"
        ),
    )


# ---------------------------------------------------------------------------
# StoryPatch and extract_story_patches
# ---------------------------------------------------------------------------

_PATCH_PATTERN = re.compile(
    r'<!--\s*STORY_PATCH_START\s+heading="([^"]+)"\s*-->'
    r"\s*(.*?)\s*"
    r"<!--\s*STORY_PATCH_END\s*-->",
    re.DOTALL | re.IGNORECASE,
)


@dataclass(frozen=True)
class StoryPatch:
    """A single targeted replacement for one section of a story file.

    Attributes:
        heading: Exact Markdown heading text (normalized lowercase, stripped)
            used to locate the section in the story file.
        content: Complete replacement content for that section, including
            the heading line itself.

    """

    heading: str
    content: str


def extract_story_patches(stdout: str) -> list[StoryPatch]:
    """Parse STORY_PATCH_START/END blocks from LLM stdout.

    Blocks have the form:
        <!-- STORY_PATCH_START heading="## acceptance criteria" -->
        [replacement content]
        <!-- STORY_PATCH_END -->

    The heading attribute is normalized (lowercased, stripped) so that
    "## Acceptance Criteria" and "## acceptance criteria" both resolve to
    "## acceptance criteria".

    Args:
        stdout: Raw LLM output string.

    Returns:
        List of StoryPatch instances in order of appearance.
        Returns [] if no patch blocks are found or on parse error.

    """
    patches: list[StoryPatch] = []
    for match in _PATCH_PATTERN.finditer(stdout):
        raw_heading = match.group(1).strip()
        normalized_heading = raw_heading.lower().strip()
        content = match.group(2).strip()
        if normalized_heading and content:
            patches.append(StoryPatch(heading=normalized_heading, content=content))
        else:
            logger.warning(
                "Skipping malformed STORY_PATCH block: heading=%r content_len=%d",
                raw_heading,
                len(content),
            )
    return patches
