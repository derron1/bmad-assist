"""Regression tests for validation synthesis resolution extraction.

Covers the fix where _extract_validation_resolution was called on raw stdout
(containing tool-call noise) instead of extracted synthesis content.
"""

from bmad_assist.core.loop.handlers.validate_story_synthesis import (
    _infer_resolution_from_structure,
    _extract_validation_resolution,
)
from bmad_assist.core.loop.synthesis_contract import ExtractionQuality


class TestValidationResolutionExtraction:
    def test_clean_synthesis_extracts_resolved(self) -> None:
        """Clean synthesis content with 'resolution: resolved' is correctly parsed."""
        synthesis = (
            "## Validation Summary\n\n"
            "All critical issues have been addressed.\n\n"
            "resolution: resolved\n"
        )
        parsed, quality = _extract_validation_resolution(synthesis)
        assert parsed is not None
        assert parsed["resolution"] == "resolved"
        assert quality == ExtractionQuality.DEGRADED

    def test_clean_synthesis_extracts_rework(self) -> None:
        """Clean synthesis content with 'resolution: rework' is correctly parsed."""
        synthesis = (
            "## Validation Summary\n\n"
            "Critical issues remain.\n\n"
            "resolution: rework\n"
        )
        parsed, quality = _extract_validation_resolution(synthesis)
        assert parsed is not None
        assert parsed["resolution"] == "rework"

    def test_raw_stdout_with_noise_may_misparse(self) -> None:
        """Raw stdout with tool-call noise can trigger false semantic matches.

        This is the regression scenario: tool-call XML containing keywords like
        'validation failed' or 'requires rework' can cause false REWORK resolution
        even when the actual synthesis says RESOLVED.
        """
        # Simulate raw stdout with tool noise before the real synthesis
        tool_noise = (
            '<tool_use>\n'
            '<tool_name>bash</tool_name>\n'
            '<parameter name="command">echo "validation failed check"</parameter>\n'
            '</tool_use>\n'
            '<tool_result>validation failed check</tool_result>\n\n'
        )
        clean_synthesis = (
            "## Validation Summary\n\n"
            "All issues have been addressed.\n\n"
            "resolution: resolved\n"
        )

        # Raw stdout: noise first, then synthesis
        raw_stdout = tool_noise + clean_synthesis

        # On raw stdout, the semantic "validation failed" in tool noise
        # could match before the header "resolution: resolved"
        # (depends on regex order, but demonstrates the risk)
        parsed_raw, _ = _extract_validation_resolution(raw_stdout)

        # On clean synthesis, resolution is unambiguously resolved
        parsed_clean, quality_clean = _extract_validation_resolution(clean_synthesis)
        assert parsed_clean is not None
        assert parsed_clean["resolution"] == "resolved"
        assert quality_clean == ExtractionQuality.DEGRADED

    def test_semantic_fallback_resolved(self) -> None:
        """Semantic fallback detects 'all critical issues fixed' without header."""
        synthesis = "## Summary\n\nAll critical issues have been fixed.\n"
        parsed, quality = _extract_validation_resolution(synthesis)
        assert parsed is not None
        assert parsed["resolution"] == "resolved"

    def test_semantic_fallback_rework(self) -> None:
        """Semantic fallback detects 'remaining critical issue' without header."""
        synthesis = "## Summary\n\nThere is a remaining critical issue.\n"
        parsed, quality = _extract_validation_resolution(synthesis)
        assert parsed is not None
        assert parsed["resolution"] == "rework"

    def test_empty_input_returns_failed(self) -> None:
        """Empty string returns (None, FAILED)."""
        parsed, quality = _extract_validation_resolution("")
        assert parsed is None
        assert quality == ExtractionQuality.FAILED


class TestValidationResolutionStructuralFallback:
    def test_rework_when_critical_issues_and_no_changes(self) -> None:
        synthesis = (
            "## Issues Verified (by severity)\n\n"
            "### Critical\n"
            "- **Issue**: Missing idempotency guard\n\n"
            "## Changes Applied\n"
        )

        assert _infer_resolution_from_structure(synthesis) == "rework"

    def test_resolved_when_no_critical_issues(self) -> None:
        synthesis = (
            "## Issues Verified (by severity)\n\n"
            "### Critical\n"
            "No critical issues remain.\n\n"
            "## Changes Applied\n"
            "**Location**: story.md - Acceptance Criteria\n"
        )

        assert _infer_resolution_from_structure(synthesis) == "resolved"

    def test_resolved_when_critical_issues_have_changes(self) -> None:
        synthesis = (
            "## Issues Verified (by severity)\n\n"
            "### Critical\n"
            "- **Issue**: Missing idempotency guard\n\n"
            "## Changes Applied\n"
            "**Location**: story.md - Acceptance Criteria\n"
            "**Change**: Added idempotency and duplicate-send guard language\n"
        )

        assert _infer_resolution_from_structure(synthesis) == "resolved"


