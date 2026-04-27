"""Tests for ``bmad_assist.skill_layout.manifest``."""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad_assist.skill_layout import ManifestError, ManifestSkill, read_skill_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"


def test_reads_sample_fixture(tmp_path: Path) -> None:
    """The sample fixture exercises the canonical column names."""
    project = tmp_path / "proj"
    (project / "_bmad" / "_config").mkdir(parents=True)
    (project / "_bmad" / "_config" / "skill-manifest.csv").write_text(
        (FIXTURES / "sample_manifest.csv").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    skills = read_skill_manifest(project)
    assert set(skills) == {"skill-alpha", "skill-beta", "skill-gamma"}
    assert skills["skill-alpha"] == ManifestSkill(
        skill_id="skill-alpha",
        module="sample",
        canonical_path="_bmad/sample/skill-alpha/SKILL.md",
        description="First sample skill",
    )
    assert "comma in description" in skills["skill-gamma"].description


def test_reads_real_manifest_in_repo() -> None:
    """Spot-check the bundled real manifest."""
    if not (REPO_ROOT / "_bmad" / "_config" / "skill-manifest.csv").exists():
        pytest.skip("real manifest not present in this checkout")
    skills = read_skill_manifest(REPO_ROOT)
    assert "bmad-create-story" in skills
    cs = skills["bmad-create-story"]
    assert cs.module == "bmm"
    assert cs.canonical_path.endswith("SKILL.md")
    if "bmad-testarch-atdd" in skills:
        assert skills["bmad-testarch-atdd"].module == "tea"


def test_missing_file_raises(tmp_path: Path) -> None:
    """An absent manifest raises :class:`ManifestError`."""
    with pytest.raises(ManifestError, match="not found"):
        read_skill_manifest(tmp_path)


def test_tolerates_extra_columns(tmp_path: Path) -> None:
    """Unknown columns are silently dropped."""
    project = tmp_path / "proj"
    (project / "_bmad" / "_config").mkdir(parents=True)
    (project / "_bmad" / "_config" / "skill-manifest.csv").write_text(
        "name,description,module,path,extra,another_extra\n"
        "skill-x,Desc x,m,_bmad/x/SKILL.md,ignored,also-ignored\n",
        encoding="utf-8",
    )
    skills = read_skill_manifest(project)
    assert skills["skill-x"].canonical_path == "_bmad/x/SKILL.md"
    assert skills["skill-x"].description == "Desc x"


def test_missing_required_column_raises(tmp_path: Path) -> None:
    """A header missing one of the four required columns raises."""
    project = tmp_path / "proj"
    (project / "_bmad" / "_config").mkdir(parents=True)
    (project / "_bmad" / "_config" / "skill-manifest.csv").write_text(
        "name,description,module\nfoo,bar,baz\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="canonical path"):
        read_skill_manifest(project)


def test_blank_rows_are_skipped(tmp_path: Path) -> None:
    """Trailing blank rows do not crash the reader."""
    project = tmp_path / "proj"
    (project / "_bmad" / "_config").mkdir(parents=True)
    (project / "_bmad" / "_config" / "skill-manifest.csv").write_text(
        "name,description,module,path\n"
        "skill-a,Desc,m,p\n"
        ",,,\n"
        "skill-b,Desc,m,p\n",
        encoding="utf-8",
    )
    skills = read_skill_manifest(project)
    assert set(skills) == {"skill-a", "skill-b"}
