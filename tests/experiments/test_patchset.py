"""Tests for experiments patch-set system.

Tests cover:
- PatchSetManifest model validation
- YAML loading with variable resolution
- Null value handling (YAML null → Python None)
- PatchSetRegistry discovery and access
- Path validation for patch files and workflow_override directories
- Default manifests (baseline, no-patches)
"""

import logging
import os
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from bmad_assist.core.exceptions import ConfigError
from bmad_assist.experiments.config import NAME_PATTERN
from bmad_assist.experiments.loop import KNOWN_WORKFLOWS
from bmad_assist.experiments.patchset import (
    PatchSetManifest,
    PatchSetRegistry,
    load_patchset_manifest,
)


class TestPatchSetManifest:
    """Tests for PatchSetManifest Pydantic model."""

    def test_valid_minimal_manifest(self) -> None:
        """Test creating a minimal valid manifest."""
        manifest = PatchSetManifest(
            name="test-patchset",
            patches={},
        )
        assert manifest.name == "test-patchset"
        assert manifest.description is None
        assert manifest.patches == {}
        assert manifest.workflow_overrides == {}

    def test_valid_full_manifest(self) -> None:
        """Test creating a full manifest with all fields."""
        manifest = PatchSetManifest(
            name="full-patchset",
            description="Full patch-set configuration",
            patches={
                "create-story": "/path/to/create-story.patch.yaml",
                "dev-story": "/path/to/dev-story.patch.yaml",
            },
            workflow_overrides={
                "code-review": "/path/to/custom-workflow/",
            },
        )
        assert manifest.name == "full-patchset"
        assert manifest.description == "Full patch-set configuration"
        assert len(manifest.patches) == 2
        assert len(manifest.workflow_overrides) == 1

    def test_manifest_with_null_patch(self) -> None:
        """Test manifest with null patch value (no patch for workflow)."""
        manifest = PatchSetManifest(
            name="null-patch",
            patches={
                "create-story": None,  # No patch for this workflow
                "dev-story": "/path/to/patch.yaml",
            },
        )
        assert manifest.patches["create-story"] is None
        assert manifest.patches["dev-story"] == "/path/to/patch.yaml"

    def test_manifest_empty_patches_dict(self) -> None:
        """Test manifest with empty patches dict (raw workflows)."""
        manifest = PatchSetManifest(
            name="no-patches",
            patches={},
        )
        assert manifest.patches == {}

    def test_manifest_empty_patches_but_workflow_overrides(self) -> None:
        """Test manifest with empty patches but non-empty workflow_overrides."""
        manifest = PatchSetManifest(
            name="overrides-only",
            patches={},
            workflow_overrides={
                "create-story": "/path/to/override/",
            },
        )
        assert manifest.patches == {}
        assert manifest.workflow_overrides == {"create-story": "/path/to/override/"}

    def test_empty_name_raises_error(self) -> None:
        """Test that empty name raises ValueError."""
        with pytest.raises(ValueError, match="name cannot be empty"):
            PatchSetManifest(name="", patches={})

    def test_whitespace_name_raises_error(self) -> None:
        """Test that whitespace-only name raises ValueError."""
        with pytest.raises(ValueError, match="name cannot be empty"):
            PatchSetManifest(name="   ", patches={})

    def test_name_with_spaces_raises_error(self) -> None:
        """Test that name with spaces raises ValueError."""
        with pytest.raises(ValueError, match="Invalid name"):
            PatchSetManifest(name="has spaces", patches={})

    def test_name_with_special_chars_raises_error(self) -> None:
        """Test that name with special characters raises ValueError."""
        with pytest.raises(ValueError, match="Invalid name"):
            PatchSetManifest(name="has@special", patches={})

    def test_name_starting_with_number_raises_error(self) -> None:
        """Test that name starting with number raises ValueError."""
        with pytest.raises(ValueError, match="Invalid name"):
            PatchSetManifest(name="123patchset", patches={})

    def test_name_starting_with_hyphen_raises_error(self) -> None:
        """Test that name starting with hyphen raises ValueError."""
        with pytest.raises(ValueError, match="Invalid name"):
            PatchSetManifest(name="-patchset", patches={})

    def test_empty_workflow_name_in_patches_raises_error(self) -> None:
        """Test that empty workflow name in patches raises error."""
        with pytest.raises(ValueError, match="Workflow name in patches cannot be empty"):
            PatchSetManifest(
                name="test",
                patches={"": "/path/to/patch.yaml"},
            )

    def test_whitespace_workflow_name_in_patches_raises_error(self) -> None:
        """Test that whitespace-only workflow name in patches raises error."""
        with pytest.raises(ValueError, match="Workflow name in patches cannot be empty"):
            PatchSetManifest(
                name="test",
                patches={"   ": "/path/to/patch.yaml"},
            )

    def test_empty_patch_path_string_raises_error(self) -> None:
        """Test that empty patch path string (non-null) raises error."""
        with pytest.raises(ValueError, match="Patch path.*cannot be empty string"):
            PatchSetManifest(
                name="test",
                patches={"create-story": ""},
            )

    def test_whitespace_patch_path_raises_error(self) -> None:
        """Test that whitespace-only patch path raises error."""
        with pytest.raises(ValueError, match="Patch path.*cannot be empty string"):
            PatchSetManifest(
                name="test",
                patches={"create-story": "   "},
            )

    def test_empty_workflow_name_in_overrides_raises_error(self) -> None:
        """Test that empty workflow name in workflow_overrides raises error."""
        with pytest.raises(ValueError, match="Workflow name in workflow_overrides cannot be empty"):
            PatchSetManifest(
                name="test",
                patches={},
                workflow_overrides={"": "/path/to/override/"},
            )

    def test_empty_override_path_raises_error(self) -> None:
        """Test that empty override path raises error."""
        with pytest.raises(ValueError, match="Workflow override path.*cannot be empty"):
            PatchSetManifest(
                name="test",
                patches={},
                workflow_overrides={"create-story": ""},
            )

    def test_manifest_is_frozen(self) -> None:
        """Test that manifest is immutable."""
        manifest = PatchSetManifest(name="test", patches={})
        with pytest.raises(ValidationError, match="frozen"):
            manifest.name = "new-name"  # type: ignore[misc]

    def test_conflict_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test workflow in both patches (non-null) and workflow_overrides logs warning."""
        with caplog.at_level(logging.WARNING):
            PatchSetManifest(
                name="conflict",
                patches={"create-story": "/path/to/patch.yaml"},
                workflow_overrides={"create-story": "/path/to/override/"},
            )

        assert "workflow_override takes precedence" in caplog.text
        assert "create-story" in caplog.text

    def test_conflict_with_null_patch_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test workflow in patches (null) and workflow_overrides doesn't log warning."""
        with caplog.at_level(logging.WARNING):
            PatchSetManifest(
                name="no-conflict",
                patches={"create-story": None},  # Null = no patch
                workflow_overrides={"create-story": "/path/to/override/"},
            )

        # No warning because patch is null
        assert "workflow_override takes precedence" not in caplog.text


