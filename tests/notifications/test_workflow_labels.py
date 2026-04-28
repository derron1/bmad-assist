"""Tests for workflow notification label resolution system.

Tests cover:
- AC1: Explicit label from workflow YAML
- AC2: Explicit icon from workflow YAML
- AC3: Pattern-based default icons
- AC4: Fallback label (name truncation)
- AC5: Module prefix handling
- AC6: Label cache at startup
- AC7: Thread safety for cache
"""

import threading
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from bmad_assist.notifications.workflow_labels import (
    DEFAULT_ICON,
    ICON_PATTERNS,
    MAX_LABEL_LENGTH,
    PREDEFINED_LABELS,
    WorkflowNotificationConfig,
    _compute_config,
    _find_workflow_yaml_paths,
    _load_workflow_notification_config,
    _match_icon_pattern,
    _smart_truncate_label,
    _strip_module_prefix,
    clear_workflow_label_cache,
    get_workflow_icon,
    get_workflow_label,
    get_workflow_notification_config,
)


class TestConstants:
    """Test module constants."""

    def test_max_label_length(self) -> None:
        """Test MAX_LABEL_LENGTH is 16."""
        assert MAX_LABEL_LENGTH == 16

    def test_default_icon(self) -> None:
        """Test DEFAULT_ICON is clipboard for unknown workflows."""
        assert DEFAULT_ICON == "📋"

    def test_icon_patterns_order(self) -> None:
        """Test icon patterns are in correct order (synth before dev)."""
        pattern_names = [p[0] for p in ICON_PATTERNS]
        synth_idx = pattern_names.index("synth")
        dev_idx = pattern_names.index("dev")
        assert synth_idx < dev_idx, "synth must be before dev to match *synthesis*"

    def test_icon_patterns_completeness(self) -> None:
        """Test all expected patterns are present."""
        expected = {"create", "valid", "synth", "dev", "review", "test", "plan", "retro"}
        actual = {p[0] for p in ICON_PATTERNS}
        assert expected == actual


class TestPredefinedLabels:
    """Test predefined labels registry (AC1, AC2)."""

    def test_create_story_predefined(self) -> None:
        """Test create-story has predefined config."""
        assert "create-story" in PREDEFINED_LABELS
        config = PREDEFINED_LABELS["create-story"]
        assert config.icon == "📝"
        assert config.label == "Create"

    def test_validate_story_predefined(self) -> None:
        """Test validate-story has predefined config."""
        assert "validate-story" in PREDEFINED_LABELS
        config = PREDEFINED_LABELS["validate-story"]
        assert config.icon == "🔍"
        assert config.label == "Validate"

    def test_validate_story_synthesis_predefined(self) -> None:
        """Test validate-story-synthesis has predefined config."""
        assert "validate-story-synthesis" in PREDEFINED_LABELS
        config = PREDEFINED_LABELS["validate-story-synthesis"]
        assert config.icon == "🔍"
        assert config.label == "Val-Synth"

    def test_dev_story_predefined(self) -> None:
        """Test dev-story has predefined config."""
        assert "dev-story" in PREDEFINED_LABELS
        config = PREDEFINED_LABELS["dev-story"]
        assert config.icon == "💻"
        assert config.label == "Develop"

    def test_code_review_predefined(self) -> None:
        """Test code-review has predefined config."""
        assert "code-review" in PREDEFINED_LABELS
        config = PREDEFINED_LABELS["code-review"]
        assert config.icon == "👀"
        assert config.label == "Review"

    def test_code_review_synthesis_predefined(self) -> None:
        """Test code-review-synthesis has predefined config."""
        assert "code-review-synthesis" in PREDEFINED_LABELS
        config = PREDEFINED_LABELS["code-review-synthesis"]
        assert config.icon == "👀"
        assert config.label == "Rev-Synth"

    def test_retrospective_predefined(self) -> None:
        """Test retrospective has predefined config."""
        assert "retrospective" in PREDEFINED_LABELS
        config = PREDEFINED_LABELS["retrospective"]
        assert config.icon == "📊"
        assert config.label == "Retro"

    def test_sprint_planning_predefined(self) -> None:
        """Test sprint-planning has predefined config."""
        assert "sprint-planning" in PREDEFINED_LABELS
        config = PREDEFINED_LABELS["sprint-planning"]
        assert config.icon == "📋"
        assert config.label == "Sprint"

    def test_tea_workflows_predefined(self) -> None:
        """Test TEA module workflows have predefined configs (keys match Phase enum conversion)."""
        tea_workflows = [
            ("tea-framework", "🧪", "Framework"),
            ("tea-nfr-assess", "🧪", "NFR"),
            ("tea-test-design", "🧪", "TestDesign"),
            ("tea-ci", "🧪", "CI"),
            ("tea-trace", "🧪", "Trace"),
            ("tea-automate", "🧪", "Automate"),
            ("atdd", "🧪", "ATDD"),
            ("test-review", "🧪", "TestReview"),
        ]
        for workflow, expected_icon, expected_label in tea_workflows:
            assert workflow in PREDEFINED_LABELS, f"Missing predefined: {workflow}"
            config = PREDEFINED_LABELS[workflow]
            assert config.icon == expected_icon, f"Wrong icon for {workflow}"
            assert config.label == expected_label, f"Wrong label for {workflow}"


