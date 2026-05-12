"""Scoring system for Deep Verify.

This module implements the evidence scoring and verdict determination logic
for Deep Verify findings. The scoring formula accounts for severity-weighted
findings and clean pass bonuses.

Formula:
    S = Σ(severity_weight × confidence) + (clean_passes × -0.5)

Where:
    - severity_weight: critical=4, error=2, warning=1, info=0.5
    - confidence: 0.0-1.0 from evidence
    - clean_passes: number of domains with zero findings (-0.5 bonus each)

Verdict thresholds (non-overlapping):
    - score > 12   → REJECT (too many high-severity findings)
    - -3 ≤ score ≤ 12 → UNCERTAIN (needs human review)
    - score < -3   → ACCEPT (clean enough)

D.3 (defer-aware verdict, 2026-05) — design note
------------------------------------------------
Scoring is intentionally pure math: ``calculate_score`` and
``determine_verdict`` operate on the raw reviewer findings produced
pre-synthesis, and the CRITICAL hard-block still forces ``REJECT`` here.

Defer marking happens later, at synthesis time, where the
``[Review][Defer]`` taxonomy is applied to verified-but-not-remediable
items.  By the time we know which findings are deferred, the scoring layer
has already produced its verdict.

The defer-aware override therefore lives one layer up, in
``bmad_assist.core.loop.synthesis_contract`` — see
``_decision_from_parsed``.  When the synthesizer reports the new
``deferred_critical`` / ``deferred_high`` / ``remaining_critical`` /
``remaining_high`` fields with no real remaining items, the contract layer
overrides this module's score-based REJECT and returns
``CanonicalResolution.RESOLVED``.  scoring.py stays as pure math; policy
about defer-aware acceptance is the contract layer's job.
"""

from __future__ import annotations

from bmad_assist.deep_verify.core.types import (
    Finding,
    Severity,
    VerdictDecision,
)

# =============================================================================
# Constants
# =============================================================================

# Severity weights for scoring
# CRITICAL=4.0: Hard blocker - must fix
# ERROR=2.0: Soft blocker - can override
# WARNING=1.0: Advisory - flag in report
# INFO=0.5: Informational - minimal weight
SEVERITY_WEIGHTS: dict[Severity, float] = {
    Severity.CRITICAL: 4.0,
    Severity.ERROR: 2.0,
    Severity.WARNING: 1.0,
    Severity.INFO: 0.5,
}

# Clean pass bonus per domain with zero findings
CLEAN_PASS_BONUS: float = -0.5

# Verdict thresholds (non-overlapping)
# REJECT_THRESHOLD raised from 6.0 → 12.0 (Deep Verify P3): the prior 6.0
# tripped REJECT on any 2 CRITICAL findings (4.0 each) which proved too
# aggressive once noise filtering (P2: GEN-*/BOUNDARY-* exclusion) and
# language-aware pattern matching (P1) landed. Most files have 1–2
# legitimate CRITICAL findings; 12.0 keeps REJECT meaningful while letting
# UNCERTAIN catch the borderline cases for human review.
REJECT_THRESHOLD: float = 12.0
ACCEPT_THRESHOLD: float = -3.0

# Pattern IDs excluded from verdict scoring (kept in report as informational).
# These are spec-level checklists rather than code antipatterns:
#   - GEN-* are generic checklist items (e.g., GEN-002 "null pointer dereference")
#     pulled from patterns/data/checklists/general.yaml.
#   - *-BOUNDARY-* are domain boundary checklists (e.g., STORAGE-BOUNDARY-002,
#     API-BOUNDARY-001) pulled from patterns/data/checklists/<domain>.yaml.
# They surface as findings during verification so reviewers can see them,
# but they should NOT drive REJECT/UNCERTAIN/ACCEPT verdicts since they are
# spec-level concerns dressed up as code findings — they previously dominated
# noise (~58% of findings on a sample run) and inflated the score.
_VERDICT_EXCLUDED_PATTERN_PREFIXES: tuple[str, ...] = ("GEN-",)
_VERDICT_EXCLUDED_PATTERN_INFIXES: tuple[str, ...] = ("-BOUNDARY-",)


def _is_excluded_from_verdict(pattern_id: str | None) -> bool:
    """Return True if a pattern_id should be excluded from verdict scoring.

    Findings with these pattern IDs remain in the report (so reviewers see
    them) but do not contribute to the score or the auto-REJECT-on-CRITICAL
    rule. See _VERDICT_EXCLUDED_PATTERN_PREFIXES /
    _VERDICT_EXCLUDED_PATTERN_INFIXES for the active rules.

    Args:
        pattern_id: Pattern identifier string (e.g., "GEN-001",
            "STORAGE-BOUNDARY-002") or None for findings without a pattern.

    Returns:
        True if this pattern_id is on the exclusion list, False otherwise
        (including when pattern_id is None).

    """
    if pattern_id is None:
        return False
    if any(pattern_id.startswith(p) for p in _VERDICT_EXCLUDED_PATTERN_PREFIXES):
        return True
    if any(infix in pattern_id for infix in _VERDICT_EXCLUDED_PATTERN_INFIXES):
        return True
    return False


