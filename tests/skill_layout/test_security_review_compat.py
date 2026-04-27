"""Side-by-side: legacy vs skill-layout security-review compile.

bmad-assist-authored CWE scanner with no patch on either side. The
bounded-difference invariants are softer because:

* The legacy ``instructions.xml`` is XML; the new SKILL.md is XML
  wrapped in outcome-based markdown frontmatter.
* The CWE pattern catalogue and any captured git diff are embedded
  identically on both sides via the legacy compiler delegate, so the
  large bulk of the body is shared.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-security-review"
LEGACY_WORKFLOW = REPO_ROOT / "src" / "bmad_assist" / "workflows" / "security-review"


def _seed_artifacts(project_root: Path) -> Path:
    docs = project_root / "docs"
    docs.mkdir()
    (docs / "project_context.md").write_text("# Project Context\n\nMinimal context.\n")
    return docs


def _install_new_layout(project_root: Path) -> None:
    target = project_root / ".claude" / "skills" / "bmad-security-review"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL, target)
    scripts = project_root / "_bmad" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "resolve_customization.py").write_text("# marker\n")


def _install_old_layout(project_root: Path) -> Path:
    target = (
        project_root / "_bmad" / "bmm" / "workflows" / "4-implementation" / "security-review"
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
    )
    return target


@pytest.fixture
def dual_project(tmp_path: Path) -> Path:
    """Project with both the legacy workflow and the new skill installed."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _seed_artifacts(proj)
    _install_old_layout(proj)
    _install_new_layout(proj)
    return proj


def _make_context(project_root: Path) -> CompilerContext:
    docs = project_root / "docs"
    return CompilerContext(
        project_root=project_root,
        output_folder=docs,
        project_knowledge=docs,
    )


# --------------------------------------------------------------------------- #
# Side-by-side                                                                #
# --------------------------------------------------------------------------- #


class TestSideBySideCompatibility:
    """Tests for SideBySideCompatibility."""

    def test_both_paths_produce_compiled_workflows(self, dual_project: Path) -> None:
        """Test both paths produce compiled workflows."""
        old = compile_workflow(
            "security-review",
            _make_context(dual_project),
            skill_layout="old",
        )
        new = compile_workflow(
            "bmad-security-review",
            _make_context(dual_project),
            skill_layout="new",
        )
        assert isinstance(old, CompiledWorkflow)
        assert isinstance(new, CompiledWorkflow)
        assert old.context
        assert new.context

    def test_workflow_xml_structure_present_on_both_sides(self, dual_project: Path) -> None:
        """Both paths produce the ``<compiled-workflow>`` envelope.

        The legacy ``filter_instructions`` whitelist drops the
        ``<analysis-instructions>`` wrapper (not whitelisted), which
        also strips the ``<step>`` children since they live inside it.
        The new SKILL.md keeps its execution body inside ``<workflow>``
        which the SKILL.md path does NOT route through the whitelist
        filter — so its ``<step>`` elements survive verbatim. We
        assert the strong common invariants on both sides and the
        path-specific tags individually.
        """
        old = compile_workflow(
            "security-review",
            _make_context(dual_project),
            skill_layout="old",
        )
        new = compile_workflow(
            "bmad-security-review",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "<compiled-workflow>" in body, f"{label} body missing envelope"
            assert "<mission>" in body, f"{label} body missing <mission>"
            assert "<context>" in body, f"{label} body missing <context>"

        assert "<workflow>" in new.context, "new body should wrap execution in <workflow>"
        assert "</workflow>" in new.context, "new body should close </workflow>"
        assert "<step n=" in new.context, "new body should keep <step> elements verbatim"

    def test_both_carry_security_patterns_context(self, dual_project: Path) -> None:
        """Both paths embed the CWE pattern catalogue under the same virtual id."""
        old = compile_workflow(
            "security-review",
            _make_context(dual_project),
            skill_layout="old",
        )
        new = compile_workflow(
            "bmad-security-review",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "security-patterns" in body, (
                f"{label} body missing security-patterns context file"
            )
            assert "CWE-" in body, f"{label} body missing CWE-* identifiers"

    def test_new_path_carries_security_report_markers(self, dual_project: Path) -> None:
        """The new SKILL.md carries the SECURITY_REPORT markers verbatim.

        Authoring note: the legacy ``instructions.xml`` puts the
        report markers inside an ``<output-format>`` block. The
        ``filter_instructions`` whitelist keeps that tag, so legacy
        also carries the markers — but we assert on the new side here
        as the strong invariant; the broader bounded-diff check below
        covers the legacy side implicitly.
        """
        new = compile_workflow(
            "bmad-security-review",
            _make_context(dual_project),
            skill_layout="new",
        )
        assert "SECURITY_REPORT_START" in new.context
        assert "SECURITY_REPORT_END" in new.context


# --------------------------------------------------------------------------- #
# Bounded-diff under provider availability                                    #
# --------------------------------------------------------------------------- #


# The new SKILL.md wraps the BMAD activation block + outcome-based body
# around the same execution semantics; the legacy XML is much smaller.
# Threshold matches the Phase 3.5 synthesis cap (1500); the observed
# delta is well under it because the embedded pattern catalogue (which
# dominates the body bytes) is byte-identical on both sides.
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
    """Legacy vs new differ by at most ``_DIFF_THRESHOLD`` change-lines."""
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
        "security-review",
        _make_context(dual_project),
        skill_layout="old",
    )
    new = compile_workflow(
        "bmad-security-review",
        _make_context(dual_project),
        skill_layout="new",
    )

    diff = list(
        difflib.unified_diff(
            old.context.splitlines(),
            new.context.splitlines(),
            fromfile="legacy/security-review",
            tofile="new/bmad-security-review",
            lineterm="",
            n=0,
        )
    )
    change_lines = [
        line for line in diff if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]

    assert len(change_lines) <= _DIFF_THRESHOLD, (
        f"diff between legacy and new security-review exceeds {_DIFF_THRESHOLD} lines "
        f"(provider_available={provider_available}, observed={len(change_lines)}). "
        "Either the new compiler regressed or the threshold needs adjustment."
    )