class TestMatchIconPattern:
    """Test pattern-based icon matching (AC3)."""

    def test_create_pattern(self) -> None:
        """Test *create* pattern matches 📝."""
        assert _match_icon_pattern("create-story") == "📝"
        assert _match_icon_pattern("my-create-task") == "📝"
        assert _match_icon_pattern("CREATE-STORY") == "📝"  # case insensitive

    def test_valid_pattern(self) -> None:
        """Test *valid* pattern matches 🔍."""
        # "validate-story" contains "valid" which matches the validation pattern
        assert _match_icon_pattern("validate-story") == "🔍"
        assert _match_icon_pattern("custom-validation") == "🔍"
        assert _match_icon_pattern("VALIDATOR") == "🔍"  # case insensitive

    def test_synth_pattern(self) -> None:
        """Test *synth* pattern matches 🔄."""
        assert _match_icon_pattern("data-synthesis") == "🔄"
        assert _match_icon_pattern("synthesizer") == "🔄"

    def test_synth_before_dev_for_synthesis(self) -> None:
        """Test *synth* matches before *dev* and *review* for synthesis workflows.

        Pattern order is: create > valid > synth > dev > review.
        So "synth" comes BEFORE "review" and "dev".
        """
        # code-review-synthesis: "synth" comes before "review" in pattern order
        assert _match_icon_pattern("code-review-synthesis") == "🔄"
        # validate-story-synthesis contains "valid" which comes before "synth"
        assert _match_icon_pattern("validate-story-synthesis") == "🔍"
        # Test pure synthesis workflows
        assert _match_icon_pattern("data-synthesis") == "🔄"
        assert _match_icon_pattern("synthesis-engine") == "🔄"
        # dev-story-synthesis: "synth" comes before "dev"
        assert _match_icon_pattern("dev-story-synthesis") == "🔄"

    def test_dev_pattern(self) -> None:
        """Test *dev* pattern matches 💻."""
        assert _match_icon_pattern("dev-story") == "💻"
        assert _match_icon_pattern("developer-tools") == "💻"
        assert _match_icon_pattern("DEV") == "💻"  # case insensitive

    def test_review_pattern(self) -> None:
        """Test *review* pattern matches 👀."""
        assert _match_icon_pattern("code-review") == "👀"
        assert _match_icon_pattern("peer-review-code") == "👀"

    def test_test_pattern(self) -> None:
        """Test *test* pattern matches 🧪."""
        assert _match_icon_pattern("unit-test-runner") == "🧪"
        assert _match_icon_pattern("testing-framework") == "🧪"

    def test_plan_pattern(self) -> None:
        """Test *plan* pattern matches 📋."""
        assert _match_icon_pattern("sprint-planning") == "📋"
        assert _match_icon_pattern("planner") == "📋"

    def test_retro_pattern(self) -> None:
        """Test *retro* pattern matches 📊."""
        assert _match_icon_pattern("retrospective") == "📊"
        assert _match_icon_pattern("epic-retrospective") == "📊"

    def test_no_pattern_match_default(self) -> None:
        """Test no pattern match returns default icon."""
        assert _match_icon_pattern("unknown-workflow") == "📋"
        assert _match_icon_pattern("random-task") == "📋"
        assert _match_icon_pattern("xyz") == "📋"

    def test_first_match_wins(self) -> None:
        """Test first pattern match wins (order matters)."""
        # "create-valid" should match "create" (📝) not "valid" (🔍)
        assert _match_icon_pattern("create-valid") == "📝"


