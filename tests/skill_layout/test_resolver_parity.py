"""Parity tests against the live BMAD resolver scripts.

We invoke ``_bmad/scripts/resolve_customization.py`` and
``_bmad/scripts/resolve_config.py`` via subprocess and compare their
JSON output byte-for-byte against our pure-Python reimplementation.

Tests are skipped (not failed) when the BMAD scripts are not present —
this keeps the suite green on installs that lack the v6.4+ layout.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bmad_assist.skill_layout import (
    ResolverError,
    resolve_central_config,
    resolve_customization,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BMAD_DIR = REPO_ROOT / "_bmad"
RESOLVE_CUSTOMIZATION = BMAD_DIR / "scripts" / "resolve_customization.py"
RESOLVE_CONFIG = BMAD_DIR / "scripts" / "resolve_config.py"
SKILLS_ROOT = REPO_ROOT / ".claude" / "skills"


def _live_skills() -> list[Path]:
    """Return ``SKILL.md``-bearing directories that *also* have a ``customize.toml``.

    Only those have a customization surface to merge. Skills without
    one cause both implementations to error out — that error-path
    parity is verified separately by
    :func:`test_skills_without_customize_toml_both_error`.
    """
    if not SKILLS_ROOT.exists():
        return []
    return sorted(
        p
        for p in SKILLS_ROOT.iterdir()
        if (p / "SKILL.md").exists() and (p / "customize.toml").exists()
    )


def _live_skills_without_customize() -> list[Path]:
    """Return SKILL.md-bearing directories that lack a ``customize.toml``."""
    if not SKILLS_ROOT.exists():
        return []
    return sorted(
        p
        for p in SKILLS_ROOT.iterdir()
        if (p / "SKILL.md").exists() and not (p / "customize.toml").exists()
    )


def _serialize(obj: object) -> str:
    """Serialize using the BMAD script's exact stdout formatting."""
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def _run_bmad_customization(skill_dir: Path, keys: list[str] | None = None) -> str:
    """Invoke the live ``resolve_customization.py`` and return its stdout."""
    cmd: list[str] = [sys.executable, str(RESOLVE_CUSTOMIZATION), "--skill", str(skill_dir)]
    for key in keys or []:
        cmd.extend(["--key", key])
    completed = subprocess.run(  # noqa: S603 — controlled inputs
        cmd,
        capture_output=True,
        text=True,
        check=True,
        cwd=str(REPO_ROOT),
    )
    return completed.stdout


def _run_bmad_config(project_root: Path, keys: list[str] | None = None) -> str:
    """Invoke the live ``resolve_config.py`` and return its stdout."""
    cmd: list[str] = [sys.executable, str(RESOLVE_CONFIG), "--project-root", str(project_root)]
    for key in keys or []:
        cmd.extend(["--key", key])
    completed = subprocess.run(  # noqa: S603 — controlled inputs
        cmd,
        capture_output=True,
        text=True,
        check=True,
        cwd=str(REPO_ROOT),
    )
    return completed.stdout


# --------------------------------------------------------------------------- #
# resolve_customization parity                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not RESOLVE_CUSTOMIZATION.exists(),
    reason="BMAD resolve_customization.py not present in this checkout",
)
@pytest.mark.parametrize(
    "skill_dir",
    _live_skills() or [pytest.param(None, marks=pytest.mark.skip("no live skills found"))],
    ids=lambda p: p.name if isinstance(p, Path) else "no-skills",
)
def test_full_dump_parity_for_each_skill(skill_dir: Path) -> None:
    """Compare full ``customize.toml`` dumps for every bundled skill."""
    expected = _run_bmad_customization(skill_dir)
    ours = _serialize(resolve_customization(skill_dir, project_root=REPO_ROOT))
    assert ours == expected


