"""Tests for ``bmad_assist.skill_layout.probe``."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from bmad_assist.skill_layout import ManifestSkill, SkillNotFound, find_skill


def _seed_skill(root: Path, prefix: str, skill_id: str, body: str = "x") -> Path:
    """Create a SKILL.md under ``root/prefix/skill_id`` with the given body."""
    target = root / prefix / skill_id / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def test_finds_in_claude_skills(tmp_path: Path) -> None:
    """``.claude/skills/`` is the first probe target."""
    expected = _seed_skill(tmp_path, ".claude/skills", "bmad-foo")
    assert find_skill("bmad-foo", tmp_path) == expected.resolve()


def test_finds_in_agents_skills_when_claude_missing(tmp_path: Path) -> None:
    """``.agents/skills/`` is the second probe target."""
    expected = _seed_skill(tmp_path, ".agents/skills", "bmad-foo")
    assert find_skill("bmad-foo", tmp_path) == expected.resolve()


def test_prefers_claude_over_agents(tmp_path: Path) -> None:
    """When both mirrors carry the file, the Claude one wins."""
    claude_path = _seed_skill(tmp_path, ".claude/skills", "bmad-foo", body="claude")
    _seed_skill(tmp_path, ".agents/skills", "bmad-foo", body="claude")  # same body
    assert find_skill("bmad-foo", tmp_path) == claude_path.resolve()


def test_falls_back_to_manifest_canonical(tmp_path: Path) -> None:
    """Without a mirror, the manifest's canonical path is the fallback."""
    target = tmp_path / "_bmad" / "core" / "bmad-foo" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    manifest = {
        "bmad-foo": ManifestSkill(
            skill_id="bmad-foo",
            module="core",
            canonical_path="_bmad/core/bmad-foo/SKILL.md",
            description="",
        )
    }
    assert find_skill("bmad-foo", tmp_path, manifest=manifest) == target.resolve()


def test_manifest_pointing_at_directory_form(tmp_path: Path) -> None:
    """A manifest path pointing at the directory still resolves to ``SKILL.md``."""
    target = tmp_path / "_bmad" / "core" / "bmad-foo" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    manifest = {
        "bmad-foo": ManifestSkill(
            skill_id="bmad-foo",
            module="core",
            canonical_path="_bmad/core/bmad-foo",
            description="",
        )
    }
    assert find_skill("bmad-foo", tmp_path, manifest=manifest) == target.resolve()


def test_raises_skill_not_found_with_paths(tmp_path: Path) -> None:
    """Missing skills produce an error message listing every probed path."""
    with pytest.raises(SkillNotFound) as exc_info:
        find_skill("bmad-missing", tmp_path)
    message = str(exc_info.value)
    assert ".claude/skills/bmad-missing/SKILL.md" in message
    assert ".agents/skills/bmad-missing/SKILL.md" in message


def test_raises_skill_not_found_includes_manifest_path(tmp_path: Path) -> None:
    """The manifest's canonical path is also listed in the not-found error."""
    manifest = {
        "bmad-missing": ManifestSkill(
            skill_id="bmad-missing",
            module="core",
            canonical_path="_bmad/core/bmad-missing/SKILL.md",
            description="",
        )
    }
    with pytest.raises(SkillNotFound) as exc_info:
        find_skill("bmad-missing", tmp_path, manifest=manifest)
    assert "_bmad/core/bmad-missing/SKILL.md" in str(exc_info.value)


def test_warns_on_checksum_mismatch(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Diverging mirror contents emit a warning."""
    _seed_skill(tmp_path, ".claude/skills", "bmad-foo", body="aaa")
    _seed_skill(tmp_path, ".agents/skills", "bmad-foo", body="bbb")
    with caplog.at_level(logging.WARNING, logger="bmad_assist.skill_layout.probe"):
        find_skill("bmad-foo", tmp_path)
    assert any("differs" in record.getMessage() for record in caplog.records)


def test_no_warning_when_mirrors_match(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Identical mirrors must not produce any warning noise."""
    _seed_skill(tmp_path, ".claude/skills", "bmad-foo", body="same")
    _seed_skill(tmp_path, ".agents/skills", "bmad-foo", body="same")
    with caplog.at_level(logging.WARNING, logger="bmad_assist.skill_layout.probe"):
        find_skill("bmad-foo", tmp_path)
    assert not [r for r in caplog.records if "differs" in r.getMessage()]