class TestStripModulePrefix:
    """Test module prefix stripping (AC5)."""

    def test_strip_testarch_prefix(self) -> None:
        """Test stripping testarch: prefix."""
        assert _strip_module_prefix("testarch:nfr") == "nfr"
        assert _strip_module_prefix("testarch:test-design") == "test-design"

    def test_no_prefix_unchanged(self) -> None:
        """Test workflow without prefix is unchanged."""
        assert _strip_module_prefix("create-story") == "create-story"
        assert _strip_module_prefix("dev-story") == "dev-story"

    def test_multiple_colons(self) -> None:
        """Test only first colon is separator."""
        assert _strip_module_prefix("a:b:c") == "b:c"

    def test_empty_after_prefix(self) -> None:
        """Test empty name after prefix."""
        assert _strip_module_prefix("prefix:") == ""


class TestSmartTruncateLabel:
    """Test smart label truncation (AC4, AC5)."""

    def test_short_label_unchanged(self) -> None:
        """Test short labels are only capitalized."""
        assert _smart_truncate_label("dev") == "Dev"
        assert _smart_truncate_label("test") == "Test"

    def test_exact_max_length_unchanged(self) -> None:
        """Test labels exactly at max length are not truncated."""
        # 12 chars exactly
        assert _smart_truncate_label("twelve-chars") == "Twelve-chars"

    def test_long_label_truncated(self) -> None:
        """Test long labels are truncated with ellipsis."""
        # "Very-long-workflow-name" = 23 chars -> truncated to 15 + "…" = 16
        assert _smart_truncate_label("very-long-workflow-name") == "Very-long-workf…"

    def test_truncation_boundary(self) -> None:
        """Test truncation at 17 chars (just over limit)."""
        # "seventeen-char-xx" = 17 chars -> "Seventeen-char-xx" capitalized = 17 chars
        # Truncated to 15 + "…" = 16 chars total
        assert _smart_truncate_label("seventeen-char-xx") == "Seventeen-char-…"
        # 16 chars exactly should not be truncated
        assert _smart_truncate_label("sixteen-chars-xx") == "Sixteen-chars-xx"
        # 13 chars is under limit, should not be truncated
        assert _smart_truncate_label("thirteen-char") == "Thirteen-char"

    def test_module_prefix_stripped_then_truncated(self) -> None:
        """Test module prefix is stripped before truncation."""
        assert _smart_truncate_label("testarch:nfr") == "Nfr"
        assert _smart_truncate_label("testarch:test-design") == "Test-design"
        # Long name after prefix strip (20 chars -> truncated to 15 + "…" = 16)
        assert _smart_truncate_label("testarch:some-very-long-name") == "Some-very-long-…"

    def test_capitalization_preserves_rest(self) -> None:
        """Test only first letter is capitalized, rest preserved."""
        assert _smart_truncate_label("camelCase") == "CamelCase"
        assert _smart_truncate_label("with-hyphens") == "With-hyphens"

    def test_empty_name(self) -> None:
        """Test empty name handling."""
        # Empty after prefix strip returns empty string
        assert _smart_truncate_label("prefix:") == ""


class TestWorkflowNotificationConfig:
    """Test WorkflowNotificationConfig dataclass."""

    def test_config_creation(self) -> None:
        """Test creating a config instance."""
        config = WorkflowNotificationConfig(icon="📝", label="Test")
        assert config.icon == "📝"
        assert config.label == "Test"

    def test_config_frozen(self) -> None:
        """Test config is frozen (immutable)."""
        config = WorkflowNotificationConfig(icon="📝", label="Test")
        with pytest.raises(AttributeError):
            config.icon = "📋"  # type: ignore

    def test_config_hashable(self) -> None:
        """Test config is hashable (can be used in sets/dicts)."""
        config1 = WorkflowNotificationConfig(icon="📝", label="Test")
        config2 = WorkflowNotificationConfig(icon="📝", label="Test")
        assert hash(config1) == hash(config2)
        assert {config1, config2} == {config1}


