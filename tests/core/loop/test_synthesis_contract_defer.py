"""Tests for defer-aware resolution overrides in ``synthesis_contract``.

D.3 (2026-05) — when the code-review-synthesis SKILL emits the new
``deferred_critical`` / ``deferred_high`` / ``remaining_critical`` /
``remaining_high`` fields, the contract layer applies three policy rules in
order:

  1. Backwards compatibility — defer fields absent → legacy behaviour.
  2. Safety cap — too many deferrals → HALT (override LLM-resolved).
  3. Defer-aware override — LLM-resolved with no real remaining items AND
     evidence verdict was REJECT/MAJOR_REWORK driven solely by deferred
     findings → trust LLM (score-layer REJECT treated as PASS).

This module covers each branch end-to-end through
``make_synthesis_decision`` plus a couple of integration cases that walk
through ``parse_resolution_block`` first.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from bmad_assist.core.loop.synthesis_contract import (
    CanonicalResolution,
    ExtractionQuality,
    FailureClass,
    make_synthesis_decision,
    parse_resolution_block,
)

if TYPE_CHECKING:
    from _pytest.logging import LogCaptureFixture


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parsed(
    resolution: str = "resolved",
    *,
    verified_critical: int = 0,
    verified_high: int = 0,
    fixed_critical: int = 0,
    fixed_high: int = 0,
    deferred_critical: int | None = None,
    deferred_high: int | None = None,
    remaining_critical: int = 0,
    remaining_high: int = 0,
) -> dict[str, Any]:
    """Build a parsed resolution dict.

    ``deferred_*`` is ``None`` by default so tests exercising the legacy
    (pre-D.3) path can omit the fields entirely.
    """
    parsed: dict[str, Any] = {
        "resolution": resolution,
        "verified_critical": verified_critical,
        "verified_high": verified_high,
        "fixed_critical": fixed_critical,
        "fixed_high": fixed_high,
        "remaining_critical": remaining_critical,
        "remaining_high": remaining_high,
    }
    if deferred_critical is not None:
        parsed["deferred_critical"] = deferred_critical
    if deferred_high is not None:
        parsed["deferred_high"] = deferred_high
    return parsed


def _evidence(critical: int = 0, important: int = 0) -> dict[str, Any]:
    """Build evidence_score_data with the given pre-synthesis findings."""
    return {"findings_summary": {"CRITICAL": critical, "IMPORTANT": important}}


# ---------------------------------------------------------------------------
# Rule 1 — backwards compatibility
# ---------------------------------------------------------------------------


class TestBackwardsCompatibility:
    """Defer fields absent → legacy behaviour preserved."""

    def test_legacy_resolved_no_evidence_returns_resolved(self) -> None:
        """Legacy block (no defer fields) + LLM resolved → RESOLVED."""
        decision = make_synthesis_decision(
            parsed=_parsed(resolution="resolved"),
            quality=ExtractionQuality.STRICT,
            evidence_verdict="PASS",
            evidence_score_data=None,
        )
        assert decision.resolution == CanonicalResolution.RESOLVED
        assert decision.failure_class is None

    def test_legacy_resolved_with_zero_accounting_still_halts(self) -> None:
        """Legacy block, REJECT verdict, nothing accounted for → HALT.

        This is the pre-D.3 cross-validation rule: if evidence shows
        CRITICAL/IMPORTANT > 0 but the LLM reports fixed+dismissed=0, we
        cannot trust the resolved claim.  Behaviour must be preserved when
        defer fields are absent.
        """
        decision = make_synthesis_decision(
            parsed=_parsed(
                resolution="resolved",
                fixed_critical=0,
                fixed_high=0,
                remaining_critical=2,
                remaining_high=1,
            ),
            quality=ExtractionQuality.STRICT,
            evidence_verdict="REJECT",
            evidence_score_data=_evidence(critical=2, important=1),
        )
        assert decision.resolution == CanonicalResolution.HALT
        assert decision.failure_class == FailureClass.HALT

    def test_legacy_resolved_with_inferred_dismissal_passes(self) -> None:
        """Legacy block + inferred dismissal (no defer fields) → RESOLVED."""
        decision = make_synthesis_decision(
            parsed=_parsed(
                resolution="resolved",
                fixed_critical=0,
                fixed_high=0,
                remaining_critical=0,
                remaining_high=0,
            ),
            quality=ExtractionQuality.STRICT,
            evidence_verdict="REJECT",
            evidence_score_data=_evidence(critical=2, important=1),
        )
        assert decision.resolution == CanonicalResolution.RESOLVED


# ---------------------------------------------------------------------------
# Rule 2 — safety cap
# ---------------------------------------------------------------------------


class TestSafetyCap:
    """Excessive deferrals → HALT regardless of LLM-resolved claim."""

    def test_deferred_critical_above_cap_halts(self) -> None:
        """deferred_critical > 3 → HALT (override LLM resolved)."""
        decision = make_synthesis_decision(
            parsed=_parsed(
                resolution="resolved",
                deferred_critical=4,
                deferred_high=0,
            ),
            quality=ExtractionQuality.STRICT,
            evidence_verdict="REJECT",
            evidence_score_data=_evidence(critical=4),
        )
        assert decision.resolution == CanonicalResolution.HALT
        assert decision.failure_class == FailureClass.HALT
        assert "safety cap" in decision.evidence_summary.lower()

    def test_deferred_high_above_cap_halts(self) -> None:
        """deferred_high > 5 → HALT (override LLM resolved)."""
        decision = make_synthesis_decision(
            parsed=_parsed(
                resolution="resolved",
                deferred_critical=0,
                deferred_high=6,
            ),
            quality=ExtractionQuality.STRICT,
            evidence_verdict="REJECT",
            evidence_score_data=_evidence(important=6),
        )
        assert decision.resolution == CanonicalResolution.HALT
        assert decision.failure_class == FailureClass.HALT

    def test_cap_boundary_critical_three_does_not_trip(self) -> None:
        """Exactly at the cap (deferred_critical=3) → defer-aware override fires."""
        decision = make_synthesis_decision(
            parsed=_parsed(
                resolution="resolved",
                deferred_critical=3,
                deferred_high=0,
                remaining_critical=0,
                remaining_high=0,
            ),
            quality=ExtractionQuality.STRICT,
            evidence_verdict="REJECT",
            evidence_score_data=_evidence(critical=3),
        )
        assert decision.resolution == CanonicalResolution.RESOLVED

    def test_cap_boundary_high_five_does_not_trip(self) -> None:
        """Exactly at the cap (deferred_high=5) → defer-aware override fires."""
        decision = make_synthesis_decision(
            parsed=_parsed(
                resolution="resolved",
                deferred_critical=0,
                deferred_high=5,
                remaining_critical=0,
                remaining_high=0,
            ),
            quality=ExtractionQuality.STRICT,
            evidence_verdict="REJECT",
            evidence_score_data=_evidence(important=5),
        )
        assert decision.resolution == CanonicalResolution.RESOLVED

    def test_cap_does_not_trip_when_llm_says_rework(self) -> None:
        """Safety cap fires only when LLM said resolved.

        Rework already implies "stop and fix" — the cap is meant to catch
        misclassified resolved outcomes.
        """
        decision = make_synthesis_decision(
            parsed=_parsed(
                resolution="rework",
                deferred_critical=10,
                deferred_high=10,
                remaining_critical=1,
            ),
            quality=ExtractionQuality.STRICT,
            evidence_verdict="REJECT",
            evidence_score_data=_evidence(critical=10),
        )
        assert decision.resolution == CanonicalResolution.REWORK


# ---------------------------------------------------------------------------
# Rule 3 — defer-aware override
# ---------------------------------------------------------------------------


class TestDeferAwareOverride:
    """LLM resolved + remaining_*==0 + REJECT-due-to-defer → RESOLVED."""

    def test_override_with_reject_verdict_returns_resolved(self) -> None:
        """REJECT verdict driven by deferred CRITICAL → defer-aware RESOLVED."""
        decision = make_synthesis_decision(
            parsed=_parsed(
                resolution="resolved",
                verified_critical=1,
                deferred_critical=1,
                deferred_high=0,
                remaining_critical=0,
                remaining_high=0,
            ),
            quality=ExtractionQuality.STRICT,
            evidence_verdict="REJECT",
            evidence_score_data=_evidence(critical=1),
        )
        assert decision.resolution == CanonicalResolution.RESOLVED
        assert decision.failure_class is None
        assert "defer-aware override" in decision.evidence_summary.lower()

    def test_override_with_major_rework_verdict_returns_resolved(self) -> None:
        """MAJOR_REWORK verdict driven by deferred HIGH → RESOLVED."""
        decision = make_synthesis_decision(
            parsed=_parsed(
                resolution="resolved",
                verified_high=2,
                deferred_critical=0,
                deferred_high=2,
                remaining_critical=0,
                remaining_high=0,
            ),
            quality=ExtractionQuality.STRICT,
            evidence_verdict="MAJOR_REWORK",
            evidence_score_data=_evidence(important=2),
        )
        assert decision.resolution == CanonicalResolution.RESOLVED

    def test_override_logs_info_when_firing(self, caplog: LogCaptureFixture) -> None:
        """Defer-aware override emits an info log so the audit trail is clear."""
        caplog.set_level(logging.INFO, logger="bmad_assist.core.loop.synthesis_contract")
        make_synthesis_decision(
            parsed=_parsed(
                resolution="resolved",
                deferred_critical=1,
                deferred_high=0,
                remaining_critical=0,
                remaining_high=0,
            ),
            quality=ExtractionQuality.STRICT,
            evidence_verdict="REJECT",
            evidence_score_data=_evidence(critical=1),
        )
        assert any(
            "defer-aware override" in record.getMessage().lower() for record in caplog.records
        )

    def test_override_skipped_when_no_deferred_items(self) -> None:
        """Defer fields = 0 (explicit) but verdict is PASS → standard RESOLVED.

        The override only adds value when the verdict was REJECT/MAJOR_REWORK.
        A PASS verdict with zero deferrals takes the normal resolved path.
        """
        decision = make_synthesis_decision(
            parsed=_parsed(
                resolution="resolved",
                deferred_critical=0,
                deferred_high=0,
                remaining_critical=0,
                remaining_high=0,
            ),
            quality=ExtractionQuality.STRICT,
            evidence_verdict="PASS",
            evidence_score_data=None,
        )
        assert decision.resolution == CanonicalResolution.RESOLVED

    def test_override_skipped_when_evidence_verdict_is_uncertain(self) -> None:
        """UNCERTAIN verdict is not a REJECT — override does not fire here.

        We fall through to the legacy accounting which trusts resolved.
        """
        decision = make_synthesis_decision(
            parsed=_parsed(
                resolution="resolved",
                deferred_critical=1,
                deferred_high=0,
                remaining_critical=0,
                remaining_high=0,
            ),
            quality=ExtractionQuality.STRICT,
            evidence_verdict="UNCERTAIN",
            evidence_score_data=_evidence(critical=1),
        )
        # Legacy accounting accepts resolved (inferred dismissed = 1).
        assert decision.resolution == CanonicalResolution.RESOLVED


# ---------------------------------------------------------------------------
# LLM-lied case — resolved but remaining > 0
# ---------------------------------------------------------------------------


class TestLLMLied:
    """LLM resolved with remaining_* > 0 must be rewritten to rework.

    ``parse_resolution_block`` performs this cross-validation before the
    contract layer sees the resolution string.
    """

    def test_llm_resolved_with_remaining_critical_rewritten(self) -> None:
        """remaining_critical > 0 forces resolution to rework."""
        block = (
            "resolution: resolved\n"
            "verified_critical: 2\n"
            "verified_high: 0\n"
            "fixed_critical: 0\n"
            "fixed_high: 0\n"
            "deferred_critical: 0\n"
            "deferred_high: 0\n"
            "remaining_critical: 2\n"
            "remaining_high: 0\n"
        )
        parsed = parse_resolution_block(block)
        assert parsed is not None
        # parse_resolution_block rewrites to "rework"
        assert parsed["resolution"] == "rework"

        decision = make_synthesis_decision(
            parsed=parsed,
            quality=ExtractionQuality.STRICT,
            evidence_verdict="REJECT",
            evidence_score_data=_evidence(critical=2),
        )
        assert decision.resolution == CanonicalResolution.REWORK

    def test_llm_resolved_with_remaining_high_rewritten(self) -> None:
        """remaining_high > 0 forces resolution to rework."""
        block = (
            "resolution: resolved\n"
            "verified_critical: 0\n"
            "verified_high: 1\n"
            "fixed_critical: 0\n"
            "fixed_high: 0\n"
            "deferred_critical: 0\n"
            "deferred_high: 0\n"
            "remaining_critical: 0\n"
            "remaining_high: 1\n"
        )
        parsed = parse_resolution_block(block)
        assert parsed is not None
        assert parsed["resolution"] == "rework"


# ---------------------------------------------------------------------------
# Standard PASS path — no deferrals, no remaining
# ---------------------------------------------------------------------------


class TestStandardPassPath:
    """LLM resolved with no defer + no remaining + PASS verdict → RESOLVED."""

    def test_clean_pass_unchanged(self) -> None:
        """No defer + PASS verdict + zero remaining → standard RESOLVED."""
        decision = make_synthesis_decision(
            parsed=_parsed(
                resolution="resolved",
                deferred_critical=0,
                deferred_high=0,
                remaining_critical=0,
                remaining_high=0,
            ),
            quality=ExtractionQuality.STRICT,
            evidence_verdict="PASS",
            evidence_score_data=_evidence(critical=0, important=0),
        )
        assert decision.resolution == CanonicalResolution.RESOLVED
        # Should not mention defer override — this is the standard path.
        assert "defer-aware override" not in decision.evidence_summary.lower()

    def test_legacy_clean_pass_unchanged(self) -> None:
        """Legacy block (no defer fields), PASS verdict → RESOLVED."""
        decision = make_synthesis_decision(
            parsed=_parsed(resolution="resolved"),
            quality=ExtractionQuality.STRICT,
            evidence_verdict="PASS",
            evidence_score_data=None,
        )
        assert decision.resolution == CanonicalResolution.RESOLVED


# ---------------------------------------------------------------------------
# parse_resolution_block round-trip for new fields
# ---------------------------------------------------------------------------


class TestParseRoundTrip:
    """End-to-end through parse_resolution_block + make_synthesis_decision."""

    def test_e2e_defer_override(self) -> None:
        """Full SYNTHESIS_RESOLUTION block with deferred fields → RESOLVED."""
        block = (
            "resolution: resolved\n"
            "verified_critical: 1\n"
            "verified_high: 0\n"
            "fixed_critical: 0\n"
            "fixed_high: 0\n"
            "deferred_critical: 1\n"
            "deferred_high: 0\n"
            "remaining_critical: 0\n"
            "remaining_high: 0\n"
        )
        parsed = parse_resolution_block(block)
        assert parsed is not None
        assert parsed["resolution"] == "resolved"
        assert parsed["deferred_critical"] == 1

        decision = make_synthesis_decision(
            parsed=parsed,
            quality=ExtractionQuality.STRICT,
            evidence_verdict="REJECT",
            evidence_score_data=_evidence(critical=1),
        )
        assert decision.resolution == CanonicalResolution.RESOLVED

    def test_e2e_safety_cap(self) -> None:
        """Full block with deferred_critical=4 → HALT via safety cap."""
        block = (
            "resolution: resolved\n"
            "verified_critical: 4\n"
            "verified_high: 0\n"
            "fixed_critical: 0\n"
            "fixed_high: 0\n"
            "deferred_critical: 4\n"
            "deferred_high: 0\n"
            "remaining_critical: 0\n"
            "remaining_high: 0\n"
        )
        parsed = parse_resolution_block(block)
        assert parsed is not None

        decision = make_synthesis_decision(
            parsed=parsed,
            quality=ExtractionQuality.STRICT,
            evidence_verdict="REJECT",
            evidence_score_data=_evidence(critical=4),
        )
        assert decision.resolution == CanonicalResolution.HALT
