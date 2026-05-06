"""A/B test configuration model.

Parses and validates YAML test definitions for A/B workflow testing.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bmad_assist.core.config.constants import MAX_CONFIG_SIZE
from bmad_assist.core.exceptions import ConfigError

logger = logging.getLogger(__name__)


class ABVariantConfig(BaseModel):
    """Configuration for a single A/B test variant."""

    model_config = ConfigDict(frozen=True)

    label: str = Field(..., min_length=1)
    config: str = Field(..., min_length=1)
    patch_set: str = Field(..., min_length=1)
    customize_set: str | None = Field(default=None)
    template_set: str | None = Field(default=None)


class StoryRef(BaseModel):
    """A story with its git ref for A/B testing.

    Each story pins to a specific commit so code reviews run against
    the original implementation, not post-review refactored code.

    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., min_length=1)
    ref: str = Field(..., min_length=1)

    @field_validator("id", mode="after")
    @classmethod
    def validate_story_id(cls, v: str) -> str:
        """Validate story ID has epic.story format."""
        if "." not in str(v):
            raise ValueError(f"Invalid story ID '{v}': must be 'epic.story' format (e.g., '3.1')")
        return str(v)


class ABTestConfig(BaseModel):
    """A/B test definition loaded from YAML.

    Attributes:
        name: Test name (used in result directory naming).
        fixture: Fixture ID from experiments/fixtures/.
        stories: List of stories with per-story git refs.
        phases: Ordered list of phase names to execute per story.
        variant_a: Configuration for variant A.
        variant_b: Configuration for variant B.
        scorecard: Whether to run scorecard after completion.

    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1)
    fixture: str = Field(..., min_length=1)
    stories: list[StoryRef] = Field(..., min_length=1)
    phases: list[str] = Field(..., min_length=1)
    variant_a: ABVariantConfig
    variant_b: ABVariantConfig
    scorecard: bool = Field(default=False)
    analysis: bool = Field(default=False)

    @field_validator("phases", mode="after")
    @classmethod
    def validate_phases(cls, v: list[str]) -> list[str]:
        """Validate phase names are recognized."""
        from bmad_assist.experiments.runner import WORKFLOW_TO_PHASE

        for phase in v:
            normalized = phase.replace("_", "-")
            if normalized not in WORKFLOW_TO_PHASE and phase not in WORKFLOW_TO_PHASE:
                try:
                    from bmad_assist.core.state import Phase

                    Phase(phase)
                except ValueError:
                    raise ValueError(f"Unknown phase '{phase}'") from None
        return v

    @model_validator(mode="after")
    def validate_variant_labels_differ(self) -> ABTestConfig:
        """Ensure variant labels are distinct."""
        if self.variant_a.label == self.variant_b.label:
            raise ValueError(f"Variant labels must be distinct, both are '{self.variant_a.label}'")
        return self

    @property
    def story_ids(self) -> list[str]:
        """Return list of story IDs for backward-compatible access."""
        return [s.id for s in self.stories]

    @property
    def story_refs(self) -> dict[str, str]:
        """Return story_id → ref mapping."""
        return {s.id: s.ref for s in self.stories}


def load_ab_test_config(path: Path) -> ABTestConfig:
    """Load and validate an A/B test definition from YAML file.

    Args:
        path: Path to the YAML test definition.

    Returns:
        Validated ABTestConfig.

    Raises:
        ConfigError: On file/parse/validation errors.

    """
    if not path.exists():
        raise ConfigError(f"A/B test definition not found: {path}")
    if not path.is_file():
        raise ConfigError(f"A/B test definition path is not a file: {path}")

    try:
        with path.open("r", encoding="utf-8") as f:
            content = f.read(MAX_CONFIG_SIZE + 1)
    except OSError as e:
        raise ConfigError(f"Cannot read A/B test definition {path}: {e}") from e

    if len(content) > MAX_CONFIG_SIZE:
        raise ConfigError(f"A/B test definition {path} exceeds 1MB limit")

    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {path}: {e}") from e

    if not isinstance(data, dict):
        raise ConfigError(f"A/B test definition must be YAML mapping, got {type(data).__name__}")

    try:
        return ABTestConfig.model_validate(data)
    except Exception as e:
        raise ConfigError(f"A/B test validation failed for {path}: {e}") from e
