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


# --------------------------------------------------------------------------- #
# BMAD tag taxonomy alignment                                                 #
# --------------------------------------------------------------------------- #


class TestBmadTagTaxonomyAlignment:
    """Verify the compiled SKILL emits the BMAD `[Review][...]` tag taxonomy
    and the deferred-work convention rather than the legacy `[AI-Review]` tag.
    """

    def _compiled_body(self, project_root: Path) -> str:
        return compile_workflow(
            "bmad-code-review-synthesis",
            _make_context(project_root),
            skill_layout="new",
        ).context

    def test_compiled_body_does_not_mention_legacy_ai_review_tag(
        self, project_root: Path
    ) -> None:
        """Synthesis SKILL must no longer emit `[AI-Review]` action items.

        The string may appear inside negated phrasing ("NOT a legacy `[AI-Review]`"),
        so we assert the legacy tag never appears as an emitted bullet (the
        common bullet shape that was previously used).
        """
        body = self._compiled_body(project_root)
        # Legacy bullet form must be gone.
        assert "- [ ] [AI-Review]" not in body
        assert "- [x] [AI-Review]" not in body

    def test_compiled_body_emits_review_patch_decision_defer_taxonomy(
        self, project_root: Path
    ) -> None:
        """Compiled SKILL must instruct emitting the BMAD tag triple."""
        body = self._compiled_body(project_root)
        assert "[Review][Patch]" in body
        assert "[Review][Decision]" in body
        assert "[Review][Defer]" in body

    def test_compiled_body_maps_high_critical_to_patch(self, project_root: Path) -> None:
        """High/critical → `[Review][Patch]`; pre-existing/out-of-scope → `[Review][Defer]`."""
        body = self._compiled_body(project_root)
        # CRITICAL/HIGH → Patch
        assert "**CRITICAL / HIGH**" in body
        assert "→ `- [ ] [Review][Patch]" in body
        # LOW / pre-existing / out-of-scope → Defer
        assert "**LOW / pre-existing / out-of-scope**" in body
        assert "→ `- [x] [Review][Defer]" in body

    def test_compiled_body_atdd_defect_uses_review_patch(self, project_root: Path) -> None:
        """ATDD defect check must emit `[Review][Patch]`, not `[AI-Review]`."""
        body = self._compiled_body(project_root)
        assert "[Review][Patch] Activate ATDD tests" in body

    def test_compiled_body_appends_deferred_work_step(self, project_root: Path) -> None:
        """Step 6.6 must instruct appending defers to deferred-work.md."""
        body = self._compiled_body(project_root)
        # The new step must be present.
        assert "Append deferred items to deferred-work.md" in body
        assert "{implementation_artifacts}/deferred-work.md" in body
        # Heading format must match BMAD's manual `bmad-code-review` convention.
        assert "## Deferred from: code review of story-" in body

    def test_action_items_count_uses_new_tags(self, project_root: Path) -> None:
        """The 'Action Items Created' invariant must reference the new tags."""
        body = self._compiled_body(project_root)
        # The wording must refer to the new tags, not the legacy [AI-Review].
        assert "[Review][Patch]" in body
        assert "[Review][Decision]" in body
        # The legacy phrasing tying the count to `[AI-Review]` must be gone.
        assert (
            'count MUST equal the number of `[ ] [AI-Review]` tasks' not in body
        )


# --------------------------------------------------------------------------- #
# Backward-compat: dev-story scanner accepts both old and new tags            #
# --------------------------------------------------------------------------- #


class TestDevStoryScannerBackwardCompat:
    """The bmad-dev-story SKILL must still recognise legacy `[AI-Review]`
    follow-ups in story files in the wild, alongside the new BMAD tags.
    """

    def test_dev_story_skill_recognises_legacy_and_new_tags(self) -> None:
        skill_md = (
            REPO_ROOT
            / "src"
            / "bmad_assist"
            / "skills"
            / "bmad-dev-story"
            / "SKILL.md"
        ).read_text(encoding="utf-8")
        # The check guarding review-follow-up branching must accept both.
        assert "[Review][Patch]" in skill_md
        assert "[Review][Decision]" in skill_md
        assert "legacy [AI-Review]" in skill_md

    def test_dev_story_checklist_recognises_legacy_and_new_tags(self) -> None:
        checklist_md = (
            REPO_ROOT
            / "src"
            / "bmad_assist"
            / "skills"
            / "bmad-dev-story"
            / "checklist.md"
        ).read_text(encoding="utf-8")
        assert "[Review][Patch]" in checklist_md
        assert "[Review][Decision]" in checklist_md
        assert "legacy [AI-Review]" in checklist_md

    def test_dev_story_scanner_excludes_defer_from_review_followup_branch(self) -> None:
        """`[Review][Defer]` items are emitted pre-checked (`[x]`) by the
        synthesis to record acknowledged-but-not-fixed findings. They must
        NOT be classified as review-follow-up tasks the dev agent should
        attempt to resolve — that would mean re-doing work the synthesis
        explicitly chose not to do.

        Guard: the dev-story SKILL's review-follow-up check predicate must
        list `[Review][Patch]` and `[Review][Decision]` (and legacy
        `[AI-Review]`) but NOT `[Review][Defer]`. Mixing checked defers and
        unchecked patches in the same Review Follow-ups section is an
        expected shape; the scanner must skip the defers cleanly.
        """
        skill_md = (
            REPO_ROOT
            / "src"
            / "bmad_assist"
            / "skills"
            / "bmad-dev-story"
            / "SKILL.md"
        ).read_text(encoding="utf-8")

        # Find the predicate line that branches on review-follow-up tasks.
        predicate_lines = [
            line
            for line in skill_md.splitlines()
            if 'check if="task is review follow-up' in line
        ]
        assert predicate_lines, (
            "Expected at least one '<check if=\"task is review follow-up...\">' "
            "guard in dev-story SKILL.md"
        )
        for line in predicate_lines:
            assert "[Review][Patch]" in line
            assert "[Review][Decision]" in line
            assert "[AI-Review]" in line  # legacy compat
            assert "[Review][Defer]" not in line, (
                f"[Review][Defer] should NOT appear in the review-follow-up "
                f"branch predicate (defers are pre-checked, not work items): {line!r}"
            )