def _filter_for_verdict(findings: list[Finding]) -> list[Finding]:
    """Filter findings down to those that should affect the verdict score.

    Excludes generic checklist patterns (see _is_excluded_from_verdict).

    Args:
        findings: All findings produced by verification.

    Returns:
        Subset of findings to use for scoring and verdict determination.

    """
    return [f for f in findings if not _is_excluded_from_verdict(f.pattern_id)]


# =============================================================================
# Scoring Functions
# =============================================================================


def calculate_score(findings: list[Finding], clean_passes: int = 0) -> float:
    """Calculate evidence score from findings and clean passes.

    Formula:
        S = Σ(severity_weight × confidence) + (clean_passes × -0.5)

    Where severity_weight is:
        - CRITICAL: 4.0
        - ERROR: 2.0
        - WARNING: 1.0
        - INFO: 0.5

    And confidence is the average confidence of evidence for each finding
    (defaulting to 1.0 if no evidence).

    Args:
        findings: List of Finding objects to score.
        clean_passes: Number of domains with zero findings (-0.5 bonus each).

    Returns:
        Calculated score rounded to 2 decimal places.

    Example:
        >>> findings = [
        ...     Finding(id="F1", severity=Severity.ERROR, ..., evidence=[...]),
        ...     Finding(id="F2", severity=Severity.WARNING, ..., evidence=[]),
        ... ]
        >>> score = calculate_score(findings, clean_passes=1)
        >>> # Score = (2.0 × 1.0) + (1.0 × 1.0) + (1 × -0.5) = 2.5

    """
    findings_score = 0.0

    # Exclude generic checklist patterns (GEN-*, *-BOUNDARY-*) from scoring;
    # they remain in the full report for reviewers but should not drive the
    # numeric verdict score.
    verdict_findings = _filter_for_verdict(findings)

    for finding in verdict_findings:
        # Get severity weight
        weight = SEVERITY_WEIGHTS[finding.severity]

        # Calculate average confidence from evidence
        if finding.evidence:
            avg_confidence = sum(e.confidence for e in finding.evidence) / len(finding.evidence)
        else:
            avg_confidence = 1.0  # Default confidence if no evidence

        findings_score += weight * avg_confidence

    # Add clean pass bonus
    clean_pass_score = clean_passes * CLEAN_PASS_BONUS

    total = findings_score + clean_pass_score
    return round(total, 2)


def determine_verdict(score: float, findings: list[Finding] | None = None) -> VerdictDecision:
    """Determine verdict from evidence score.

    Uses non-overlapping thresholds:
        - score > 12   → REJECT (too many high-severity findings)
        - -3 ≤ score ≤ 12 → UNCERTAIN (needs human review)
        - score < -3   → ACCEPT (clean enough)

    CRITICAL findings always result in REJECT verdict (hard block).

    The clean pass bonus (-0.5 per clean domain) enables negative scores,
    which are needed to reach ACCEPT verdict.

    Args:
        score: Evidence score from calculate_score().
        findings: Optional list of findings to check for CRITICAL severity.

    Returns:
        VerdictDecision based on thresholds.

    Example:
        >>> determine_verdict(15.0)  # VerdictDecision.REJECT
        >>> determine_verdict(2.0)   # VerdictDecision.UNCERTAIN
        >>> determine_verdict(-4.0)  # VerdictDecision.ACCEPT

    """
    # CRITICAL findings are hard blocks - always REJECT.
    # Excluded checklist patterns (GEN-*, *-BOUNDARY-*) do NOT trigger this
    # rule even when their severity is CRITICAL: they are spec-level
    # checklists, not real code antipatterns, and would otherwise force
    # REJECT regardless of the actual code-level findings.
    if findings and any(
        f.severity == Severity.CRITICAL and not _is_excluded_from_verdict(f.pattern_id)
        for f in findings
    ):
        return VerdictDecision.REJECT

    if score > REJECT_THRESHOLD:
        return VerdictDecision.REJECT
    elif score < ACCEPT_THRESHOLD:
        return VerdictDecision.ACCEPT
    else:
        return VerdictDecision.UNCERTAIN


# =============================================================================
# EvidenceScorer Class
# =============================================================================