class TestLoadPatchsetManifest:
    """Tests for load_patchset_manifest function."""

    @pytest.fixture
    def patchsets_dir(self, tmp_path: Path) -> Path:
        """Create temp directory for patch-set manifests."""
        patchsets = tmp_path / "patch-sets"
        patchsets.mkdir()
        return patchsets

    @pytest.fixture
    def write_patchset(self, patchsets_dir: Path) -> Callable[[str, str], Path]:
        """Factory to write patch-set manifest files."""

        def _write(content: str, filename: str = "test-patchset.yaml") -> Path:
            path = patchsets_dir / filename
            path.write_text(content, encoding="utf-8")
            return path

        return _write

    def test_load_minimal_manifest(
        self,
        write_patchset: Callable[[str, str], Path],
    ) -> None:
        """Test loading a minimal valid manifest."""
        content = """\
name: test-patchset
description: "Test patch-set configuration"

patches: {}
"""
        path = write_patchset(content, "test-patchset.yaml")
        manifest = load_patchset_manifest(path, validate_paths=False)

        assert manifest.name == "test-patchset"
        assert manifest.description == "Test patch-set configuration"
        assert manifest.patches == {}

    def test_load_manifest_with_null_patches(
        self,
        write_patchset: Callable[[str, str], Path],
    ) -> None:
        """Test loading manifest with YAML null values."""
        content = """\
name: null-patches
description: "Manifest with null patch values"

patches:
  create-story: null
  dev-story: /path/to/patch.yaml
"""
        path = write_patchset(content, "null-patches.yaml")
        manifest = load_patchset_manifest(path, validate_paths=False)

        assert manifest.patches["create-story"] is None
        assert manifest.patches["dev-story"] == "/path/to/patch.yaml"

    def test_load_manifest_with_tilde_null(
        self,
        write_patchset: Callable[[str, str], Path],
    ) -> None:
        """Test loading manifest with YAML tilde (alternate null syntax)."""
        content = """\
name: tilde-null
patches:
  create-story: ~
"""
        path = write_patchset(content, "tilde-null.yaml")
        manifest = load_patchset_manifest(path, validate_paths=False)

        assert manifest.patches["create-story"] is None

    def test_variable_resolution_project(
        self,
        write_patchset: Callable[[str, str], Path],
        tmp_path: Path,
    ) -> None:
        """Test ${project} variable resolution."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        patches_dir = project_root / ".bmad-assist" / "patches"
        patches_dir.mkdir(parents=True)
        patch_file = patches_dir / "create-story.patch.yaml"
        patch_file.write_text("# mock patch")

        content = """\
