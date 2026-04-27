"""Side-by-side: legacy vs skill-layout validate-story-synthesis compile.

Multi-LLM-aggregation orphan with no patch on either side. The
bounded-difference invariants are softer here because:

* The legacy ``instructions.xml`` is XML; the new SKILL.md is XML
  wrapped in outcome-based markdown frontmatter.
* The contract / metrics / synthesis markers are authored directly
  into both source files (no patch injects them), so they appear in
  both bodies — this gives the test stronger marker-equivalence
  checks than the patched orphans get.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext
from bmad_assist.validation.anonymizer import AnonymizedValidation

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-validate-story-synthesis"
LEGACY_WORKFLOW = REPO_ROOT / "src" / "bmad_assist" / "workflows" / "validate-story-synthesis"


def _seed_artifacts(project_root: Path) -> Path:
    docs = project_root / "docs"
    docs.mkdir()
    (docs / "project_context.md").write_text("# Project Context\n\nMinimal context.\n")
    sprint = docs / "sprint-artifacts"
    sprint.mkdir()
    (sprint / "10-1-initial-setup.md").write_text(
        "# Story 10.1: Initial Setup\n\n## Status\n\nready-for-validation\n\n"
        "## Acceptance Criteria\n\n- [ ] AC1\n"
    )
    return docs


def _install_new_layout(project_root: Path) -> None:
    target = project_root / ".claude" / "skills" / "bmad-validate-story-synthesis"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL, target)
    scripts = project_root / "_bmad" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "resolve_customization.py").write_text("# marker\n")


def _install_old_layout(project_root: Path) -> Path:
    target = (
        project_root / "_bmad" / "bmm" / "workflows" / "4-implementation"
        / "validate-story-synthesis"
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


def _make_anonymized_validations() -> list[AnonymizedValidation]:
    return [
        AnonymizedValidation(
            validator_id="Validator A",
            content="<!-- VALIDATION_REPORT_START -->\nFinding A1.\n<!-- VALIDATION_REPORT_END -->",
            original_ref="ref-a",
        ),
        AnonymizedValidation(
            validator_id="Validator B",
            content="<!-- VALIDATION_REPORT_START -->\nFinding B1.\n<!-- VALIDATION_REPORT_END -->",
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
            "anonymized_validations": _make_anonymized_validations(),
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
            "validate-story-synthesis",
            _make_context(dual_project),
            skill_layout="old",
        )
        new = compile_workflow(
            "bmad-validate-story-synthesis",
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
            "validate-story-synthesis",
            _make_context(dual_project),
            skill_layout="old",
        )
        new = compile_workflow(
            "bmad-validate-story-synthesis",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "<workflow>" in body, f"{label} body missing <workflow>"
            assert "</workflow>" in body, f"{label} body missing </workflow>"

    def test_both_carry_validator_outputs(self, dual_project: Path) -> None:
        """Both paths embed [Validator X] virtual files in the context."""
        old = compile_workflow(
            "validate-story-synthesis",
            _make_context(dual_project),
            skill_layout="old",
        )
        new = compile_workflow(
            "bmad-validate-story-synthesis",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "Validator A" in body, f"{label} body missing Validator A"
            assert "Validator B" in body, f"{label} body missing Validator B"

    def test_new_path_carries_full_contract_markers(self, dual_project: Path) -> None:
        """The new path preserves all four synthesis markers in the compiled body.

        Authoring note: the legacy path's :func:`filter_instructions`
        strips ``<output-format>`` blocks whose direct text contains
        the substring "HALT" (case-insensitive) — see filtering.py
        line 191-193. The legacy ``instructions.xml`` puts
        ``resolution: {resolved|rework|halt}`` inside an
        ``<output-format>`` block, so the HALT-containing block is
        silently dropped by the filter on the legacy side.

        The new SKILL.md path bypasses XML filtering entirely (the
        SKILL.md body starts with YAML frontmatter, which
        ``_is_markdown_content`` detects as markdown), so the markers
        survive. We assert on the new path only, with a comment noting
        the legacy quirk so reviewers don't try to "fix" the test by
        also asserting against the legacy body.
        """
        new = compile_workflow(
            "bmad-validate-story-synthesis",
            _make_context(dual_project),
            skill_layout="new",
        )
        assert "VALIDATION_CONTRACT_START" in new.context
        assert "VALIDATION_CONTRACT_END" in new.context
        assert "METRICS_JSON_START" in new.context
        assert "VALIDATION_SYNTHESIS_END" in new.context

    def test_legacy_carries_at_least_the_critical_section(self, dual_project: Path) -> None:
        """Legacy path keeps at least the contract-marker critical block.

        The ``<output-format>`` block may be stripped by the
        ``filter_instructions`` HALT-text quirk, but the
        ``<critical>`` block referencing the contract markers always
        survives. This is the minimum signal we expect on legacy.
        """
        old = compile_workflow(
            "validate-story-synthesis",
            _make_context(dual_project),
            skill_layout="old",
        )
        # The legacy <critical> block at line 110 mentions the markers
        # in its text — that's the minimum signal we expect the legacy
        # path to retain.
        assert "VALIDATION_CONTRACT_START/END" in old.context, (
            "legacy body should at least retain the <critical> reference to the contract markers"
        )


# --------------------------------------------------------------------------- #
# Bounded-diff under provider availability                                    #
# --------------------------------------------------------------------------- #


# Synthesis SKILL.md is materially larger than the legacy
# instructions.xml because the SKILL.md bundles both the activation
# block AND the execution body whereas the legacy has only the
# execution body. Threshold is generous to absorb that.
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
        "validate-story-synthesis",
        _make_context(dual_project),
        skill_layout="old",
    )
    new = compile_workflow(
        "bmad-validate-story-synthesis",
        _make_context(dual_project),
        skill_layout="new",
    )

    diff = list(
        difflib.unified_diff(
            old.context.splitlines(),
            new.context.splitlines(),
            fromfile="legacy/validate-story-synthesis",
            tofile="new/bmad-validate-story-synthesis",
            lineterm="",
            n=0,
        )
    )
    change_lines = [
        line for line in diff if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]

    assert len(change_lines) <= _DIFF_THRESHOLD, (
        f"diff between legacy and new validate-story-synthesis exceeds {_DIFF_THRESHOLD} lines "
        f"(provider_available={provider_available}, observed={len(change_lines)}). "
        "Either the new compiler regressed or the threshold needs adjustment."
    )
