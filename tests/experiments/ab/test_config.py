"""Tests for A/B test configuration model.

Tests cover:
- ABVariantConfig and ABTestConfig model validation
- YAML loading via load_ab_test_config()
- Story ID format validation
- Phase name validation
- Variant label uniqueness
- File/parsing error handling
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from bmad_assist.core.exceptions import ConfigError
from bmad_assist.experiments.ab.config import (
    ABTestConfig,
    ABVariantConfig,
    StoryRef,
    load_ab_test_config,
)

VALID_YAML = """\
name: prompt-v2-test
fixture: minimal
stories:
  - id: "3.1"
    ref: abc1234
  - id: "3.2"
    ref: def5678
phases:
  - create-story
  - dev-story
variant_a:
  label: baseline
  config: opus-solo
  patch_set: baseline
variant_b:
  label: prompt-v2
  config: opus-solo
  patch_set: prompt-v2
scorecard: false
"""


class TestABVariantConfig:
    """Tests for ABVariantConfig model."""

    def test_valid_variant(self) -> None:
        """Create a valid variant config."""
        v = ABVariantConfig(label="baseline", config="opus-solo", patch_set="baseline")
        assert v.label == "baseline"
        assert v.config == "opus-solo"
        assert v.patch_set == "baseline"

    def test_frozen(self) -> None:
        """Frozen model rejects attribute mutation."""
        v = ABVariantConfig(label="x", config="c", patch_set="p")
        with pytest.raises(ValidationError):
            v.label = "y"  # type: ignore[misc]

    def test_empty_label_rejected(self) -> None:
        """Empty label violates min_length."""
        with pytest.raises(ValidationError):
            ABVariantConfig(label="", config="c", patch_set="p")

    def test_empty_config_rejected(self) -> None:
        """Empty config violates min_length."""
        with pytest.raises(ValidationError):
            ABVariantConfig(label="x", config="", patch_set="p")

    def test_empty_patch_set_rejected(self) -> None:
        """Empty patch_set violates min_length."""
        with pytest.raises(ValidationError):
            ABVariantConfig(label="x", config="c", patch_set="")

    def test_customize_set_defaults_none(self) -> None:
        """customize_set defaults to None when omitted."""
        v = ABVariantConfig(label="x", config="c", patch_set="p")
        assert v.customize_set is None

    def test_template_set_defaults_none(self) -> None:
        """template_set defaults to None when omitted."""
        v = ABVariantConfig(label="x", config="c", patch_set="p")
        assert v.template_set is None

    def test_customize_set_explicit(self) -> None:
        """customize_set can be set explicitly."""
        v = ABVariantConfig(label="x", config="c", patch_set="p", customize_set="agents-team-frame")
        assert v.customize_set == "agents-team-frame"

    def test_customize_set_accepts_none(self) -> None:
        """customize_set accepts explicit None."""
        v = ABVariantConfig(label="x", config="c", patch_set="p", customize_set=None)
        assert v.customize_set is None

    def test_template_set_explicit(self) -> None:
        """template_set can be set explicitly."""
        v = ABVariantConfig(label="x", config="c", patch_set="p", template_set="optimized-v1")
        assert v.template_set == "optimized-v1"

    def test_both_sets_explicit(self) -> None:
        """Both customize_set and template_set can be set together."""
        v = ABVariantConfig(
            label="x",
            config="c",
            patch_set="p",
            customize_set="cs",
            template_set="tpl",
        )
        assert v.customize_set == "cs"
        assert v.template_set == "tpl"

    def test_frozen_rejects_customize_set_mutation(self) -> None:
        """Frozen model rejects customize_set mutation."""
        v = ABVariantConfig(label="x", config="c", patch_set="p", customize_set="cs")
        with pytest.raises(ValidationError):
            v.customize_set = "other"  # type: ignore[misc]

    def test_frozen_rejects_template_set_mutation(self) -> None:
        """Frozen model rejects template_set mutation."""
        v = ABVariantConfig(label="x", config="c", patch_set="p", template_set="tpl")
        with pytest.raises(ValidationError):
            v.template_set = "other"  # type: ignore[misc]


class TestABTestConfig:
    """Tests for ABTestConfig model."""

    def test_valid_config(self) -> None:
        """Create a valid test config with all required fields."""
        cfg = ABTestConfig(
            name="test",
            fixture="minimal",
            stories=[StoryRef(id="3.1", ref="abc"), StoryRef(id="3.2", ref="def")],
            phases=["create-story"],
            variant_a=ABVariantConfig(label="a", config="c1", patch_set="p1"),
            variant_b=ABVariantConfig(label="b", config="c2", patch_set="p2"),
        )
        assert cfg.name == "test"
        assert len(cfg.stories) == 2
        assert cfg.scorecard is False

    def test_scorecard_defaults_false(self) -> None:
        """Scorecard defaults to False when omitted."""
        cfg = ABTestConfig(
            name="t",
            fixture="f",
            stories=[StoryRef(id="1.1", ref="abc")],
            phases=["create-story"],
            variant_a=ABVariantConfig(label="a", config="c", patch_set="p"),
            variant_b=ABVariantConfig(label="b", config="c", patch_set="p"),
        )
        assert cfg.scorecard is False

    def test_scorecard_true(self) -> None:
        """Scorecard can be set to True."""
        cfg = ABTestConfig(
            name="t",
            fixture="f",
            stories=[StoryRef(id="1.1", ref="abc")],
            phases=["create-story"],
            variant_a=ABVariantConfig(label="a", config="c", patch_set="p"),
            variant_b=ABVariantConfig(label="b", config="c", patch_set="p"),
            scorecard=True,
        )
        assert cfg.scorecard is True

    def test_frozen(self) -> None:
        """Frozen model rejects attribute mutation."""
        cfg = ABTestConfig(
            name="t",
            fixture="f",
            stories=[StoryRef(id="1.1", ref="abc")],
            phases=["create-story"],
            variant_a=ABVariantConfig(label="a", config="c", patch_set="p"),
            variant_b=ABVariantConfig(label="b", config="c", patch_set="p"),
        )
        with pytest.raises(ValidationError):
            cfg.name = "new"  # type: ignore[misc]


class TestStoryValidation:
    """Tests for story ID format validation."""

    def test_valid_story_ids(self) -> None:
        """Story IDs with dots are accepted."""
        ABTestConfig(
            name="t",
            fixture="f",
            stories=[
                StoryRef(id="3.1", ref="a"),
                StoryRef(id="3.2", ref="b"),
                StoryRef(id="10.5", ref="c"),
            ],
            phases=["create-story"],
            variant_a=ABVariantConfig(label="a", config="c", patch_set="p"),
            variant_b=ABVariantConfig(label="b", config="c", patch_set="p"),
        )

    def test_story_without_dot_rejected(self) -> None:
        """Story ID without dot fails epic.story validation."""
        with pytest.raises(ValidationError, match="epic.story"):
            ABTestConfig(
                name="t",
                fixture="f",
                stories=[StoryRef(id="31", ref="abc")],
                phases=["create-story"],
                variant_a=ABVariantConfig(label="a", config="c", patch_set="p"),
                variant_b=ABVariantConfig(label="b", config="c", patch_set="p"),
            )

    def test_mixed_valid_invalid_stories(self) -> None:
        """One bad story ID in the list fails validation."""
        with pytest.raises(ValidationError, match="epic.story"):
            ABTestConfig(
                name="t",
                fixture="f",
                stories=[StoryRef(id="3.1", ref="a"), StoryRef(id="bad", ref="b")],
                phases=["create-story"],
                variant_a=ABVariantConfig(label="a", config="c", patch_set="p"),
                variant_b=ABVariantConfig(label="b", config="c", patch_set="p"),
            )

    def test_empty_stories_rejected(self) -> None:
        """Empty stories list violates min_length."""
        with pytest.raises(ValidationError):
            ABTestConfig(
                name="t",
                fixture="f",
                stories=[],
                phases=["create-story"],
                variant_a=ABVariantConfig(label="a", config="c", patch_set="p"),
                variant_b=ABVariantConfig(label="b", config="c", patch_set="p"),
            )


class TestPhaseValidation:
    """Tests for phase name validation."""

    def test_valid_kebab_phases(self) -> None:
        """Kebab-case phase names are accepted."""
        ABTestConfig(
            name="t",
            fixture="f",
            stories=[StoryRef(id="1.1", ref="abc")],
            phases=["create-story", "dev-story", "code-review"],
            variant_a=ABVariantConfig(label="a", config="c", patch_set="p"),
            variant_b=ABVariantConfig(label="b", config="c", patch_set="p"),
        )

    def test_valid_snake_case_phases(self) -> None:
        """Snake_case phases are accepted (normalized to kebab)."""
        ABTestConfig(
            name="t",
            fixture="f",
            stories=[StoryRef(id="1.1", ref="abc")],
            phases=["create_story", "dev_story"],
            variant_a=ABVariantConfig(label="a", config="c", patch_set="p"),
            variant_b=ABVariantConfig(label="b", config="c", patch_set="p"),
        )

    def test_unknown_phase_rejected(self) -> None:
        """Unknown phase name fails validation."""
        with pytest.raises(ValidationError, match="Unknown phase"):
            ABTestConfig(
                name="t",
                fixture="f",
                stories=[StoryRef(id="1.1", ref="abc")],
                phases=["nonexistent-phase"],
                variant_a=ABVariantConfig(label="a", config="c", patch_set="p"),
                variant_b=ABVariantConfig(label="b", config="c", patch_set="p"),
            )

    def test_empty_phases_rejected(self) -> None:
        """Empty phases list violates min_length."""
        with pytest.raises(ValidationError):
            ABTestConfig(
                name="t",
                fixture="f",
                stories=[StoryRef(id="1.1", ref="abc")],
                phases=[],
                variant_a=ABVariantConfig(label="a", config="c", patch_set="p"),
                variant_b=ABVariantConfig(label="b", config="c", patch_set="p"),
            )


class TestVariantLabelValidation:
    """Tests for variant label uniqueness."""

    def test_different_labels_accepted(self) -> None:
        """Distinct variant labels pass validation."""
        ABTestConfig(
            name="t",
            fixture="f",
            stories=[StoryRef(id="1.1", ref="abc")],
            phases=["create-story"],
            variant_a=ABVariantConfig(label="baseline", config="c", patch_set="p"),
            variant_b=ABVariantConfig(label="experimental", config="c", patch_set="p"),
        )

    def test_same_labels_rejected(self) -> None:
        """Identical variant labels fail model validation."""
        with pytest.raises(ValidationError, match="distinct"):
            ABTestConfig(
                name="t",
                fixture="f",
                stories=[StoryRef(id="1.1", ref="abc")],
                phases=["create-story"],
                variant_a=ABVariantConfig(label="same", config="c1", patch_set="p1"),
                variant_b=ABVariantConfig(label="same", config="c2", patch_set="p2"),
            )


class TestLoadABTestConfig:
    """Tests for YAML loading."""

    def test_load_valid_yaml(self, tmp_path: Path) -> None:
        """Load and parse a valid YAML test definition."""
        path = tmp_path / "test.yaml"
        path.write_text(VALID_YAML)
        cfg = load_ab_test_config(path)
        assert cfg.name == "prompt-v2-test"
        assert cfg.fixture == "minimal"
        assert cfg.story_ids == ["3.1", "3.2"]
        assert cfg.phases == ["create-story", "dev-story"]
        assert cfg.variant_a.label == "baseline"
        assert cfg.variant_b.label == "prompt-v2"

    def test_file_not_found(self, tmp_path: Path) -> None:
        """Nonexistent file raises ConfigError."""
        with pytest.raises(ConfigError, match="not found"):
            load_ab_test_config(tmp_path / "nope.yaml")

    def test_not_a_file(self, tmp_path: Path) -> None:
        """Directory path raises ConfigError."""
        with pytest.raises(ConfigError, match="not a file"):
            load_ab_test_config(tmp_path)

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        """Malformed YAML raises ConfigError."""
        path = tmp_path / "bad.yaml"
        path.write_text(":\n  :\n  :::bad")
        with pytest.raises(ConfigError, match="Invalid YAML"):
            load_ab_test_config(path)

    def test_yaml_not_mapping(self, tmp_path: Path) -> None:
        """YAML list instead of mapping raises ConfigError."""
        path = tmp_path / "list.yaml"
        path.write_text("- item1\n- item2\n")
        with pytest.raises(ConfigError, match="mapping"):
            load_ab_test_config(path)

    def test_missing_required_fields(self, tmp_path: Path) -> None:
        """Incomplete YAML fails pydantic validation."""
        path = tmp_path / "missing.yaml"
        path.write_text("name: test\n")
        with pytest.raises(ConfigError, match="validation failed"):
            load_ab_test_config(path)

    def test_oversized_file(self, tmp_path: Path) -> None:
        """File exceeding 1MB limit raises ConfigError."""
        path = tmp_path / "huge.yaml"
        path.write_text("x" * (1024 * 1024 + 2))
        with pytest.raises(ConfigError, match="1MB"):
            load_ab_test_config(path)
