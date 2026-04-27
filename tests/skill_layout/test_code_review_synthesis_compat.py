"""Side-by-side: legacy vs skill-layout code-review-synthesis compile.

Multi-LLM aggregation orphan with no patch on either side. Mirrors
:mod:`tests.skill_layout.test_validate_story_synthesis_compat`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext
from bmad_assist.validation.anonymizer import AnonymizedValidation

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-code-review-synthesis"
LEGACY_WORKFLOW = REPO_ROOT / "src" / "bmad_assist" / "workflows" / "code-review-synthesis"


def _seed_artifacts(project_root: Path) -> Path:
    docs = project_root / "docs"
    docs.mkdir()
    (docs / "project_context.md").write_text("# Project Context\n\nMinimal context.\n")
    sprint = docs / "sprint-artifacts"
    sprint.mkdir()
    (sprint / "10-1-initial-setup.md").write_text(
        "# Story 10.1: Initial Setup\n\n## Status\n\nready-for-review\n\n"
        "## Acceptance Criteria\n\n- [x] AC1\n\n"
        "## File List\n\n- src/example.py\n"
    )
    return docs


def _install_new_layout(project_root: Path) -> None:
    target = project_root / ".claude" / "skills" / "bmad-code-review-synthesis"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL, target)
    scripts = project_root / "_bmad" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "resolve_customization.py").write_text("# marker\n")


def _install_old_layout(project_root: Path) -> Path:
    target = (
        project_root / "_bmad" / "bmm" / "workflows" / "4-implementation"
        / "code-review-synthesis"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(LEGACY_WORKFLOW, target)
    config_dir = project_root / "_bmad" / "bmm"
    config = config_dir / "config.yaml"
    docs = project_root / "docs"
    config.write_text(
        f"project_name: test\n"
        f"output_folder: '{docs}'\n"
        f"planning_artifacts: '{docs}'\n"
        f"implementation_artifacts: '{docs}'\n"
        f"sprint_artifacts: '{docs / 'sprint-artifacts'}'\n"
    )
    return target


@pytest.fixture
def dual_project(tmp_path: Path) -> Path:
    """Dual project."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _seed_artifacts(proj)
    _install_old_layout(proj)
    _install_new_layout(proj)
    return proj


def _make_anonymized_reviews() -> list[AnonymizedValidation]:
    return [
        AnonymizedValidation(
            validator_id="Reviewer A",
            content="Review A: missing error handling.",
            original_ref="ref-a",
        ),
        AnonymizedValidation(
            validator_id="Reviewer B",
            content="Review B: missing error handling.",
            original_ref="ref-b",
        ),
    ]


def _make_context(project_root: Path) -> CompilerContext:
    docs = project_root / "docs"
    return CompilerContext(
        project_root=project_root,
        output_folder=docs,
        project_knowledge=docs,
        resolved_variables={
            "epic_num": 10,
            "story_num": 1,
            "session_id": "test",
            "anonymized_reviews": _make_anonymized_reviews(),
        },
    )


# --------------------------------------------------------------------------- #
# Side-by-side                                                                #
# --------------------------------------------------------------------------- #


class TestSideBySideCompatibility:
    """Tests for SideBySideCompatibility."""

    def test_both_paths_produce_compiled_workflows(self, dual_project: Path) -> None:
        """Test both paths produce compiled workflows."""
        old = compile_workflow(
            "code-review-synthesis",
            _make_context(dual_project),
            skill_layout="old",
        )
        new = compile_workflow(
            "bmad-code-review-synthesis",
            _make_context(dual_project),
            skill_layout="new",
        )
        assert isinstance(old, CompiledWorkflow)
        assert isinstance(new, CompiledWorkflow)
        assert old.context
        assert new.context

    def test_workflow_xml_structure_present_on_both_sides(self, dual_project: Path) -> None:
        """Test workflow xml structure present on both sides."""
        old = compile_workflow(
            "code-review-synthesis",
            _make_context(dual_project),
            skill_layout="old",
        )
        new = compile_workflow(
            "bmad-code-review-synthesis",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "<workflow>" in body, f"{label} body missing <workflow>"
            assert "</workflow>" in body, f"{label} body missing </workflow>"

    def test_both_carry_reviewer_outputs(self, dual_project: Path) -> None:
        """Test both carry reviewer outputs."""
        old = compile_workflow(
            "code-review-synthesis",
            _make_context(dual_project),
            skill_layout="old",
        )
        new = compile_workflow(
            "bmad-code-review-synthesis",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "Reviewer A" in body, f"{label} body missing Reviewer A"
            assert "Reviewer B" in body, f"{label} body missing Reviewer B"

    def test_new_path_carries_full_synthesis_markers(self, dual_project: Path) -> None:
        """New path preserves all synthesis markers.

        Legacy may strip ``<output-format>`` blocks containing HALT
        due to the ``filter_instructions`` quirk.
        """
        new = compile_workflow(
            "bmad-code-review-synthesis",
            _make_context(dual_project),
            skill_layout="new",
        )
        assert "CODE_REVIEW_SYNTHESIS_START" in new.context
        assert "CODE_REVIEW_SYNTHESIS_END" in new.context
        assert "SYNTHESIS_RESOLUTION_START" in new.context
        assert "METRICS_JSON_START" in new.context


# --------------------------------------------------------------------------- #
# Bounded-diff under provider availability                                    #
# --------------------------------------------------------------------------- #


_DIFF_THRESHOLD = 1500


def _stub_master_provider_config():
    from bmad_assist.core.config.models.providers import MasterProviderConfig

    return MasterProviderConfig(
        provider="claude-subprocess",
        model="opus",
        model_name="opus-test",
    )


def _stub_config_with_master(master) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        providers=SimpleNamespace(master=master, multi=[]),
        timeouts=None,
        timeout=300,
        phase_models=None,
    )


@pytest.mark.parametrize("provider_available", [True, False])
def test_legacy_vs_new_compile_diff_is_bounded(
    provider_available: bool,
    dual_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test legacy vs new compile diff is bounded."""
    import difflib

    if provider_available:
        monkeypatch.setattr(
            "bmad_assist.core.config.get_config",
            lambda: _stub_config_with_master(_stub_master_provider_config()),
        )
    else:
        monkeypatch.setattr(
            "bmad_assist.core.config.get_config",
            lambda: _stub_config_with_master(None),
        )

    old = compile_workflow(
        "code-review-synthesis",
        _make_context(dual_project),
        skill_layout="old",
    )
    new = compile_workflow(
        "bmad-code-review-synthesis",
        _make_context(dual_project),
        skill_layout="new",
    )

    diff = list(
        difflib.unified_diff(
            old.context.splitlines(),
            new.context.splitlines(),
            fromfile="legacy/code-review-synthesis",
            tofile="new/bmad-code-review-synthesis",
            lineterm="",
            n=0,
        )
    )
    change_lines = [
        line for line in diff if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]

    assert len(change_lines) <= _DIFF_THRESHOLD, (
        f"diff between legacy and new code-review-synthesis exceeds {_DIFF_THRESHOLD} lines "
        f"(provider_available={provider_available}, observed={len(change_lines)}). "
        "Either the new compiler regressed or the threshold needs adjustment."
    )
