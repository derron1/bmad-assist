"""Project quality-gate runner for bmad-assist phase success checks.

Runs project pre-commit hooks (or any configured hook command) against a set
of changed files BEFORE a phase declares success. Hard-fail by design — no
LLM fix-iteration retry. The phase fails, captured output is surfaced, and
the user re-runs or fixes manually.

This module is intentionally standalone: it imports nothing from
``bmad_assist.core``, ``bmad_assist.validation``, or other subsystems so
that handlers can consume it without creating circular dependencies.
"""

from bmad_assist.quality_gate.runner import (
    QualityGateResult,
    run_quality_gate,
)

__all__ = [
    "QualityGateResult",
    "run_quality_gate",
]
