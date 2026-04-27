"""Tests for layout-aware workflow discovery (Phase 4).

Verifies that :func:`discover_workflow_source` returns the unified
:class:`WorkflowSource` shape and selects the correct branch based on
the project layout.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad_assist.compiler.types import WorkflowSource
from bmad_assist.compiler.workflow_discovery import (
    WORKFLOW_TO_SKILL_ID,
    discover_workflow_dir,
    discover_workflow_source,
)


def _make_legacy_workflow(project_root: Path, name: str, sub: str | None = None) -> Path:
    sub = sub or name
    workflow_dir = project_root / "_bmad" / "bmm" / "workflows" / "4-implementation" / sub
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "workflow.yaml").write_text(f"name: {name}\n", encoding="utf-8")
    (workflow_dir / "workflow.md").write_text(f"# {name}\n", encoding="utf-8")
    return workflow_dir


def _make_new_skill(project_root: Path, skill_id: str, mirror: str = ".claude/skills") -> Path:
    skill_dir = project_root / mirror / skill_id
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {skill_id}\ndescription: test\n---\n\n# {skill_id}\n",
        encoding="utf-8",
    )
    (skill_dir / "customize.toml").write_text("", encoding="utf-8")
    return skill_dir


# --- WorkflowSource shape ----------------------------------------------------


def test_source_carries_skill_id_and_layout_for_new(tmp_path: Path) -> None:
    """A v6.4+ skill match returns layout='new' and the canonical skill_id."""
    skill_dir = _make_new_skill(tmp_path, "bmad-create-story")

    source = discover_workflow_source("create-story", tmp_path)
    assert source is not None
    assert isinstance(source, WorkflowSource)
    assert source.workflow_name == "create-story"
    assert source.skill_id == "bmad-create-story"
    assert source.layout == "new"
    assert source.path == skill_dir


def test_source_carries_layout_old_for_legacy(tmp_path: Path) -> None:
    """A legacy match returns layout='old' and includes the skill_id mapping."""
    workflow_dir = _make_legacy_workflow(tmp_path, "create-story")

    source = discover_workflow_source("create-story", tmp_path, layout="old")
    assert source is not None
    assert source.layout == "old"
    assert source.path == workflow_dir
    # skill_id mapping is still populated for downstream telemetry.
    assert source.skill_id == "bmad-create-story"


def test_source_marks_bundled_when_no_install(tmp_path: Path) -> None:
    """A bare project falls back to bundled with layout='bundled'."""
    source = discover_workflow_source("create-story", tmp_path, layout="old")
    assert source is not None
    assert source.layout == "bundled"
    assert source.path.is_dir()


def test_source_marks_override_when_present(tmp_path: Path) -> None:
    """A ``.bmad-assist/workflows/<name>/`` override beats any other source."""
    override = tmp_path / ".bmad-assist" / "workflows" / "create-story"
    override.mkdir(parents=True)
    (override / "workflow.yaml").write_text("name: create-story\n", encoding="utf-8")
    # Even with a competing legacy install present, override wins.
    _make_legacy_workflow(tmp_path, "create-story")

    source = discover_workflow_source("create-story", tmp_path)
    assert source is not None
    assert source.layout == "override"
    assert source.path == override


# --- Layout selection --------------------------------------------------------


def test_new_layout_preferred_when_skill_present(tmp_path: Path) -> None:
    """When both a skill and a legacy workflow exist, new layout wins on 'new'."""
    skill_dir = _make_new_skill(tmp_path, "bmad-create-story")
    _make_legacy_workflow(tmp_path, "create-story")

    source = discover_workflow_source("create-story", tmp_path, layout="new")
    assert source is not None
    assert source.layout == "new"
    assert source.path == skill_dir


def test_legacy_layout_skips_new_probe(tmp_path: Path) -> None:
    """An explicit layout='old' must not pick up a v6.4+ skill mirror."""
    _make_new_skill(tmp_path, "bmad-create-story")
    legacy_dir = _make_legacy_workflow(tmp_path, "create-story")

    source = discover_workflow_source("create-story", tmp_path, layout="old")
    assert source is not None
    assert source.layout == "old"
    assert source.path == legacy_dir


def test_new_layout_falls_through_to_legacy(tmp_path: Path) -> None:
    """When new-layout probe misses, we still match the legacy workflow."""
    legacy_dir = _make_legacy_workflow(tmp_path, "create-story")

    source = discover_workflow_source("create-story", tmp_path, layout="new")
    assert source is not None
    assert source.layout == "old"
    assert source.path == legacy_dir


def test_new_layout_uses_agents_mirror_when_claude_missing(tmp_path: Path) -> None:
    """``.agents/skills`` is probed when ``.claude/skills`` lacks the skill."""
    skill_dir = _make_new_skill(tmp_path, "bmad-create-story", mirror=".agents/skills")

    source = discover_workflow_source("create-story", tmp_path, layout="new")
    assert source is not None
    assert source.layout == "new"
    assert source.path == skill_dir


# --- Equivalence with legacy callers ----------------------------------------


@pytest.mark.parametrize(
    "workflow_name",
    [
        "create-story",
        "dev-story",
        "retrospective",
        "testarch-atdd",
        "testarch-trace",
    ],
)
def test_known_workflows_have_skill_id_mapping(workflow_name: str) -> None:
    """Every standard workflow we ship has a corresponding skill id mapping."""
    assert workflow_name in WORKFLOW_TO_SKILL_ID
    assert WORKFLOW_TO_SKILL_ID[workflow_name].startswith("bmad-")


def test_discover_workflow_dir_returns_same_path(tmp_path: Path) -> None:
    """Backward-compat: discover_workflow_dir == discover_workflow_source().path."""
    legacy_dir = _make_legacy_workflow(tmp_path, "create-story")

    direct = discover_workflow_dir("create-story", tmp_path)
    source = discover_workflow_source("create-story", tmp_path)
    assert direct == legacy_dir
    assert source is not None and source.path == direct


def test_custom_workflow_always_bundled(tmp_path: Path) -> None:
    """Custom workflows ignore project install state and use bundled."""
    # Even with both layouts present, custom workflow goes bundled.
    _make_legacy_workflow(tmp_path, "validate-story")
    _make_new_skill(tmp_path, "bmad-validate-story")

    source = discover_workflow_source("validate-story", tmp_path)
    assert source is not None
    assert source.layout == "bundled"
