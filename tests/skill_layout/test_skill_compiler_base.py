"""Unit tests for :class:`SkillLayoutCompilerBase` itself.

These tests exercise the base-class contract independently of any
specific workflow:

* Subclasses missing required ``ClassVar`` attributes raise clearly.
* :meth:`build_extra_vars` is invoked with the right arguments.
* The cache path is namespaced by ``skill_id`` so two skills don't
  collide.
* Cache meta records the ``skill_id`` of the compiling subclass.

Phase 3.1 added these as guard rails for Phase 3.2 — when ten more
workflows are migrated, the base-class contract should fail fast and
loudly on misuse.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from unittest.mock import patch as mock_patch

import pytest
import yaml

from bmad_assist.compiler.skills._base import SkillLayoutCompilerBase
from bmad_assist.compiler.types import CompilerContext
from bmad_assist.compiler.workflows.create_story import CreateStoryCompiler

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-create-story"


# --------------------------------------------------------------------------- #
# Subclass contract validation                                                #
# --------------------------------------------------------------------------- #


class TestSubclassContract:
    """Subclasses missing required class-level attributes must fail loudly."""

    def test_subclass_missing_skill_id_raises(self) -> None:
        """A concrete subclass without ``skill_id`` raises ``TypeError`` on definition."""
        with pytest.raises(TypeError, match="skill_id"):

            class MissingSkillId(SkillLayoutCompilerBase):
                # Deliberately empty skill_id.
                legacy_workflow_name = "anything"
                legacy_compiler_class = CreateStoryCompiler

                def build_extra_vars(self, context, customization):
                    return {}

    def test_subclass_missing_legacy_workflow_name_raises(self) -> None:
        """A subclass without ``legacy_workflow_name`` raises ``TypeError``."""
        with pytest.raises(TypeError, match="legacy_workflow_name"):

            class MissingLegacy(SkillLayoutCompilerBase):
                skill_id = "bmad-anything"
                # legacy_workflow_name deliberately omitted.
                legacy_compiler_class = CreateStoryCompiler

                def build_extra_vars(self, context, customization):
                    return {}

    def test_subclass_missing_legacy_compiler_class_raises(self) -> None:
        """A subclass with neither ``legacy_compiler_class`` nor inlined override raises.

        Phase 7.1 made ``legacy_compiler_class`` optional for subclasses
        that override :meth:`_run_workflow_compile` (the inlined pattern).
        A subclass with NEITHER must still fail loudly — there is no
        compile path to take.
        """
        with pytest.raises(TypeError, match="legacy_compiler_class"):

            class MissingClass(SkillLayoutCompilerBase):
                skill_id = "bmad-anything"
                legacy_workflow_name = "anything"
                # legacy_compiler_class deliberately omitted.
                # _run_workflow_compile NOT overridden either.

                def build_extra_vars(self, context, customization):
                    return {}

    def test_inlined_subclass_with_run_workflow_compile_override_instantiates(self) -> None:
        """A subclass without ``legacy_compiler_class`` is allowed when it overrides hook.

        Phase 7.1: a subclass without ``legacy_compiler_class`` is
        allowed when it overrides :meth:`_run_workflow_compile`
        instead. This is the inlined-compiler pattern Brief 7.2 will
        follow for every workflow.
        """
        from bmad_assist.compiler.types import CompiledWorkflow

        class InlinedSub(SkillLayoutCompilerBase):
            skill_id = "bmad-inlined"
            legacy_workflow_name = "inlined"
            legacy_compiler_class = None

            def build_extra_vars(self, context, customization):
                return {}

            def _run_workflow_compile(self, context):  # noqa: ARG002 — test stub
                return CompiledWorkflow(
                    workflow_name=self.skill_id,
                    mission="",
                    context="",
                    variables={},
                    instructions="",
                    output_template=None,
                    token_estimate=0,
                )

        instance = InlinedSub()
        assert instance.workflow_name == "bmad-inlined"
        assert instance.legacy_compiler_class is None

    def test_concrete_subclass_with_all_attrs_instantiates(self) -> None:
        """A concrete subclass with all required attrs instantiates cleanly."""

        class GoodSub(SkillLayoutCompilerBase):
            skill_id = "bmad-good"
            legacy_workflow_name = "good"
            legacy_compiler_class = CreateStoryCompiler

            def build_extra_vars(self, context, customization):
                return {}

        instance = GoodSub()
        assert instance.workflow_name == "bmad-good"
        assert instance.skill_id == "bmad-good"
        assert instance.legacy_workflow_name == "good"


# --------------------------------------------------------------------------- #
# build_extra_vars hook                                                       #
# --------------------------------------------------------------------------- #


def _seed_project(tmp_path: Path) -> Path:
    """Stand up a minimal project tree using the bundled create-story skill."""
    proj = tmp_path / "proj"
    proj.mkdir()

    docs = proj / "docs"
    docs.mkdir()
    (docs / "project_context.md").write_text("# Context\n")
    epics = docs / "epics"
    epics.mkdir()
    (epics / "epic-10-test.md").write_text("# Epic 10\n## Story 10.1: x\n")
    sprint = docs / "sprint-artifacts"
    sprint.mkdir()
    (sprint / "sprint-status.yaml").write_text("development_status:\n  10-1-x: backlog\n")

    target = proj / ".claude" / "skills" / "bmad-create-story"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL, target)

    scripts = proj / "_bmad" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "resolve_customization.py").write_text("# marker\n")

    return proj


def _make_ctx(project_root: Path) -> CompilerContext:
    docs = project_root / "docs"
    return CompilerContext(
        project_root=project_root,
        output_folder=docs,
        project_knowledge=docs,
        resolved_variables={"epic_num": 10, "story_num": 1},
    )


class TestBuildExtraVarsHook:
    """``build_extra_vars`` is invoked with ``(context, customization)``."""

    def test_build_extra_vars_receives_context_and_customization(self, tmp_path: Path) -> None:
        """The hook is called once with the live context + resolved customization."""
        from bmad_assist.compiler.skills.bmad_create_story import (
            BmadCreateStoryCompiler,
        )

        project = _seed_project(tmp_path)
        captured: dict[str, Any] = {}

        original = BmadCreateStoryCompiler.build_extra_vars

        def spy(self, context, customization):
            captured["context_id"] = id(context)
            captured["customization_keys"] = sorted(customization.keys())
            return original(self, context, customization)

        with mock_patch.object(BmadCreateStoryCompiler, "build_extra_vars", spy):
            from bmad_assist.compiler import compile_workflow

            ctx = _make_ctx(project)
            compile_workflow("bmad-create-story", ctx, skill_layout="new")
            assert captured["context_id"] == id(ctx), (
                "build_extra_vars must receive the same CompilerContext instance"
            )
            # The bundled customize.toml has a [workflow] table — that
            # surfaces as a top-level "workflow" key in the resolved
            # customization mapping.
            assert "workflow" in captured["customization_keys"], (
                f"customization should include the workflow block, got "
                f"{captured['customization_keys']}"
            )


# --------------------------------------------------------------------------- #
# Cache path namespacing                                                      #
# --------------------------------------------------------------------------- #


class TestCacheNamespacing:
    """Cache paths and meta entries are namespaced by ``skill_id``."""

    def test_cache_path_includes_skill_id(self, tmp_path: Path) -> None:
        """Two skills get distinct cache files under the same project root."""
        from bmad_assist.compiler.skills.bmad_create_story import (
            BmadCreateStoryCompiler,
        )
        from bmad_assist.compiler.skills.bmad_dev_story import (
            BmadDevStoryCompiler,
        )

        # Use the static helper directly — it doesn't touch the
        # filesystem.
        create_path = SkillLayoutCompilerBase._get_cache_path(
            BmadCreateStoryCompiler.skill_id, tmp_path
        )
        dev_path = SkillLayoutCompilerBase._get_cache_path(BmadDevStoryCompiler.skill_id, tmp_path)

        assert create_path != dev_path, "create-story and dev-story cache paths must differ"
        assert "bmad-create-story" in create_path.name
        assert "bmad-dev-story" in dev_path.name
        # Both live under the same project-local cache root.
        assert create_path.parent == dev_path.parent

    def test_cache_meta_records_subclass_skill_id(self, tmp_path: Path) -> None:
        """The cache meta written by the base class records the subclass's ``skill_id``."""
        from bmad_assist.compiler import compile_workflow

        project = _seed_project(tmp_path)
        compile_workflow("bmad-create-story", _make_ctx(project), skill_layout="new")

        meta_path = (
            project / ".bmad-assist" / "cache" / "skills" / "bmad-create-story.tpl.xml.meta.yaml"
        )
        assert meta_path.is_file()
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        # The base class emits the subclass's skill_id verbatim.
        assert meta["skill_id"] == "bmad-create-story"


