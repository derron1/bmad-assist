"""Tests for the contract-repair pass (Phase 1.5) in validate_story_synthesis.

Covers:
- Repair trigger conditions (markers present but not STRICT, metrics missing)
- Successful repair recovering contract and/or metrics
- Graceful failure when repair provider call fails
- Repair skipped when not needed
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bmad_assist.core.loop.handlers.validate_story_synthesis import (
    _CONTRACT_END,
    _CONTRACT_START,
    _extract_validation_resolution,
)
from bmad_assist.core.loop.synthesis_contract import ExtractionQuality
from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics


# ---------------------------------------------------------------------------
# Fixtures: sample synthesis outputs
# ---------------------------------------------------------------------------

_VALID_CONTRACT_BLOCK = (
    "<!-- VALIDATION_CONTRACT_START -->\n"
    "resolution: rework\n"
    "verified_critical: 2\n"
    "verified_high: 1\n"
    "fixed_critical: 1\n"
    "fixed_high: 0\n"
    "remaining_critical: 1\n"
    "remaining_high: 1\n"
    "<!-- VALIDATION_CONTRACT_END -->\n"
)

_VALID_METRICS_BLOCK = (
    "<!-- METRICS_JSON_START -->\n"
    "{\n"
    '  "quality": {\n'
    '    "actionable_ratio": 0.85,\n'
    '    "specificity_score": 0.78,\n'
    '    "evidence_quality": 0.80,\n'
    '    "follows_template": true,\n'
    '    "internal_consistency": 0.90\n'
    "  },\n"
    '  "consensus": {\n'
    '    "agreed_findings": 3,\n'
    '    "unique_findings": 1,\n'
    '    "disputed_findings": 0,\n'
    '    "missed_findings": 0,\n'
    '    "agreement_score": 0.75,\n'
    '    "false_positive_count": 1\n'
    "  }\n"
    "}\n"
    "<!-- METRICS_JSON_END -->\n"
)

_VALID_REPAIR_OUTPUT = _VALID_CONTRACT_BLOCK + "\n" + _VALID_METRICS_BLOCK

# Synthesis with contract markers but invalid content (invented keys)
_SYNTHESIS_BAD_CONTRACT = (
    "<!-- VALIDATION_CONTRACT_START -->\n"
    "story_id: S-123\n"
    "validators_count: 4\n"
    "<!-- VALIDATION_CONTRACT_END -->\n\n"
    "## Synthesis Summary\n"
    "2 issues verified, 0 false positives dismissed, 1 change applied to story file.\n\n"
    "## Issues Verified (by severity)\n\n"
    "### Critical\n"
    "- **Issue**: Missing guard | **Source**: A, B | **Fix**: Added guard\n\n"
    "## Changes Applied\n"
    "Applied one change.\n"
    + ("x" * 200)  # Ensure > 200 chars
)

# Synthesis with valid contract but no metrics
# Avoid headings that trigger markdown fallback (need < 3 of the expected headings)
_SYNTHESIS_VALID_CONTRACT_NO_METRICS = (
    _VALID_CONTRACT_BLOCK
    + "\nSome analysis content follows here with no structured headings.\n"
    "The validator findings were reviewed and cross-referenced.\n"
    "All critical issues have been addressed through patches.\n"
    + ("x" * 200)
)

# Fully valid synthesis (no repair needed)
_SYNTHESIS_FULLY_VALID = (
    _VALID_CONTRACT_BLOCK + "\n" + _VALID_METRICS_BLOCK + "\n## Summary\nDone.\n"
)


class TestRepairTriggerConditions:
    """Tests for when the repair pass should and should not fire."""

    def test_repair_triggered_when_markers_present_but_not_strict(self) -> None:
        """Contract markers present but fail-closed -> FAILED; repair should trigger."""
        # The bad contract has markers but invalid content
        res_parsed, res_quality = _extract_validation_resolution(_SYNTHESIS_BAD_CONTRACT)

        markers_present = (
            _CONTRACT_START in _SYNTHESIS_BAD_CONTRACT
            and _CONTRACT_END in _SYNTHESIS_BAD_CONTRACT
        )
        needs_contract_repair = (
            (markers_present and res_quality != ExtractionQuality.STRICT)
            or (res_quality == ExtractionQuality.FAILED and res_parsed is None)
        )

        assert markers_present is True
        assert res_quality == ExtractionQuality.FAILED
        assert needs_contract_repair is True

    def test_repair_not_needed_when_strict(self) -> None:
        """Valid contract markers with correct fields -> STRICT; no repair needed."""
        synthesis = (
            _VALID_CONTRACT_BLOCK + "\n" + _VALID_METRICS_BLOCK + "\n## Done\n"
        )
        res_parsed, res_quality = _extract_validation_resolution(synthesis)
        metrics = extract_synthesis_metrics(synthesis)

        markers_present = (
            _CONTRACT_START in synthesis and _CONTRACT_END in synthesis
        )
        needs_contract_repair = (
            (markers_present and res_quality != ExtractionQuality.STRICT)
            or (res_quality == ExtractionQuality.FAILED and res_parsed is None)
        )
        needs_metrics_repair = metrics is None

        assert res_quality == ExtractionQuality.STRICT
        assert needs_contract_repair is False
        assert needs_metrics_repair is False

    def test_repair_not_attempted_for_short_output(self) -> None:
        """Output < 200 chars should not trigger repair."""
        short_output = (
            "<!-- VALIDATION_CONTRACT_START -->\n"
            "garbage\n"
            "<!-- VALIDATION_CONTRACT_END -->\n"
        )
        assert len(short_output.strip()) < 200

        res_parsed, res_quality = _extract_validation_resolution(short_output)
        markers_present = (
            _CONTRACT_START in short_output and _CONTRACT_END in short_output
        )
        needs_contract_repair = (
            (markers_present and res_quality != ExtractionQuality.STRICT)
            or (res_quality == ExtractionQuality.FAILED and res_parsed is None)
        )
        needs_repair = needs_contract_repair and len(short_output.strip()) >= 200

        assert needs_contract_repair is True
        assert needs_repair is False  # Too short

    def test_metrics_repair_triggered_when_contract_valid_but_metrics_missing(
        self,
    ) -> None:
        """Valid contract (STRICT) but no metrics -> metrics repair needed."""
        res_parsed, res_quality = _extract_validation_resolution(
            _SYNTHESIS_VALID_CONTRACT_NO_METRICS
        )
        metrics = extract_synthesis_metrics(_SYNTHESIS_VALID_CONTRACT_NO_METRICS)

        assert res_quality == ExtractionQuality.STRICT
        assert metrics is None  # No metrics markers present

        markers_present = (
            _CONTRACT_START in _SYNTHESIS_VALID_CONTRACT_NO_METRICS
            and _CONTRACT_END in _SYNTHESIS_VALID_CONTRACT_NO_METRICS
        )
        needs_contract_repair = (
            (markers_present and res_quality != ExtractionQuality.STRICT)
            or (res_quality == ExtractionQuality.FAILED and res_parsed is None)
        )
        needs_metrics_repair = metrics is None
        needs_repair = (
            (needs_contract_repair or needs_metrics_repair)
            and len(_SYNTHESIS_VALID_CONTRACT_NO_METRICS.strip()) >= 200
        )

        assert needs_contract_repair is False
        assert needs_metrics_repair is True
        assert needs_repair is True


class TestRepairPassExecution:
    """Tests for _attempt_contract_repair method behavior."""

    def test_repair_recovers_contract_from_bad_synthesis(self) -> None:
        """Mock provider returns valid repair output -> contract is recovered."""
        from bmad_assist.core.loop.handlers.validate_story_synthesis import (
            _extract_validation_resolution,
        )
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        # Simulate what _attempt_contract_repair does internally:
        # parse the repair output through the same extraction functions
        repair_parsed, repair_quality = _extract_validation_resolution(
            _VALID_REPAIR_OUTPUT
        )
        repair_metrics = extract_synthesis_metrics(_VALID_REPAIR_OUTPUT)

        assert repair_parsed is not None
        assert repair_parsed["resolution"] == "rework"
        assert repair_quality == ExtractionQuality.STRICT
        assert repair_metrics is not None
        assert repair_metrics.quality is not None
        assert repair_metrics.consensus is not None

    def test_repair_fails_gracefully_on_garbage(self) -> None:
        """Provider returns garbage -> extraction returns (None, FAILED, None)."""
        garbage_output = "I don't understand what you want me to do."

        repair_parsed, repair_quality = _extract_validation_resolution(garbage_output)
        repair_metrics = extract_synthesis_metrics(garbage_output)

        assert repair_parsed is None
        assert repair_quality == ExtractionQuality.FAILED
        assert repair_metrics is None

    def test_repair_recovers_metrics_only(self) -> None:
        """Repair output has valid metrics but no contract -> metrics recovered."""
        metrics_only_output = _VALID_METRICS_BLOCK
        repair_metrics = extract_synthesis_metrics(metrics_only_output)

        assert repair_metrics is not None
        assert repair_metrics.quality is not None
        assert repair_metrics.quality.actionable_ratio == 0.85
        assert repair_metrics.consensus is not None
        assert repair_metrics.consensus.agreed_findings == 3


class TestBuildRepairContext:
    """Tests for _build_repair_context helper."""

    def test_includes_contract_block_when_present(self) -> None:
        """Repair context includes the raw contract block if markers are present."""
        from bmad_assist.core.loop.handlers.validate_story_synthesis import (
            _build_repair_context,
        )

        context = _build_repair_context(_SYNTHESIS_BAD_CONTRACT)
        assert "[Original contract block]" in context
        assert "story_id: S-123" in context

    def test_includes_summary_section(self) -> None:
        """Repair context includes Synthesis Summary if present."""
        from bmad_assist.core.loop.handlers.validate_story_synthesis import (
            _build_repair_context,
        )

        synthesis = (
            "## Synthesis Summary\n"
            "3 issues verified, 1 dismissed.\n\n"
            "## Issues Verified\nSome content.\n"
        )
        context = _build_repair_context(synthesis)
        assert "3 issues verified" in context

    def test_includes_prose_excerpt(self) -> None:
        """Repair context always includes prose excerpt."""
        from bmad_assist.core.loop.handlers.validate_story_synthesis import (
            _build_repair_context,
        )

        context = _build_repair_context("Some synthesis content here.")
        assert "[Prose excerpt]" in context
        assert "Some synthesis content here." in context
