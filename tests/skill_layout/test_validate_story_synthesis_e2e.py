"""End-to-end tests for the Phase 3.5 ``bmad-validate-story-synthesis`` compiler.

This is a multi-LLM aggregation orphan: there is no patch on disk, so
the base class's no-patch path (Phase 3.5 enhancement) is exercised
on every compile. The test seeds anonymized validator outputs in
``context.resolved_variables`` so the legacy compiler's
``_build_synthesis_context`` can fold them in as ``[Validator X]``
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
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-validate-story-synthesis"


def _install_skill(project_root: Path) -> Path:
    target = project_root / ".claude" / "skills" / "bmad-validate-story-synthesis"
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
        "## Status\n\nready-for-validation\n\n"
        "## Acceptance Criteria\n\n- [ ] AC1\n"
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


def _make_anonymized_validations() -> list[AnonymizedValidation]:
    """Two synthetic validator outputs — minimum required for synthesis."""
    return [
        AnonymizedValidation(
            validator_id="Validator A",
            content="<!-- VALIDATION_REPORT_START -->\nFound 1 critical issue: missing AC2.\n"
            "<!-- VALIDATION_REPORT_END -->",
            original_ref="ref-a",
        ),
        AnonymizedValidation(
            validator_id="Validator B",
            content="<!-- VALIDATION_REPORT_START -->\nFound 1 critical issue: missing AC2.\n"
            "<!-- VALIDATION_REPORT_END -->",
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
            "anonymized_validations": _make_anonymized_validations(),
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
            "bmad-validate-story-synthesis",
            _make_context(project_root),
            skill_layout="new",
        )

        assert result.workflow_name == "bmad-validate-story-synthesis"
        body = result.context
        assert body, "compiled workflow body must be non-empty"
        assert "<workflow>" in body

    def test_skill_layout_new_routes_through_new_path(self, project_root: Path) -> None:
        """Test skill layout new routes through new path."""
        from bmad_assist.compiler.core import get_workflow_compiler

        compiler = get_workflow_compiler(
            "bmad-validate-story-synthesis",
            skill_layout="new",
            project_root=project_root,
        )
        assert type(compiler).__module__.startswith("bmad_assist.compiler.skills.")
        assert type(compiler).__name__ == "BmadValidateStorySynthesisCompiler"

    def test_validator_outputs_embedded_as_virtual_files(self, project_root: Path) -> None:
        """Anonymized validator outputs flow through to the compiled context."""
        result = compile_workflow(
            "bmad-validate-story-synthesis",
            _make_context(project_root),
            skill_layout="new",
        )
        body = result.context
        assert "Validator A" in body, (
            "compiled body should contain the anonymized [Validator A] file"
        )
        assert "Validator B" in body, (
            "compiled body should contain the anonymized [Validator B] file"
        )

    def test_compiled_body_contains_contract_markers(self, project_root: Path) -> None:
        """SKILL.md authors the contract / metrics block markers — they survive substitution."""
        result = compile_workflow(
            "bmad-validate-story-synthesis",
            _make_context(project_root),
            skill_layout="new",
        )
        body = result.context
        assert "VALIDATION_CONTRACT_START" in body
        assert "VALIDATION_CONTRACT_END" in body
        assert "METRICS_JSON_START" in body
        assert "METRICS_JSON_END" in body
        assert "VALIDATION_SYNTHESIS_START" in body
        assert "VALIDATION_SYNTHESIS_END" in body


# --------------------------------------------------------------------------- #
# No-patch path (Phase 3.5 base-class enhancement)                            #
# --------------------------------------------------------------------------- #


class TestNoPatchPath:
    """validate-story-synthesis ships without a patch — exercise that branch."""

    def test_compile_succeeds_with_no_patch_on_disk(self, project_root: Path) -> None:
        """No patch file → no LLM transforms / no regex post-process — body is SKILL.md verbatim."""
        result = compile_workflow(
            "bmad-validate-story-synthesis",
            _make_context(project_root),
            skill_layout="new",
        )
        # Successful compile with substituted SKILL.md body.
        assert result.context
        assert "<workflow>" in result.context

    def test_no_patch_emits_debug_log(
        self,
        project_root: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Phase 3.5 base-class enhancement logs the no-patch decision."""
        import logging

        from bmad_assist.compiler.skills import _base as base_mod

        caplog.set_level(logging.DEBUG, logger=base_mod.logger.name)
        compile_workflow(
            "bmad-validate-story-synthesis",
            _make_context(project_root),
            skill_layout="new",
        )
        assert any(
            "No patch found for bmad-validate-story-synthesis" in rec.message
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
            cache_dir / "bmad-validate-story-synthesis.tpl.xml",
            cache_dir / "bmad-validate-story-synthesis.tpl.xml.meta.yaml",
        )

    def test_cache_is_written_on_first_compile(self, project_root: Path) -> None:
        """Test cache is written on first compile."""
        cache_path, meta_path = self._cache_paths(project_root)
        assert not cache_path.exists()
        compile_workflow(
            "bmad-validate-story-synthesis",
            _make_context(project_root),
            skill_layout="new",
        )
        assert cache_path.is_file()
        assert meta_path.is_file()

    def test_cache_meta_records_empty_patch_hash(self, project_root: Path) -> None:
        """No patch → cache meta records empty patch_hash."""
        import yaml

        _, meta_path = self._cache_paths(project_root)
        compile_workflow(
            "bmad-validate-story-synthesis",
            _make_context(project_root),
            skill_layout="new",
        )
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        assert meta["skill_layout_mode"] == "new"
        assert meta["skill_id"] == "bmad-validate-story-synthesis"
        assert meta["patch_hash"] == "", (
            f"no-patch path should record empty patch_hash; got {meta['patch_hash']!r}"
        )


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #


class TestValidateContextChecks:
    """The skill-layout compiler enforces minimum-validators / required-args early."""

    def test_missing_validations_raises(self, project_root: Path) -> None:
        """Test missing validations raises."""
        from bmad_assist.core.exceptions import CompilerError

        ctx = CompilerContext(
            project_root=project_root,
            output_folder=project_root / "docs",
            project_knowledge=project_root / "docs",
            resolved_variables={"epic_num": 10, "story_num": 1},
        )
        with pytest.raises(CompilerError, match="No anonymized validations"):
            compile_workflow(
                "bmad-validate-story-synthesis",
                ctx,
                skill_layout="new",
            )

    def test_single_validation_below_minimum_raises(self, project_root: Path) -> None:
        """Test single validation below minimum raises."""
        from bmad_assist.core.exceptions import CompilerError

        ctx = CompilerContext(
            project_root=project_root,
            output_folder=project_root / "docs",
            project_knowledge=project_root / "docs",
            resolved_variables={
                "epic_num": 10,
                "story_num": 1,
                "session_id": "x",
                "anonymized_validations": [_make_anonymized_validations()[0]],
            },
        )
        with pytest.raises(CompilerError, match="at least 2 validations"):
            compile_workflow(
                "bmad-validate-story-synthesis",
                ctx,
                skill_layout="new",
            )
