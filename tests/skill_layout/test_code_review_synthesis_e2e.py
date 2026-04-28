"""End-to-end tests for the Phase 3.5 ``bmad-code-review-synthesis`` compiler.

Multi-LLM aggregation orphan with no patch on disk. Mirrors the
validate-story-synthesis e2e tests; the legacy compiler reads
``anonymized_reviews`` (rather than ``anonymized_validations``) from
``context.resolved_variables`` and embeds them as ``[Reviewer X]``
virtual files.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.types import CompilerContext
from bmad_assist.validation.anonymizer import AnonymizedValidation

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-code-review-synthesis"


def _install_skill(project_root: Path) -> Path:
    target = project_root / ".claude" / "skills" / "bmad-code-review-synthesis"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL, target)
    scripts_dir = project_root / "_bmad" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "resolve_customization.py").write_text("# test marker\n")
    return target


def _seed_project_artifacts(project_root: Path) -> Path:
    docs = project_root / "docs"
    docs.mkdir()
    (docs / "project_context.md").write_text("# Project Context\n\nMinimal context.\n")
    sprint = docs / "sprint-artifacts"
    sprint.mkdir()
    (sprint / "10-1-initial-setup.md").write_text(
        "# Story 10.1: Initial Setup\n\n"
        "## Status\n\nready-for-review\n\n"
        "## Acceptance Criteria\n\n- [x] AC1\n\n"
        "## File List\n\n- src/example.py\n"
    )
    return docs


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Project root."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _seed_project_artifacts(proj)
    _install_skill(proj)
    return proj


def _make_anonymized_reviews() -> list[AnonymizedValidation]:
    """Two synthetic reviewer outputs — minimum required for synthesis."""
    return [
        AnonymizedValidation(
            validator_id="Reviewer A",
            content="Review A: missing error handling at line 12.",
            original_ref="ref-a",
        ),
        AnonymizedValidation(
            validator_id="Reviewer B",
            content="Review B: missing error handling at line 12.",
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
            "session_id": "test-session",
            "anonymized_reviews": _make_anonymized_reviews(),
        },
    )


# --------------------------------------------------------------------------- #
# Core proof-of-architecture                                                  #
# --------------------------------------------------------------------------- #


class TestSkillLayoutCompileE2E:
    """Tests for SkillLayoutCompileE2E."""

    def test_compile_returns_well_shaped_compiled_workflow(self, project_root: Path) -> None:
        """Test compile returns well shaped compiled workflow."""
        result = compile_workflow(
            "bmad-code-review-synthesis",
            _make_context(project_root),
            skill_layout="new",
        )

        assert result.workflow_name == "bmad-code-review-synthesis"
        body = result.context
        assert body, "compiled workflow body must be non-empty"
        assert "<workflow>" in body

    def test_skill_layout_new_routes_through_new_path(self, project_root: Path) -> None:
        """Test skill layout new routes through new path."""
        from bmad_assist.compiler.core import get_workflow_compiler

        compiler = get_workflow_compiler(
            "bmad-code-review-synthesis",
            skill_layout="new",
            project_root=project_root,
        )
        assert type(compiler).__module__.startswith("bmad_assist.compiler.skills.")
        assert type(compiler).__name__ == "BmadCodeReviewSynthesisCompiler"

    def test_reviewer_outputs_embedded_as_virtual_files(self, project_root: Path) -> None:
        """Test reviewer outputs embedded as virtual files."""
        result = compile_workflow(
            "bmad-code-review-synthesis",
            _make_context(project_root),
            skill_layout="new",
        )
        body = result.context
        assert "Reviewer A" in body
        assert "Reviewer B" in body

    def test_compiled_body_contains_synthesis_markers(self, project_root: Path) -> None:
        """SKILL.md authors the synthesis markers — they survive substitution."""
        result = compile_workflow(
            "bmad-code-review-synthesis",
            _make_context(project_root),
            skill_layout="new",
        )
        body = result.context
        assert "CODE_REVIEW_SYNTHESIS_START" in body
        assert "CODE_REVIEW_SYNTHESIS_END" in body
        assert "SYNTHESIS_RESOLUTION_START" in body
        assert "SYNTHESIS_RESOLUTION_END" in body


# --------------------------------------------------------------------------- #
# No-patch path                                                               #
# --------------------------------------------------------------------------- #


class TestNoPatchPath:
    """Tests for NoPatchPath."""

    def test_compile_succeeds_with_no_patch_on_disk(self, project_root: Path) -> None:
        """Test compile succeeds with no patch on disk."""
        result = compile_workflow(
            "bmad-code-review-synthesis",
            _make_context(project_root),
            skill_layout="new",
        )
        assert result.context
        assert "<workflow>" in result.context

    def test_no_patch_emits_debug_log(
        self,
        project_root: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test no patch emits debug log."""
        import logging

        from bmad_assist.compiler.skills import _base as base_mod

        caplog.set_level(logging.DEBUG, logger=base_mod.logger.name)
        compile_workflow(
            "bmad-code-review-synthesis",
            _make_context(project_root),
            skill_layout="new",
        )
        assert any(
            "No patch found for bmad-code-review-synthesis" in rec.message
            for rec in caplog.records
            if rec.name == base_mod.logger.name
        )


