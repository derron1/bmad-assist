"""Tests for synthesis metrics parser.

Story 13.6: Synthesizer Schema Integration
Tests cover:
- AC3: Synthesis output parser functionality
- AC4: Graceful parsing failures
- AC7: Quality field calculations
- AC8: Consensus field calculations
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from _pytest.logging import LogCaptureFixture


class TestExtractSynthesisMetrics:
    """Test extract_synthesis_metrics function (AC3, AC4)."""

    @pytest.fixture
    def valid_json_output(self) -> str:
        """Create valid synthesis output with metrics JSON."""
        return """## Synthesis Summary

This is the synthesis report content.

## Issues Verified

Some verified issues here.

<!-- METRICS_JSON_START -->
{
  "quality": {
    "actionable_ratio": 0.85,
    "specificity_score": 0.75,
    "evidence_quality": 0.7,
    "follows_template": true,
    "internal_consistency": 0.9
  },
  "consensus": {
    "agreed_findings": 5,
    "unique_findings": 2,
    "disputed_findings": 1,
    "missed_findings": 0,
    "agreement_score": 0.625,
    "false_positive_count": 0
  }
}
<!-- METRICS_JSON_END -->

## Changes Applied

Final section.
"""

    def test_extracts_valid_metrics(self, valid_json_output: str) -> None:
        """Extracts metrics from valid synthesis output."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        result = extract_synthesis_metrics(valid_json_output)

        assert result is not None
        assert result.quality is not None
        assert result.consensus is not None

        # Quality fields
        assert result.quality.actionable_ratio == 0.85
        assert result.quality.specificity_score == 0.75
        assert result.quality.evidence_quality == 0.7
        assert result.quality.follows_template is True
        assert result.quality.internal_consistency == 0.9

        # Consensus fields
        assert result.consensus.agreed_findings == 5
        assert result.consensus.unique_findings == 2
        assert result.consensus.disputed_findings == 1
        assert result.consensus.missed_findings == 0
        assert result.consensus.agreement_score == 0.625
        assert result.consensus.false_positive_count == 0

    def test_returns_none_when_markers_missing(self, caplog: LogCaptureFixture) -> None:
        """Returns None with warning when markers not found."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output_without_markers = """## Synthesis Summary

This is a synthesis report without metrics markers.
No JSON here.
"""

        with caplog.at_level(logging.WARNING):
            result = extract_synthesis_metrics(output_without_markers)

        assert result is None
        assert "Metrics extraction failed" in caplog.text

    def test_returns_none_on_invalid_json(self, caplog: LogCaptureFixture) -> None:
        """Returns None with warning when JSON is invalid."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output_with_bad_json = """## Synthesis Summary

<!-- METRICS_JSON_START -->
{ this is not: valid json, missing quotes }
<!-- METRICS_JSON_END -->
"""

        with caplog.at_level(logging.WARNING):
            result = extract_synthesis_metrics(output_with_bad_json)

        assert result is None
        assert "Invalid JSON" in caplog.text

    def test_returns_none_on_schema_validation_failure(self, caplog: LogCaptureFixture) -> None:
        """Returns None when Pydantic validation fails for both quality and consensus."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        # Missing required fields in both quality and consensus
        output_with_invalid_schema = """## Synthesis Summary

<!-- METRICS_JSON_START -->
{
  "quality": {
    "actionable_ratio": "not_a_float"
  },
  "consensus": {
    "agreed_findings": "not_an_int"
  }
}
<!-- METRICS_JSON_END -->
"""

        with caplog.at_level(logging.WARNING):
            result = extract_synthesis_metrics(output_with_invalid_schema)

        # Both quality and consensus failed validation
        assert result is None
        assert "schema validation failed" in caplog.text.lower()

    def test_partial_extraction_quality_only(self, caplog: LogCaptureFixture) -> None:
        """Extracts quality when consensus validation fails."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output_quality_only = """## Synthesis Summary

<!-- METRICS_JSON_START -->
{
  "quality": {
    "actionable_ratio": 0.8,
    "specificity_score": 0.7,
    "evidence_quality": 0.6,
    "follows_template": true,
    "internal_consistency": 0.85
  },
  "consensus": {
    "agreed_findings": "invalid_type"
  }
}
<!-- METRICS_JSON_END -->
"""

        with caplog.at_level(logging.WARNING):
            result = extract_synthesis_metrics(output_quality_only)

        assert result is not None
        assert result.quality is not None
        assert result.quality.actionable_ratio == 0.8
        assert result.consensus is None
        assert "Consensus schema validation failed" in caplog.text

    def test_partial_extraction_consensus_only(self, caplog: LogCaptureFixture) -> None:
        """Extracts consensus when quality validation fails."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output_consensus_only = """## Synthesis Summary

<!-- METRICS_JSON_START -->
{
  "quality": {
    "actionable_ratio": "invalid_type"
  },
  "consensus": {
    "agreed_findings": 3,
    "unique_findings": 1,
    "disputed_findings": 0,
    "missed_findings": 0,
    "agreement_score": 0.75,
    "false_positive_count": 0
  }
}
<!-- METRICS_JSON_END -->
"""

        with caplog.at_level(logging.WARNING):
            result = extract_synthesis_metrics(output_consensus_only)

        assert result is not None
        assert result.quality is None
        assert result.consensus is not None
        assert result.consensus.agreed_findings == 3
        assert "Quality schema validation failed" in caplog.text


class TestQualityFieldRanges:
    """Test quality field value ranges (AC7)."""

    def test_quality_values_in_valid_range(self) -> None:
        """Quality float fields are in 0.0-1.0 range."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output = """
<!-- METRICS_JSON_START -->
{
  "quality": {
    "actionable_ratio": 0.0,
    "specificity_score": 1.0,
    "evidence_quality": 0.5,
    "follows_template": false,
    "internal_consistency": 0.99
  },
  "consensus": {
    "agreed_findings": 0,
    "unique_findings": 0,
    "disputed_findings": 0,
    "missed_findings": 0,
    "agreement_score": 1.0,
    "false_positive_count": 0
  }
}
<!-- METRICS_JSON_END -->
"""

        result = extract_synthesis_metrics(output)

        assert result is not None
        assert result.quality is not None
        assert 0.0 <= result.quality.actionable_ratio <= 1.0
        assert 0.0 <= result.quality.specificity_score <= 1.0
        assert 0.0 <= result.quality.evidence_quality <= 1.0
        assert 0.0 <= result.quality.internal_consistency <= 1.0


class TestConsensusFieldValues:
    """Test consensus field value calculations (AC8)."""

    def test_agreement_score_calculation(self) -> None:
        """Agreement score = agreed / (agreed + unique + disputed)."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        # 5 / (5 + 2 + 1) = 0.625
        output = """
<!-- METRICS_JSON_START -->
{
  "quality": {
    "actionable_ratio": 0.5,
    "specificity_score": 0.5,
    "evidence_quality": 0.5,
    "follows_template": true,
    "internal_consistency": 0.5
  },
  "consensus": {
    "agreed_findings": 5,
    "unique_findings": 2,
    "disputed_findings": 1,
    "missed_findings": 0,
    "agreement_score": 0.625,
    "false_positive_count": 0
  }
}
<!-- METRICS_JSON_END -->
"""

        result = extract_synthesis_metrics(output)

        assert result is not None
        assert result.consensus is not None
        assert result.consensus.agreement_score == 0.625
        assert result.consensus.agreed_findings == 5
        assert result.consensus.unique_findings == 2
        assert result.consensus.disputed_findings == 1

    def test_zero_findings_edge_case(self) -> None:
        """When no findings, agreement_score should be 1.0."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output = """
