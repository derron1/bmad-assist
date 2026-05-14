"""Tests for verdict-scoring exclusion of checklist patterns.

These tests cover the Deep Verify P2 fix that excludes spec-level checklist
findings (`GEN-*`, `*-BOUNDARY-*`) from the verdict score and from the
auto-REJECT-on-CRITICAL rule, while still letting them appear in the report.

See:
- bmad_assist.deep_verify.core.scoring._VERDICT_EXCLUDED_PATTERN_PREFIXES
- bmad_assist.deep_verify.core.scoring._VERDICT_EXCLUDED_PATTERN_INFIXES
- bmad_assist.deep_verify.core.scoring._is_excluded_from_verdict
- bmad_assist.deep_verify.core.scoring._filter_for_verdict
"""

from __future__ import annotations

from bmad_assist.deep_verify.core.scoring import (
    SEVERITY_WEIGHTS,
    EvidenceScorer,
    _filter_for_verdict,
    _is_excluded_from_verdict,
    calculate_score,
    determine_verdict,
)
from bmad_assist.deep_verify.core.types import (
    Finding,
    MethodId,
    PatternId,
    Severity,
    VerdictDecision,
)


def _finding(
    fid: str,
    severity: Severity,
    pattern_id: str | None,
) -> Finding:
    """Construct a minimal Finding for verdict-filter tests.

    Evidence is intentionally empty so each finding scores at confidence 1.0
    (which makes the expected scores trivially equal to severity weights).
    """
    return Finding(
        id=fid,
        severity=severity,
        title=f"{fid} title",
        description=f"{fid} description",
        method_id=MethodId("#153"),
        pattern_id=PatternId(pattern_id) if pattern_id is not None else None,
    )


# =============================================================================
# _is_excluded_from_verdict / _filter_for_verdict unit checks
# =============================================================================


class TestIsExcludedFromVerdict:
    """Direct tests for the exclusion predicate."""

    def test_gen_prefix_excluded(self) -> None:
        """GEN-* checklist IDs should be excluded from the verdict."""
        assert _is_excluded_from_verdict("GEN-001") is True
        assert _is_excluded_from_verdict("GEN-002") is True
        assert _is_excluded_from_verdict("GEN-008") is True

    def test_boundary_infix_excluded(self) -> None:
        """*-BOUNDARY-* checklist IDs should be excluded from the verdict."""
        assert _is_excluded_from_verdict("STORAGE-BOUNDARY-002") is True
        assert _is_excluded_from_verdict("API-BOUNDARY-001") is True
        assert _is_excluded_from_verdict("CC-BOUNDARY-004") is True
        assert _is_excluded_from_verdict("MSG-BOUNDARY-006") is True
        assert _is_excluded_from_verdict("SEC-BOUNDARY-003") is True
        assert _is_excluded_from_verdict("TF-BOUNDARY-002") is True

    def test_real_code_patterns_not_excluded(self) -> None:
        """Real code-pattern IDs (RCW-*, CQ-*-CODE-*, CC-*, SEC-*) pass through."""
        assert _is_excluded_from_verdict("RCW-001") is False
        assert _is_excluded_from_verdict("CQ-002-CODE-GO") is False
        assert _is_excluded_from_verdict("CQ-005-CODE-PY") is False
        assert _is_excluded_from_verdict("CC-001") is False
        assert _is_excluded_from_verdict("SEC-004") is False

    def test_none_pattern_not_excluded(self) -> None:
        """Findings without a pattern_id (None) are NOT excluded."""
        # Method-level findings without a pattern match should still count
        # toward the verdict.
        assert _is_excluded_from_verdict(None) is False


