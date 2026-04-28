"""Tests for ``bmad_assist.skill_layout.detection`` after Phase 6.

Phase 6 collapsed routing onto the v6.4+ skill layout, so
:func:`detect_layout` is now a permanent ``"new"`` shim. The function is
preserved as a compatibility surface; the tests below pin the new
contract.
"""

from __future__ import annotations

from pathlib import Path

from bmad_assist.skill_layout import detect_layout

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_repo_returns_new_layout() -> None:
    """The bmad-assist repo resolves to 'new' after Phase 6."""
    assert detect_layout(REPO_ROOT) == "new"


def test_arbitrary_directory_returns_new_layout(tmp_path: Path) -> None:
    """A bare directory still resolves to 'new' — Phase 6 dropped 'old'."""
    assert detect_layout(tmp_path) == "new"


def test_resolver_script_signal_returns_new(tmp_path: Path) -> None:
    """A project with the v6.4+ resolver script still classifies as 'new'."""
    scripts = tmp_path / "_bmad" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "resolve_customization.py").write_text("# stub", encoding="utf-8")
    assert detect_layout(tmp_path) == "new"


def test_legacy_workflow_dir_still_returns_new(tmp_path: Path) -> None:
    """Even a legacy ``_bmad/...`` install resolves to 'new' after Phase 6."""
    legacy = tmp_path / "_bmad" / "bmm" / "workflows" / "4-implementation" / "create-story"
    legacy.mkdir(parents=True)
    (legacy / "workflow.yaml").write_text("name: create-story\n", encoding="utf-8")
    assert detect_layout(tmp_path) == "new"