<!-- METRICS_JSON_START -->
{
  "quality": {
    "actionable_ratio": 1.0,
    "specificity_score": 1.0,
    "evidence_quality": 1.0,
    "follows_template": true,
    "internal_consistency": 1.0
  },
  "consensus": {
    "agreed_findings": 0,
    "unique_findings": 0,
    "disputed_findings": 0,
    "missed_findings": 0,
    "agreement_score": 1.0,
    "false_positive_count": 0
  }
}
<!-- METRICS_JSON_END -->
"""

        result = extract_synthesis_metrics(output)

        assert result is not None
        assert result.consensus is not None
        assert result.consensus.agreement_score == 1.0

    def test_post_hoc_fields_zero(self) -> None:
        """missed_findings and false_positive_count should be 0 from synthesizer."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output = """
<!-- METRICS_JSON_START -->
{
  "quality": {
    "actionable_ratio": 0.5,
    "specificity_score": 0.5,
    "evidence_quality": 0.5,
    "follows_template": true,
    "internal_consistency": 0.5
  },
  "consensus": {
    "agreed_findings": 3,
    "unique_findings": 1,
    "disputed_findings": 0,
    "missed_findings": 0,
    "agreement_score": 0.75,
    "false_positive_count": 0
  }
}
<!-- METRICS_JSON_END -->
"""

        result = extract_synthesis_metrics(output)

        assert result is not None
        assert result.consensus is not None
        # These are POST_HOC fields - should be 0 from synthesizer
        assert result.consensus.missed_findings == 0
        assert result.consensus.false_positive_count == 0


class TestSynthesisMetricsDataclass:
    """Test SynthesisMetrics dataclass."""

    def test_dataclass_is_frozen(self) -> None:
        """SynthesisMetrics is immutable (frozen)."""
        from bmad_assist.validation.synthesis_parser import SynthesisMetrics

        metrics = SynthesisMetrics(quality=None, consensus=None)

        with pytest.raises(AttributeError):
            metrics.quality = None  # type: ignore[misc]

    def test_dataclass_allows_both_none(self) -> None:
        """SynthesisMetrics allows both fields to be None."""
        from bmad_assist.validation.synthesis_parser import SynthesisMetrics

        metrics = SynthesisMetrics(quality=None, consensus=None)

        assert metrics.quality is None
        assert metrics.consensus is None


class TestLoggingExcerpt:
    """Test logging includes output excerpt (AC4)."""

    def test_log_includes_output_excerpt_on_missing_markers(
        self, caplog: LogCaptureFixture
    ) -> None:
        """Log includes first 500 chars when markers missing."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output = "A" * 600  # More than 500 chars

        with caplog.at_level(logging.WARNING):
            extract_synthesis_metrics(output)

        # Check log message includes excerpt
        assert "Metrics extraction failed" in caplog.text
        # Excerpt should be truncated to ~500 chars
        assert "A" * 500 in caplog.text


class TestCreateSynthesizerRecord:
    """Test create_synthesizer_record function (AC5, AC6)."""

    @pytest.fixture
    def valid_synthesis_output(self) -> str:
        """Create valid synthesis output with metrics JSON."""
        return """## Synthesis Summary

5 issues verified, 2 false positives dismissed, 3 changes applied.

<!-- METRICS_JSON_START -->
{
  "quality": {
    "actionable_ratio": 0.8,
    "specificity_score": 0.75,
    "evidence_quality": 0.7,
    "follows_template": true,
    "internal_consistency": 0.9
  },
  "consensus": {
    "agreed_findings": 4,
    "unique_findings": 1,
    "disputed_findings": 0,
    "missed_findings": 0,
    "agreement_score": 0.8,
    "false_positive_count": 2
  }
}
<!-- METRICS_JSON_END -->
"""

    @pytest.fixture
    def sample_workflow_info(self) -> "WorkflowInfo":
        """Sample workflow info."""
        from bmad_assist.benchmarking import PatchInfo, WorkflowInfo

        return WorkflowInfo(
            id="validate-story-synthesis",
            version="1.0.0",
            variant="default",
            patch=PatchInfo(applied=True),
        )

    @pytest.fixture
    def sample_story_info(self) -> "StoryInfo":
        """Sample story info."""
        from bmad_assist.benchmarking import StoryInfo

        return StoryInfo(
            epic_num=13,
            story_num=6,
            title="Synthesizer Schema Integration",
            complexity_flags={},
        )

    def test_creates_record_with_synthesizer_role(
        self,
        valid_synthesis_output: str,
        sample_workflow_info: "WorkflowInfo",
        sample_story_info: "StoryInfo",
    ) -> None:
        """Creates LLMEvaluationRecord with role=SYNTHESIZER."""
        from datetime import UTC, datetime

        from bmad_assist.benchmarking import EvaluatorRole
        from bmad_assist.validation.benchmarking_integration import (
            create_synthesizer_record,
        )

        start_time = datetime(2025, 12, 20, 10, 0, 0, tzinfo=UTC)
        end_time = datetime(2025, 12, 20, 10, 1, 0, tzinfo=UTC)

        record = create_synthesizer_record(
            synthesis_output=valid_synthesis_output,
            workflow_info=sample_workflow_info,
            story_info=sample_story_info,
            provider="claude",
            model="opus-4",
            start_time=start_time,
            end_time=end_time,
            input_tokens=5000,
            output_tokens=2000,
            validator_count=4,
        )

        assert record.evaluator.role == EvaluatorRole.SYNTHESIZER
        assert record.evaluator.role_id is None  # CRITICAL: synthesizer has no role_id

    def test_populates_quality_and_consensus(
        self,
        valid_synthesis_output: str,
        sample_workflow_info: "WorkflowInfo",
        sample_story_info: "StoryInfo",
    ) -> None:
        """Populates quality and consensus from extracted metrics."""
        from datetime import UTC, datetime

        from bmad_assist.validation.benchmarking_integration import (
            create_synthesizer_record,
        )

        start_time = datetime.now(UTC)
        end_time = datetime.now(UTC)

        record = create_synthesizer_record(
            synthesis_output=valid_synthesis_output,
            workflow_info=sample_workflow_info,
            story_info=sample_story_info,
            provider="claude",
            model="opus-4",
            start_time=start_time,
            end_time=end_time,
            input_tokens=5000,
            output_tokens=2000,
            validator_count=4,
        )

        assert record.quality is not None
        assert record.quality.actionable_ratio == 0.8
        assert record.consensus is not None
        assert record.consensus.agreed_findings == 4

    def test_handles_extraction_failure(
        self,
        sample_workflow_info: "WorkflowInfo",
        sample_story_info: "StoryInfo",
    ) -> None:
        """Returns None for quality/consensus when extraction fails."""
        from datetime import UTC, datetime

        from bmad_assist.validation.benchmarking_integration import (
            create_synthesizer_record,
        )

        # Output without metrics markers
        bad_output = "## Synthesis Summary\n\nNo metrics here."

        start_time = datetime.now(UTC)
        end_time = datetime.now(UTC)

        record = create_synthesizer_record(
            synthesis_output=bad_output,
            workflow_info=sample_workflow_info,
            story_info=sample_story_info,
            provider="claude",
            model="opus-4",
            start_time=start_time,
            end_time=end_time,
            input_tokens=5000,
            output_tokens=500,
            validator_count=4,
        )

        # Record should still be created, just with None metrics
        assert record is not None
        assert record.quality is None
        assert record.consensus is None

    def test_calculates_duration_from_times(
        self,
        valid_synthesis_output: str,
        sample_workflow_info: "WorkflowInfo",
        sample_story_info: "StoryInfo",
    ) -> None:
        """Calculates duration_ms from start and end times."""
        from datetime import UTC, datetime

        from bmad_assist.validation.benchmarking_integration import (
            create_synthesizer_record,
        )

        start_time = datetime(2025, 12, 20, 10, 0, 0, tzinfo=UTC)
        end_time = datetime(2025, 12, 20, 10, 0, 30, tzinfo=UTC)  # 30 seconds later

        record = create_synthesizer_record(
            synthesis_output=valid_synthesis_output,
            workflow_info=sample_workflow_info,
            story_info=sample_story_info,
            provider="claude",
            model="opus-4",
            start_time=start_time,
            end_time=end_time,
            input_tokens=5000,
            output_tokens=2000,
            validator_count=4,
        )

        assert record.execution.duration_ms == 30000  # 30 seconds in ms

    def test_sequence_position_is_validator_count(
        self,
        valid_synthesis_output: str,
        sample_workflow_info: "WorkflowInfo",
        sample_story_info: "StoryInfo",
    ) -> None:
        """Synthesizer runs after all validators, so sequence_position = validator_count."""
        from datetime import UTC, datetime

        from bmad_assist.validation.benchmarking_integration import (
            create_synthesizer_record,
        )

        start_time = datetime.now(UTC)
        end_time = datetime.now(UTC)

        record = create_synthesizer_record(
            synthesis_output=valid_synthesis_output,
            workflow_info=sample_workflow_info,
            story_info=sample_story_info,
            provider="claude",
            model="opus-4",
            start_time=start_time,
            end_time=end_time,
            input_tokens=5000,
            output_tokens=2000,
            validator_count=4,  # 4 validators ran before synthesizer
        )

        assert record.execution.sequence_position == 4