class TestFindWorkflowYamlPaths:
    """Test workflow YAML path discovery."""

    def test_standard_workflow_paths(self) -> None:
        """Test standard workflow search paths."""
        paths = _find_workflow_yaml_paths("create-story")
        path_strs = [str(p) for p in paths]

        # Should include implementation phase paths
        assert any("4-implementation/create-story/workflow.yaml" in p for p in path_strs)
        # Should include generic paths
        assert any("workflows/create-story/workflow.yaml" in p for p in path_strs)

    def test_module_workflow_paths(self) -> None:
        """Test module workflow search paths."""
        paths = _find_workflow_yaml_paths("testarch:nfr")
        path_strs = [str(p) for p in paths]

        # Should search in module directory
        assert any("testarch/nfr/workflow.yaml" in p for p in path_strs)


class TestLoadWorkflowNotificationConfig:
    """Test YAML loading for workflow notification config (AC1, AC2)."""

    def test_load_with_label_and_icon(self) -> None:
        """Test loading config with both label and icon from YAML."""
        yaml_content = """
notification:
  label: "MyLabel"
  icon: "🎯"
"""
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            with patch.object(Path, "exists", return_value=True):
                # Mock _find_workflow_yaml_paths to return a fake path
                with patch(
                    "bmad_assist.notifications.workflow_labels._find_workflow_yaml_paths",
                    return_value=[Path("/fake/workflow.yaml")],
                ):
                    config = _load_workflow_notification_config("test-workflow")
                    assert config is not None
                    assert config.icon == "🎯"
                    assert config.label == "MyLabel"

    def test_load_with_short_alias(self) -> None:
        """Test loading config with notification.short alias."""
        yaml_content = """
notification:
  short: "Alias"
  icon: "🔥"
"""
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            with patch.object(Path, "exists", return_value=True):
                with patch(
                    "bmad_assist.notifications.workflow_labels._find_workflow_yaml_paths",
                    return_value=[Path("/fake/workflow.yaml")],
                ):
                    config = _load_workflow_notification_config("test-workflow")
                    assert config is not None
                    assert config.label == "Alias"

    def test_load_label_only_uses_pattern_icon(self) -> None:
        """Test loading config with only label uses pattern-matched icon."""
        yaml_content = """
notification:
  label: "MyCreate"
"""
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            with patch.object(Path, "exists", return_value=True):
                with patch(
                    "bmad_assist.notifications.workflow_labels._find_workflow_yaml_paths",
                    return_value=[Path("/fake/workflow.yaml")],
                ):
                    config = _load_workflow_notification_config("create-custom")
                    assert config is not None
                    assert config.label == "MyCreate"
                    assert config.icon == "📝"  # Pattern match for "create"

    def test_load_icon_only_uses_truncated_label(self) -> None:
        """Test loading config with only icon uses truncated label."""
        yaml_content = """
notification:
  icon: "🌟"
"""
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            with patch.object(Path, "exists", return_value=True):
                with patch(
                    "bmad_assist.notifications.workflow_labels._find_workflow_yaml_paths",
                    return_value=[Path("/fake/workflow.yaml")],
                ):
                    config = _load_workflow_notification_config("short-name")
                    assert config is not None
                    assert config.icon == "🌟"
                    assert config.label == "Short-name"

    def test_load_long_label_truncated(self) -> None:
        """Test loading config with label exceeding 12 chars is truncated per AC1."""
        yaml_content = """
notification:
  label: "VeryLongLabelExceeds"
  icon: "🎯"
"""
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            with patch.object(Path, "exists", return_value=True):
                with patch(
                    "bmad_assist.notifications.workflow_labels._find_workflow_yaml_paths",
                    return_value=[Path("/fake/workflow.yaml")],
                ):
                    config = _load_workflow_notification_config("test-workflow")
                    assert config is not None
                    assert len(config.label) <= 16
                    assert config.label == "VeryLongLabelEx…"
                    assert config.icon == "🎯"

    def test_load_missing_notification_section(self) -> None:
        """Test loading config with no notification section returns None."""
        yaml_content = """
name: test-workflow
description: A test workflow
"""
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            with patch.object(Path, "exists", return_value=True):
                with patch(
                    "bmad_assist.notifications.workflow_labels._find_workflow_yaml_paths",
                    return_value=[Path("/fake/workflow.yaml")],
                ):
                    config = _load_workflow_notification_config("test-workflow")
                    assert config is None

    def test_load_empty_notification_section(self) -> None:
        """Test loading config with empty notification section returns None."""
        yaml_content = """
notification: {}
"""
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            with patch.object(Path, "exists", return_value=True):
                with patch(
                    "bmad_assist.notifications.workflow_labels._find_workflow_yaml_paths",
                    return_value=[Path("/fake/workflow.yaml")],
                ):
                    config = _load_workflow_notification_config("test-workflow")
                    assert config is None

    def test_load_file_not_found(self) -> None:
        """Test loading config when file doesn't exist returns None."""
        with patch.object(Path, "exists", return_value=False):
            with patch(
                "bmad_assist.notifications.workflow_labels._find_workflow_yaml_paths",
                return_value=[Path("/fake/workflow.yaml")],
            ):
                config = _load_workflow_notification_config("nonexistent")
                assert config is None

    def test_load_malformed_yaml(self) -> None:
        """Test loading malformed YAML returns None and logs warning."""
        malformed_yaml = """
notification:
  label: "Test
  broken: yaml: [syntax
"""
        with patch("builtins.open", mock_open(read_data=malformed_yaml)):
            with patch.object(Path, "exists", return_value=True):
                with patch(
                    "bmad_assist.notifications.workflow_labels._find_workflow_yaml_paths",
                    return_value=[Path("/fake/workflow.yaml")],
                ):
                    with patch("bmad_assist.notifications.workflow_labels.logger") as mock_logger:
                        config = _load_workflow_notification_config("test-workflow")
                        assert config is None
                        mock_logger.warning.assert_called()