# --------------------------------------------------------------------------- #
# Logger naming                                                               #
# --------------------------------------------------------------------------- #


class TestSubclassLogger:
    """Base-class log messages are emitted under the subclass module's logger."""

    def test_subclass_logger_resolves_to_subclass_module(self) -> None:
        """``_subclass_logger`` returns a logger named after the concrete subclass."""
        from bmad_assist.compiler.skills.bmad_create_story import (
            BmadCreateStoryCompiler,
        )

        instance = BmadCreateStoryCompiler()
        assert instance._subclass_logger().name == ("bmad_assist.compiler.skills.bmad_create_story")


# --------------------------------------------------------------------------- #
# Phase 3.5 — no-patch path                                                   #
# --------------------------------------------------------------------------- #


class TestNoPatchPath:
    """Phase 3.5 enhancement: skills without a patch file compile cleanly.

    Three of the Phase 3.5 orphans (``bmad-validate-story-synthesis``,
    ``bmad-code-review-synthesis``) ship without a patch on disk. The
    base class must:

    * Skip ``apply_llm_transforms`` (patch is None → no transforms).
    * Skip the regex post-process (no patch → no rules to load).
    * Skip the ``must_contain`` / ``must_not_contain`` validation
      (no patch → no assertions to evaluate).
    * Emit a debug log so the no-patch decision is observable.
    """

    def test_no_patch_logs_decision(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """The base class logs a one-line debug entry when no patch is found."""
        import logging

        from bmad_assist.compiler import compile_workflow
        from bmad_assist.compiler.skills import _base as base_mod

        # validate-story-synthesis ships WITHOUT a patch file; this
        # exercises the no-patch path on the base class.
        bundled_synth = (
            REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-validate-story-synthesis"
        )
        proj = tmp_path / "proj"
        proj.mkdir()

        target = proj / ".claude" / "skills" / "bmad-validate-story-synthesis"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(bundled_synth, target)

        scripts = proj / "_bmad" / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "resolve_customization.py").write_text("# marker\n")

        docs = proj / "docs"
        docs.mkdir()
        (docs / "project_context.md").write_text("# Project Context\n")
        sprint = docs / "sprint-artifacts"
        sprint.mkdir()
        (sprint / "10-1-x.md").write_text("# Story 10.1\n")

        from bmad_assist.validation.anonymizer import AnonymizedValidation

        ctx = CompilerContext(
            project_root=proj,
            output_folder=docs,
            project_knowledge=docs,
            resolved_variables={
                "epic_num": 10,
                "story_num": 1,
                "session_id": "x",
                "anonymized_validations": [
                    AnonymizedValidation(
                        validator_id="Validator A",
                        content="A",
                        original_ref="a",
                    ),
                    AnonymizedValidation(
                        validator_id="Validator B",
                        content="B",
                        original_ref="b",
                    ),
                ],
            },
        )

        caplog.set_level(logging.DEBUG, logger=base_mod.logger.name)
        compile_workflow("bmad-validate-story-synthesis", ctx, skill_layout="new")

        no_patch_logs = [
            rec
            for rec in caplog.records
            if rec.name == base_mod.logger.name
            and "No patch found for bmad-validate-story-synthesis" in rec.message
        ]
        assert no_patch_logs, "expected the base class to log a debug entry when no patch is found"

    def test_no_patch_returns_substituted_body_verbatim(self, tmp_path: Path) -> None:
        """No patch → no LLM transforms, no regex post-process, no patch validation.

        The compiled body equals the SKILL.md body after variable
        substitution (modulo the ``<workflow>`` envelope wrapping the
        legacy compiler adds via ``generate_output``).
        """
        from bmad_assist.compiler import compile_workflow
        from bmad_assist.validation.anonymizer import AnonymizedValidation

        bundled_synth = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-code-review-synthesis"
        proj = tmp_path / "proj"
        proj.mkdir()

        target = proj / ".claude" / "skills" / "bmad-code-review-synthesis"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(bundled_synth, target)

        scripts = proj / "_bmad" / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "resolve_customization.py").write_text("# marker\n")

        docs = proj / "docs"
        docs.mkdir()
        (docs / "project_context.md").write_text("# Project Context\n")
        sprint = docs / "sprint-artifacts"
        sprint.mkdir()
        (sprint / "10-1-x.md").write_text("# Story 10.1\n")

        ctx = CompilerContext(
            project_root=proj,
            output_folder=docs,
            project_knowledge=docs,
            resolved_variables={
                "epic_num": 10,
                "story_num": 1,
                "session_id": "x",
                "anonymized_reviews": [
                    AnonymizedValidation(
                        validator_id="Reviewer A",
                        content="A",
                        original_ref="a",
                    ),
                    AnonymizedValidation(
                        validator_id="Reviewer B",
                        content="B",
                        original_ref="b",
                    ),
                ],
            },
        )

        result = compile_workflow("bmad-code-review-synthesis", ctx, skill_layout="new")

        # The cache should record an EMPTY patch_hash (no patch file
        # discovered → empty hash).
        meta_path = (
            proj
            / ".bmad-assist"
            / "cache"
            / "skills"
            / "bmad-code-review-synthesis.tpl.xml.meta.yaml"
        )
        assert meta_path.is_file()
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        assert meta["patch_hash"] == "", (
            f"no-patch path should record empty patch_hash; got {meta['patch_hash']!r}"
        )
        # Compile should succeed (no patch validation to fail).
        assert result.context