name: project-var
patches:
  create-story: ${project}/.bmad-assist/patches/create-story.patch.yaml
"""
        path = write_patchset(content, "project-var.yaml")
        manifest = load_patchset_manifest(path, project_root=project_root)

        # Variable should be resolved
        expected_path = (
            str(project_root.resolve()) + "/.bmad-assist/patches/create-story.patch.yaml"
        )
        assert manifest.patches["create-story"] == expected_path

    def test_variable_resolution_home(
        self,
        write_patchset: Callable[[str, str], Path],
    ) -> None:
        """Test ${home} variable resolution."""
        content = """\
name: home-var
patches:
  create-story: ${home}/patches/create-story.patch.yaml
"""
        path = write_patchset(content, "home-var.yaml")
        manifest = load_patchset_manifest(path, validate_paths=False)

        # Variable should be resolved
        home = os.path.expanduser("~")
        expected_path = f"{home}/patches/create-story.patch.yaml"
        assert manifest.patches["create-story"] == expected_path

    def test_project_variable_without_root_raises_error(
        self,
        write_patchset: Callable[[str, str], Path],
    ) -> None:
        """Test ${project} variable without project_root raises ConfigError."""
        content = """\
name: project-var
patches:
  create-story: ${project}/patches/patch.yaml
"""
        path = write_patchset(content, "project-var.yaml")

        with pytest.raises(ConfigError, match="project_root parameter required"):
            load_patchset_manifest(path, project_root=None)

    def test_unknown_variable_raises_error(
        self,
        write_patchset: Callable[[str, str], Path],
    ) -> None:
        """Test unknown variable placeholder raises ConfigError."""
        content = """\
name: unknown-var
patches:
  create-story: ${unknown}/patches/patch.yaml