_VALID_METRICS_JSON = """{
  "quality": {
    "actionable_ratio": 0.8,
    "specificity_score": 0.7,
    "evidence_quality": 0.6,
    "follows_template": true,
    "internal_consistency": 0.9
  },
  "consensus": {
    "agreed_findings": 5,
    "unique_findings": 2,
    "disputed_findings": 1,
    "missed_findings": 0,
    "agreement_score": 0.625,
    "false_positive_count": 0
  }
}"""


class TestNoBackwardScan:
    """Verify backward scan was removed — no JSON extraction without markers or LLM fallback."""

    def test_bare_json_without_markers_returns_none(self) -> None:
        """Bare JSON near end without markers is NOT extracted (backward scan removed)."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output = f"## Synthesis Summary\n\nReview complete.\n\n{_VALID_METRICS_JSON}\n"
        # Without markers and without llm_fallback, this should fall through
        # to markdown fallback (which won't find enough headings)
        result = extract_synthesis_metrics(output)
        assert result is None

    def test_fenced_json_without_markers_returns_none(self) -> None:
        """Fenced JSON without markers or contract end is NOT extracted."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output = (
            "## Synthesis Summary\n\n"
            "All issues addressed.\n\n"
            f"```json\n{_VALID_METRICS_JSON}\n```\n"
        )
        result = extract_synthesis_metrics(output)
        assert result is None

    def test_no_json_still_returns_none(self, caplog: "LogCaptureFixture") -> None:
        """No markers and no JSON anywhere → returns None."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output = "## Summary\n\nPure text, no JSON.\n"
        with caplog.at_level(logging.WARNING):
            result = extract_synthesis_metrics(output)
        assert result is None


# ---------------------------------------------------------------------------
# LLM fallback extraction
# ---------------------------------------------------------------------------


class TestExtractMetricsViaLlm:
    """Tests for extract_metrics_via_llm() function."""

    def _make_mock_provider(self, stdout: str, exit_code: int = 0):
        """Create a mock provider that returns given stdout."""
        from unittest.mock import MagicMock

        provider = MagicMock()
        result = MagicMock()
        result.exit_code = exit_code
        result.stdout = stdout
        result.stderr = ""
        provider.invoke.return_value = result
        return provider

    def test_success_returns_metrics_with_follows_template_false(self) -> None:
        """Successful LLM extraction sets follows_template=False."""
        from unittest.mock import patch

        from bmad_assist.validation.synthesis_parser import extract_metrics_via_llm

        valid_response = """{
  "quality": {
    "actionable_ratio": 0.7,
    "specificity_score": 0.6,
    "evidence_quality": 0.5,
    "internal_consistency": 0.8
  },
  "consensus": {
    "agreed_findings": 3,
    "unique_findings": 1,
    "disputed_findings": 0,
    "missed_findings": 0,
    "agreement_score": 0.75,
    "false_positive_count": 1
  }
}"""
        mock_provider = self._make_mock_provider(valid_response)

        with patch(
            "bmad_assist.providers.registry.get_provider",
            return_value=mock_provider,
        ):
            result = extract_metrics_via_llm(
                "Some synthesis output " * 20,
                provider_name="claude",
                model="haiku",
            )

        assert result is not None
        assert result.quality is not None
        assert result.quality.follows_template is False
        assert result.quality.actionable_ratio == 0.7
        assert result.consensus is not None
        assert result.consensus.agreed_findings == 3

    def test_retries_on_bad_json_then_succeeds(self) -> None:
        """First attempt returns bad JSON, second returns valid → succeeds."""
        from unittest.mock import MagicMock, patch

        from bmad_assist.validation.synthesis_parser import extract_metrics_via_llm

        valid_response = """{
  "quality": {
    "actionable_ratio": 0.5,
    "specificity_score": 0.5,
    "evidence_quality": 0.5,
    "internal_consistency": 0.5
  },
  "consensus": {
    "agreed_findings": 1,
    "unique_findings": 0,
    "disputed_findings": 0,
    "missed_findings": 0,
    "agreement_score": 1.0,
    "false_positive_count": 0
  }
}"""
        bad_result = MagicMock()
        bad_result.exit_code = 0
        bad_result.stdout = "{ not valid json"

        good_result = MagicMock()
        good_result.exit_code = 0
        good_result.stdout = valid_response

        mock_provider = MagicMock()
        mock_provider.invoke.side_effect = [bad_result, good_result]

        with patch(
            "bmad_assist.providers.registry.get_provider",
            return_value=mock_provider,
        ):
            result = extract_metrics_via_llm(
                "Some synthesis output " * 20,
                provider_name="claude",
                model="haiku",
                max_retries=2,
            )

        assert result is not None
        assert mock_provider.invoke.call_count == 2

    def test_all_retries_fail_returns_none(self) -> None:
        """All retry attempts fail → returns None."""
        from unittest.mock import patch

        from bmad_assist.validation.synthesis_parser import extract_metrics_via_llm

        mock_provider = self._make_mock_provider("{ invalid }")

        with patch(
            "bmad_assist.providers.registry.get_provider",
            return_value=mock_provider,
        ):
            result = extract_metrics_via_llm(
                "Some synthesis output " * 20,
                provider_name="claude",
                model="haiku",
                max_retries=2,
            )

        assert result is None
        assert mock_provider.invoke.call_count == 2

    def test_preserves_existing_quality(self) -> None:
        """When existing_quality provided, only consensus is extracted from LLM."""
        from unittest.mock import patch

        from bmad_assist.benchmarking.schema import QualitySignals
        from bmad_assist.validation.synthesis_parser import extract_metrics_via_llm

        existing_quality = QualitySignals(
            actionable_ratio=0.9,
            specificity_score=0.8,
            evidence_quality=0.7,
            follows_template=True,
            internal_consistency=0.95,
        )

        consensus_response = """{
  "consensus": {
    "agreed_findings": 4,
    "unique_findings": 1,
    "disputed_findings": 0,
    "missed_findings": 0,
    "agreement_score": 0.8,
    "false_positive_count": 2
  }
}"""
        mock_provider = self._make_mock_provider(consensus_response)

        with patch(
            "bmad_assist.providers.registry.get_provider",
            return_value=mock_provider,
        ):
            result = extract_metrics_via_llm(
                "Some synthesis output " * 20,
                provider_name="claude",
                model="haiku",
                existing_quality=existing_quality,
            )

        assert result is not None
        # Quality preserved from existing, but follows_template set to False
        assert result.quality is not None
        assert result.quality.actionable_ratio == 0.9
        assert result.quality.follows_template is False
        # Consensus from LLM
        assert result.consensus is not None
        assert result.consensus.agreed_findings == 4

    def test_both_existing_skips_llm_call(self) -> None:
        """When both sections already valid, no LLM call is made."""
        from bmad_assist.benchmarking.schema import ConsensusData, QualitySignals
        from bmad_assist.validation.synthesis_parser import extract_metrics_via_llm

        existing_quality = QualitySignals(
            actionable_ratio=0.5,
            specificity_score=0.5,
            evidence_quality=0.5,
            follows_template=True,
            internal_consistency=0.5,
        )
        existing_consensus = ConsensusData(
            agreed_findings=1,
            unique_findings=0,
            disputed_findings=0,
            missed_findings=0,
            agreement_score=1.0,
            false_positive_count=0,
        )

        # No mock needed — should not call provider at all
        result = extract_metrics_via_llm(
            "Some output",
            provider_name="claude",
            model="haiku",
            existing_quality=existing_quality,
            existing_consensus=existing_consensus,
        )

        assert result is not None
        assert result.quality is existing_quality
        assert result.consensus is existing_consensus

    def test_provider_failure_returns_none(self) -> None:
        """Provider get failure returns None gracefully."""
        from unittest.mock import patch

        from bmad_assist.validation.synthesis_parser import extract_metrics_via_llm

        with patch(
            "bmad_assist.providers.registry.get_provider",
            side_effect=RuntimeError("No such provider"),
        ):
            result = extract_metrics_via_llm(
                "Some synthesis output " * 20,
                provider_name="nonexistent",
                model="haiku",
            )

        assert result is None


class TestExtractSynthesisMetricsLlmFallback:
    """Tests for LLM fallback integration in extract_synthesis_metrics."""

    def _make_mock_provider(self, stdout: str, exit_code: int = 0):
        """Create a mock provider that returns given stdout."""
        from unittest.mock import MagicMock

        provider = MagicMock()
        result = MagicMock()
        result.exit_code = exit_code
        result.stdout = stdout
        result.stderr = ""
        provider.invoke.return_value = result
        return provider

    def test_llm_fallback_on_pydantic_failure(self) -> None:
        """When markers have JSON with wrong field names, LLM fallback is called."""
        from unittest.mock import patch

        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        # Markers present but with wrong field names
        output = """## Synthesis Summary

