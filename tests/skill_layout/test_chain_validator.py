"""Tests for :mod:`bmad_assist.skill_layout.chain_validator`.

Covers:

* All bundled skills validate cleanly (regression test for the entire
  shipped bundle — this is the test that would have caught the
  ``{skill-root}`` truncation bug).
* A synthetic skill with a missing follow-on step is reported.
* A synthetic skill with an unresolved frontmatter token is reported.
* Single-file skills (no ``steps-?/`` dirs) validate as no-op.
* End-of-chain (no ``nextStepFile``) validates cleanly.
"""

from __future__ import annotations

from pathlib import Path

from bmad_assist.skill_layout.chain_validator import (
    ChainValidationError,
    validate_skill_chains,
)
from bmad_assist.skills import get_bundled_skill_dir, list_bundled_skills

# --- Real bundled skills -----------------------------------------------------


def test_all_bundled_skills_have_intact_chains() -> None:
    """Every shipped bundled skill walks its tri-modal chains cleanly.

    This is the regression test for the entire bundle — any future
    breakage to ``{skill-root}`` substitution, a renamed step file, or
    a typo in a ``nextStepFile`` reference will fail this test.
    """
    failures: list[ChainValidationError] = []
    for skill_id in list_bundled_skills():
        skill_dir = get_bundled_skill_dir(skill_id)
        assert skill_dir is not None, f"bundled skill {skill_id!r} has no dir"
        failures.extend(validate_skill_chains(skill_dir))

    assert failures == [], "Broken chains in bundled skills:\n" + "\n".join(
        f.format() for f in failures
    )


# --- Synthetic skill helpers -------------------------------------------------


def _write_skill_md(skill_root: Path) -> None:
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: synthetic\ndescription: test\n---\n# Synthetic\n",
        encoding="utf-8",
    )


def _write_step(step_path: Path, *, next_step: str | None) -> None:
    step_path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter_lines = [
        "---",
        f"name: '{step_path.stem}'",
        "description: 'synthetic step'",
    ]
    if next_step is not None:
        frontmatter_lines.append(f"nextStepFile: '{next_step}'")
    frontmatter_lines.append("---")
    frontmatter_lines.append("")
    frontmatter_lines.append("# Step body")
    step_path.write_text("\n".join(frontmatter_lines) + "\n", encoding="utf-8")


# --- Synthetic skill: broken chains ------------------------------------------


def test_missing_next_step_file_is_reported(tmp_path: Path) -> None:
    """A nextStepFile pointing at a non-existent file produces one error."""
    skill = tmp_path / "synthetic-skill"
    _write_skill_md(skill)
    _write_step(
        skill / "steps-c" / "step-01-start.md",
        next_step="{skill-root}/steps-c/step-02-missing.md",
    )

    errors = validate_skill_chains(skill)

    assert len(errors) == 1
    err = errors[0]
    assert err.skill_dir == skill
    assert err.entry_step.name == "step-01-start.md"
    assert err.broken_step is not None
    assert err.broken_step.name == "step-01-start.md"
    assert err.next_step_ref == "{skill-root}/steps-c/step-02-missing.md"
    assert "does not exist" in err.reason


def test_unresolved_token_is_reported(tmp_path: Path) -> None:
    """A nextStepFile with a non-{skill-root} token raises CompilerError."""
    skill = tmp_path / "synthetic-skill"
    _write_skill_md(skill)
    _write_step(
        skill / "steps-c" / "step-01-start.md",
        next_step="{bogus-token}/foo.md",
    )

    errors = validate_skill_chains(skill)

    assert len(errors) == 1
    err = errors[0]
    assert err.entry_step.name == "step-01-start.md"
    assert "{bogus-token}" in err.reason or "Unresolved token" in err.reason


# --- Synthetic skill: clean chains -------------------------------------------


def test_chain_with_real_followup_validates_clean(tmp_path: Path) -> None:
    """Two-step chain where step-02 exists is reported as no errors."""
    skill = tmp_path / "synthetic-skill"
    _write_skill_md(skill)
    _write_step(
        skill / "steps-c" / "step-01-start.md",
        next_step="{skill-root}/steps-c/step-02-end.md",
    )
    _write_step(skill / "steps-c" / "step-02-end.md", next_step=None)

    assert validate_skill_chains(skill) == []


def test_single_step_terminus_validates_clean(tmp_path: Path) -> None:
    """Entry step with no nextStepFile is a legitimate end-of-chain."""
    skill = tmp_path / "synthetic-skill"
    _write_skill_md(skill)
    _write_step(skill / "steps-v" / "step-01-only.md", next_step=None)

    assert validate_skill_chains(skill) == []


def test_skill_with_no_step_dirs_is_noop(tmp_path: Path) -> None:
    """Single-file SKILL.md skills (no steps-?/) validate vacuously."""
    skill = tmp_path / "single-file-skill"
    _write_skill_md(skill)

    assert validate_skill_chains(skill) == []


def test_nonexistent_skill_dir_returns_empty(tmp_path: Path) -> None:
    """A missing skill dir is not a chain concern; return [] not raise."""
    assert validate_skill_chains(tmp_path / "does-not-exist") == []