class TestComputeConfig:
    """Test config computation logic."""

    def test_predefined_takes_priority(self) -> None:
        """Test predefined registry is checked first."""
        clear_workflow_label_cache()
        config = _compute_config("create-story")
        assert config == PREDEFINED_LABELS["create-story"]

    def test_fallback_for_unknown_workflow(self) -> None:
        """Test fallback for unknown workflow."""
        clear_workflow_label_cache()
        config = _compute_config("unknown-xyz-workflow")
        assert config.icon == "📋"  # Default icon
        assert config.label == "Unknown-xyz-wor…"  # Truncated to 16 chars


class TestCaching:
    """Test cache behavior (AC6)."""

    def test_cache_stores_result(self) -> None:
        """Test cache stores computed result."""
        clear_workflow_label_cache()

        # First call computes and caches
        config1 = get_workflow_notification_config("create-story")

        # Second call should return cached result
        config2 = get_workflow_notification_config("create-story")

        assert config1 is config2  # Same object (cached)

    def test_cache_clear_resets(self) -> None:
        """Test clear_workflow_label_cache resets cache."""
        # Populate cache
        get_workflow_notification_config("create-story")

        # Clear cache
        clear_workflow_label_cache()

        # Should recompute (but result is same for predefined)
        config = get_workflow_notification_config("create-story")
        assert config == PREDEFINED_LABELS["create-story"]

    def test_cache_different_workflows(self) -> None:
        """Test cache works for different workflows."""
        clear_workflow_label_cache()

        config1 = get_workflow_notification_config("create-story")
        config2 = get_workflow_notification_config("dev-story")

        assert config1.label == "Create"
        assert config2.label == "Develop"


