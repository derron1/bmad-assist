"""End-to-end tests for the Phase 6-prep ``bmad-security-review`` compiler.

Last single-workflow migration: bmad-assist-authored CWE scanner with
no patch on disk. Mirrors the Phase 3.5 no-patch e2e tests; the legacy
compiler embeds the bundled CWE pattern catalogue, the captured git
diff, and any modified source files as virtual context entries.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.types import CompilerContext

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-security-review"


def _install_skill(project_root: Path) -> Path:
    target = project_root / ".claude" / "skills" / "bmad-security-review"
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
    return docs


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Project root with the bundled skill installed."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _seed_project_artifacts(proj)
    _install_skill(proj)
    return proj


def _make_context(project_root: Path) -> CompilerContext:
    docs = project_root / "docs"
    return CompilerContext(
        project_root=project_root,
        output_folder=docs,
        project_knowledge=docs,
    )


# --------------------------------------------------------------------------- #
# Core proof-of-architecture                                                  #
# --------------------------------------------------------------------------- #


class TestSkillLayoutCompileE2E:
    """Tests for SkillLayoutCompileE2E."""

    def test_compile_returns_well_shaped_compiled_workflow(self, project_root: Path) -> None:
        """Compile yields a CompiledWorkflow stamped with the bmad- skill id."""
        result = compile_workflow(
            "bmad-security-review",
            _make_context(project_root),
            skill_layout="new",
        )

        assert result.workflow_name == "bmad-security-review"
        body = result.context
        assert body, "compiled workflow body must be non-empty"
        assert "<workflow>" in body

    def test_skill_layout_new_routes_through_new_path(self, project_root: Path) -> None:
        """Test skill layout new routes through new path."""
        from bmad_assist.compiler.core import get_workflow_compiler

        compiler = get_workflow_compiler(
            "bmad-security-review",
            skill_layout="new",
            project_root=project_root,
        )
        assert type(compiler).__module__.startswith("bmad_assist.compiler.skills.")
        assert type(compiler).__name__ == "BmadSecurityReviewCompiler"

    def test_legacy_workflow_name_routes_to_new_compiler_when_layout_new(
        self, project_root: Path
    ) -> None:
        """Test legacy workflow name routes to new compiler when layout new."""
        from bmad_assist.compiler.core import get_workflow_compiler

        compiler = get_workflow_compiler(
            "security-review", skill_layout="new", project_root=project_root
        )
        assert type(compiler).__module__.startswith("bmad_assist.compiler.skills.")
        assert type(compiler).__name__ == "BmadSecurityReviewCompiler"

    def test_compiled_body_contains_security_report_markers(self, project_root: Path) -> None:
        """SKILL.md authors the SECURITY_REPORT markers — they survive substitution."""
        result = compile_workflow(
            "bmad-security-review",
            _make_context(project_root),
            skill_layout="new",
        )
        body = result.context
        assert "SECURITY_REPORT_START" in body
        assert "SECURITY_REPORT_END" in body

    def test_compiled_body_embeds_cwe_pattern_context_file(self, project_root: Path) -> None:
        """Pattern catalogue is embedded as the ``security-patterns`` virtual file."""
        result = compile_workflow(
            "bmad-security-review",
            _make_context(project_root),
            skill_layout="new",
        )
        body = result.context
        # The legacy compiler embeds the catalogue under this virtual id.
        assert "security-patterns" in body
        # CWE-prefixed ids appear inside core.yaml — strong signal the
        # patterns were actually loaded and embedded, not just labeled.
        assert "CWE-" in body


# --------------------------------------------------------------------------- #
# No-patch path (Phase 3.5 base-class enhancement)                            #
# --------------------------------------------------------------------------- #


class TestNoPatchPath:
    """security-review ships without a patch — exercise that branch."""

    def test_compile_succeeds_with_no_patch_on_disk(self, project_root: Path) -> None:
        """No patch file → no LLM transforms / no regex post-process — body is SKILL.md verbatim."""
        result = compile_workflow(
            "bmad-security-review",
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
        """The base-class no-patch path logs the decision under base.logger."""
        import logging

        from bmad_assist.compiler.skills import _base as base_mod

        caplog.set_level(logging.DEBUG, logger=base_mod.logger.name)
        compile_workflow(
            "bmad-security-review",
            _make_context(project_root),
            skill_layout="new",
        )
        assert any(
            "No patch found for bmad-security-review" in rec.message
            for rec in caplog.records
            if rec.name == base_mod.logger.name
        )

    def test_llm_transforms_skipped_when_no_provider_configured(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No master provider → ``_resolve_transform_mode`` returns regex_only.

        With no patch on disk *and* regex-only mode, ``_apply_llm_transforms``
        short-circuits before reaching the (stubbed) apply function. This
        guards the behaviour Phase 3.5 added when the run env has no
        configured provider — relevant to the real-LLM smoke test path.
        """
        from types import SimpleNamespace

        from bmad_assist.compiler.skills import bmad_security_review as skill_mod

        monkeypatch.setattr(
            "bmad_assist.core.config.get_config",
            lambda: SimpleNamespace(providers=SimpleNamespace(master=None, multi=[])),
        )

        def _fail(**_kwargs):
            raise AssertionError(
                "apply_llm_transforms must NOT be called with no provider + no patch"
            )

        monkeypatch.setattr(skill_mod, "apply_llm_transforms", _fail)

        result = compile_workflow(
            "bmad-security-review",
            _make_context(project_root),
            skill_layout="new",
        )
        assert result.context  # compile completed via the regex-only path


# --------------------------------------------------------------------------- #
# Cache lifecycle                                                             #
# --------------------------------------------------------------------------- #


class TestSkillLayoutCacheLifecycle:
    """Tests for SkillLayoutCacheLifecycle."""

    def _cache_paths(self, project_root: Path) -> tuple[Path, Path]:
        cache_dir = project_root / ".bmad-assist" / "cache" / "skills"
        return (
            cache_dir / "bmad-security-review.tpl.xml",
            cache_dir / "bmad-security-review.tpl.xml.meta.yaml",
        )

    def test_cache_is_written_on_first_compile(self, project_root: Path) -> None:
        """First compile writes both the body cache and its meta sidecar."""
        cache_path, meta_path = self._cache_paths(project_root)
        assert not cache_path.exists()
        compile_workflow(
            "bmad-security-review",
            _make_context(project_root),
            skill_layout="new",
        )
        assert cache_path.is_file()
        assert meta_path.is_file()

    def test_cache_meta_records_empty_patch_hash(self, project_root: Path) -> None:
        """No-patch path records empty patch_hash and the bmad-prefixed skill id."""
        import yaml

        _, meta_path = self._cache_paths(project_root)
        compile_workflow(
            "bmad-security-review",
            _make_context(project_root),
            skill_layout="new",
        )
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        assert meta["skill_layout_mode"] == "new"
        assert meta["skill_id"] == "bmad-security-review"
        assert meta["patch_hash"] == "", (
            f"no-patch path should record empty patch_hash; got {meta['patch_hash']!r}"
        )


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #


class TestValidateContextChecks:
    """The skill-layout compiler enforces project_root + skill source early."""

    def test_missing_project_root_raises(self, project_root: Path) -> None:
        """A None project_root errors out with a friendly suggestion."""
        from bmad_assist.core.exceptions import CompilerError

        ctx = CompilerContext(
            project_root=None,
            output_folder=project_root / "docs",
            project_knowledge=project_root / "docs",
        )
        with pytest.raises(CompilerError, match="project_root"):
            compile_workflow(
                "bmad-security-review",
                ctx,
                skill_layout="new",
            )
