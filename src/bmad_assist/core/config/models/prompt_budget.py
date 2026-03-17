"""Prompt budget configuration for final compiled prompt caps.

Separate from source-context budgets (which cap source-file collection within
SourceContextService). Prompt budget caps apply to the final compiled prompt
after all context sections are assembled.
"""

from pydantic import BaseModel, ConfigDict, Field


class PromptBudgetConfig(BaseModel):
    """Final compiled prompt budget caps.

    These caps are applied after all context sections (strategic, TEA, source,
    git diff, story) are assembled. When the total exceeds the cap, the enforcer
    trims strategic and TEA sections first before logging a warning.

    Distinct from ``SourceContextBudgetsConfig`` which caps source-file
    collection within ``SourceContextService``.

    Attributes:
        enabled: Whether prompt budget enforcement is active.
        default_cap: Default total prompt token cap (0 = disabled).
        workflow_caps: Per-workflow overrides for the default cap.

    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(
        default=True,
        description="Whether prompt budget enforcement is active",
        json_schema_extra={"security": "safe", "ui_widget": "checkbox"},
    )
    default_cap: int = Field(
        default=80000,
        ge=0,
        description="Default total prompt token cap (0 = disabled)",
        json_schema_extra={"security": "safe", "ui_widget": "number"},
    )
    workflow_caps: dict[str, int] = Field(
        default_factory=lambda: {
            "code_review": 50000,
            "code_review_synthesis": 60000,
            "validate_story_synthesis": 40000,
        },
        description="Per-workflow total prompt token caps (overrides default_cap)",
        json_schema_extra={"security": "safe"},
    )

    def get_cap(self, workflow_name: str) -> int:
        """Get the prompt budget cap for a workflow.

        Args:
            workflow_name: Name of the workflow (e.g., 'code_review').

        Returns:
            Token cap for the workflow, or default_cap if not overridden.
            Returns 0 if enforcement is disabled.

        """
        if not self.enabled:
            return 0
        normalized = workflow_name.replace("-", "_")
        return self.workflow_caps.get(normalized, self.default_cap)