class TestThreadSafety:
    """Test thread safety for cache (AC7)."""

    def test_thread_safety_concurrent_access(self) -> None:
        """Verify thread safety under concurrent load."""
        clear_workflow_label_cache()

        results: list[WorkflowNotificationConfig] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker(workflow: str) -> None:
            try:
                for _ in range(100):
                    config = get_workflow_notification_config(workflow)
                    with lock:
                        results.append(config)
            except Exception as e:
                with lock:
                    errors.append(e)

        # Create threads for different workflows
        threads = [threading.Thread(target=worker, args=(f"test-workflow-{i}",)) for i in range(10)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 1000  # 10 threads * 100 iterations

    def test_thread_safety_same_workflow(self) -> None:
        """Test thread safety when all threads access same workflow."""
        clear_workflow_label_cache()

        results: list[WorkflowNotificationConfig] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker() -> None:
            try:
                for _ in range(100):
                    config = get_workflow_notification_config("create-story")
                    with lock:
                        results.append(config)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 1000
        # All results should be the same object (cached)
        assert all(r == results[0] for r in results)


class TestPublicAPI:
    """Test public API functions."""

    def test_get_workflow_icon_predefined(self) -> None:
        """Test get_workflow_icon for predefined workflow."""
        clear_workflow_label_cache()
        assert get_workflow_icon("create-story") == "📝"
        assert get_workflow_icon("dev-story") == "💻"
        assert get_workflow_icon("code-review") == "👀"

    def test_get_workflow_icon_pattern_match(self) -> None:
        """Test get_workflow_icon uses pattern matching for unknown."""
        clear_workflow_label_cache()
        # Pattern match on "create"
        assert get_workflow_icon("my-create-task") == "📝"

    def test_get_workflow_icon_default(self) -> None:
        """Test get_workflow_icon returns default for no match."""
        clear_workflow_label_cache()
        assert get_workflow_icon("unknown-xyz") == "📋"

    def test_get_workflow_label_predefined(self) -> None:
        """Test get_workflow_label for predefined workflow."""
        clear_workflow_label_cache()
        assert get_workflow_label("create-story") == "Create"
        assert get_workflow_label("validate-story-synthesis") == "Val-Synth"

    def test_get_workflow_label_truncation(self) -> None:
        """Test get_workflow_label truncates long names."""
        clear_workflow_label_cache()
        label = get_workflow_label("some-unknown-long-workflow")
        assert len(label) <= 16
        assert label == "Some-unknown-lo…"

    def test_get_workflow_label_module_prefix(self) -> None:
        """Test get_workflow_label handles module prefixes."""
        clear_workflow_label_cache()
        # Predefined TEA workflow (key matches Phase enum after _phase_to_workflow_name)
        assert get_workflow_label("tea-nfr-assess") == "NFR"
        # Unknown module workflow - strips prefix and capitalizes
        label = get_workflow_label("unknown:short")
        assert label == "Short"

    def test_get_workflow_notification_config_predefined(self) -> None:
        """Test get_workflow_notification_config for predefined."""
        clear_workflow_label_cache()
        config = get_workflow_notification_config("create-story")
        assert config.icon == "📝"
        assert config.label == "Create"

    def test_get_workflow_notification_config_fallback(self) -> None:
        """Test get_workflow_notification_config fallback."""
        clear_workflow_label_cache()
        config = get_workflow_notification_config("unknown-workflow")
        assert config.icon == "📋"
        assert config.label == "Unknown-workflow"


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_workflow_name(self) -> None:
        """Test empty workflow name."""
        clear_workflow_label_cache()
        config = get_workflow_notification_config("")
        assert config.icon == "📋"
        assert config.label == ""

    def test_single_char_workflow(self) -> None:
        """Test single character workflow name."""
        clear_workflow_label_cache()
        config = get_workflow_notification_config("x")
        assert config.icon == "📋"
        assert config.label == "X"

    def test_unicode_in_workflow_name(self) -> None:
        """Test unicode characters in workflow name."""
        clear_workflow_label_cache()
        config = get_workflow_notification_config("café-workflow")
        assert config.label.startswith("C")

    def test_synthesis_icon_correct(self) -> None:
        """Test synthesis workflows get correct icon from predefined labels."""
        clear_workflow_label_cache()

        # code-review-synthesis is in predefined labels with 👀 (Rev-Synth)
        icon = get_workflow_icon("code-review-synthesis")
        assert icon == "👀", f"Expected 👀 (predefined Rev-Synth) but got {icon}"

        # validate-story-synthesis is in predefined labels with 🔍 (Val-Synth)
        icon = get_workflow_icon("validate-story-synthesis")
        assert icon == "🔍", f"Expected 🔍 (predefined Val-Synth) but got {icon}"

        # Pure synthesis workflow without other patterns uses synth icon
        icon = get_workflow_icon("data-synthesis")
        assert icon == "🔄", f"Expected 🔄 (synth pattern) but got {icon}"


class TestParametrized:
    """Parametrized tests for comprehensive coverage."""

    @pytest.mark.parametrize(
        "workflow,expected_icon,expected_label",
        [
            # Predefined workflows
            ("create-story", "📝", "Create"),
            ("validate-story", "🔍", "Validate"),
            ("dev-story", "💻", "Develop"),
            ("code-review", "👀", "Review"),
            ("retrospective", "📊", "Retro"),
            # TEA workflows (keys match Phase enum conversion)
            ("tea-nfr-assess", "🧪", "NFR"),
            ("tea-framework", "🧪", "Framework"),
        ],
    )
    def test_predefined_workflows(
        self, workflow: str, expected_icon: str, expected_label: str
    ) -> None:
        """Test predefined workflows return correct config."""
        clear_workflow_label_cache()
        config = get_workflow_notification_config(workflow)
        assert config.icon == expected_icon
        assert config.label == expected_label

    @pytest.mark.parametrize(
        "workflow,expected_icon",
        [
            ("my-create-task", "📝"),
            ("custom-validation", "🔍"),
            ("dev-helper", "💻"),
            ("peer-review", "👀"),
            ("testing-utils", "🧪"),
            ("data-synthesis", "🔄"),
            ("sprint-planner", "📋"),
            ("epic-retro", "📊"),
            ("unknown-xyz", "📋"),
        ],
    )
    def test_pattern_icon_matching(self, workflow: str, expected_icon: str) -> None:
        """Test pattern matching for unknown workflows."""
        clear_workflow_label_cache()
        icon = get_workflow_icon(workflow)
        assert icon == expected_icon

    @pytest.mark.parametrize(
        "name,expected_label",
        [
            ("short", "Short"),
            ("twelve-chars", "Twelve-chars"),  # exactly 12 chars
            ("thirteen-char", "Thirteen-char"),  # 13 chars (under 16 limit)
            ("sixteen-chars-xx", "Sixteen-chars-xx"),  # exactly 16 chars
            ("very-long-workflow", "Very-long-workf…"),  # 18 chars -> truncated to 15 + "…" = 16
            ("testarch:abc", "Abc"),
        ],
    )
    def test_label_truncation_parametrized(self, name: str, expected_label: str) -> None:
        """Test label truncation for various lengths."""
        label = _smart_truncate_label(name)
        assert label == expected_label
        assert len(label) <= MAX_LABEL_LENGTH


class TestSkillMdProbe:
    """v6.4+ ``SKILL.md`` description probe (post-Phase-7 fix).

    Before 0.6.0, ``_compute_config`` only consulted the legacy
    ``_bmad/.../workflow.yaml`` for non-predefined workflows, so v6.4+
    users got fallback labels for any workflow not in
    ``PREDEFINED_LABELS``. The probe added in 0.6.0 reads the
    ``description`` field from ``.claude/skills/bmad-<name>/SKILL.md``
    (or the ``.agents`` mirror, or the bundled fallback) so v6.4+
    users see the workflow's actual description as its label.
    """

    def setup_method(self) -> None:
        """Reset the cache so each probe test starts clean."""
        clear_workflow_label_cache()

    def teardown_method(self) -> None:
        """Drop any cached results recorded by the probe."""
        clear_workflow_label_cache()

    def _write_skill_md(self, root: Path, skill_id: str, description: str) -> Path:
        """Write a minimal valid SKILL.md and return its path."""
        skill_dir = root / skill_id
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            f"---\nname: {skill_id}\ndescription: {description}\n---\n\n# {skill_id}\n",
            encoding="utf-8",
        )
        return skill_md

    def test_probe_reads_claude_skills_description(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """v6.4+ users get a label sourced from ``.claude/skills/.../SKILL.md``."""
        monkeypatch.chdir(tmp_path)
        self._write_skill_md(
            tmp_path / ".claude" / "skills",
            "bmad-custom-flow",
            "Custom Flow",
        )

        # ``custom-flow`` is not in PREDEFINED_LABELS so the probe runs.
        config = get_workflow_notification_config("custom-flow")
        assert config.label == "Custom Flow"
        # Icon comes from pattern matching since SKILL.md frontmatter
        # doesn't carry a notification.icon — and "custom-flow" matches
        # no pattern, so the default neutral icon wins.
        assert config.icon == DEFAULT_ICON

    def test_probe_falls_back_to_agents_skills(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``.agents/skills`` is consulted when ``.claude/skills`` lacks the skill."""
        monkeypatch.chdir(tmp_path)
        self._write_skill_md(
            tmp_path / ".agents" / "skills",
            "bmad-custom-flow",
            "Agents Mirror Description",
        )

        config = get_workflow_notification_config("custom-flow")
        # _smart_truncate_label keeps first 15 chars then appends "…".
        assert config.label == "Agents Mirror D…"
        assert len(config.label) == MAX_LABEL_LENGTH

    def test_probe_falls_back_to_bundled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bundled ``src/bmad_assist/skills/...`` fallback wins when no project install."""
        monkeypatch.chdir(tmp_path)

        # ``bmad-create-story`` is bundled — but it's also predefined,
        # so we need a name that bypasses PREDEFINED_LABELS to actually
        # exercise the SKILL.md probe path. We patch PREDEFINED_LABELS
        # to drop the predefined entry for this assertion only.
        monkeypatch.setattr(
            "bmad_assist.notifications.workflow_labels.PREDEFINED_LABELS",
            {},
        )
        clear_workflow_label_cache()

        config = get_workflow_notification_config("create-story")
        # The bundled SKILL.md description for bmad-create-story is
        # non-empty; we just assert the probe found *something* and did
        # not fall through to the smart-truncated workflow name.
        assert config.label != "Create-story"
        assert len(config.label) <= MAX_LABEL_LENGTH

    def test_probe_does_not_consult_legacy_yaml_for_v64_users(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When SKILL.md resolves, the legacy ``_bmad/...`` YAML is not read.

        This pins the post-Phase-7 ordering: SKILL.md wins over the
        legacy workflow.yaml notification fields when both exist.
        """
        monkeypatch.chdir(tmp_path)
        # SKILL.md wins.
        self._write_skill_md(
            tmp_path / ".claude" / "skills",
            "bmad-custom-flow",
            "Wins From SKILL.md",
        )
        # Legacy YAML present but should not be consulted.
        legacy = tmp_path / "_bmad" / "bmm" / "workflows" / "4-implementation" / "custom-flow"
        legacy.mkdir(parents=True)
        (legacy / "workflow.yaml").write_text(
            "notification:\n  label: 'Legacy Loses'\n  icon: '💥'\n",
            encoding="utf-8",
        )

        config = get_workflow_notification_config("custom-flow")
        # _smart_truncate_label keeps first 15 chars then appends "…".
        assert config.label == "Wins From SKILL…"
        assert len(config.label) == MAX_LABEL_LENGTH
        # Icon is pattern-derived, not the legacy "💥".
        assert config.icon != "💥"

    def test_probe_skips_malformed_skill_md(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A SKILL.md without valid frontmatter is silently skipped."""
        monkeypatch.chdir(tmp_path)
        skill_dir = tmp_path / ".claude" / "skills" / "bmad-custom-flow"
        skill_dir.mkdir(parents=True)
        # No frontmatter delimiters — parse_skill raises MalformedSkill.
        (skill_dir / "SKILL.md").write_text("# No frontmatter here\n", encoding="utf-8")

        # No SKILL.md, no legacy YAML, no predefined entry — fall back
        # to pattern + truncation.
        config = get_workflow_notification_config("custom-flow")
        assert config.label == _smart_truncate_label("custom-flow")
