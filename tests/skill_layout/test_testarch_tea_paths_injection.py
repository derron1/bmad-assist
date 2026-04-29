"""Tests for the compile-time `<tea-paths>` block injection.

The TEA tri-modal step files reference tokens like ``{test_artifacts}``
in their frontmatter. BMAD's SKILL.md activation only loads
``user_name`` and ``communication_language``, so without compile-time
help the LLM sees unresolved tokens at runtime and may guess paths.

The fix injects a ``<tea-paths>`` block in
``compiler.skills._testarch_base._inject_tea_paths_block`` so the
resolved values are concretely visible in the compiled prompt. These
tests cover the two behaviours that aren't already pinned by
``tests/skill_layout/test_snapshots.py``:

1. The block is emitted when ``_bmad/tea/config.yaml`` is present.
2. The block omits keys that are missing from the config (no empty
   children are emitted).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.types import CompilerContext

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-testarch-test-design"


def _install_skill(project_root: Path) -> None:
    target = project_root / ".claude" / "skills" / "bmad-testarch-test-design"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL, target)
    scripts_dir = project_root / "_bmad" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "resolve_customization.py").write_text("# test marker\n")


def _seed_docs(project_root: Path) -> Path:
    docs = project_root / "docs"
    docs.mkdir()
    (docs / "project_context.md").write_text("# Project Context\n")
    (docs / "prd.md").write_text("# PRD\n")
    (docs / "architecture.md").write_text("# Architecture\n")
    (docs / "epics").mkdir()
    (docs / "epics" / "epic-10.md").write_text("# Epic 10\n")
    sprint_dir = docs / "sprint-artifacts"
    sprint_dir.mkdir()
    (sprint_dir / "sprint-status.yaml").write_text(
        "development_status:\n  10-1-foo: ready-for-dev\n"
    )
    (sprint_dir / "10-1-foo.md").write_text("# Story 10.1\n## Status\nready-for-dev\n")
    return docs


def _make_context(project_root: Path) -> CompilerContext:
    docs = project_root / "docs"
    return CompilerContext(
        project_root=project_root,
        output_folder=docs,
        project_knowledge=docs,
        resolved_variables={"epic_num": 10},
    )


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Set up an isolated TEA project tree with the bundled skill installed."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _seed_docs(proj)
    _install_skill(proj)
    return proj


def _write_tea_yaml(project_root: Path, body: str) -> None:
    tea_dir = project_root / "_bmad" / "tea"
    tea_dir.mkdir(parents=True, exist_ok=True)
    (tea_dir / "config.yaml").write_text(body)


class TestTeaPathsInjection:
    """Verify the `<tea-paths>` block appears in compiled TEA prompts."""

    def test_testarch_compile_injects_tea_paths_block(self, project_root: Path) -> None:
        """When `_bmad/tea/config.yaml` exists, the block is emitted with all keys."""
        _write_tea_yaml(
            project_root,
            'test_artifacts: "{project-root}/_bmad-output/test-artifacts"\n'
            "test_design_output: _bmad-output/test-artifacts/test-design\n"
            "test_review_output: _bmad-output/test-artifacts/test-reviews\n"
            "trace_output: _bmad-output/test-artifacts/traceability\n",
        )

        result = compile_workflow(
            "bmad-testarch-test-design", _make_context(project_root), skill_layout="new"
        )
        body = result.context

        assert "<tea-paths>" in body
        assert "</tea-paths>" in body
        # All four configured keys appear with their resolved values.
        assert (
            f"<test_artifacts>{project_root.resolve()}/_bmad-output/test-artifacts</test_artifacts>"
            in body
        )
        assert (
            "<test_design_output>_bmad-output/test-artifacts/test-design</test_design_output>"
            in body
        )
        assert (
            "<test_review_output>_bmad-output/test-artifacts/test-reviews</test_review_output>"
            in body
        )
        assert "<trace_output>_bmad-output/test-artifacts/traceability</trace_output>" in body
        # Block lives near the top of the envelope (after </mission>, before <context>).
        assert body.index("<tea-paths>") < body.index("<context>")
        assert body.index("</mission>") < body.index("<tea-paths>")

    def test_testarch_compile_omits_missing_tea_keys(self, project_root: Path) -> None:
        """Keys absent from config aren't emitted as empty children."""
        _write_tea_yaml(
            project_root,
            'test_artifacts: "{project-root}/_bmad-output/test-artifacts"\n',
        )

        result = compile_workflow(
            "bmad-testarch-test-design", _make_context(project_root), skill_layout="new"
        )
        body = result.context

        assert "<tea-paths>" in body
        assert "<test_artifacts>" in body
        # Other path keys must NOT appear (no empty children, no defaults).
        assert "<test_design_output>" not in body
        assert "<test_review_output>" not in body
        assert "<trace_output>" not in body
        assert "<test_dir>" not in body

    def test_testarch_compile_skips_block_when_no_tea_config(self, project_root: Path) -> None:
        """No `_bmad/tea/config.yaml` and no TOML fallback -> block omitted entirely."""
        # Intentionally do not write any tea config.
        result = compile_workflow(
            "bmad-testarch-test-design", _make_context(project_root), skill_layout="new"
        )
        body = result.context

        assert "<tea-paths>" not in body