5 issues verified, 2 false positives dismissed.

<!-- METRICS_JSON_START -->
{
  "quality": {
    "false_positive_rate": 0.846,
    "findings_raised": 13
  },
  "consensus": {
    "both_reviewers_agree": 3
  }
}
<!-- METRICS_JSON_END -->

## Issues Verified
## Issues Dismissed
## Changes Applied
""" + ("x" * 100)

        valid_llm_response = """{
  "quality": {
    "actionable_ratio": 0.6,
    "specificity_score": 0.5,
    "evidence_quality": 0.4,
    "internal_consistency": 0.7
  },
  "consensus": {
    "agreed_findings": 3,
    "unique_findings": 2,
    "disputed_findings": 0,
    "missed_findings": 0,
    "agreement_score": 0.6,
    "false_positive_count": 2
  }
}"""
        mock_provider = self._make_mock_provider(valid_llm_response)

        with patch(
            "bmad_assist.providers.registry.get_provider",
            return_value=mock_provider,
        ):
            result = extract_synthesis_metrics(
                output,
                llm_fallback=True,
                provider_name="claude",
                model="haiku",
            )

        assert result is not None
        assert result.quality is not None
        assert result.quality.follows_template is False
        assert result.quality.actionable_ratio == 0.6
        assert result.consensus is not None
        assert result.consensus.agreed_findings == 3
        mock_provider.invoke.assert_called_once()

    def test_llm_fallback_on_no_json(self) -> None:
        """When no markers and no JSON at all, LLM fallback is called."""
        from unittest.mock import patch

        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output = (
            "## Synthesis Summary\n\n"
            "The reviewers identified several issues.\n"
            "3 issues verified, 1 false positive dismissed.\n\n"
            "## Issues Verified\n\n"
            "- Missing error handling\n\n"
            "## Issues Dismissed\n\n"
            "- Unnecessary refactoring\n\n"
            "## Changes Applied\n\n"
            "Updated acceptance criteria.\n"
        ) + ("x" * 100)

        valid_llm_response = """{
  "quality": {
    "actionable_ratio": 0.8,
    "specificity_score": 0.7,
    "evidence_quality": 0.6,
    "internal_consistency": 0.9
  },
  "consensus": {
    "agreed_findings": 3,
    "unique_findings": 0,
    "disputed_findings": 0,
    "missed_findings": 0,
    "agreement_score": 1.0,
    "false_positive_count": 1
  }
}"""
        mock_provider = self._make_mock_provider(valid_llm_response)

        with patch(
            "bmad_assist.providers.registry.get_provider",
            return_value=mock_provider,
        ):
            result = extract_synthesis_metrics(
                output,
                llm_fallback=True,
                provider_name="claude",
                model="haiku",
            )

        assert result is not None
        assert result.quality is not None
        assert result.quality.follows_template is False
        mock_provider.invoke.assert_called_once()

    def test_no_llm_when_strict_succeeds(self) -> None:
        """When markers have valid JSON, LLM fallback is NOT called."""
        from unittest.mock import patch

        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output = f"""<!-- METRICS_JSON_START -->
{_VALID_METRICS_JSON}
<!-- METRICS_JSON_END -->
"""
        mock_provider = self._make_mock_provider("should not be called")

        with patch(
            "bmad_assist.providers.registry.get_provider",
            return_value=mock_provider,
        ):
            result = extract_synthesis_metrics(
                output,
                llm_fallback=True,
                provider_name="claude",
                model="haiku",
            )

        assert result is not None
        assert result.quality is not None
        assert result.quality.actionable_ratio == 0.8
        mock_provider.invoke.assert_not_called()

    def test_llm_fallback_false_skips_llm(self) -> None:
        """With llm_fallback=False, no LLM call even on failure."""
        from unittest.mock import patch

        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output = """<!-- METRICS_JSON_START -->
{
  "quality": {"invented_field": 123},
  "consensus": {"wrong_field": true}
}
<!-- METRICS_JSON_END -->
"""
        mock_provider = self._make_mock_provider("should not be called")

        with patch(
            "bmad_assist.providers.registry.get_provider",
            return_value=mock_provider,
        ) as mock_get:
            result = extract_synthesis_metrics(output, llm_fallback=False)

        # Should not attempt LLM fallback
        mock_get.assert_not_called()
        assert result is None

    def test_llm_fallback_missing_config_falls_through(self, caplog: "LogCaptureFixture") -> None:
        """When provider_name/model is None, logs warning and falls through."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        # Must be >= 200 chars to trigger LLM fallback path
        output = (
            "<!-- METRICS_JSON_START -->\n"
            '{\n  "quality": {"invented_field": 123},\n'
            '  "consensus": {"wrong_field": true}\n}\n'
            "<!-- METRICS_JSON_END -->\n"
        ) + ("x" * 200)

        with caplog.at_level(logging.WARNING):
            result = extract_synthesis_metrics(
                output,
                llm_fallback=True,
                provider_name=None,
                model=None,
            )

        assert result is None
        assert "not set" in caplog.text

    def test_partial_quality_valid_uses_llm_for_consensus_only(self) -> None:
        """When quality validates but consensus fails, LLM extracts only consensus."""
        from unittest.mock import patch

        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output = """<!-- METRICS_JSON_START -->
{
  "quality": {
    "actionable_ratio": 0.8,
    "specificity_score": 0.7,
    "evidence_quality": 0.6,
    "follows_template": true,
    "internal_consistency": 0.9
  },
  "consensus": {
    "wrong_field": "bad"
  }
}
<!-- METRICS_JSON_END -->
""" + ("x" * 100)

        consensus_response = """{
  "consensus": {
    "agreed_findings": 5,
    "unique_findings": 1,
    "disputed_findings": 0,
    "missed_findings": 0,
    "agreement_score": 0.83,
    "false_positive_count": 0
  }
}"""
        mock_provider = self._make_mock_provider(consensus_response)

        with patch(
            "bmad_assist.providers.registry.get_provider",
            return_value=mock_provider,
        ):
            result = extract_synthesis_metrics(
                output,
                llm_fallback=True,
                provider_name="claude",
                model="haiku",
            )

        assert result is not None
        # Quality from original JSON (but follows_template=False because haiku fired)
        assert result.quality is not None
        assert result.quality.actionable_ratio == 0.8
        assert result.quality.follows_template is False
        # Consensus from LLM
        assert result.consensus is not None
        assert result.consensus.agreed_findings == 5