@pytest.mark.skipif(
    not RESOLVE_CUSTOMIZATION.exists(),
    reason="BMAD resolve_customization.py not present",
)
@pytest.mark.parametrize(
    "skill_dir",
    _live_skills_without_customize()
    or [pytest.param(None, marks=pytest.mark.skip("no non-customizable skills"))],
    ids=lambda p: p.name if isinstance(p, Path) else "n/a",
)
def test_skills_without_customize_toml_both_error(skill_dir: Path) -> None:
    """Skills lacking ``customize.toml`` must error in both implementations."""
    bmad_completed = subprocess.run(  # noqa: S603 — controlled inputs
        [sys.executable, str(RESOLVE_CUSTOMIZATION), "--skill", str(skill_dir)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert bmad_completed.returncode != 0, "BMAD script unexpectedly succeeded"
    with pytest.raises(ResolverError):
        resolve_customization(skill_dir, project_root=REPO_ROOT)


@pytest.mark.skipif(
    not RESOLVE_CUSTOMIZATION.exists(),
    reason="BMAD resolve_customization.py not present",
)
def test_workflow_key_filter_parity() -> None:
    """The ``--key workflow`` filter — the path real SKILL.md files use."""
    skills = _live_skills()
    if not skills:
        pytest.skip("no live skills found")
    for skill in skills[:5]:  # sampled — full set already covered above
        expected = _run_bmad_customization(skill, keys=["workflow"])
        ours = _serialize(
            resolve_customization(skill, project_root=REPO_ROOT, keys=["workflow"])
        )
        assert ours == expected, f"mismatch for {skill.name}"


@pytest.mark.skipif(
    not RESOLVE_CUSTOMIZATION.exists(),
    reason="BMAD resolve_customization.py not present",
)
def test_dotted_key_filter_parity() -> None:
    """Dotted keys must round-trip identically."""
    skills = _live_skills()
    if not skills:
        pytest.skip("no live skills found")
    skill = next((s for s in skills if s.name == "bmad-create-story"), skills[0])
    expected = _run_bmad_customization(skill, keys=["workflow.persistent_facts"])
    ours = _serialize(
        resolve_customization(
            skill,
            project_root=REPO_ROOT,
            keys=["workflow.persistent_facts"],
        )
    )
    assert ours == expected


@pytest.mark.skipif(
    not RESOLVE_CUSTOMIZATION.exists(),
    reason="BMAD resolve_customization.py not present",
)
def test_missing_key_is_omitted_silently_parity() -> None:
    """Unknown ``--key`` entries are silently dropped (BMAD ``_MISSING`` semantics)."""
    skills = _live_skills()
    if not skills:
        pytest.skip("no live skills found")
    skill = skills[0]
    expected = _run_bmad_customization(skill, keys=["does.not.exist"])
    ours = _serialize(
        resolve_customization(skill, project_root=REPO_ROOT, keys=["does.not.exist"])
    )
    assert ours == expected
    assert json.loads(expected) == {}


@pytest.mark.skipif(
    not RESOLVE_CUSTOMIZATION.exists(),
    reason="BMAD resolve_customization.py not present",
)
def test_empty_customize_toml_parity(tmp_path: Path) -> None:
    """An empty ``customize.toml`` should yield ``{}`` from both implementations."""
    skill = tmp_path / "bmad-empty"
    skill.mkdir()
    (skill / "customize.toml").write_text("", encoding="utf-8")
    expected = _run_bmad_customization(skill)
    ours = _serialize(resolve_customization(skill, project_root=tmp_path))
    assert ours == expected


@pytest.mark.skipif(
    not RESOLVE_CUSTOMIZATION.exists(),
    reason="BMAD resolve_customization.py not present",
)
def test_with_team_and_user_overrides_parity(tmp_path: Path) -> None:
    """A synthetic three-layer override stack must match the BMAD script."""
    skill = tmp_path / "bmad-test-skill"
    skill.mkdir()
    (skill / "customize.toml").write_text(
        '[workflow]\n'
        'persistent_facts = ["base fact"]\n'
        'on_complete = ""\n'
        '[[agent.menu]]\ncode = "help"\ndescription = "Help"\n'
        '[[agent.menu]]\ncode = "quit"\ndescription = "Quit"\n',
        encoding="utf-8",
    )
    custom = tmp_path / "_bmad" / "custom"
    custom.mkdir(parents=True)
    (custom / "bmad-test-skill.toml").write_text(
        '[workflow]\n'
        'persistent_facts = ["team fact"]\n'
        'on_complete = "team-complete"\n'
        '[[agent.menu]]\ncode = "help"\ndescription = "Help (team)"\n',
        encoding="utf-8",
    )
    (custom / "bmad-test-skill.user.toml").write_text(
        '[workflow]\n'
        'persistent_facts = ["user fact"]\n'
        '[[agent.menu]]\ncode = "extra"\ndescription = "Personal"\n',
        encoding="utf-8",
    )
    expected = _run_bmad_customization(skill)
    ours = _serialize(resolve_customization(skill, project_root=tmp_path))
    assert ours == expected


# --------------------------------------------------------------------------- #
# resolve_central_config parity                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not RESOLVE_CONFIG.exists(),
    reason="BMAD resolve_config.py not present",
)
def test_central_config_full_dump_parity() -> None:
    """The full central-config dump must match the BMAD script byte-for-byte."""
    expected = _run_bmad_config(REPO_ROOT)
    ours = _serialize(resolve_central_config(REPO_ROOT))
    assert ours == expected


@pytest.mark.skipif(
    not RESOLVE_CONFIG.exists(),
    reason="BMAD resolve_config.py not present",
)
def test_central_config_keyed_filter_parity() -> None:
    """A representative ``--key`` filter against the live central config."""
    expected = _run_bmad_config(REPO_ROOT, keys=["installation"])
    ours = _serialize(resolve_central_config(REPO_ROOT, keys=["installation"]))
    assert ours == expected


@pytest.mark.skipif(
    not RESOLVE_CONFIG.exists(),
    reason="BMAD resolve_config.py not present",
)
def test_central_config_fixture_parity() -> None:
    """Parity against the synthetic central-config fixture."""
    fixture = Path(__file__).parent / "fixtures" / "sample_central_config"
    expected = _run_bmad_config(fixture)
    ours = _serialize(resolve_central_config(fixture))
    assert ours == expected
