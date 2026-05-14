"""Boundary Analysis Method (#154) for Deep Verify.

This module implements the Boundary Analysis verification method that detects
edge cases via domain-specific checklists analyzed by an LLM.

Method #154 is one of two methods that always run regardless of domain
detection results (the other being #153 Pattern Match).

All checklist items are evaluated in a single batched LLM call per artifact.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from bmad_assist.deep_verify.core.types import (
    ArtifactDomain,
    Evidence,
    Finding,
    MethodId,
    PatternId,
    Severity,
)
from bmad_assist.deep_verify.methods.base import BaseVerificationMethod
from bmad_assist.providers import ClaudeSDKProvider

logger = logging.getLogger(__name__)

__all__ = [
    "BoundaryAnalysisMethod",
    "ChecklistItem",
    "ChecklistLoader",
    "ChecklistItemYaml",
    "ChecklistYaml",
    "ChecklistAnalysisResponse",
    "BOUNDARY_ANALYSIS_SYSTEM_PROMPT",
]

# =============================================================================
# Constants
# =============================================================================

DEFAULT_MODEL = "haiku"
DEFAULT_TIMEOUT = 60
DEFAULT_THRESHOLD = 0.6
MAX_ARTIFACT_LENGTH = 3000

# =============================================================================
# System Prompt
# =============================================================================

BOUNDARY_ANALYSIS_SYSTEM_PROMPT = """You are a boundary analysis expert for code review.
Analyze the provided artifact against ALL checklist items below.

Your task:
1. For EACH checklist item, determine if the artifact properly handles the described edge case
2. If NOT handled (violated), provide evidence from the code
3. Return a JSON array with one entry per checklist item

Response format (JSON array only, no other text):
[
    {
        "id": "CHECKLIST-ID",
        "violated": true/false,
        "confidence": 0.0-1.0,
        "evidence_quote": "Relevant code snippet (if violated, else empty string)",
        "line_number": 123,
        "explanation": "Brief explanation"
    }
]