class TestFilterForVerdict:
    """Tests for the list-level filter helper."""

    def test_filters_excluded_keeps_others(self) -> None:
        """The filter drops excluded patterns and keeps real ones, in order."""
        findings = [
            _finding("F1", Severity.CRITICAL, "GEN-001"),
            _finding("F2", Severity.ERROR, "STORAGE-BOUNDARY-002"),
            _finding("F3", Severity.CRITICAL, "RCW-001"),
            _finding("F4", Severity.ERROR, "CQ-002-CODE-GO"),
            _finding("F5", Severity.WARNING, "CQ-005-CODE-PY"),
        ]

        kept = _filter_for_verdict(findings)
        kept_ids = [f.id for f in kept]

        assert kept_ids == ["F3", "F4", "F5"]

    def test_empty_input(self) -> None:
        """An empty findings list filters down to an empty list."""
        assert _filter_for_verdict([]) == []

    def test_all_excluded(self) -> None:
        """A list containing only excluded patterns filters to empty."""
        findings = [
            _finding("F1", Severity.CRITICAL, "GEN-001"),
            _finding("F2", Severity.ERROR, "API-BOUNDARY-002"),
        ]
        assert _filter_for_verdict(findings) == []


# =============================================================================
# Mixed-finding scoring + verdict end-to-end checks
# =============================================================================


