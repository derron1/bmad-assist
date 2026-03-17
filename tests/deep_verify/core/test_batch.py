"""Tests for Deep Verify batch verification helpers."""

from pathlib import Path

from bmad_assist.deep_verify.config import DeepVerifyConfig, ResourceLimitConfig
from bmad_assist.deep_verify.core import Evidence, Finding, Severity
from bmad_assist.deep_verify.core.batch import BatchVerifyOrchestrator
from bmad_assist.deep_verify.core.types import MethodId


def _make_finding(
    finding_id: str,
    *,
    severity: Severity,
    method_id: str,
    quote: str,
    pattern_id: str | None = None,
) -> Finding:
    return Finding(
        id=finding_id,
        severity=severity,
        title=f"Finding {finding_id}",
        description="desc",
        method_id=MethodId(method_id),
        evidence=[Evidence(quote=quote, confidence=0.9)],
        pattern_id=pattern_id,
    )


def test_batch_build_verdict_deduplicates_before_scoring(tmp_path: Path) -> None:
    """Duplicate findings should collapse before verdict scoring in batch mode."""
    orchestrator = BatchVerifyOrchestrator(DeepVerifyConfig(), tmp_path)
    findings = [
        _make_finding("a", severity=Severity.WARNING, method_id="#201", quote="same quote"),
        _make_finding("b", severity=Severity.ERROR, method_id="#201", quote="same quote"),
    ]

    verdict = orchestrator._build_verdict(findings, [], [MethodId("#201")])

    assert len(verdict.findings) == 1
    assert verdict.findings[0].severity == Severity.ERROR


def test_batch_build_verdict_applies_finding_limits(tmp_path: Path) -> None:
    """Batch verdicts should apply the configured per-method and total finding caps."""
    config = DeepVerifyConfig(
        resource_limits=ResourceLimitConfig(
            max_findings_per_method=1,
            max_total_findings=10,
        )
    )
    orchestrator = BatchVerifyOrchestrator(config, tmp_path)
    findings = [
        _make_finding(
            "a",
            severity=Severity.ERROR,
            method_id="#201",
            quote="database transaction rollback missing on payment failure",
        ),
        _make_finding(
            "b",
            severity=Severity.WARNING,
            method_id="#201",
            quote="input validation missing on retry token header",
        ),
        _make_finding(
            "c",
            severity=Severity.WARNING,
            method_id="#204",
            quote="webhook integration omits idempotency key propagation",
        ),
    ]

    verdict = orchestrator._build_verdict(
        findings,
        [],
        [MethodId("#201"), MethodId("#204")],
    )

    assert len(verdict.findings) == 2
    assert [finding.id for finding in verdict.findings] == ["F1", "F2"]
