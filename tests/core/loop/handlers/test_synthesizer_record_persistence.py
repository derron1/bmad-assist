"""Tests for post-repair synthesizer record persistence.

Verifies that _save_synthesizer_record receives post-repair metrics and
custom metadata, and that the saved record reflects the final state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from bmad_assist.benchmarking import (
    ConsensusData,
    LLMEvaluationRecord,
    QualitySignals,
)
from bmad_assist.validation.synthesis_parser import SynthesisMetrics


def _make_handler() -> MagicMock:
    """Create a minimal mock of ValidateStorySynthesisHandler."""
    from bmad_assist.core.loop.handlers.validate_story_synthesis import (
        ValidateStorySynthesisHandler,
    )

    handler = MagicMock(spec=ValidateStorySynthesisHandler)
    handler.config = MagicMock()
    handler.config.benchmarking.enabled = True
    handler.project_path = "/tmp/test-project"
    handler.get_provider.return_value.provider_name = "claude"
    handler.get_model.return_value = "sonnet-4"
    handler._compression_metrics = None
    # Bind the real method to the mock instance
    handler._save_synthesizer_record = (
        ValidateStorySynthesisHandler._save_synthesizer_record.__get__(handler)
    )
    return handler


def _make_code_review_handler() -> MagicMock:
    """Create a minimal mock of CodeReviewSynthesisHandler."""
    from bmad_assist.core.loop.handlers.code_review_synthesis import (
        CodeReviewSynthesisHandler,
    )

    handler = MagicMock(spec=CodeReviewSynthesisHandler)
    handler.config = MagicMock()
    handler.config.benchmarking.enabled = True
    handler.config.workflow_variant = "experiment-a"
    handler.config.providers.master.provider = "claude"
    handler.project_path = "/tmp/test-project"
    handler.get_provider.return_value.provider_name = "claude"
    handler.get_model.return_value = "sonnet-4"
    handler._compression_metrics = None
    handler._save_synthesizer_record = (
        CodeReviewSynthesisHandler._save_synthesizer_record.__get__(handler)
    )
    return handler


def _make_quality() -> QualitySignals:
    return QualitySignals(
        actionable_ratio=0.9,
        specificity_score=0.8,
        evidence_quality=0.7,
        follows_template=True,
        internal_consistency=0.95,
    )


def _make_consensus() -> ConsensusData:
    return ConsensusData(
        agreed_findings=5,
        unique_findings=2,
        disputed_findings=1,
        missed_findings=0,
        agreement_score=0.85,
        false_positive_count=0,
    )


class TestSynthesizerRecordPostRepair:
    """Tests that saved synthesizer record reflects post-repair state."""

    @patch(
        "bmad_assist.benchmarking.storage.save_evaluation_record"
    )
    @patch(
        "bmad_assist.benchmarking.storage.get_benchmark_base_dir"
    )
    @patch(
        "bmad_assist.validation.benchmarking_integration.should_collect_benchmarking",
        return_value=True,
    )
    def test_save_record_after_repair_has_non_null_quality(
        self, _mock_collect, _mock_base_dir, mock_save
    ) -> None:
        """When repaired metrics are provided, saved record has quality/consensus."""
        handler = _make_handler()
        repaired_metrics = SynthesisMetrics(
            quality=_make_quality(), consensus=_make_consensus()
        )

        handler._save_synthesizer_record(
            synthesis_output="no metrics json here",
            epic_num=1,
            story_num=1,
            story_title="test",
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC),
            input_tokens=0,
            output_tokens=50,
            validator_count=2,
            metrics=repaired_metrics,
        )

        record: LLMEvaluationRecord = mock_save.call_args[0][0]
        assert record.quality is not None
        assert record.quality.actionable_ratio == 0.9
        assert record.consensus is not None
        assert record.consensus.agreed_findings == 5

    @patch(
        "bmad_assist.benchmarking.storage.save_evaluation_record"
    )
    @patch(
        "bmad_assist.benchmarking.storage.get_benchmark_base_dir"
    )
    @patch(
        "bmad_assist.validation.benchmarking_integration.should_collect_benchmarking",
        return_value=True,
    )
    def test_save_record_without_repair_unchanged(
        self, _mock_collect, _mock_base_dir, mock_save
    ) -> None:
        """When no metrics provided, record extracts from output (None for plain text)."""
        handler = _make_handler()

        handler._save_synthesizer_record(
            synthesis_output="plain text, no json",
            epic_num=1,
            story_num=1,
            story_title="test",
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC),
            input_tokens=0,
            output_tokens=50,
            validator_count=2,
        )

        record: LLMEvaluationRecord = mock_save.call_args[0][0]
        assert record.quality is None
        assert record.consensus is None

    @patch(
        "bmad_assist.benchmarking.storage.save_evaluation_record"
    )
    @patch(
        "bmad_assist.benchmarking.storage.get_benchmark_base_dir"
    )
    @patch(
        "bmad_assist.validation.benchmarking_integration.should_collect_benchmarking",
        return_value=True,
    )
    def test_repair_metadata_in_custom_field(
        self, _mock_collect, _mock_base_dir, mock_save
    ) -> None:
        """Repair metadata appears in custom dict when repair occurred."""
        handler = _make_handler()

        handler._save_synthesizer_record(
            synthesis_output="test",
            epic_num=1,
            story_num=1,
            story_title="test",
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC),
            input_tokens=0,
            output_tokens=50,
            validator_count=2,
            record_custom={
                "final_extraction_quality": "strict",
                "final_resolution": "approved",
                "repaired_contract": True,
                "repaired_metrics": False,
            },
        )

        record: LLMEvaluationRecord = mock_save.call_args[0][0]
        assert record.custom is not None
        assert record.custom["final_extraction_quality"] == "strict"
        assert record.custom["final_resolution"] == "approved"
        assert record.custom["repaired_contract"] is True
        assert record.custom["repaired_metrics"] is False

    @patch(
        "bmad_assist.benchmarking.storage.save_evaluation_record"
    )
    @patch(
        "bmad_assist.benchmarking.storage.get_benchmark_base_dir"
    )
    @patch(
        "bmad_assist.validation.benchmarking_integration.should_collect_benchmarking",
        return_value=True,
    )
    def test_final_extraction_quality_always_present(
        self, _mock_collect, _mock_base_dir, mock_save
    ) -> None:
        """final_extraction_quality is in custom dict even without repair."""
        handler = _make_handler()

        handler._save_synthesizer_record(
            synthesis_output="test",
            epic_num=1,
            story_num=1,
            story_title="test",
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC),
            input_tokens=0,
            output_tokens=50,
            validator_count=2,
            record_custom={
                "final_extraction_quality": "degraded",
                "final_resolution": "rework",
            },
        )

        record: LLMEvaluationRecord = mock_save.call_args[0][0]
        assert record.custom is not None
        assert record.custom["final_extraction_quality"] == "degraded"
        assert record.custom["final_resolution"] == "rework"
        # No repair keys when repair didn't happen
        assert "repaired_contract" not in record.custom
        assert "repaired_metrics" not in record.custom


class TestCodeReviewSynthesizerRecordPersistence:
    """Tests that code-review synthesis records persist final metadata too."""

    @patch("bmad_assist.benchmarking.storage.save_evaluation_record")
    @patch("bmad_assist.benchmarking.storage.get_benchmark_base_dir")
    @patch(
        "bmad_assist.validation.benchmarking_integration.should_collect_benchmarking",
        return_value=True,
    )
    def test_save_record_persists_final_resolution_metadata(
        self, _mock_collect, _mock_base_dir, mock_save
    ) -> None:
        """Code-review synthesis stores final extraction quality and resolution."""
        handler = _make_code_review_handler()

        handler._save_synthesizer_record(
            synthesis_output="plain text, no json",
            epic_num=2,
            story_num="7",
            story_title="review story",
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC),
            input_tokens=0,
            output_tokens=25,
            reviewer_count=3,
            record_custom={
                "final_extraction_quality": "degraded",
                "final_resolution": "rework",
            },
        )

        record: LLMEvaluationRecord = mock_save.call_args[0][0]
        assert record.custom is not None
        assert record.custom["phase"] == "code-review-synthesis"
        assert record.custom["reviewer_count"] == 3
        assert record.custom["final_extraction_quality"] == "degraded"
        assert record.custom["final_resolution"] == "rework"