class TestMarkdownFallback:
    """Tests for heading-based markdown fallback."""

    def test_markdown_fallback_sets_follows_template_false(self) -> None:
        """Markdown fallback is a non-strict path so follows_template must be False."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output = """## Synthesis Summary

5 issues verified, 2 false positives dismissed, 3 changes applied.

## Issues Verified

### Critical
- **Issue**: Missing idempotency guard

## Issues Dismissed

- **Claimed Issue**: Duplicate notification audit

## Changes Applied

**Location**: story.md - Acceptance Criteria
"""
        result = extract_synthesis_metrics(output)

        assert result is not None
        assert result.quality is not None
        assert result.quality.follows_template is False

    def test_markdown_fallback_recovers_consensus_counts(self) -> None:
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output = """## Synthesis Summary

9 issues verified, 3 false positives dismissed, 5 changes applied.

## Issues Verified

## Issues Dismissed

## Changes Applied
"""
        result = extract_synthesis_metrics(output)

        assert result is not None
        assert result.consensus is not None
        assert result.consensus.agreed_findings == 9
        assert result.consensus.false_positive_count == 3
        assert result.consensus.agreement_score == pytest.approx(0.75)

    def test_markdown_fallback_returns_none_for_unstructured_output(self) -> None:
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output = "Plain prose with no headings, no JSON, and no recoverable structure."
        result = extract_synthesis_metrics(output)
        assert result is None


# ---------------------------------------------------------------------------
# Fallback semantics: follows_template, additive merging, success criteria
# ---------------------------------------------------------------------------


class TestFollowsTemplateSemanticsAndAdditiveLayer:
    """Tests for follows_template invariant and additive fallback merging."""

    def _make_mock_provider(self, stdout: str, exit_code: int = 0):
        from unittest.mock import MagicMock

        provider = MagicMock()
        result = MagicMock()
        result.exit_code = exit_code
        result.stdout = stdout
        result.stderr = ""
        provider.invoke.return_value = result
        return provider

    def test_strict_only_preserves_follows_template_true(self) -> None:
        """When both sections come from strict JSON, follows_template is preserved."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output = f"""<!-- METRICS_JSON_START -->
{_VALID_METRICS_JSON}
<!-- METRICS_JSON_END -->
"""
        result = extract_synthesis_metrics(output)
        assert result is not None
        assert result.quality is not None
        assert result.quality.follows_template is True
        assert result.consensus is not None

    def test_strict_quality_llm_fails_partial_has_follows_template_false(self) -> None:
        """Strict quality valid, consensus invalid, LLM fallback attempted and fails →
        returned partial quality has follows_template=False."""
        from unittest.mock import patch

        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output = """<!-- METRICS_JSON_START -->
{
  "quality": {
    "actionable_ratio": 0.8,
    "specificity_score": 0.7,
    "evidence_quality": 0.6,
    "follows_template": true,
    "internal_consistency": 0.9
  },
  "consensus": {
    "wrong_field": "bad"
  }
}
<!-- METRICS_JSON_END -->
""" + ("x" * 200)

        # LLM returns garbage → fallback fails
        mock_provider = self._make_mock_provider("{ invalid }")

        with patch(
            "bmad_assist.providers.registry.get_provider",
            return_value=mock_provider,
        ):
            result = extract_synthesis_metrics(
                output,
                llm_fallback=True,
                provider_name="claude",
                model="haiku",
            )

        assert result is not None
        assert result.quality is not None
        assert result.quality.actionable_ratio == 0.8
        # Must be False because LLM fallback was attempted
        assert result.quality.follows_template is False
        assert result.consensus is None

    def test_llm_with_existing_quality_fails_when_consensus_not_recovered(
        self, caplog: "LogCaptureFixture"
    ) -> None:
        """extract_metrics_via_llm with existing_quality but LLM response
        missing consensus → returns None, does not log success."""
        from unittest.mock import patch

        from bmad_assist.benchmarking.schema import QualitySignals
        from bmad_assist.validation.synthesis_parser import extract_metrics_via_llm

        existing_quality = QualitySignals(
            actionable_ratio=0.9,
            specificity_score=0.8,
            evidence_quality=0.7,
            follows_template=True,
            internal_consistency=0.95,
        )

        # LLM returns valid JSON but no consensus section
        response_without_consensus = '{"quality": {"actionable_ratio": 0.5, "specificity_score": 0.5, "evidence_quality": 0.5, "internal_consistency": 0.5}}'
        mock_provider = self._make_mock_provider(response_without_consensus)

        with (
            patch(
                "bmad_assist.providers.registry.get_provider",
                return_value=mock_provider,
            ),
            caplog.at_level(logging.INFO),
        ):
            result = extract_metrics_via_llm(
                "Some synthesis output " * 20,
                provider_name="claude",
                model="haiku",
                existing_quality=existing_quality,
                max_retries=1,
            )

        assert result is None
        assert "succeeded" not in caplog.text

    def test_llm_with_existing_consensus_fails_when_quality_not_recovered(
        self, caplog: "LogCaptureFixture"
    ) -> None:
        """extract_metrics_via_llm with existing_consensus but LLM response
        missing quality → returns None, does not log success."""
        from unittest.mock import patch

        from bmad_assist.benchmarking.schema import ConsensusData
        from bmad_assist.validation.synthesis_parser import extract_metrics_via_llm

        existing_consensus = ConsensusData(
            agreed_findings=3,
            unique_findings=1,
            disputed_findings=0,
            missed_findings=0,
            agreement_score=0.75,
            false_positive_count=1,
        )

        # LLM returns valid JSON but no quality section
        response_without_quality = '{"consensus": {"agreed_findings": 5, "unique_findings": 0, "disputed_findings": 0, "missed_findings": 0, "agreement_score": 1.0, "false_positive_count": 0}}'
        mock_provider = self._make_mock_provider(response_without_quality)

        with (
            patch(
                "bmad_assist.providers.registry.get_provider",
                return_value=mock_provider,
            ),
            caplog.at_level(logging.INFO),
        ):
            result = extract_metrics_via_llm(
                "Some synthesis output " * 20,
                provider_name="claude",
                model="haiku",
                existing_consensus=existing_consensus,
                max_retries=1,
            )

        assert result is None
        assert "succeeded" not in caplog.text

    def test_strict_quality_markdown_fills_consensus(self) -> None:
        """Strict quality valid, no consensus in JSON, markdown infers consensus →
        final result preserves strict quality values, fills consensus from markdown,
        and quality.follows_template=False."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output = """## Synthesis Summary

5 issues verified, 2 false positives dismissed, 3 changes applied.

## Issues Verified

### Critical
- **Issue**: Missing idempotency guard

## Issues Dismissed

- **Claimed Issue**: Duplicate notification audit

## Changes Applied

**Location**: story.md - Acceptance Criteria