class TestMixedFindingsScoring:
    """End-to-end test of the scenario described by the P2 fix.

    Mixed findings:
      - GEN-001                 CRITICAL  (excluded — checklist)
      - STORAGE-BOUNDARY-002    ERROR     (excluded — checklist)
      - RCW-001                 CRITICAL  (counted — real code finding)
      - CQ-002-CODE-GO          ERROR     (counted — real code finding)
      - CQ-005-CODE-PY          WARNING   (counted — real code finding)

    Expected counted score = 4.0 (CRITICAL) + 2.0 (ERROR) + 1.0 (WARNING) = 7.0
    """

    @staticmethod
    def _mixed_findings() -> list[Finding]:
        return [
            _finding("F1", Severity.CRITICAL, "GEN-001"),
            _finding("F2", Severity.ERROR, "STORAGE-BOUNDARY-002"),
            _finding("F3", Severity.CRITICAL, "RCW-001"),
            _finding("F4", Severity.ERROR, "CQ-002-CODE-GO"),
            _finding("F5", Severity.WARNING, "CQ-005-CODE-PY"),
        ]

    def test_calculate_score_only_counts_non_excluded(self) -> None:
        """calculate_score sums weights only for non-excluded findings."""
        findings = self._mixed_findings()

        expected = (
            SEVERITY_WEIGHTS[Severity.CRITICAL]  # RCW-001
            + SEVERITY_WEIGHTS[Severity.ERROR]  # CQ-002-CODE-GO
            + SEVERITY_WEIGHTS[Severity.WARNING]  # CQ-005-CODE-PY
        )

        assert calculate_score(findings) == round(expected, 2) == 7.0

    def test_calculate_score_baseline_without_excluded(self) -> None:
        """Pre-filtering excluded findings yields the same score."""
        # Sanity: removing the excluded findings ahead of time gives the
        # same numeric result — i.e., the filter is doing what we expect.
        findings = self._mixed_findings()
        non_excluded = [
            f for f in findings if f.pattern_id not in {"GEN-001", "STORAGE-BOUNDARY-002"}
        ]

        assert calculate_score(findings) == calculate_score(non_excluded)

    def test_evidence_scorer_calculate_score_filters(self) -> None:
        """EvidenceScorer.calculate_score also applies the verdict filter."""
        scorer = EvidenceScorer()
        findings = self._mixed_findings()
        # 4.0 (CRITICAL RCW) + 2.0 (ERROR CQ-CODE-GO) + 1.0 (WARNING CQ-CODE-PY)
        assert scorer.calculate_score(findings) == 7.0

    def test_determine_verdict_critical_in_excluded_does_not_force_reject(self) -> None:
        """A CRITICAL on an excluded pattern must not auto-REJECT the verdict."""
        # Construct a list whose ONLY CRITICAL is GEN-001 (excluded).
        # With score below the REJECT threshold the verdict must NOT be REJECT.
        findings = [
            _finding("F1", Severity.CRITICAL, "GEN-001"),  # excluded CRITICAL
            _finding("F2", Severity.WARNING, "CQ-005-CODE-PY"),
        ]
        score = calculate_score(findings)  # 1.0 (WARNING only)
        verdict = determine_verdict(score, findings)

        # Score 1.0 falls in the UNCERTAIN band; the auto-REJECT rule must
        # NOT trigger because the only CRITICAL came from an excluded
        # checklist pattern.
        assert verdict != VerdictDecision.REJECT
        assert verdict == VerdictDecision.UNCERTAIN

    def test_determine_verdict_two_real_criticals_still_rejects(self) -> None:
        """Two CRITICALs on real (non-excluded) patterns still auto-REJECT (D.8)."""
        # D.8 raised the hard-block floor to N >= 2 non-excluded CRITICALs.
        # Two real CRITICALs still force REJECT, regardless of score.
        findings = [
            _finding("F1", Severity.CRITICAL, "RCW-001"),
            _finding("F2", Severity.CRITICAL, "CQ-002-CODE-GO"),
        ]
        # Pass an artificially-low score to prove the CRITICAL rule is what
        # drives REJECT here.
        assert determine_verdict(-100.0, findings) == VerdictDecision.REJECT

    def test_evidence_scorer_determine_verdict_filters_critical(self) -> None:
        """EvidenceScorer.determine_verdict mirrors the standalone behavior."""
        scorer = EvidenceScorer()
        # Two excluded CRITICALs: still no hard-block, score-based path
        # applies and lands UNCERTAIN at score 0.0.
        excluded_only = [
            _finding("F1", Severity.CRITICAL, "API-BOUNDARY-002"),
            _finding("F2", Severity.CRITICAL, "GEN-001"),
        ]
        assert scorer.determine_verdict(0.0, excluded_only) == VerdictDecision.UNCERTAIN

        # Two real CRITICALs cross the D.8 default threshold → REJECT.
        real_criticals = [
            _finding("F3", Severity.CRITICAL, "RCW-001"),
            _finding("F4", Severity.CRITICAL, "CQ-002-CODE-GO"),
        ]
        assert scorer.determine_verdict(0.0, real_criticals) == VerdictDecision.REJECT

    def test_mixed_findings_full_pipeline(self) -> None:
        """End-to-end: mixed findings, only one real CRITICAL → score-based path (UNCERTAIN)."""
        # Headline of the combined P2 + D.8 fix: with mixed findings, the
        # GEN-* CRITICAL does not auto-reject, AND a single real CRITICAL
        # (RCW-001) no longer hard-blocks either — we fall through to the
        # score-based path (score 7.0 < REJECT_THRESHOLD 12.0 → UNCERTAIN).
        findings = self._mixed_findings()
        score = calculate_score(findings)
        verdict = determine_verdict(score, findings)

        # Score = 7.0 (CRITICAL RCW + ERROR CQ-GO + WARNING CQ-PY).
        assert score == 7.0
        # One real CRITICAL on its own no longer forces REJECT under D.8.
        assert verdict == VerdictDecision.UNCERTAIN

    def test_mixed_findings_without_real_critical(self) -> None:
        """Without a real CRITICAL the GEN-* CRITICAL no longer drives REJECT."""
        # If we drop the real CRITICAL finding, the GEN-001 CRITICAL must
        # NOT force REJECT — the score (now 3.0) lands UNCERTAIN.
        findings = [
            _finding("F1", Severity.CRITICAL, "GEN-001"),
            _finding("F2", Severity.ERROR, "STORAGE-BOUNDARY-002"),
            _finding("F4", Severity.ERROR, "CQ-002-CODE-GO"),
            _finding("F5", Severity.WARNING, "CQ-005-CODE-PY"),
        ]
        score = calculate_score(findings)
        verdict = determine_verdict(score, findings)

        assert score == 3.0  # ERROR (CQ-GO) + WARNING (CQ-PY)
        assert verdict == VerdictDecision.UNCERTAIN
