"""Configuration for Deep Verify module.

This module provides Pydantic configuration models for the Deep Verify
module, including method enable/disable flags and threshold overrides.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bmad_assist.deep_verify.core.types import Severity


class DeepVerifyProviderConfig(BaseModel):
    """Provider configuration for Deep Verify LLM calls.

    If specified in deep_verify.provider, overrides the global helper provider.
    Supports full provider configuration including thinking mode.

    Attributes:
        provider: Provider name (e.g., "claude-subprocess", "claude-sdk", "gemini").
        model: Model identifier for CLI invocation (e.g., "haiku", "sonnet").
        model_name: Display name for the model (e.g., "glm-4.5"). If set,
            used in logs/reports instead of model.
        settings: Optional path to provider settings JSON file (tilde expanded).
        thinking: Enable thinking mode for supported providers (e.g., kimi).

    Example:
        >>> config = DeepVerifyProviderConfig(
        ...     provider="claude-subprocess",
        ...     model="haiku",
        ...     thinking=False,
        ... )

    """

    model_config = ConfigDict(frozen=True)

    provider: str = Field(
        ...,
        description="Provider name: claude-subprocess, claude-sdk, gemini, kimi, etc.",
    )
    model: str = Field(
        ...,
        description="Model identifier: haiku, sonnet, gemini-2.5-flash, etc.",
    )
    model_name: str | None = Field(
        None,
        description="Display name for model (used in logs/reports instead of model)",
    )
    settings: str | None = Field(
        None,
        description="Path to provider settings JSON (tilde expanded)",
    )
    thinking: bool = Field(
        False,
        description="Enable thinking mode for supported providers (kimi)",
    )

    @property
    def display_model(self) -> str:
        """Return model name for display (model_name if set, else model)."""
        return self.model_name or self.model

    @property
    def settings_path(self) -> Path | None:
        """Return expanded settings path, or None if not set."""
        if self.settings is None:
            return None
        return Path(self.settings).expanduser()


class DeepVerifyContextConfig(BaseModel):
    """Configuration for additional context documents in DV analysis.

    Controls which strategic documents (PRD, Architecture, project-context)
    are included alongside the story file in Deep Verify analysis.

    By default, only the story file is analyzed. Users can optionally
    include additional documents for cross-referencing.

    Attributes:
        include_prd: Include PRD in DV analysis.
        include_architecture: Include architecture doc in DV analysis.
        include_project_context: Include project-context.md in DV analysis.
        max_context_size: Max combined size of context documents in bytes.

    Example:
        >>> config = DeepVerifyContextConfig(
        ...     include_prd=True,
        ...     include_architecture=True,
        ...     max_context_size=51200,
        ... )

    """

    model_config = ConfigDict(frozen=True)

    include_prd: bool = Field(
        default=False,
        description="Include PRD in DV analysis",
    )
    include_architecture: bool = Field(
        default=False,
        description="Include architecture doc in DV analysis",
    )
    include_project_context: bool = Field(
        default=False,
        description="Include project-context.md in DV analysis",
    )
    max_context_size: int = Field(
        default=51200,  # 50KB default
        ge=1024,
        le=524288,  # 512KB max allowed
        description="Max combined size of context documents in bytes (default 50KB, max 512KB)",
    )
    file_context_budget: int = Field(
        default=4000,
        ge=2000,
        le=20000,
        description="Max characters per file for intelligent context extraction (default 4000, min 2000)",
    )


class ResourceLimitConfig(BaseModel):
    """Configuration for resource limits and error handling.

    This configuration controls input validation, finding limits,
    and timeout settings to prevent OOM and system instability.

    Example:
        >>> config = ResourceLimitConfig(
        ...     max_artifact_size_bytes=102400,
        ...     max_line_count=5000,
        ...     max_findings_per_method=50,
        ...     max_total_findings=200,
        ...     regex_timeout_seconds=5.0,
        ... )

    """

    model_config = ConfigDict(frozen=True)

    max_artifact_size_bytes: int = Field(
        default=102400,
        ge=1024,
        le=10485760,
        description="Maximum artifact size in bytes (default: 100KB)",
    )
    max_line_count: int = Field(
        default=5000,
        ge=10,
        le=100000,
        description="Maximum number of lines in artifact",
    )
    max_findings_per_method: int = Field(
        default=50,
        ge=1,
        le=1000,
        description="Maximum findings per method before truncation",
    )
    max_total_findings: int = Field(
        default=200,
        ge=10,
        le=5000,
        description="Maximum total findings before truncation",
    )
    regex_timeout_seconds: float = Field(
        default=5.0,
        ge=0.5,
        le=60.0,
        description="Timeout for regex pattern matching in seconds",
    )


class LLMConfig(BaseModel):
    """Configuration for LLM infrastructure.

    This configuration controls retry logic, rate limiting, cost tracking,
    and timeout handling for LLM calls.

    Example:
        >>> config = LLMConfig(
        ...     max_retries=3,
        ...     tokens_per_minute_limit=100000,
        ...     cost_tracking_enabled=True,
        ... )

    """

    model_config = ConfigDict(frozen=True)

    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum number of retry attempts for failed LLM calls",
    )
    base_delay_seconds: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        description="Initial delay between retries (seconds)",
    )
    max_delay_seconds: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        description="Maximum delay between retries (cap)",
    )
    tokens_per_minute_limit: int = Field(
        default=100000,
        ge=1000,
        le=1000000,
        description="Maximum tokens per minute (rate limiting)",
    )
    cost_tracking_enabled: bool = Field(
        default=True,
        description="Enable cost tracking for LLM calls",
    )
    log_all_calls: bool = Field(
        default=True,
        description="Log all LLM calls with timing and token usage",
    )
    method_stagger_seconds: float = Field(
        default=0.5,
        ge=0.0,
        le=30.0,
        description="Delay between spawning consecutive verification methods (seconds). "
        "Applied with ±20% jitter to avoid thundering herd.",
    )
    default_timeout_seconds: int = Field(
        default=30,
        ge=5,
        le=300,
        description="Default timeout for LLM calls (seconds)",
    )
    total_timeout_seconds: int = Field(
        default=90,
        ge=10,
        le=600,
        description="Total timeout for entire verification (seconds)",
    )


class MethodConfig(BaseModel):
    """Configuration for a single verification method.

    Attributes:
        enabled: Whether this method is enabled.
        timeout_seconds: Timeout for this method (None = use global).

    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    timeout_seconds: int | None = Field(
        default=None,
        description="Method-specific timeout in seconds (None = use global)",
    )