<!-- METRICS_JSON_START -->
{
  "quality": {
    "actionable_ratio": 0.85,
    "specificity_score": 0.75,
    "evidence_quality": 0.7,
    "follows_template": true,
    "internal_consistency": 0.9
  }
}
<!-- METRICS_JSON_END -->
"""
        result = extract_synthesis_metrics(output)

        assert result is not None
        # Quality from strict JSON but follows_template forced False
        assert result.quality is not None
        assert result.quality.actionable_ratio == 0.85
        assert result.quality.follows_template is False
        # Consensus from markdown fallback
        assert result.consensus is not None
        assert result.consensus.agreed_findings == 5
        assert result.consensus.false_positive_count == 2

    def test_strict_consensus_markdown_fills_quality(self) -> None:
        """Strict consensus valid, no quality in JSON, markdown infers quality →
        final result preserves strict consensus, fills quality from markdown,
        and quality.follows_template=False."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output = """## Synthesis Summary

5 issues verified, 2 false positives dismissed, 3 changes applied.

## Issues Verified

### Critical
- **Issue**: Missing idempotency guard

## Issues Dismissed

- **Claimed Issue**: Duplicate notification audit

## Changes Applied

**Location**: story.md - Acceptance Criteria

<!-- METRICS_JSON_START -->
{
  "consensus": {
    "agreed_findings": 10,
    "unique_findings": 2,
    "disputed_findings": 1,
    "missed_findings": 0,
    "agreement_score": 0.77,
    "false_positive_count": 3
  }
}
<!-- METRICS_JSON_END -->
"""
        result = extract_synthesis_metrics(output)

        assert result is not None
        # Consensus from strict JSON (not overwritten by markdown)
        assert result.consensus is not None
        assert result.consensus.agreed_findings == 10
        assert result.consensus.agreement_score == 0.77
        # Quality from markdown fallback
        assert result.quality is not None
        assert result.quality.actionable_ratio == 0.0
        assert result.quality.follows_template is False

    def test_llm_neither_existing_partial_recovery_succeeds(self) -> None:
        """extract_metrics_via_llm with no existing sections, LLM recovers only
        quality → counts as success (partial result acceptable)."""
        from unittest.mock import patch

        from bmad_assist.validation.synthesis_parser import extract_metrics_via_llm

        quality_only_response = """{
  "quality": {
    "actionable_ratio": 0.6,
    "specificity_score": 0.5,
    "evidence_quality": 0.4,
    "internal_consistency": 0.7
  }
}"""
        mock_provider = self._make_mock_provider(quality_only_response)

        with patch(
            "bmad_assist.providers.registry.get_provider",
            return_value=mock_provider,
        ):
            result = extract_metrics_via_llm(
                "Some synthesis output " * 20,
                provider_name="claude",
                model="haiku",
            )

        assert result is not None
        assert result.quality is not None
        assert result.quality.follows_template is False
        assert result.consensus is None


# ---------------------------------------------------------------------------
# Layer 1.5: Post-contract fenced JSON recovery
# ---------------------------------------------------------------------------


class TestLayer15PostContractFencedJson:
    """Tests for Layer 1.5: fenced JSON after VALIDATION_CONTRACT_END."""

    def test_fenced_json_after_contract_end_extracted(self) -> None:
        """Fenced JSON with correct keys immediately after contract end is extracted."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        output = (
            "<!-- VALIDATION_SYNTHESIS_START -->\n"
            "<!-- VALIDATION_CONTRACT_START -->\n"
            "resolution: rework\n"
            "verified_critical: 1\n"
            "verified_high: 1\n"
            "fixed_critical: 1\n"
            "fixed_high: 0\n"
            "remaining_critical: 0\n"
            "remaining_high: 1\n"
            "<!-- VALIDATION_CONTRACT_END -->\n\n"
            f"```json\n{_VALID_METRICS_JSON}\n```\n\n"
            "## Synthesis Summary\n\nDone.\n"
        )
        result = extract_synthesis_metrics(output)
        assert result is not None
        assert result.quality is not None
        assert result.quality.actionable_ratio == 0.8
        assert result.consensus is not None
        assert result.consensus.agreed_findings == 5

    def test_fenced_json_with_extra_keys_rejected(self) -> None:
        """Fenced JSON with invented keys like story_id is rejected by Layer 1.5."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        bad_json = """{
  "quality": {
    "actionable_ratio": 0.8,
    "specificity_score": 0.7,
    "evidence_quality": 0.6,
    "follows_template": true,
    "internal_consistency": 0.9
  },
  "consensus": {
    "agreed_findings": 5,
    "unique_findings": 2,
    "disputed_findings": 1,
    "missed_findings": 0,
    "agreement_score": 0.625,
    "false_positive_count": 0
  },
  "story_id": "S-123"
}"""
        output = (
            "<!-- VALIDATION_CONTRACT_START -->\n"
            "resolution: rework\n"
            "verified_critical: 1\n"
            "verified_high: 0\n"
            "fixed_critical: 1\n"
            "fixed_high: 0\n"
            "remaining_critical: 0\n"
            "remaining_high: 0\n"
            "<!-- VALIDATION_CONTRACT_END -->\n\n"
            f"```json\n{bad_json}\n```\n"
        )
        # Layer 1.5 should reject due to extra keys, but backward scan (Layer 2)
        # may still pick it up since it has quality/consensus keys.
        # The important thing is Layer 1.5 specifically rejects it.
        from bmad_assist.validation.synthesis_parser import (
            _try_post_contract_fenced_json,
        )

        result = _try_post_contract_fenced_json(output)
        assert result is None  # Rejected by strict key check

    def test_fenced_json_too_far_from_contract_rejected(self) -> None:
        """Fenced JSON more than 500 chars from contract end is not matched."""
        from bmad_assist.validation.synthesis_parser import (
            _try_post_contract_fenced_json,
        )

        gap = "x" * 550
        output = f"<!-- VALIDATION_CONTRACT_END -->\n{gap}\n```json\n{_VALID_METRICS_JSON}\n```\n"
        result = _try_post_contract_fenced_json(output)
        assert result is None

    def test_fenced_json_ignored_when_metrics_markers_present(self) -> None:
        """When METRICS_JSON markers exist, Layer 1.5 is skipped entirely."""
        from bmad_assist.validation.synthesis_parser import extract_synthesis_metrics

        # Markers present with broken JSON + fenced JSON after contract end
        output = (
            "<!-- VALIDATION_CONTRACT_START -->\n"
            "resolution: resolved\n"
            "verified_critical: 0\n"
            "verified_high: 0\n"
            "fixed_critical: 0\n"
            "fixed_high: 0\n"
            "remaining_critical: 0\n"
            "remaining_high: 0\n"
            "<!-- VALIDATION_CONTRACT_END -->\n\n"
            f"```json\n{_VALID_METRICS_JSON}\n```\n\n"
            "<!-- METRICS_JSON_START -->\n"
            "{ broken json }\n"
            "<!-- METRICS_JSON_END -->\n"
        )
        # The METRICS_JSON markers are present, so Layer 1.5 should NOT fire.
        # Layer 1 tries markers and gets bad JSON.
        # Without backward scan or LLM fallback, falls through to markdown fallback.
        result = extract_synthesis_metrics(output)
        # No backward scan anymore, and no markdown headings → None
        assert result is None


# ---------------------------------------------------------------------------
# Contract block key validation
# ---------------------------------------------------------------------------


class TestContractBlockKeyValidation:
    """Tests for contract block with wrong/invented keys."""

    def test_contract_with_invented_keys_still_parses_if_valid_fields_present(
        self,
    ) -> None:
        """Extra keys like story_id alongside valid keys -> parse succeeds."""
        from bmad_assist.core.loop.synthesis_contract import parse_resolution_block

        block = (
            "resolution: rework\n"
            "verified_critical: 1\n"
            "verified_high: 2\n"
            "fixed_critical: 1\n"
            "fixed_high: 1\n"
            "remaining_critical: 0\n"
            "remaining_high: 1\n"
            "story_id: S-123\n"
            "validators_count: 4\n"
        )
        result = parse_resolution_block(block)
        assert result is not None
        assert result["resolution"] == "rework"
        assert result["verified_critical"] == 1
        # Extra keys are present but don't cause failure
        assert result.get("story_id") == "S-123"

    def test_contract_missing_resolution_fails(self) -> None:
        """Contract block without resolution key -> returns None."""
        from bmad_assist.core.loop.synthesis_contract import parse_resolution_block

        block = (
            "verified_critical: 1\n"
            "verified_high: 2\n"
            "fixed_critical: 1\n"
            "fixed_high: 1\n"
            "remaining_critical: 0\n"
            "remaining_high: 1\n"
        )
        result = parse_resolution_block(block)
        assert result is None

    def test_contract_with_renamed_keys_and_no_valid_resolution_fails(self) -> None:
        """Contract with only renamed keys and no valid resolution -> returns None."""
        from bmad_assist.core.loop.synthesis_contract import parse_resolution_block

        block = (
            "resolution: maybe\n"  # invalid resolution value
            "critical_verified: 1\n"
            "high_verified: 2\n"
        )
        result = parse_resolution_block(block)
        assert result is None


class TestDeferredFieldsBackwardCompat:
    """Backward-compat for `deferred_critical` / `deferred_high` (Step 3, 2026-05).

    Pre-2026-05 synthesis outputs omitted the deferred_* fields entirely. The
    parser must continue to accept those blocks (treating deferred as absent),
    while new-format blocks must round-trip the integer values.
    """

    def test_old_format_without_deferred_fields_still_parses(self) -> None:
        """Pre-2026-05 resolution block (no deferred_*) parses unchanged."""
        from bmad_assist.core.loop.synthesis_contract import parse_resolution_block

        block = (
            "resolution: resolved\n"
            "verified_critical: 0\n"
            "verified_high: 0\n"
            "fixed_critical: 0\n"
            "fixed_high: 0\n"
            "remaining_critical: 0\n"
            "remaining_high: 0\n"
        )
        result = parse_resolution_block(block)
        assert result is not None
        assert result["resolution"] == "resolved"
        # deferred_* keys must NOT be invented when absent from input
        assert "deferred_critical" not in result
        assert "deferred_high" not in result

    def test_new_format_with_deferred_fields_parses_as_ints(self) -> None:
        """New-format block populates deferred_* as ints; cross-validation
        respects remaining_* (which is what the LLM emits)."""
        from bmad_assist.core.loop.synthesis_contract import parse_resolution_block

        block = (
            "resolution: resolved\n"
            "verified_critical: 1\n"
            "verified_high: 0\n"
            "fixed_critical: 0\n"
            "fixed_high: 0\n"
            "deferred_critical: 1\n"
            "deferred_high: 0\n"
            "remaining_critical: 0\n"  # = verified - fixed - deferred
            "remaining_high: 0\n"
        )
        result = parse_resolution_block(block)
        assert result is not None
        assert result["resolution"] == "resolved"
        assert result["deferred_critical"] == 1
        assert result["deferred_high"] == 0
        assert result["remaining_critical"] == 0
        assert result["remaining_high"] == 0

    def test_negative_deferred_value_fails(self) -> None:
        """Negative deferred counts are rejected like other count fields."""
        from bmad_assist.core.loop.synthesis_contract import parse_resolution_block

        block = (
            "resolution: resolved\n"
            "verified_critical: 0\n"
            "deferred_critical: -1\n"
            "remaining_critical: 0\n"
        )
        result = parse_resolution_block(block)
        assert result is None


# ---------------------------------------------------------------------------
# D.3 (2026-05) — Review Findings markdown extraction
# ---------------------------------------------------------------------------


_FULL_REVIEW_FINDINGS_BLOCK = """## Tasks / Subtasks