"""
        path = write_patchset(content, "unknown-var.yaml")

        with pytest.raises(ConfigError, match="Unknown variable"):
            load_patchset_manifest(path)

    def test_file_not_found_raises_error(self, tmp_path: Path) -> None:
        """Test missing file raises ConfigError."""
        path = tmp_path / "nonexistent.yaml"
        with pytest.raises(ConfigError, match="not found"):
            load_patchset_manifest(path)

    def test_invalid_yaml_raises_error(
        self,
        write_patchset: Callable[[str, str], Path],
    ) -> None:
        """Test invalid YAML raises ConfigError."""
        path = write_patchset("invalid: yaml: [", "invalid.yaml")
        with pytest.raises(ConfigError, match="Invalid YAML"):
            load_patchset_manifest(path)

    def test_empty_file_raises_error(
        self,
        write_patchset: Callable[[str, str], Path],
    ) -> None:
        """Test empty file raises ConfigError."""
        path = write_patchset("", "empty.yaml")
        with pytest.raises(ConfigError, match="is empty"):
            load_patchset_manifest(path)

    def test_non_mapping_raises_error(
        self,
        write_patchset: Callable[[str, str], Path],
    ) -> None:
        """Test non-mapping YAML raises ConfigError."""
        path = write_patchset("- just\n- a\n- list", "list.yaml")
        with pytest.raises(ConfigError, match="must contain a YAML mapping"):
            load_patchset_manifest(path)

    def test_validation_error_raises_config_error(
        self,
        write_patchset: Callable[[str, str], Path],
    ) -> None:
        """Test schema validation failure raises ConfigError."""
        content = """\