class EvidenceScorer:
    """Scorer for Deep Verify findings with configurable thresholds.

    This class provides a configurable interface to the scoring system,
    allowing threshold overrides via configuration.

    Attributes:
        severity_weights: Mapping of severity to weight.
        clean_pass_bonus: Bonus per clean domain (negative value).
        reject_threshold: Score threshold for REJECT verdict.
        accept_threshold: Score threshold for ACCEPT verdict.

    """

    def __init__(
        self,
        severity_weights: dict[Severity, float] | None = None,
        clean_pass_bonus: float = CLEAN_PASS_BONUS,
        reject_threshold: float = REJECT_THRESHOLD,
        accept_threshold: float = ACCEPT_THRESHOLD,
    ) -> None:
        """Initialize EvidenceScorer with optional custom thresholds.

        Args:
            severity_weights: Custom severity weights (defaults to SEVERITY_WEIGHTS).
            clean_pass_bonus: Custom clean pass bonus (defaults to -0.5).
            reject_threshold: Custom reject threshold (defaults to 12.0).
            accept_threshold: Custom accept threshold (defaults to -3.0).

        Raises:
            ValueError: If thresholds are invalid (reject <= accept).

        """
        self.severity_weights = severity_weights or SEVERITY_WEIGHTS.copy()
        self.clean_pass_bonus = clean_pass_bonus
        self.reject_threshold = reject_threshold
        self.accept_threshold = accept_threshold

        # Validate thresholds
        if reject_threshold <= accept_threshold:
            raise ValueError(
                f"reject_threshold ({reject_threshold}) must be greater than "
                f"accept_threshold ({accept_threshold})"
            )

    def calculate_score(self, findings: list[Finding], clean_passes: int = 0) -> float:
        """Calculate evidence score using instance configuration.

        Formula:
            S = Σ(severity_weight × confidence) + (clean_passes × clean_pass_bonus)

        Args:
            findings: List of Finding objects to score.
            clean_passes: Number of domains with zero findings.

        Returns:
            Calculated score rounded to 2 decimal places.

        """
        findings_score = 0.0

        # Exclude generic checklist patterns (GEN-*, *-BOUNDARY-*) from scoring;
        # they remain in the full report for reviewers but should not drive
        # the numeric verdict score.
        verdict_findings = _filter_for_verdict(findings)

        for finding in verdict_findings:
            weight = self.severity_weights[finding.severity]

            if finding.evidence:
                avg_confidence = sum(e.confidence for e in finding.evidence) / len(finding.evidence)
            else:
                avg_confidence = 1.0  # Default confidence if no evidence

            findings_score += weight * avg_confidence

        clean_pass_score = clean_passes * self.clean_pass_bonus
        total = findings_score + clean_pass_score
        return round(total, 2)

    def determine_verdict(
        self, score: float, findings: list[Finding] | None = None
    ) -> VerdictDecision:
        """Determine verdict using instance thresholds.

        CRITICAL findings always result in REJECT verdict (hard block).

        Args:
            score: Evidence score from calculate_score().
            findings: Optional list of findings to check for CRITICAL severity.

        Returns:
            VerdictDecision based on instance thresholds.

        """
        # CRITICAL findings are hard blocks - always REJECT.
        # Excluded checklist patterns (GEN-*, *-BOUNDARY-*) do NOT trigger
        # this rule even when their severity is CRITICAL: they are
        # spec-level checklists, not real code antipatterns, and would
        # otherwise force REJECT regardless of the actual code-level
        # findings.
        if findings and any(
            f.severity == Severity.CRITICAL and not _is_excluded_from_verdict(f.pattern_id)
            for f in findings
        ):
            return VerdictDecision.REJECT

        if score > self.reject_threshold:
            return VerdictDecision.REJECT
        elif score < self.accept_threshold:
            return VerdictDecision.ACCEPT
        else:
            return VerdictDecision.UNCERTAIN

    def get_verdict_with_confidence(
        self,
        score: float,
        total_findings: int,
        critical_count: int,
    ) -> tuple[VerdictDecision, float]:
        """Get verdict with confidence level.

        Confidence is based on:
        - Distance from thresholds
        - Presence of CRITICAL findings
        - Total number of findings

        Args:
            score: Evidence score.
            total_findings: Total number of findings.
            critical_count: Number of CRITICAL severity findings.

        Returns:
            Tuple of (verdict, confidence) where confidence is 0.0-1.0.

        """
        verdict = self.determine_verdict(score)

        # Base confidence on distance from nearest threshold
        if verdict == VerdictDecision.REJECT:
            # Confidence increases as score exceeds reject_threshold
            distance = score - self.reject_threshold
            base_confidence = min(0.5 + (distance / 10.0), 1.0)
            # Boost for critical findings
            if critical_count > 0:
                base_confidence = min(base_confidence + 0.2, 1.0)
        elif verdict == VerdictDecision.ACCEPT:
            # Confidence increases as score falls below accept_threshold
            distance = abs(score - self.accept_threshold)
            base_confidence = min(0.5 + (distance / 6.0), 1.0)
            # Boost for many clean passes
            if total_findings == 0:
                base_confidence = min(base_confidence + 0.2, 1.0)
        else:  # UNCERTAIN
            # Confidence decreases as score approaches middle of uncertain range
            middle = (self.reject_threshold + self.accept_threshold) / 2
            distance = abs(score - middle)
            max_distance = (self.reject_threshold - self.accept_threshold) / 2
            base_confidence = distance / max_distance if max_distance > 0 else 0.5

        return verdict, round(base_confidence, 2)