class TestContractMarkerExtraction:
    """Tests for Layer 0: VALIDATION_CONTRACT_START/END marker extraction."""

    def test_contract_markers_return_strict_quality(self) -> None:
        """Full contract block with valid resolution returns STRICT quality."""
        synthesis = (
            "<!-- VALIDATION_CONTRACT_START -->\n"
            "resolution: resolved\n"
            "<!-- VALIDATION_CONTRACT_END -->\n\n"
            "## Synthesis Summary\n"
            "All issues addressed.\n"
        )
        parsed, quality = _extract_validation_resolution(synthesis)
        assert parsed is not None
        assert parsed["resolution"] == "resolved"
        assert quality == ExtractionQuality.STRICT

    def test_contract_with_count_fields(self) -> None:
        """Integer count fields are parsed correctly from contract block."""
        synthesis = (
            "<!-- VALIDATION_CONTRACT_START -->\n"
            "resolution: rework\n"
            "verified_critical: 2\n"
            "verified_high: 3\n"
            "fixed_critical: 1\n"
            "fixed_high: 2\n"
            "remaining_critical: 1\n"
            "remaining_high: 1\n"
            "<!-- VALIDATION_CONTRACT_END -->\n"
        )
        parsed, quality = _extract_validation_resolution(synthesis)
        assert parsed is not None
        assert quality == ExtractionQuality.STRICT
        assert parsed["resolution"] == "rework"
        assert parsed["verified_critical"] == 2
        assert parsed["fixed_critical"] == 1
        assert parsed["remaining_critical"] == 1
        assert parsed["remaining_high"] == 1

    def test_contract_cross_validation_overrides(self) -> None:
        """Resolution 'resolved' with remaining_critical > 0 overrides to 'rework'."""
        synthesis = (
            "<!-- VALIDATION_CONTRACT_START -->\n"
            "resolution: resolved\n"
            "verified_critical: 2\n"
            "fixed_critical: 1\n"
            "remaining_critical: 1\n"
            "<!-- VALIDATION_CONTRACT_END -->\n"
        )
        parsed, quality = _extract_validation_resolution(synthesis)
        assert parsed is not None
        assert quality == ExtractionQuality.STRICT
        assert parsed["resolution"] == "rework"

    def test_contract_markers_invalid_returns_failed(self) -> None:
        """Malformed block between markers returns (None, FAILED), does NOT fall through."""
        synthesis = (
            "<!-- VALIDATION_CONTRACT_START -->\n"
            "this is garbage data with no resolution\n"
            "<!-- VALIDATION_CONTRACT_END -->\n\n"
            "resolution: resolved\n"  # Layer 1 would match this, but fail-closed prevents it
        )
        parsed, quality = _extract_validation_resolution(synthesis)
        assert parsed is None
        assert quality == ExtractionQuality.FAILED

    def test_contract_first_with_prose_after(self) -> None:
        """Realistic full output with contract + metrics + prose + patches extracts STRICT."""
        synthesis = (
            "<!-- VALIDATION_CONTRACT_START -->\n"
            "resolution: resolved\n"
            "verified_critical: 1\n"
            "verified_high: 1\n"
            "fixed_critical: 1\n"
            "fixed_high: 1\n"
            "remaining_critical: 0\n"
            "remaining_high: 0\n"
            "<!-- VALIDATION_CONTRACT_END -->\n\n"
            "<!-- METRICS_JSON_START -->\n"
            '{"quality": {"actionable_ratio": 0.8}, "consensus": {"agreed_findings": 3}}\n'
            "<!-- METRICS_JSON_END -->\n\n"
            "## Synthesis Summary\n"
            "1 critical issue fixed, 1 high issue fixed.\n\n"
            "## Issues Verified (by severity)\n\n"
            "### Critical\n"
            "- **Issue**: Missing guard | **Source**: A, B | **Fix**: Added guard\n\n"
            "## Changes Applied\n"
            "**Location**: story.md - AC\n\n"
            '<!-- STORY_PATCH_START heading="## acceptance criteria" -->\n'
            "## Acceptance Criteria\n"
            "- Updated criteria\n"
            "<!-- STORY_PATCH_END -->\n"
        )
        parsed, quality = _extract_validation_resolution(synthesis)
        assert parsed is not None
        assert quality == ExtractionQuality.STRICT
        assert parsed["resolution"] == "resolved"
        assert parsed["remaining_critical"] == 0

    def test_backward_compat_no_markers(self) -> None:
        """Old format without contract markers still returns DEGRADED via Layer 1."""
        synthesis = (
            "## Synthesis Summary\n"
            "All issues addressed.\n\n"
            "resolution: resolved\n"
        )
        parsed, quality = _extract_validation_resolution(synthesis)
        assert parsed is not None
        assert parsed["resolution"] == "resolved"
        assert quality == ExtractionQuality.DEGRADED