class DeepVerifyConfig(BaseModel):
    """Configuration for the Deep Verify module.

    This configuration controls:
    - Master enable/disable switch
    - Method enable/disable flags for all 8 methods
    - Threshold overrides for scoring
    - Severity weight overrides

    Example:
        >>> config = DeepVerifyConfig(
        ...     enabled=True,
        ...     method_153_pattern_match=MethodConfig(enabled=True),
        ...     reject_threshold=12.0,
        ... )

    """

    model_config = ConfigDict(frozen=True)

    # Master enable/disable switch
    enabled: bool = Field(
        default=True,
        description="Master enable/disable switch for Deep Verify module",
    )

    # Provider override (optional) - if not set, uses global helper provider
    provider: DeepVerifyProviderConfig | None = Field(
        default=None,
        description="Provider override for Deep Verify LLM calls. If None, uses global helper.",
    )

    # Threshold overrides
    # NOTE: reject_threshold raised from 6.0 → 12.0 (Deep Verify P3) to avoid
    # tripping REJECT on any 2 CRITICAL findings (4.0 each). With noise
    # filtering (P2: GEN-*/BOUNDARY-* exclusion) and language-aware pattern
    # matching (P1) in place, most files have 1–2 legitimate CRITICAL findings;
    # 12.0 keeps REJECT meaningful while UNCERTAIN catches borderline cases.
    reject_threshold: float = Field(
        default=12.0,
        description="Score threshold for REJECT verdict",
        ge=-100.0,
        le=100.0,
    )
    uncertain_low: float = Field(
        default=-3.0,
        description="Lower bound of UNCERTAIN range",
        ge=-100.0,
        le=100.0,
    )
    uncertain_high: float = Field(
        default=6.0,
        description="Upper bound of UNCERTAIN range",
        ge=-100.0,
        le=100.0,
    )
    accept_threshold: float = Field(
        default=-3.0,
        description="Score threshold for ACCEPT verdict",
        ge=-100.0,
        le=100.0,
    )

    # CRITICAL hard-block threshold (D.8). Default 2 lets single
    # non-excluded CRITICALs fall through to the score-based path (P3
    # raised REJECT_THRESHOLD to 12.0 for exactly this case). Set to 1
    # to restore the legacy single-CRITICAL hard-block behavior.
    critical_count_threshold: int = Field(
        default=2,
        ge=1,
        le=20,
        description=(
            "Number of non-excluded CRITICAL findings required to force REJECT. "
            "Default 2 lets single CRITICALs fall through to score-based path (P3)."
        ),
    )

    # Severity weight overrides
    critical_weight: float = Field(
        default=4.0,
        description="Weight for CRITICAL severity findings",
        ge=0.0,
        le=100.0,
    )
    error_weight: float = Field(
        default=2.0,
        description="Weight for ERROR severity findings",
        ge=0.0,
        le=100.0,
    )
    warning_weight: float = Field(
        default=1.0,
        description="Weight for WARNING severity findings",
        ge=0.0,
        le=100.0,
    )
    info_weight: float = Field(
        default=0.5,
        description="Weight for INFO severity findings",
        ge=0.0,
        le=100.0,
    )

    # LLM infrastructure configuration
    llm_config: LLMConfig = Field(
        default_factory=LLMConfig,
        description="LLM infrastructure configuration (retry, rate limiting, cost tracking)",
    )

    # Resource limits and error handling configuration
    resource_limits: ResourceLimitConfig = Field(
        default_factory=ResourceLimitConfig,
        description="Resource limit configuration (input validation, finding limits, timeouts)",
    )

    # Method enable/disable flags for all 8 methods
    # Always-run methods
    method_153_pattern_match: MethodConfig = Field(
        default_factory=lambda: MethodConfig(enabled=True),
        description="#153 Pattern Match method (always run)",
    )
    method_154_boundary_analysis: MethodConfig = Field(
        default_factory=lambda: MethodConfig(enabled=True),
        description="#154 Boundary Analysis method (always run)",
    )

    # Conditional methods
    method_155_assumption_surfacing: MethodConfig = Field(
        default_factory=lambda: MethodConfig(enabled=True),
        description="#155 Assumption Surfacing method (CONCURRENCY, API domains)",
    )
    method_157_temporal_consistency: MethodConfig = Field(
        default_factory=lambda: MethodConfig(enabled=True),
        description="#157 Temporal Consistency method (MESSAGING, STORAGE domains)",
    )
    method_201_adversarial_review: MethodConfig = Field(
        default_factory=lambda: MethodConfig(enabled=True),
        description="#201 Adversarial Review method (SECURITY, API domains)",
    )
    method_203_domain_expert: MethodConfig = Field(
        default_factory=lambda: MethodConfig(enabled=True),
        description="#203 Domain Expert Simulation method (all domains)",
    )
    method_204_integration_analysis: MethodConfig = Field(
        default_factory=lambda: MethodConfig(enabled=True),
        description="#204 Integration Analysis method (API, MESSAGING domains)",
    )
    method_205_worst_case: MethodConfig = Field(
        default_factory=lambda: MethodConfig(enabled=True),
        description="#205 Worst-Case Construction method (CONCURRENCY, MESSAGING domains)",
    )

    # Clean pass bonus override
    clean_pass_bonus: float = Field(
        default=-0.5,
        description="Bonus per domain with zero findings",
        ge=-10.0,
        le=10.0,
    )

    # Context configuration for validate_story phase
    context: DeepVerifyContextConfig = Field(
        default_factory=DeepVerifyContextConfig,
        description="Configuration for additional context documents in DV analysis",
    )

    @model_validator(mode="after")
    def validate_thresholds(self) -> DeepVerifyConfig:
        """Validate that thresholds are in correct order.

        Required: reject_threshold >= uncertain_high >= uncertain_low >= accept_threshold

        Raises:
            ValueError: If thresholds are not in valid order.

        """
        if self.reject_threshold < self.uncertain_high:
            raise ValueError(
                f"reject_threshold ({self.reject_threshold}) must be >= "
                f"uncertain_high ({self.uncertain_high})"
            )
        if self.uncertain_high < self.uncertain_low:
            raise ValueError(
                f"uncertain_high ({self.uncertain_high}) must be >= "
                f"uncertain_low ({self.uncertain_low})"
            )
        if self.uncertain_low < self.accept_threshold:
            raise ValueError(
                f"uncertain_low ({self.uncertain_low}) must be >= "
                f"accept_threshold ({self.accept_threshold})"
            )
        return self

    def get_enabled_methods(self) -> list[str]:
        """Get list of enabled method IDs.

        Returns:
            List of method IDs (e.g., "#153", "#154") that are enabled.

        """
        methods = []
        method_map = {
            "#153": self.method_153_pattern_match,
            "#154": self.method_154_boundary_analysis,
            "#155": self.method_155_assumption_surfacing,
            "#157": self.method_157_temporal_consistency,
            "#201": self.method_201_adversarial_review,
            "#203": self.method_203_domain_expert,
            "#204": self.method_204_integration_analysis,
            "#205": self.method_205_worst_case,
        }
        for method_id, config in method_map.items():
            if config.enabled:
                methods.append(method_id)
        return methods

    def get_severity_weights(self) -> dict[Severity, float]:
        """Get severity weights as a dictionary.

        Returns:
            Mapping of Severity to weight values.

        """
        return {
            Severity.CRITICAL: self.critical_weight,
            Severity.ERROR: self.error_weight,
            Severity.WARNING: self.warning_weight,
            Severity.INFO: self.info_weight,
        }
