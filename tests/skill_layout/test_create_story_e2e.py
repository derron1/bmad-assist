"""End-to-end test for the Phase 2 ``bmad-create-story`` compiler.

This is the proof-of-architecture test for Phase 2: it stands up a
synthetic BMAD v6.4+ project, invokes :func:`compile_workflow` through
the new path (``skill_layout="new"``), and checks that the resulting
:class:`CompiledWorkflow` is well-shaped, that the cache is written and
re-used, and that mutating ``customize.toml`` invalidates the cache.

Phase 2.5 adds (mock-based) tests for the LLM-transform layer:
``apply_llm_transforms`` is invoked when a master provider is
configured, skipped (with INFO log) otherwise, and the
``transform_mode`` field in the cache meta now invalidates when the
mode flips.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.types import CompilerContext

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-create-story"
PATCH_FILE = REPO_ROOT / ".bmad-assist" / "patches" / "create-story.patch.yaml"


def _install_skill(project_root: Path) -> Path:
    """Mirror the bundled skill into ``.claude/skills/bmad-create-story/``.

    Returns the installed skill directory so tests can mutate it (e.g.
    rewrite ``customize.toml`` to test cache invalidation).
    """
    target = project_root / ".claude" / "skills" / "bmad-create-story"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL, target)
    # Also ensure the v6.4+ marker file exists so detect_layout()
    # returns "new" against this project root.
    scripts_dir = project_root / "_bmad" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "resolve_customization.py").write_text("# test marker\n")
    return target


def _seed_project_artifacts(project_root: Path) -> Path:
    """Populate the tmp project with the docs the compiler expects."""
    docs = project_root / "docs"
    docs.mkdir()
    (docs / "project_context.md").write_text("# Project Context\n\nMinimal context for tests.\n")
    (docs / "prd.md").write_text("# PRD\n\nProject requirements.\n")
    (docs / "architecture.md").write_text("# Architecture\n\nLayered.\n")
    epics_dir = docs / "epics"
    epics_dir.mkdir()
    (epics_dir / "epic-10-test.md").write_text(
        "# Epic 10: Test Epic\n\n## Story 10.1: Initial Setup\n\nContent.\n"
    )
    sprint_dir = docs / "sprint-artifacts"
    sprint_dir.mkdir()
    (sprint_dir / "sprint-status.yaml").write_text(
        "development_status:\n  10-1-initial-setup: backlog\n"
    )
    return docs


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Set up an isolated project tree with the v6.4+ skill installed."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _seed_project_artifacts(proj)
    _install_skill(proj)
    return proj


def _make_context(project_root: Path) -> CompilerContext:
    """Build a minimal compiler context pointing at the seeded docs/."""
    docs = project_root / "docs"
    return CompilerContext(
        project_root=project_root,
        output_folder=docs,
        project_knowledge=docs,
        resolved_variables={"epic_num": 10, "story_num": 1},
    )


# --------------------------------------------------------------------------- #
# Core proof-of-architecture                                                  #
# --------------------------------------------------------------------------- #


class TestSkillLayoutCompileE2E:
    """End-to-end compile through the Phase 2 path."""

    def test_compile_returns_well_shaped_compiled_workflow(self, project_root: Path) -> None:
        """``compile_workflow`` returns a non-empty, well-shaped result."""
        result = compile_workflow(
            "bmad-create-story",
            _make_context(project_root),
            skill_layout="new",
        )

        assert result.workflow_name == "bmad-create-story"
        # Body is non-empty and contains the workflow XML markers from
        # SKILL.md.
        body = result.context
        assert body, "compiled workflow body must be non-empty"
        assert "<workflow>" in body
        assert "<step n=" in body

        # The patch's regex post-process injects this CRITICAL block;
        # its presence proves we're flowing through the patch system.
        assert "SCOPE LIMITATION" in body

        # And the post-process strips sprint-status references that
        # the bare SKILL.md still mentions in its early steps.
        assert "<action>Update {{sprint_status}}</action>" not in body

    def test_skill_layout_new_routes_through_new_path(self, project_root: Path) -> None:
        """``skill_layout="new"`` picks the skill-layout compiler."""
        from bmad_assist.compiler.core import get_workflow_compiler

        compiler = get_workflow_compiler(
            "bmad-create-story",
            skill_layout="new",
            project_root=project_root,
        )
        assert type(compiler).__module__.startswith("bmad_assist.compiler.skills.")


# --------------------------------------------------------------------------- #
# Cache lifecycle                                                             #
# --------------------------------------------------------------------------- #


class TestSkillLayoutCacheLifecycle:
    """Cache write + invalidation behaviour for the skill-layout path."""

    def _cache_paths(self, project_root: Path) -> tuple[Path, Path]:
        """Return the project-local cache + meta paths for the skill."""
        cache_dir = project_root / ".bmad-assist" / "cache" / "skills"
        return (
            cache_dir / "bmad-create-story.tpl.xml",
            cache_dir / "bmad-create-story.tpl.xml.meta.yaml",
        )

    def test_cache_is_written_on_first_compile(self, project_root: Path) -> None:
        """First compile writes ``.tpl.xml`` and ``.tpl.xml.meta.yaml``."""
        cache_path, meta_path = self._cache_paths(project_root)
        assert not cache_path.exists()

        compile_workflow(
            "bmad-create-story",
            _make_context(project_root),
            skill_layout="new",
        )

        assert cache_path.is_file(), f"expected cache at {cache_path}"
        assert meta_path.is_file(), f"expected cache meta at {meta_path}"

    def test_cache_invalidates_when_customize_toml_changes(self, project_root: Path) -> None:
        """Mutating ``customize.toml`` rewrites the cache meta."""
        cache_path, meta_path = self._cache_paths(project_root)

        # First compile populates the cache.
        compile_workflow(
            "bmad-create-story",
            _make_context(project_root),
            skill_layout="new",
        )
        original_meta = meta_path.read_text(encoding="utf-8")

        # Mutate customize.toml — anything observable that changes its
        # SHA suffices.
        customize = project_root / ".claude" / "skills" / "bmad-create-story" / "customize.toml"
        customize.write_text(
            customize.read_text(encoding="utf-8") + "\n# tweak to invalidate cache\n",
            encoding="utf-8",
        )

        compile_workflow(
            "bmad-create-story",
            _make_context(project_root),
            skill_layout="new",
        )
        new_meta = meta_path.read_text(encoding="utf-8")
        assert new_meta != original_meta, (
            "mutating customize.toml must invalidate the cache and produce a new meta file"
        )

    def test_cache_invalidates_when_skill_md_changes(self, project_root: Path) -> None:
        """Mutating ``SKILL.md`` rewrites the cache meta."""
        cache_path, meta_path = self._cache_paths(project_root)

        compile_workflow(
            "bmad-create-story",
            _make_context(project_root),
            skill_layout="new",
        )
        original_meta = meta_path.read_text(encoding="utf-8")

        skill_md = project_root / ".claude" / "skills" / "bmad-create-story" / "SKILL.md"
        skill_md.write_text(
            skill_md.read_text(encoding="utf-8") + "\n<!-- mutation -->\n",
            encoding="utf-8",
        )

        compile_workflow(
            "bmad-create-story",
            _make_context(project_root),
            skill_layout="new",
        )
        new_meta = meta_path.read_text(encoding="utf-8")
        assert new_meta != original_meta

    def test_cache_meta_records_skill_layout_mode_and_hashes(self, project_root: Path) -> None:
        """Cache meta records the layout mode + content hashes."""
        import yaml

        cache_path, meta_path = self._cache_paths(project_root)

        compile_workflow(
            "bmad-create-story",
            _make_context(project_root),
            skill_layout="new",
        )

        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        assert meta["skill_layout_mode"] == "new"
        assert meta["skill_id"] == "bmad-create-story"
        assert meta["skill_md_hash"], "skill_md_hash must be populated"
        assert meta["customize_toml_hash"], "customize_toml_hash must be populated"
        # patch_hash may be empty if no patch found, but key must exist.
        assert "patch_hash" in meta
        # Phase 2.5: transform_mode is recorded so cache invalidates on
        # provider availability flips.
        assert meta["transform_mode"] in {"llm", "regex_only"}


# --------------------------------------------------------------------------- #
# Phase 2.5 — LLM transform integration                                       #
# --------------------------------------------------------------------------- #


def _stub_master_provider_config():
    """Return a real :class:`MasterProviderConfig` object.

    The skill compiler's ``_resolve_transform_mode`` only inspects
    ``config.providers.master`` for None-ness, but downstream code paths
    read ``provider``, ``model``, ``model_name`` and ``settings_path``.
    A real model gives us truthful access semantics without mocking.
    """
    from bmad_assist.core.config.models.providers import MasterProviderConfig

    return MasterProviderConfig(
        provider="claude-subprocess",
        model="opus",
        model_name="opus-test",
    )


def _stub_config_with_master(master) -> object:
    """Build a minimal config-like object with a ``providers.master``."""
    from types import SimpleNamespace

    return SimpleNamespace(
        providers=SimpleNamespace(master=master),
        timeouts=None,
        timeout=300,
        phase_models=None,
    )


class TestSkillLayoutLLMTransforms:
    """Phase 2.5: LLM-transform wiring + cache mode invalidation."""

    def test_compile_with_provider_invokes_apply_llm_transforms(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Compiler routes through ``apply_llm_transforms`` when a master provider is set.

        We mock ``apply_llm_transforms`` so no real LLM is contacted.
        The mock returns a body that satisfies the patch's
        ``must_contain`` assertions and parses as XML.
        """
        from bmad_assist.compiler.patching.types import TransformResult
        from bmad_assist.compiler.skills import bmad_create_story as skill_mod

        captured: dict[str, object] = {}

        def fake_apply(
            *,
            content: str,
            transforms: list[str],
            provider_config,
            config,
            phase_name: str,
            workflow_label: str,
            **_: object,
        ):
            captured.update(
                content=content,
                transforms=list(transforms),
                phase_name=phase_name,
                workflow_label=workflow_label,
            )
            # Echo back a body that:
            #  * preserves the <workflow> envelope so XML validation
            #    passes,
            #  * keeps a <step> and <critical> marker so the patch's
            #    must_contain rules pass,
            #  * strips the sprint-status hooks the must_not_contain
            #    rules forbid.
            transformed = (
                "<workflow>\n"
                "<critical>SCOPE LIMITATION: do nothing else.</critical>\n"
                '<step n="1" goal="Mock">noop</step>\n'
                "</workflow>"
            )
            results = [
                TransformResult(success=True, transform_index=i) for i in range(len(transforms))
            ]
            return transformed, results

        monkeypatch.setattr(skill_mod, "apply_llm_transforms", fake_apply)
        # Force "llm" mode by stubbing get_config().
        monkeypatch.setattr(
            "bmad_assist.core.config.get_config",
            lambda: _stub_config_with_master(_stub_master_provider_config()),
        )

        compile_workflow(
            "bmad-create-story",
            _make_context(project_root),
            skill_layout="new",
        )

        assert captured, "apply_llm_transforms was never called"
        assert captured["workflow_label"] == "bmad-create-story"
        # The phase name strips the bmad- prefix and underscores.
        assert captured["phase_name"] == "create_story"
        # The transforms list is the unmodified list from the patch
        # (apply_llm_transforms adds retry hints internally).
        assert isinstance(captured["transforms"], list)
        assert len(captured["transforms"]) >= 1
        # The body that was passed in must be the variable-substituted
        # SKILL.md body — i.e. it must contain the workflow envelope.
        assert "<workflow>" in captured["content"]

    def test_compile_without_provider_skips_llm_transforms(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Skip ``apply_llm_transforms`` when ``providers.master`` is None.

        Verifies the regex-only fall-back fires and an INFO log line
        explains the skip.
        """
        import logging

        from bmad_assist.compiler.skills import bmad_create_story as skill_mod

        called = {"hit": False}

        def boom(**kwargs):
            called["hit"] = True
            raise AssertionError("apply_llm_transforms should not be called")

        monkeypatch.setattr(skill_mod, "apply_llm_transforms", boom)
        monkeypatch.setattr(
            "bmad_assist.core.config.get_config",
            lambda: _stub_config_with_master(None),
        )

        caplog.set_level(logging.INFO, logger=skill_mod.logger.name)
        compile_workflow(
            "bmad-create-story",
            _make_context(project_root),
            skill_layout="new",
        )
        assert called["hit"] is False
        # An INFO log line announces the skipped transforms.
        assert any(
            "Skipping LLM transforms" in rec.message
            for rec in caplog.records
            if rec.name == skill_mod.logger.name
        )

    def test_cache_invalidates_on_transform_mode_change(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Switching transform mode between compiles must invalidate the cache."""
        import yaml as _yaml

        from bmad_assist.compiler.patching.types import TransformResult
        from bmad_assist.compiler.skills import bmad_create_story as skill_mod

        cache_dir = project_root / ".bmad-assist" / "cache" / "skills"
        meta_path = cache_dir / "bmad-create-story.tpl.xml.meta.yaml"

        # First pass: regex-only (no master configured).
        monkeypatch.setattr(
            "bmad_assist.core.config.get_config",
            lambda: _stub_config_with_master(None),
        )
        compile_workflow(
            "bmad-create-story",
            _make_context(project_root),
            skill_layout="new",
        )
        meta_v1 = _yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        assert meta_v1["transform_mode"] == "regex_only"

        # Second pass: master configured + LLM mocked.
        def fake_apply(*, content, transforms, **_):
            transformed = (
                "<workflow>\n"
                "<critical>SCOPE LIMITATION: stay focused.</critical>\n"
                '<step n="1" goal="Mock">noop</step>\n'
                "</workflow>"
            )
            return transformed, [
                TransformResult(success=True, transform_index=i) for i in range(len(transforms))
            ]

        monkeypatch.setattr(skill_mod, "apply_llm_transforms", fake_apply)
        monkeypatch.setattr(
            "bmad_assist.core.config.get_config",
            lambda: _stub_config_with_master(_stub_master_provider_config()),
        )

        compile_workflow(
            "bmad-create-story",
            _make_context(project_root),
            skill_layout="new",
        )
        meta_v2 = _yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        assert meta_v2["transform_mode"] == "llm"
        # All other hashes are stable between runs, so the only thing
        # that flipped is the mode — proves cache truly invalidated.
        assert meta_v1["skill_md_hash"] == meta_v2["skill_md_hash"]
        assert meta_v1["patch_hash"] == meta_v2["patch_hash"]

    def test_validation_must_contain_failure_raises(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If the LLM output drops a ``must_contain`` token, compile raises."""
        from bmad_assist.compiler.patching.types import TransformResult
        from bmad_assist.compiler.skills import bmad_create_story as skill_mod
        from bmad_assist.core.exceptions import CompilerError

        def bad_apply(*, content, transforms, **_):
            # Returns a well-formed <workflow> but no <step or <critical
            # markers, so the patch's must_contain rules will fail.
            transformed = "<workflow>\n<no-steps/>\n</workflow>"
            return transformed, [
                TransformResult(success=True, transform_index=i) for i in range(len(transforms))
            ]

        monkeypatch.setattr(skill_mod, "apply_llm_transforms", bad_apply)
        monkeypatch.setattr(
            "bmad_assist.core.config.get_config",
            lambda: _stub_config_with_master(_stub_master_provider_config()),
        )

        with pytest.raises(CompilerError, match="patch validation"):
            compile_workflow(
                "bmad-create-story",
                _make_context(project_root),
                skill_layout="new",
            )

    def test_workflow_xml_parse_failure_raises(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If the LLM output's ``<workflow>`` block is malformed XML, compile raises."""
        from bmad_assist.compiler.patching.types import TransformResult
        from bmad_assist.compiler.skills import bmad_create_story as skill_mod
        from bmad_assist.core.exceptions import CompilerError

        def malformed_apply(*, content, transforms, **_):
            # Mismatched <step> / </stp> — ET.fromstring will reject this.
            transformed = (
                "<workflow>\n"
                "<critical>SCOPE LIMITATION</critical>\n"
                '<step n="1">unclosed</stp>\n'
                "</workflow>"
            )
            return transformed, [
                TransformResult(success=True, transform_index=i) for i in range(len(transforms))
            ]

        monkeypatch.setattr(skill_mod, "apply_llm_transforms", malformed_apply)
        monkeypatch.setattr(
            "bmad_assist.core.config.get_config",
            lambda: _stub_config_with_master(_stub_master_provider_config()),
        )

        with pytest.raises(CompilerError, match="malformed <workflow> XML"):
            compile_workflow(
                "bmad-create-story",
                _make_context(project_root),
                skill_layout="new",
            )