# --------------------------------------------------------------------------- #
# Cache lifecycle                                                             #
# --------------------------------------------------------------------------- #


class TestSkillLayoutCacheLifecycle:
    """Tests for SkillLayoutCacheLifecycle."""

    def _cache_paths(self, project_root: Path) -> tuple[Path, Path]:
        cache_dir = project_root / ".bmad-assist" / "cache" / "skills"
        return (
            cache_dir / "bmad-code-review-synthesis.tpl.xml",
            cache_dir / "bmad-code-review-synthesis.tpl.xml.meta.yaml",
        )

    def test_cache_is_written_on_first_compile(self, project_root: Path) -> None:
        """Test cache is written on first compile."""
        cache_path, meta_path = self._cache_paths(project_root)
        assert not cache_path.exists()
        compile_workflow(
            "bmad-code-review-synthesis",
            _make_context(project_root),
            skill_layout="new",
        )
        assert cache_path.is_file()
        assert meta_path.is_file()

    def test_cache_meta_records_empty_patch_hash(self, project_root: Path) -> None:
        """Test cache meta records empty patch hash."""
        import yaml

        _, meta_path = self._cache_paths(project_root)
        compile_workflow(
            "bmad-code-review-synthesis",
            _make_context(project_root),
            skill_layout="new",
        )
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        assert meta["skill_layout_mode"] == "new"
        assert meta["skill_id"] == "bmad-code-review-synthesis"
        assert meta["patch_hash"] == "", (
            f"no-patch path should record empty patch_hash; got {meta['patch_hash']!r}"
        )


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #


class TestValidateContextChecks:
    """Tests for ValidateContextChecks."""

    def test_missing_reviews_raises(self, project_root: Path) -> None:
        """Test missing reviews raises."""
        from bmad_assist.core.exceptions import CompilerError

        ctx = CompilerContext(
            project_root=project_root,
            output_folder=project_root / "docs",
            project_knowledge=project_root / "docs",
            resolved_variables={"epic_num": 10, "story_num": 1},
        )
        with pytest.raises(CompilerError, match="No anonymized reviews"):
            compile_workflow(
                "bmad-code-review-synthesis",
                ctx,
                skill_layout="new",
            )

    def test_single_review_below_minimum_raises(self, project_root: Path) -> None:
        """Test single review below minimum raises."""
        from bmad_assist.core.exceptions import CompilerError

        ctx = CompilerContext(
            project_root=project_root,
            output_folder=project_root / "docs",
            project_knowledge=project_root / "docs",
            resolved_variables={
                "epic_num": 10,
                "story_num": 1,
                "session_id": "x",
                "anonymized_reviews": [_make_anonymized_reviews()[0]],
            },
        )
        with pytest.raises(CompilerError, match="at least 2 reviews"):
            compile_workflow(
                "bmad-code-review-synthesis",
                ctx,
                skill_layout="new",
            )