### Review Findings
- **Date:** 2026-05-12
- **Reviewer:** AI Code Review Synthesis
- **Outcome:** Approved with Reservations
- **Issues Found:** 5
- **Issues Fixed:** 3
- **Deferred (Critical):** 1
- **Deferred (High):** 0
- **Remaining Critical (non-deferred):** 0
- **Remaining High (non-deferred):** 0
- **Action Items Created:** 2

#### Review Follow-ups (AI)
- [ ] [Review][Patch] Activate ATDD tests [tests/foo.spec.ts] — HIGH: convert all test.fixme() to test()
- [x] [Review][Defer] Permutation FST [src/fst.py:42] — deferred from AI review (methodology change)

## Dev Agent Record
"""


_LEGACY_REVIEW_FINDINGS_BLOCK = """## Tasks / Subtasks

### Review Findings
- **Date:** 2026-04-30
- **Reviewer:** AI Code Review Synthesis
- **Outcome:** Approved
- **Issues Found:** 2
- **Issues Fixed:** 2
- **Action Items Created:** 0

## Dev Agent Record
"""


class TestExtractReviewFindings:
    """D.3 — parsing the appended ``### Review Findings`` block."""

    def test_extracts_all_fields_from_new_format(self) -> None:
        """New-format block populates every field on ``ReviewFindings``."""
        from bmad_assist.validation.synthesis_parser import extract_review_findings

        result = extract_review_findings(_FULL_REVIEW_FINDINGS_BLOCK)
        assert result is not None
        assert result.outcome == "Approved with Reservations"
        assert result.issues_found == 5
        assert result.issues_fixed == 3
        assert result.deferred_critical == 1
        assert result.deferred_high == 0
        assert result.remaining_critical == 0
        assert result.remaining_high == 0
        assert result.action_items_created == 2

    def test_legacy_block_returns_none_for_defer_fields(self) -> None:
        """Pre-D.3 blocks omit the four defer-related fields."""
        from bmad_assist.validation.synthesis_parser import extract_review_findings

        result = extract_review_findings(_LEGACY_REVIEW_FINDINGS_BLOCK)
        assert result is not None
        assert result.outcome == "Approved"
        assert result.issues_found == 2
        assert result.issues_fixed == 2
        # The four defer-related fields must be None so callers can detect
        # the legacy schema and fall through to the old code path.
        assert result.deferred_critical is None
        assert result.deferred_high is None
        assert result.remaining_critical is None
        assert result.remaining_high is None
        assert result.action_items_created == 0

    def test_missing_heading_returns_none(self) -> None:
        """No ``### Review Findings`` heading anywhere → returns None."""
        from bmad_assist.validation.synthesis_parser import extract_review_findings

        result = extract_review_findings("just some unrelated markdown\n")
        assert result is None

    def test_uses_last_review_findings_block_when_multiple_rounds(self) -> None:
        """Rework loops append new Review Findings blocks; last wins."""
        from bmad_assist.validation.synthesis_parser import extract_review_findings

        report = (
            "### Review Findings\n"
            "- **Date:** 2026-05-01\n"
            "- **Outcome:** Changes Requested\n"
            "- **Issues Found:** 5\n"
            "- **Issues Fixed:** 2\n"
            "- **Deferred (Critical):** 0\n"
            "- **Deferred (High):** 0\n"
            "- **Remaining Critical (non-deferred):** 1\n"
            "- **Remaining High (non-deferred):** 2\n"
            "- **Action Items Created:** 3\n"
            "\n"
            "### Review Findings\n"
            "- **Date:** 2026-05-02\n"
            "- **Outcome:** Approved\n"
            "- **Issues Found:** 5\n"
            "- **Issues Fixed:** 5\n"
            "- **Deferred (Critical):** 0\n"
            "- **Deferred (High):** 0\n"
            "- **Remaining Critical (non-deferred):** 0\n"
            "- **Remaining High (non-deferred):** 0\n"
            "- **Action Items Created:** 0\n"
        )
        result = extract_review_findings(report)
        assert result is not None
        assert result.outcome == "Approved"
        assert result.issues_fixed == 5
        assert result.remaining_critical == 0

    def test_non_integer_count_value_returns_none_for_that_field(self) -> None:
        """Garbage in a count cell → the specific field is None, not an error."""
        from bmad_assist.validation.synthesis_parser import extract_review_findings

        report = (
            "### Review Findings\n"
            "- **Outcome:** Approved\n"
            "- **Issues Found:** unknown\n"
            "- **Deferred (Critical):** 1\n"
        )
        result = extract_review_findings(report)
        assert result is not None
        assert result.outcome == "Approved"
        assert result.issues_found is None
        assert result.deferred_critical == 1


