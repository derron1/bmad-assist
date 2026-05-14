"""Base class for all Deep Verify verification methods.

This module defines the abstract base class that all verification methods
(Pattern Match, Boundary Analysis, Assumption Surfacing, etc.) must implement.

The ABC pattern ensures consistent interfaces across all methods while allowing
for method-specific implementations.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from bmad_assist.deep_verify.core.types import Severity

if TYPE_CHECKING:
    from bmad_assist.deep_verify.core.types import Finding, MethodId


# Severity ordering for per-method caps (D.8 Agent A).
#
# This ordering exists solely so a method can downgrade findings that exceed
# its own confidence ceiling. It is INTENTIONALLY independent of
# scoring.SEVERITY_WEIGHTS (which expresses verdict math, not rank) — keeping
# them separate avoids accidental coupling where a tweak to verdict weights
# silently reorders the cap logic.
_SEVERITY_RANK: dict[Severity, int] = {
    Severity.INFO: 1,
    Severity.WARNING: 2,
    Severity.ERROR: 3,
    Severity.CRITICAL: 4,
}


class BaseVerificationMethod(ABC):
    """Abstract base class for Deep Verify verification methods.

    All verification methods (Pattern Match #153, Boundary Analysis #154, etc.)
    must inherit from this class and implement the analyze() method.

    Attributes:
        method_id: Unique method identifier (e.g., "#153", "#154").

    Example:
        >>> class PatternMatchMethod(BaseVerificationMethod):
        ...     method_id = MethodId("#153")
        ...
        ...     async def analyze(
        ...         self,
        ...         artifact_text: str,
        ...         **kwargs: dict[str, object]
        ...     ) -> list[Finding]:
        ...         # Method-specific implementation
        ...         return findings

    """

    method_id: MethodId

    # Per-method severity ceiling (D.8 Agent A).
    #
    # When set, any Finding emitted by this method whose severity outranks the
    # cap is downgraded to the cap. Findings are NEVER dropped — only their
    # severity changes. Set on the subclass for methods prone to severity
    # inflation (#205 worst_case, #203 domain_expert, #154 boundary_analysis).
    # See _cap_severities() for the application point.
    max_severity: Severity | None = None

    @abstractmethod
    async def analyze(
        self,
        artifact_text: str,
        **kwargs: dict[str, object],
    ) -> list[Finding]:
        """Analyze artifact text and return findings.

        Args:
            artifact_text: The text content to analyze.
            **kwargs: Additional context including:
                - domains: Optional list of ArtifactDomain to filter patterns
                - config: Optional DeepVerifyConfig for method configuration
                - context: Optional additional context for analysis

        Returns:
            List of Finding objects with method-prefixed temporary IDs.
            The DeepVerifyEngine will reassign final sequential IDs (F1, F2, ...).

        Raises:
            Exception: Method implementations should handle their own errors
                gracefully and return empty list on failure.

        """
        ...

    def get_method_prompt(self, **kwargs: object) -> str:
        """Return method's analysis instructions WITHOUT file content.

        Sent as Turn 1 of multi-turn batch session. Override in subclasses
        that support batch mode.

        Args:
            **kwargs: Additional context, may include 'domains'.

        Returns:
            Method instruction prompt string.

        Raises:
            NotImplementedError: If method doesn't support batch mode.

        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support batch mode")

    def get_file_prompt(self, file_path: str, content: str) -> str:
        """Return per-file prompt for Turn 2..N of batch session.

        Args:
            file_path: Path to the file being analyzed.
            content: File content to analyze.

        Returns:
            Formatted file analysis prompt.

        """
        return (
            f"Analyze this file:\n"
            f"=== FILE: {file_path} ===\n{content}\n=== END ===\n\n"
            f"Return your findings in the same JSON format as instructed."
        )

    def parse_file_response(self, raw_response: str, file_path: str) -> list[Finding]:
        """Parse LLM response for a single file in batch mode.

        Args:
            raw_response: Raw LLM response text for one file.
            file_path: Path to the file that was analyzed.

        Returns:
            List of Finding objects extracted from the response.

        Raises:
            NotImplementedError: If method doesn't support batch mode.

        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support batch mode")

    @property
    def supports_batch(self) -> bool:
        """Whether this method supports batch mode."""
        return False

    def _cap_severities(self, findings: list[Finding]) -> list[Finding]:
        """Downgrade any finding whose severity exceeds this method's cap.

        Applies ``max_severity`` (set as a class attribute on subclasses) as
        an upper bound on emitted finding severities. Findings exceeding the
        cap are returned with severity replaced by the cap; all other fields
        (id, method_id, pattern_id, title, description, evidence, domain) are
        preserved. Findings at or below the cap pass through unchanged.

        If ``max_severity`` is None (the default), findings are returned
        unmodified.

        Args:
            findings: Findings emitted by the method's analyze() implementation.

        Returns:
            New list of findings with capped severities. The list and any
            modified Finding instances are fresh objects — the input list and
            its members are not mutated.

        """
        cap = self.max_severity
        if cap is None:
            return findings

        cap_rank = _SEVERITY_RANK[cap]
        capped: list[Finding] = []
        for finding in findings:
            if _SEVERITY_RANK[finding.severity] > cap_rank:
                capped.append(dataclasses.replace(finding, severity=cap))
            else:
                capped.append(finding)
        return capped

    def __repr__(self) -> str:
        """Return a string representation of the method."""
        method_id = getattr(self, "method_id", "unknown")
        return f"{self.__class__.__name__}(method_id={method_id!r})"