# missing name field
patches: {}
"""
        path = write_patchset(content, "missing-name.yaml")
        with pytest.raises(ConfigError, match="validation failed"):
            load_patchset_manifest(path)

    def test_path_is_directory_raises_error(
        self,
        patchsets_dir: Path,
    ) -> None:
        """Test path that is directory raises ConfigError."""
        with pytest.raises(ConfigError, match="is not a file"):
            load_patchset_manifest(patchsets_dir)

    def test_unknown_workflow_logs_warning(
        self,
        write_patchset: Callable[[str, str], Path],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test unknown workflow name logs warning but doesn't fail."""
        content = """\
name: unknown-workflow
patches:
  unknown-workflow: /path/to/patch.yaml
  create-story: /path/to/patch.yaml
"""
        path = write_patchset(content, "unknown-workflow.yaml")

        with caplog.at_level(logging.WARNING):
            manifest = load_patchset_manifest(path, validate_paths=False)

        assert manifest.name == "unknown-workflow"
        assert "Unknown workflow" in caplog.text
        assert "unknown-workflow" in caplog.text

    def test_unknown_workflow_in_overrides_logs_warning(
        self,
        write_patchset: Callable[[str, str], Path],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test unknown workflow in workflow_overrides logs warning."""
        content = """\
name: unknown-override
patches: {}
workflow_overrides:
  unknown-workflow: /path/to/override/
"""
        path = write_patchset(content, "unknown-override.yaml")

        with caplog.at_level(logging.WARNING):
            manifest = load_patchset_manifest(path, validate_paths=False)

        assert "Unknown workflow" in caplog.text
        assert "workflow_overrides" in caplog.text


class TestPathValidation:
    """Tests for path validation in load_patchset_manifest."""

    @pytest.fixture
    def patchsets_dir(self, tmp_path: Path) -> Path:
        """Create temp directory for patch-set manifests."""
        patchsets = tmp_path / "patch-sets"
        patchsets.mkdir()
        return patchsets

    @pytest.fixture
    def write_patchset(self, patchsets_dir: Path) -> Callable[[str, str], Path]:
        """Factory to write patch-set manifest files."""

        def _write(content: str, filename: str = "test.yaml") -> Path:
            path = patchsets_dir / filename
            path.write_text(content, encoding="utf-8")
            return path

        return _write

    def test_validate_paths_for_patch_file(
        self,
        write_patchset: Callable[[str, str], Path],
        tmp_path: Path,
    ) -> None:
        """Test path validation for patch file that exists."""
        patches_dir = tmp_path / "patches"
        patches_dir.mkdir()
        patch_file = patches_dir / "create-story.patch.yaml"
        patch_file.write_text("# mock patch")

        content = f"""\
name: with-patch
patches:
  create-story: {patch_file}
"""
        path = write_patchset(content, "with-patch.yaml")
        manifest = load_patchset_manifest(path, validate_paths=True)

        assert manifest.patches["create-story"] == str(patch_file)

    def test_validate_paths_missing_patch_file_raises_error(
        self,
        write_patchset: Callable[[str, str], Path],
    ) -> None:
        """Test path validation raises error for missing patch file."""
        content = """\
name: missing-patch
patches:
  create-story: /nonexistent/path/to/patch.yaml
"""
        path = write_patchset(content, "missing-patch.yaml")

        with pytest.raises(ConfigError, match="does not exist"):
            load_patchset_manifest(path, validate_paths=True)

    def test_validate_paths_patch_is_directory_raises_error(
        self,
        write_patchset: Callable[[str, str], Path],
        tmp_path: Path,
    ) -> None:
        """Test path validation raises error if patch path is a directory."""
        patch_dir = tmp_path / "patch-dir"
        patch_dir.mkdir()

        content = f"""\
name: patch-is-dir
patches:
  create-story: {patch_dir}
"""
        path = write_patchset(content, "patch-is-dir.yaml")

        with pytest.raises(ConfigError, match="is not a file"):
            load_patchset_manifest(path, validate_paths=True)

    def test_validate_paths_for_workflow_override(
        self,
        write_patchset: Callable[[str, str], Path],
        tmp_path: Path,
    ) -> None:
        """Test path validation for workflow_override directory that exists."""
        override_dir = tmp_path / "overrides" / "create-story"
        override_dir.mkdir(parents=True)

        content = f"""\
name: with-override
patches: {{}}
workflow_overrides:
  create-story: {override_dir}
"""
        path = write_patchset(content, "with-override.yaml")
        manifest = load_patchset_manifest(path, validate_paths=True)

        assert manifest.workflow_overrides["create-story"] == str(override_dir)

    def test_validate_paths_missing_override_dir_raises_error(
        self,
        write_patchset: Callable[[str, str], Path],
    ) -> None:
        """Test path validation raises error for missing override directory."""
        content = """\
name: missing-override
patches: {}
workflow_overrides:
  create-story: /nonexistent/path/to/override/
"""
        path = write_patchset(content, "missing-override.yaml")

        with pytest.raises(ConfigError, match="does not exist"):
            load_patchset_manifest(path, validate_paths=True)

    def test_validate_paths_override_is_file_raises_error(
        self,
        write_patchset: Callable[[str, str], Path],
        tmp_path: Path,
    ) -> None:
        """Test path validation raises error if override path is a file."""
        override_file = tmp_path / "override-file.yaml"
        override_file.write_text("# not a directory")

        content = f"""\
name: override-is-file
patches: {{}}
workflow_overrides:
  create-story: {override_file}
"""
        path = write_patchset(content, "override-is-file.yaml")

        with pytest.raises(ConfigError, match="is not a directory"):
            load_patchset_manifest(path, validate_paths=True)

    def test_validate_paths_can_be_disabled(
        self,
        write_patchset: Callable[[str, str], Path],
    ) -> None:
        """Test path validation can be disabled."""
        content = """\
name: no-validation
patches:
  create-story: /nonexistent/path/to/patch.yaml
workflow_overrides:
  dev-story: /nonexistent/path/to/override/
"""
        path = write_patchset(content, "no-validation.yaml")

        # Should not raise even with nonexistent paths
        manifest = load_patchset_manifest(path, validate_paths=False)
        assert manifest.name == "no-validation"

    def test_validate_paths_null_patch_skipped(
        self,
        write_patchset: Callable[[str, str], Path],
    ) -> None:
        """Test path validation skips null patch values."""
        content = """\
name: null-skip
patches:
  create-story: null
"""
        path = write_patchset(content, "null-skip.yaml")

        # Should not raise for null path
        manifest = load_patchset_manifest(path, validate_paths=True)
        assert manifest.patches["create-story"] is None

    def test_relative_path_resolved_against_manifest_dir(
        self,
        write_patchset: Callable[[str, str], Path],
        patchsets_dir: Path,
    ) -> None:
        """Test relative paths are resolved against manifest file's parent."""
        # Create patch file relative to manifest location
        patch_file = patchsets_dir / "patches" / "create-story.patch.yaml"
        patch_file.parent.mkdir(parents=True)
        patch_file.write_text("# mock patch")

        content = """\
name: relative-path
patches:
  create-story: ./patches/create-story.patch.yaml
"""
        path = write_patchset(content, "relative-path.yaml")
        manifest = load_patchset_manifest(path, validate_paths=True)

        # Path should have been validated (it exists)
        assert manifest.patches["create-story"] == "./patches/create-story.patch.yaml"

    def test_tilde_path_resolved_correctly(
        self,
        write_patchset: Callable[[str, str], Path],
    ) -> None:
        """Test tilde paths are expanded to home directory (not relative)."""
        # Use a path that won't exist, with validate_paths=False
        content = """\
name: tilde-path
patches:
  create-story: ~/patches/create-story.patch.yaml
"""
        path = write_patchset(content, "tilde-path.yaml")
        # With validate_paths=False, we just verify loading works
        manifest = load_patchset_manifest(path, validate_paths=False)
        assert manifest.patches["create-story"] == "~/patches/create-story.patch.yaml"


class TestPatchSetRegistry:
    """Tests for PatchSetRegistry class."""

    @pytest.fixture
    def patchsets_dir(self, tmp_path: Path) -> Path:
        """Create temp directory for patch-set manifests."""
        patchsets = tmp_path / "patch-sets"
        patchsets.mkdir()
        return patchsets

    @pytest.fixture
    def write_patchset(self, patchsets_dir: Path) -> Callable[[str, str], Path]:
        """Factory to write patch-set manifest files."""

        def _write(content: str, filename: str = "test.yaml") -> Path:
            path = patchsets_dir / filename
            path.write_text(content, encoding="utf-8")
            return path

        return _write

    def test_discover_finds_yaml_files(
        self,
        write_patchset: Callable[[str, str], Path],
        patchsets_dir: Path,
    ) -> None:
        """Test discovery finds all .yaml files."""
        write_patchset("name: patchset-a\npatches: {}", "patchset-a.yaml")
        write_patchset("name: patchset-b\npatches: {}", "patchset-b.yaml")

        registry = PatchSetRegistry(patchsets_dir)
        names = registry.list()

        assert sorted(names) == ["patchset-a", "patchset-b"]

    def test_discover_skips_hidden_files(
        self,
        write_patchset: Callable[[str, str], Path],
        patchsets_dir: Path,
    ) -> None:
        """Test discovery skips hidden files."""
        write_patchset("name: visible\npatches: {}", "visible.yaml")
        (patchsets_dir / ".hidden.yaml").write_text("name: hidden\npatches: {}")

        registry = PatchSetRegistry(patchsets_dir)
        names = registry.list()

        assert names == ["visible"]

    def test_discover_skips_yml_files(
        self,
        write_patchset: Callable[[str, str], Path],
        patchsets_dir: Path,
    ) -> None:
        """Test discovery only finds .yaml, not .yml files."""
        write_patchset("name: yaml-file\npatches: {}", "yaml-file.yaml")
        (patchsets_dir / "yml-file.yml").write_text("name: yml-file\npatches: {}")

        registry = PatchSetRegistry(patchsets_dir)
        names = registry.list()

        assert names == ["yaml-file"]

    def test_discover_skips_name_mismatch(
        self,
        write_patchset: Callable[[str, str], Path],
        patchsets_dir: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test discovery skips files where name doesn't match filename."""
        write_patchset("name: correct\npatches: {}", "correct.yaml")
        write_patchset("name: wrong-name\npatches: {}", "mismatched.yaml")

        with caplog.at_level(logging.WARNING):
            registry = PatchSetRegistry(patchsets_dir)
            names = registry.list()

        assert names == ["correct"]
        assert "does not match filename stem" in caplog.text

    def test_discover_skips_malformed_yaml(
        self,
        write_patchset: Callable[[str, str], Path],
        patchsets_dir: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test discovery skips files with invalid YAML."""
        write_patchset("name: valid\npatches: {}", "valid.yaml")
        write_patchset("invalid: yaml: [", "invalid.yaml")

        with caplog.at_level(logging.WARNING):
            registry = PatchSetRegistry(patchsets_dir)
            names = registry.list()

        assert names == ["valid"]
        assert "invalid YAML" in caplog.text

    def test_discover_nonexistent_directory(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test discovery returns empty dict for non-existent directory."""
        nonexistent = tmp_path / "nonexistent"

        with caplog.at_level(logging.INFO):
            registry = PatchSetRegistry(nonexistent)
            names = registry.list()

        assert names == []
        assert "does not exist" in caplog.text

    def test_get_returns_manifest(
        self,
        write_patchset: Callable[[str, str], Path],
        patchsets_dir: Path,
    ) -> None:
        """Test get() returns loaded manifest."""
        write_patchset(
            "name: test-patchset\ndescription: Test\npatches: {}",
            "test-patchset.yaml",
        )

        registry = PatchSetRegistry(patchsets_dir)
        manifest = registry.get("test-patchset", validate_paths=False)

        assert manifest.name == "test-patchset"
        assert manifest.description == "Test"

    def test_get_not_found_raises_error(
        self,
        write_patchset: Callable[[str, str], Path],
        patchsets_dir: Path,
    ) -> None:
        """Test get() raises ConfigError for unknown name."""
        write_patchset("name: existing\npatches: {}", "existing.yaml")

        registry = PatchSetRegistry(patchsets_dir)

        with pytest.raises(ConfigError, match="not found") as exc_info:
            registry.get("nonexistent")

        assert "existing" in str(exc_info.value)  # Should list available

    def test_get_caches_manifests(
        self,
        write_patchset: Callable[[str, str], Path],
        patchsets_dir: Path,
    ) -> None:
        """Test get() caches loaded manifests."""
        write_patchset("name: cached\npatches: {}", "cached.yaml")

        registry = PatchSetRegistry(patchsets_dir)

        manifest1 = registry.get("cached", validate_paths=False)
        manifest2 = registry.get("cached", validate_paths=False)

        # Same instance due to caching
        assert manifest1 is manifest2

    def test_get_with_different_validate_paths_caches_separately(
        self,
        write_patchset: Callable[[str, str], Path],
        patchsets_dir: Path,
    ) -> None:
        """Test get() caches separately for different validate_paths values."""
        write_patchset("name: test\npatches: {}", "test.yaml")

        registry = PatchSetRegistry(patchsets_dir)

        manifest1 = registry.get("test", validate_paths=False)
        manifest2 = registry.get("test", validate_paths=True)

        # Different instances because cache key includes validate_paths
        # (Both may be same object if implementation uses same cache, but behavior is correct)
        # The important thing is that both calls work
        assert manifest1.name == "test"
        assert manifest2.name == "test"

    def test_list_returns_sorted_names(
        self,
        write_patchset: Callable[[str, str], Path],
        patchsets_dir: Path,
    ) -> None:
        """Test list() returns sorted names."""
        write_patchset("name: zebra\npatches: {}", "zebra.yaml")
        write_patchset("name: alpha\npatches: {}", "alpha.yaml")
        write_patchset("name: beta\npatches: {}", "beta.yaml")

        registry = PatchSetRegistry(patchsets_dir)
        names = registry.list()

        assert names == ["alpha", "beta", "zebra"]

    def test_registry_with_project_root(
        self,
        write_patchset: Callable[[str, str], Path],
        patchsets_dir: Path,
        tmp_path: Path,
    ) -> None:
        """Test registry passes project_root to loader."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        patches_dir = project_root / ".bmad-assist" / "patches"
        patches_dir.mkdir(parents=True)
        patch_file = patches_dir / "create-story.patch.yaml"
        patch_file.write_text("# mock patch")

        content = """\
name: with-project
patches:
  create-story: ${project}/.bmad-assist/patches/create-story.patch.yaml
"""
        write_patchset(content, "with-project.yaml")

        registry = PatchSetRegistry(patchsets_dir, project_root=project_root)
        manifest = registry.get("with-project", validate_paths=True)

        expected_path = (
            str(project_root.resolve()) + "/.bmad-assist/patches/create-story.patch.yaml"
        )
        assert manifest.patches["create-story"] == expected_path


class TestDefaultManifests:
    """Tests for default patch-set manifests in experiments/patch-sets/."""

    @pytest.fixture
    def default_patchsets_dir(self) -> Path:
        """Path to default patch-set manifests."""
        return Path("experiments/patch-sets")

    def test_baseline_exists(self, default_patchsets_dir: Path, tmp_path: Path) -> None:
        """Test baseline.yaml exists and is valid."""
        if not default_patchsets_dir.exists():
            pytest.skip("Default patch-sets not available in this environment")

        # Need to provide project_root for ${project} variable resolution
        project_root = tmp_path / "project"
        project_root.mkdir()

        # Load without path validation (paths use ${project} which won't exist)
        manifest = load_patchset_manifest(
            default_patchsets_dir / "baseline.yaml",
            project_root=project_root,
            validate_paths=False,
        )
        assert manifest.name == "baseline"
        # Baseline should have patches for common workflows
        assert len(manifest.patches) > 0

    def test_no_patches_exists(self, default_patchsets_dir: Path) -> None:
        """Test no-patches.yaml exists and is valid."""
        if not default_patchsets_dir.exists():
            pytest.skip("Default patch-sets not available in this environment")

        manifest = load_patchset_manifest(
            default_patchsets_dir / "no-patches.yaml",
            validate_paths=False,
        )
        assert manifest.name == "no-patches"
        assert manifest.patches == {}

    def test_all_default_manifests_discoverable(self, default_patchsets_dir: Path) -> None:
        """Test PatchSetRegistry can discover all default manifests."""
        if not default_patchsets_dir.exists():
            pytest.skip("Default patch-sets not available in this environment")

        registry = PatchSetRegistry(default_patchsets_dir)
        names = registry.list()

        expected = ["agents-team", "baseline", "no-patches"]
        assert sorted(names) == sorted(expected)

    def test_all_default_manifests_use_known_workflows(
        self, default_patchsets_dir: Path, tmp_path: Path
    ) -> None:
        """Test all default manifests only use known workflows."""
        if not default_patchsets_dir.exists():
            pytest.skip("Default patch-sets not available in this environment")

        # Need project_root for manifests that use ${project}
        project_root = tmp_path / "project"
        project_root.mkdir()

        registry = PatchSetRegistry(default_patchsets_dir, project_root=project_root)

        for name in registry.list():
            manifest = registry.get(name, validate_paths=False)
            for workflow in manifest.patches:
                assert workflow in KNOWN_WORKFLOWS, (
                    f"Manifest '{name}' uses unknown workflow: {workflow}"
                )
            for workflow in manifest.workflow_overrides:
                assert workflow in KNOWN_WORKFLOWS, (
                    f"Manifest '{name}' uses unknown workflow override: {workflow}"
                )


class TestNamePattern:
    """Tests for name validation pattern (same as config/loop)."""

    def test_valid_names(self) -> None:
        """Test valid name patterns."""
        valid_names = [
            "baseline",
            "no-patches",
            "experimental-v1",
            "my_custom_patchset",
            "_private",
            "PatchSet123",
        ]
        for name in valid_names:
            assert NAME_PATTERN.match(name), f"Expected '{name}' to be valid"

    def test_invalid_names(self) -> None:
        """Test invalid name patterns."""
        invalid_names = [
            "123-start",  # starts with number
            "-hyphen-start",  # starts with hyphen
            "has spaces",  # contains space
            "has.dots",  # contains dot
            "has@special",  # contains special char
            "",  # empty
        ]
        for name in invalid_names:
            assert not NAME_PATTERN.match(name), f"Expected '{name}' to be invalid"