class TestCountReviewFollowupsBySeverity:
    """D.3 — parser-observed counts under ``#### Review Follow-ups (AI)``."""

    def test_counts_critical_and_high_bullets(self) -> None:
        """Inline severity prefixes drive bucket counts."""
        from bmad_assist.validation.synthesis_parser import (
            count_review_followups_by_severity,
        )

        report = (
            "#### Review Follow-ups (AI)\n"
            "- [ ] [Review][Patch] Fix SQL injection [api/db.py:12] — CRITICAL: ...\n"
            "- [ ] [Review][Patch] Validate input [api/router.py:5] — HIGH: ...\n"
            "- [ ] [Review][Decision] Pick API surface — MEDIUM: ...\n"
            "- [x] [Review][Defer] Out-of-scope item — deferred from AI review\n"
        )
        counts = count_review_followups_by_severity(report)
        assert counts == {
            "critical": 1,
            "high": 1,
            "medium": 1,
            "low": 0,
            "unknown": 0,
        }

    def test_excludes_checked_and_deferred(self) -> None:
        """Checked tasks AND Defer markers are excluded by construction."""
        from bmad_assist.validation.synthesis_parser import (
            count_review_followups_by_severity,
        )

        report = (
            "- [x] [Review][Patch] already done [a.py:1] — CRITICAL: ...\n"
            "- [ ] [Review][Defer] never counted [b.py:2] — deferred ...\n"
            "- [x] [Review][Defer] also defer [c.py:3] — deferred ...\n"
        )
        counts = count_review_followups_by_severity(report)
        assert counts["critical"] == 0
        assert counts["high"] == 0

    def test_missing_severity_buckets_as_unknown(self) -> None:
        """Bullets without an inline severity hint land in ``unknown``."""
        from bmad_assist.validation.synthesis_parser import (
            count_review_followups_by_severity,
        )

        report = (
            "- [ ] [Review][Patch] no inline severity [a.py:1] — just a detail\n"
            "- [ ] [Review][Patch] another one [b.py:2]\n"
        )
        counts = count_review_followups_by_severity(report)
        assert counts["unknown"] == 2
        assert counts["critical"] == 0
        assert counts["high"] == 0

    def test_important_aliases_to_high(self) -> None:
        """IMPORTANT and HIGH share the same accounting bucket."""
        from bmad_assist.validation.synthesis_parser import (
            count_review_followups_by_severity,
        )

        report = (
            "- [ ] [Review][Patch] one [a.py:1] — HIGH: ...\n"
            "- [ ] [Review][Patch] two [b.py:2] — IMPORTANT: ...\n"
        )
        counts = count_review_followups_by_severity(report)
        assert counts["high"] == 2

    def test_empty_report_returns_zeros(self) -> None:
        """Empty input returns the five-key zero dict."""
        from bmad_assist.validation.synthesis_parser import (
            count_review_followups_by_severity,
        )

        counts = count_review_followups_by_severity("")
        assert counts == {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "unknown": 0,
        }


class TestCrossCheckDeferCounts:
    """D.3 — cross-check LLM-reported counts against parser-observed tasks."""

    def test_matching_counts_trusts_llm(self) -> None:
        """LLM and parser agree → LLM counts returned, no warning logged."""
        from bmad_assist.validation.synthesis_parser import (
            cross_check_defer_counts,
            extract_review_findings,
        )

        report = (
            "### Review Findings\n"
            "- **Outcome:** Changes Requested\n"
            "- **Issues Found:** 3\n"
            "- **Issues Fixed:** 0\n"
            "- **Deferred (Critical):** 0\n"
            "- **Deferred (High):** 0\n"
            "- **Remaining Critical (non-deferred):** 1\n"
            "- **Remaining High (non-deferred):** 1\n"
            "- **Action Items Created:** 2\n"
            "\n"
            "#### Review Follow-ups (AI)\n"
            "- [ ] [Review][Patch] critical bug [a.py:1] — CRITICAL: ...\n"
            "- [ ] [Review][Patch] high bug [b.py:2] — HIGH: ...\n"
        )
        findings = extract_review_findings(report)
        rc, rh = cross_check_defer_counts(findings, report)
        assert rc == 1
        assert rh == 1

    def test_full_block_mismatch_warns_and_uses_parser(self, caplog: LogCaptureFixture) -> None:
        """The hand-built fixture LLM-claims 0 high-remaining but ships one.

        It carries one unchecked HIGH task — the parser must override and warn.
        """
        from bmad_assist.validation.synthesis_parser import (
            cross_check_defer_counts,
            extract_review_findings,
        )

        findings = extract_review_findings(_FULL_REVIEW_FINDINGS_BLOCK)
        caplog.set_level(logging.WARNING, logger="bmad_assist.validation.synthesis_parser")
        rc, rh = cross_check_defer_counts(findings, _FULL_REVIEW_FINDINGS_BLOCK)
        # LLM remaining_high=0, but the report has one unchecked HIGH task.
        assert rc == 0
        assert rh == 1
        assert any("cross-check mismatch" in r.getMessage().lower() for r in caplog.records)

    def test_mismatch_logs_warning_and_returns_parser_counts(
        self, caplog: LogCaptureFixture
    ) -> None:
        """LLM claims 0 remaining_critical but parser sees 2 → parser wins."""
        from bmad_assist.validation.synthesis_parser import (
            ReviewFindings,
            cross_check_defer_counts,
        )

        report = (
            "### Review Findings\n"
            "- **Outcome:** Approved\n"
            "- **Deferred (Critical):** 0\n"
            "- **Deferred (High):** 0\n"
            "- **Remaining Critical (non-deferred):** 0\n"
            "- **Remaining High (non-deferred):** 0\n"
            "- **Action Items Created:** 2\n"
            "\n"
            "#### Review Follow-ups (AI)\n"
            "- [ ] [Review][Patch] first [a.py:1] — CRITICAL: ...\n"
            "- [ ] [Review][Patch] second [b.py:2] — CRITICAL: ...\n"
        )
        findings = ReviewFindings(
            outcome="Approved",
            issues_found=None,
            issues_fixed=None,
            deferred_critical=0,
            deferred_high=0,
            remaining_critical=0,
            remaining_high=0,
            action_items_created=2,
        )
        caplog.set_level(logging.WARNING, logger="bmad_assist.validation.synthesis_parser")
        rc, rh = cross_check_defer_counts(findings, report)
        assert rc == 2
        assert rh == 0
        # Warning was logged
        assert any(
            "cross-check mismatch" in record.getMessage().lower() for record in caplog.records
        )

    def test_none_findings_returns_none(self) -> None:
        """``findings is None`` short-circuits to ``(None, None)``."""
        from bmad_assist.validation.synthesis_parser import cross_check_defer_counts

        rc, rh = cross_check_defer_counts(None, "irrelevant")
        assert rc is None and rh is None

    def test_legacy_findings_returns_none(self) -> None:
        """remaining_* is None on legacy reports → caller falls back."""
        from bmad_assist.validation.synthesis_parser import (
            cross_check_defer_counts,
            extract_review_findings,
        )

        findings = extract_review_findings(_LEGACY_REVIEW_FINDINGS_BLOCK)
        rc, rh = cross_check_defer_counts(findings, _LEGACY_REVIEW_FINDINGS_BLOCK)
        assert rc is None and rh is None


# Type hints for fixtures
if TYPE_CHECKING:
    from bmad_assist.benchmarking import StoryInfo, WorkflowInfo
