"""Tests for workflow discovery.

:func:`discover_workflow_source` probes only:

1. ``.bmad-assist/workflows/<name>/`` override
2. ``.claude/skills/<bmad-id>/`` and ``.agents/skills/<bmad-id>/``
3. ``_bmad/...`` legacy install
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad_assist.compiler.core import WORKFLOW_REGISTRY
from bmad_assist.compiler.types import WorkflowSource
from bmad_assist.compiler.workflow_discovery import discover_workflow_source


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

    source = discover_workflow_source("bmad-create-story", tmp_path)
    assert source is not None
    assert isinstance(source, WorkflowSource)
    assert source.workflow_name == "bmad-create-story"
    assert source.skill_id == "bmad-create-story"
    assert source.layout == "new"
    assert source.path == skill_dir


def test_source_carries_layout_old_for_legacy(tmp_path: Path) -> None:
    """A legacy match returns layout='old' and includes the skill_id mapping.

    Legacy ``_bmad/...`` installs use the short BMAD directory name
    (``create-story``); ``_probe_legacy_user_install`` strips the
    ``bmad-`` prefix as a fallback so canonical names still resolve.
    """
    workflow_dir = _make_legacy_workflow(tmp_path, "create-story")

    source = discover_workflow_source("bmad-create-story", tmp_path)
    assert source is not None
    assert source.layout == "old"
    assert source.path == workflow_dir
    assert source.skill_id == "bmad-create-story"


def test_no_install_returns_none(tmp_path: Path) -> None:
    """A bare project (no override, no skill, no legacy install) returns None."""
    source = discover_workflow_source("bmad-create-story", tmp_path)
    assert source is None


def test_source_marks_override_when_present(tmp_path: Path) -> None:
    """A ``.bmad-assist/workflows/<name>/`` override beats any other source."""
    override = tmp_path / ".bmad-assist" / "workflows" / "bmad-create-story"
    override.mkdir(parents=True)
    (override / "workflow.yaml").write_text("name: bmad-create-story\n", encoding="utf-8")
    # Even with a competing legacy install present, override wins.
    _make_legacy_workflow(tmp_path, "create-story")

    source = discover_workflow_source("bmad-create-story", tmp_path)
    assert source is not None
    assert source.layout == "override"
    assert source.path == override


# --- Probe order -------------------------------------------------------------


def test_new_layout_preferred_over_legacy(tmp_path: Path) -> None:
    """When both a skill and a legacy workflow exist, new layout wins."""
    skill_dir = _make_new_skill(tmp_path, "bmad-create-story")
    _make_legacy_workflow(tmp_path, "create-story")

    source = discover_workflow_source("bmad-create-story", tmp_path)
    assert source is not None
    assert source.layout == "new"
    assert source.path == skill_dir


def test_falls_through_to_legacy_when_no_skill(tmp_path: Path) -> None:
    """When the new-layout probe misses, the legacy install is used."""
    legacy_dir = _make_legacy_workflow(tmp_path, "create-story")

    source = discover_workflow_source("bmad-create-story", tmp_path)
    assert source is not None
    assert source.layout == "old"
    assert source.path == legacy_dir


def test_uses_agents_mirror_when_claude_missing(tmp_path: Path) -> None:
    """``.agents/skills`` is probed when ``.claude/skills`` lacks the skill."""
    skill_dir = _make_new_skill(tmp_path, "bmad-create-story", mirror=".agents/skills")

    source = discover_workflow_source("bmad-create-story", tmp_path)
    assert source is not None
    assert source.layout == "new"
    assert source.path == skill_dir


# --- Registry -----------------------------------------------------------------


@pytest.mark.parametrize(
    "workflow_name",
    [
        "bmad-create-story",
        "bmad-dev-story",
        "bmad-retrospective",
        "bmad-testarch-atdd",
        "bmad-testarch-trace",
    ],
)
def test_known_workflows_have_registry_entry(workflow_name: str) -> None:
    """Every standard workflow we ship is registered in WORKFLOW_REGISTRY."""
    assert workflow_name in WORKFLOW_REGISTRY
    assert WORKFLOW_REGISTRY[workflow_name] == workflow_name