Rules:
- Return one entry per checklist item, using the exact checklist ID
- "violated" = true means the edge case is NOT properly handled
- confidence should reflect how certain you are (0.5 = uncertain, 0.9 = very certain)
- evidence_quote must be verbatim from the artifact (empty string if not violated)
- line_number should be accurate if identifiable, null otherwise
- Only include items where you have a clear assessment"""


# =============================================================================
# Data Classes
# =============================================================================


@dataclass(frozen=True)
class ChecklistItem:
    """Single checklist item for boundary analysis.

    The domain field is a string (not ArtifactDomain enum) to support:
    - "general" for always-applied checklists
    - "security", "storage", "messaging", etc. for domain-specific checklists

    Attributes:
        id: Unique identifier (e.g., "GEN-001", "SEC-BOUNDARY-001")
        category: Logical grouping (e.g., "empty_input", "auth_bypass")
        question: Yes/no question for LLM to answer
        description: Detailed explanation of the edge case
        severity: Severity level if this boundary case is violated
        domain: Domain this checklist belongs to ("general" or domain name)

    """

    id: str
    category: str
    question: str
    description: str
    severity: Severity
    domain: str

    def __repr__(self) -> str:
        """Return a string representation of the checklist item."""
        return f"ChecklistItem(id={self.id!r}, category={self.category!r}, domain={self.domain!r})"


# =============================================================================
# Pydantic Models for YAML Validation
# =============================================================================


class ChecklistItemYaml(BaseModel):
    """Pydantic model for validating checklist YAML structure."""

    id: str = Field(pattern=r"^[A-Z]+-[A-Z0-9-]+$")
    category: str
    question: str
    description: str
    severity_if_violated: str
    domain: str

    @field_validator("severity_if_violated")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        """Validate that severity is a valid Severity enum value."""
        valid = {s.value for s in Severity}
        if v not in valid:
            raise ValueError(f"Invalid severity: {v}. Must be one of: {valid}")
        return v

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, v: str) -> str:
        """Validate that domain is a valid domain string."""
        valid = {"general", "security", "storage", "messaging", "transform", "concurrency", "api"}
        if v not in valid:
            raise ValueError(f"Invalid domain: {v}. Must be one of: {valid}")
        return v

    def to_checklist_item(self) -> ChecklistItem:
        """Convert validated YAML to ChecklistItem dataclass."""
        return ChecklistItem(
            id=self.id,
            category=self.category,
            question=self.question,
            description=self.description,
            severity=Severity(self.severity_if_violated),
            domain=self.domain,
        )


class ChecklistYaml(BaseModel):
    """Root model for checklist YAML files."""

    checklist: list[ChecklistItemYaml]


# =============================================================================
# Pydantic Model for LLM Response
# =============================================================================


class ChecklistAnalysisResponse(BaseModel):
    """Expected LLM response structure for checklist analysis."""

    id: str = ""
    violated: bool
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_quote: str | None = ""
    line_number: int | None = None
    explanation: str | None = ""


# =============================================================================
# Checklist Loader
# =============================================================================


class ChecklistLoader:
    """Loads and manages boundary analysis checklists from YAML files."""

    def __init__(self, checklist_dir: Path) -> None:
        """Initialize the checklist loader.

        Args:
            checklist_dir: Directory containing checklist YAML files.

        """
        self._checklist_dir = checklist_dir

    def __repr__(self) -> str:
        """Return a string representation of the checklist loader."""
        return f"ChecklistLoader(dir={self._checklist_dir!r})"

    def load(self, domains: list[ArtifactDomain] | None = None) -> list[ChecklistItem]:
        """Load checklists for specified domains plus general checklist.

        Always loads general.yaml first, then domain-specific checklists.
        Domain-specific items override general items with same ID.

        Args:
            domains: Optional list of domains to load domain-specific checklists.
                     If None, only general checklist is loaded.

        Returns:
            List of ChecklistItem objects.

        """
        items: list[ChecklistItem] = []

        # Always load general checklist first
        items.extend(self._load_checklist_file("general"))

        # Load domain-specific checklists
        if domains:
            domain_file_map = {
                ArtifactDomain.SECURITY: "security",
                ArtifactDomain.STORAGE: "storage",
                ArtifactDomain.MESSAGING: "messaging",
                ArtifactDomain.API: "api",
                ArtifactDomain.CONCURRENCY: "concurrency",
                ArtifactDomain.TRANSFORM: "transform",
            }

            loaded_files: set[str] = set()
            for domain in domains:
                file_name = domain_file_map.get(domain)
                if file_name and file_name not in loaded_files:
                    items.extend(self._load_checklist_file(file_name))
                    loaded_files.add(file_name)

        # Deduplicate by ID (later overrides earlier)
        seen: dict[str, ChecklistItem] = {}
        for item in items:
            seen[item.id] = item

        return list(seen.values())

    def _load_checklist_file(self, name: str) -> list[ChecklistItem]:
        """Load checklist items from a YAML file.

        Args:
            name: Base name of the checklist file (e.g., "general", "security").

        Returns:
            List of ChecklistItem objects from the file.

        """
        file_path = self._checklist_dir / f"{name}.yaml"

        if not file_path.exists():
            logger.debug("Checklist file not found: %s", file_path)
            return []

        try:
            with open(file_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if data is None:
                logger.debug("Empty checklist file: %s", file_path)
                return []

            # Validate with Pydantic
            checklist_yaml = ChecklistYaml(**data)

            # Convert to ChecklistItem dataclasses
            items = [item_yaml.to_checklist_item() for item_yaml in checklist_yaml.checklist]

            logger.debug("Loaded %d items from %s", len(items), file_path)
            return items

        except yaml.YAMLError as e:
            logger.error("Failed to parse checklist YAML %s: %s", file_path, e)
            raise RuntimeError(f"Failed to parse checklist {file_path}: {e}") from e
        except Exception as e:
            logger.error("Failed to load checklist %s: %s", file_path, e)
            raise RuntimeError(f"Failed to load checklist {file_path}: {e}") from e


# =============================================================================
# Boundary Analysis Method
# =============================================================================


class BoundaryAnalysisMethod(BaseVerificationMethod):
    """Boundary Analysis Method (#154) - Edge case detection via checklists.

    This method analyzes artifact text against domain-specific checklists
    using a single batched LLM call to identify unhandled edge cases and
    boundary conditions that pattern matching might miss.

    Attributes:
        method_id: Unique method identifier "#154".
        _provider: ClaudeSDKProvider for LLM calls.
        _model: Model identifier for LLM calls.
        _threshold: Minimum confidence threshold for findings (0.0-1.0).
        _timeout: Timeout in seconds for LLM calls.
        _loader: ChecklistLoader for loading domain-specific checklists.

    Example:
        >>> from pathlib import Path
        >>> method = BoundaryAnalysisMethod()
        >>> findings = await method.analyze("code with potential edge cases")
        >>> for f in findings:
        ...     print(f"{f.id}: {f.title}")

    """

    method_id: MethodId

    # D.8 Agent A: cap #154 at WARNING.
    #
    # Boundary Analysis is checklist-driven (deterministic pattern emission
    # against YAML-defined rules), so it inflates less than #205 — but it can
    # still mark broad-edge-case checklist items as CRITICAL. Cap is
    # conservative; expected real-world impact is small relative to #205.
    max_severity: Severity | None = Severity.WARNING

    def __init__(
        self,
        checklist_dir: Path | None = None,
        model: str = DEFAULT_MODEL,
        threshold: float = DEFAULT_THRESHOLD,
        timeout: int = DEFAULT_TIMEOUT,
        llm_client: Any | None = None,
    ) -> None:
        """Initialize the Boundary Analysis Method.

        Args:
            checklist_dir: Optional directory containing checklist YAML files.
                          If None, uses default location.
            model: Model identifier for LLM calls (default: "haiku").
            threshold: Minimum confidence threshold for findings (default: 0.6).
            timeout: Timeout in seconds for LLM calls (default: 60).
            llm_client: Optional LLMClient for managed LLM calls. If provided,
                       uses LLMClient (with retry, rate limiting, cost tracking).
                       If None, creates direct ClaudeSDKProvider.

        Raises:
            ValueError: If threshold is not between 0.0 and 1.0.

        """
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"threshold must be between 0.0 and 1.0, got {threshold}")

        self.method_id = MethodId("#154")
        self._llm_client = llm_client
        # Only create provider if LLMClient NOT provided
        self._provider: ClaudeSDKProvider | None
        if llm_client is None:
            self._provider = ClaudeSDKProvider()
        else:
            self._provider = None
        self._model = model
        self._threshold = threshold
        self._timeout = timeout

        # Initialize checklist loader
        if checklist_dir is None:
            checklist_dir = Path(__file__).parent.parent / "patterns" / "data" / "checklists"
        self._loader = ChecklistLoader(checklist_dir)

    def __repr__(self) -> str:
        """Return a string representation of the method."""
        return f"BoundaryAnalysisMethod(method_id={self.method_id!r}, model='{self._model}', threshold={self._threshold})"

    async def analyze(
        self,
        artifact_text: str,
        **kwargs: dict[str, object],
    ) -> list[Finding]:
        """Analyze artifact against all boundary checklists in a single LLM call.

        Args:
            artifact_text: The text content to analyze for boundary issues.
            **kwargs: Additional context including:
                - domains: Optional list[ArtifactDomain] to select checklists
                - config: Optional DeepVerifyConfig (not used currently)

        Returns:
            List of Finding objects for violated checklist items with
            confidence >= threshold. Findings have temporary IDs "#154-F1",
            "#154-F2", etc. which will be reassigned by DeepVerifyEngine.

        """
        if not artifact_text or not artifact_text.strip():
            logger.debug("Empty artifact text, returning no findings")
            return []

        try:
            # Extract domains from kwargs for checklist selection
            domains: list[ArtifactDomain] | None = kwargs.get("domains")  # type: ignore[assignment]

            # Load appropriate checklists
            checklist_items = self._loader.load(domains)

            if not checklist_items:
                logger.warning("No checklists available for analysis")
                return []

            logger.debug(
                "Analyzing artifact against %d checklist items in single batch (domains=%s)",
                len(checklist_items),
                domains,
            )

            # Single batched LLM call with all checklist items
            responses = await asyncio.to_thread(
                self._analyze_all_items_sync, artifact_text, checklist_items
            )

            # Build lookup for checklist items by ID
            items_by_id = {item.id: item for item in checklist_items}

            # Collect findings from batch response
            findings: list[Finding] = []
            finding_idx = 0
            for resp in responses:
                if resp.violated and resp.confidence >= self._threshold:
                    item = items_by_id.get(resp.id)
                    if item is None:
                        logger.debug("LLM returned unknown checklist ID: %s", resp.id)
                        continue
                    finding_idx += 1
                    findings.append(self._create_finding(resp, item, finding_idx))

            logger.debug(
                "Boundary analysis found %d violations (threshold=%.2f)",
                len(findings),
                self._threshold,
            )

            return self._cap_severities(findings)

        except Exception as e:
            logger.warning("Boundary analysis failed: %s", e, exc_info=True)
            return []

    def _analyze_all_items_sync(
        self,
        artifact_text: str,
        items: list[ChecklistItem],
    ) -> list[ChecklistAnalysisResponse]:
        """Analyze all checklist items in a single batched LLM call.

        Args:
            artifact_text: The text to analyze.
            items: All checklist items to evaluate.

        Returns:
            List of ChecklistAnalysisResponse, one per checklist item.

        """
        prompt = self._build_batch_prompt(artifact_text, items)

        if self._llm_client:
            from bmad_assist.core.async_utils import run_async_in_thread

            result = run_async_in_thread(
                self._llm_client.invoke(
                    prompt=prompt,
                    model=self._model,
                    timeout=self._timeout,
                    method_id=str(self.method_id),
                )
            )
            raw_response = result.stdout
        else:
            assert self._provider is not None
            result = self._provider.invoke(
                prompt=prompt,
                model=self._model,
                timeout=self._timeout,
            )
            raw_response = self._provider.parse_output(result)

        return self._parse_batch_response(raw_response)

    def _build_batch_prompt(self, artifact_text: str, items: list[ChecklistItem]) -> str:
        """Build a single prompt containing all checklist items.

        Args:
            artifact_text: The text to analyze.
            items: All checklist items to evaluate.

        Returns:
            Formatted prompt string.

        """
        truncated = artifact_text[:MAX_ARTIFACT_LENGTH]

        checklist_section = []
        for i, item in enumerate(items, 1):
            checklist_section.append(
                f"{i}. ID: {item.id}\n"
                f"   Category: {item.category}\n"
                f"   Question: {item.question}\n"
                f"   Description: {item.description}"
            )

        return (
            f"{BOUNDARY_ANALYSIS_SYSTEM_PROMPT}\n\n"
            f"Artifact to analyze:\n```\n{truncated}\n```\n\n"
            f"Checklist items to evaluate ({len(items)} items):\n\n"
            + "\n\n".join(checklist_section)
            + "\n\nAnalyze the artifact against ALL checklist items above. "
            "Respond with a JSON array only."
        )

    def _parse_batch_response(self, raw_response: str) -> list[ChecklistAnalysisResponse]:
        """Parse batched LLM response containing array of checklist results.

        Handles:
        - JSON array inside markdown code blocks
        - Raw JSON arrays
        - Fallback: extract individual JSON objects

        Args:
            raw_response: Raw text response from LLM.

        Returns:
            List of ChecklistAnalysisResponse objects.

        """
        # Try markdown code block with array
        match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw_response, re.DOTALL)
        if match:
            json_str = match.group(1)
        else:
            # Find JSON array by matching brackets
            bracket_depth = 0
            start_idx = -1
            json_str = ""
            for i, char in enumerate(raw_response):
                if char == "[":
                    if bracket_depth == 0:
                        start_idx = i
                    bracket_depth += 1
                elif char == "]":
                    bracket_depth -= 1
                    if bracket_depth == 0 and start_idx >= 0:
                        json_str = raw_response[start_idx : i + 1]
                        break

            if not json_str:
                # Last resort: try to find individual JSON objects
                return self._parse_individual_objects(raw_response)

        data = json.loads(json_str)
        if not isinstance(data, list):
            raise ValueError("Expected JSON array in response")

        responses = []
        for entry in data:
            try:
                responses.append(ChecklistAnalysisResponse(**entry))
            except Exception as e:
                logger.debug("Skipping malformed batch entry: %s", e)
        return responses

    def _parse_individual_objects(self, raw_response: str) -> list[ChecklistAnalysisResponse]:
        """Fallback parser: extract individual JSON objects from response.

        Args:
            raw_response: Raw text that may contain multiple JSON objects.

        Returns:
            List of parsed responses.

        """
        responses = []
        brace_depth = 0
        start_idx = -1

        for i, char in enumerate(raw_response):
            if char == "{":
                if brace_depth == 0:
                    start_idx = i
                brace_depth += 1
            elif char == "}":
                brace_depth -= 1
                if brace_depth == 0 and start_idx >= 0:
                    json_str = raw_response[start_idx : i + 1]
                    try:
                        data = json.loads(json_str)
                        responses.append(ChecklistAnalysisResponse(**data))
                    except Exception:
                        pass
                    start_idx = -1

        if not responses:
            logger.warning("No valid JSON found in boundary analysis response")
        return responses

    def _create_finding(
        self,
        result: ChecklistAnalysisResponse,
        item: ChecklistItem,
        index: int,
    ) -> Finding:
        """Convert checklist analysis result to Finding.

        Args:
            result: The analysis result from LLM.
            item: The checklist item that was evaluated.
            index: The index for generating finding ID.

        Returns:
            Finding object with all relevant details.

        """
        finding_id = f"#154-F{index + 1}"

        # Truncate title to 80 chars
        title = item.question
        if len(title) > 80:
            title = title[:77] + "..."

        # Build description
        description_parts = [item.description]
        if result.explanation:
            description_parts.append(f"\nAnalysis: {result.explanation}")
        description_parts.append(f"\nChecklist: {item.id} ({item.category})")
        description = "".join(description_parts)

        # Create evidence
        evidence = []
        if result.evidence_quote:
            evidence.append(
                Evidence(
                    quote=result.evidence_quote,
                    line_number=result.line_number,
                    source=item.id,
                    confidence=result.confidence,
                )
            )

        # Map domain string to ArtifactDomain enum if applicable
        domain: ArtifactDomain | None = None
        if item.domain != "general":
            with contextlib.suppress(ValueError):
                domain = ArtifactDomain(item.domain)

        return Finding(
            id=finding_id,
            severity=item.severity,
            title=title,
            description=description,
            method_id=MethodId("#154"),
            pattern_id=PatternId(item.id),
            domain=domain,
            evidence=evidence,
        )

    # =========================================================================
    # Batch Interface
    # =========================================================================

    @property
    def supports_batch(self) -> bool:
        """Whether this method supports batch mode."""
        return True

    def get_method_prompt(self, **kwargs: object) -> str:
        """Return method's analysis instructions WITHOUT file content.

        Sent as Turn 1 of multi-turn batch session. Includes the system prompt
        and all checklist items so the LLM knows what to evaluate.

        Args:
            **kwargs: Additional context, may include 'domains' as
                      list[ArtifactDomain] to select domain-specific checklists.

        Returns:
            Method instruction prompt string with checklist items.

        """
        # Extract domains from kwargs for checklist selection
        domains: list[ArtifactDomain] | None = kwargs.get("domains")  # type: ignore[assignment]

        # Load appropriate checklists (same logic as analyze())
        checklist_items = self._loader.load(domains)

        # Build checklist section (same format as _build_batch_prompt)
        checklist_section = []
        for i, item in enumerate(checklist_items, 1):
            checklist_section.append(
                f"{i}. ID: {item.id}\n"
                f"   Category: {item.category}\n"
                f"   Question: {item.question}\n"
                f"   Description: {item.description}"
            )

        return (
            f"{BOUNDARY_ANALYSIS_SYSTEM_PROMPT}\n\n"
            f"Checklist items to evaluate ({len(checklist_items)} items):\n\n"
            + "\n\n".join(checklist_section)
            + "\n\nI will send files one at a time. For each file, analyze all "
            "checklist items and return the JSON array."
        )

    def parse_file_response(self, raw_response: str, file_path: str) -> list[Finding]:
        """Parse LLM response for a single file in batch mode.

        Reuses _parse_batch_response() for JSON extraction and _create_finding()
        for finding creation.

        Args:
            raw_response: Raw LLM response text for one file.
            file_path: Path to the file that was analyzed.

        Returns:
            List of Finding objects extracted from the response.

        """
        try:
            responses = self._parse_batch_response(raw_response)

            # Load checklist items to build lookup (use general only since
            # we don't have domain context per-file in batch mode)
            all_items = self._loader.load()
            items_by_id = {item.id: item for item in all_items}

            findings: list[Finding] = []
            finding_idx = 0
            for resp in responses:
                if resp.violated and resp.confidence >= self._threshold:
                    item = items_by_id.get(resp.id)
                    if item is None:
                        logger.debug("LLM returned unknown checklist ID: %s", resp.id)
                        continue
                    finding_idx += 1
                    findings.append(self._create_finding(resp, item, finding_idx))

            return self._cap_severities(findings)

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.debug("Failed to parse batch file response for %s: %s", file_path, e)
            return []
