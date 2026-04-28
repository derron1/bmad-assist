"""Snapshot capture + verification tests for every migrated skill-layout workflow.

Phase 7.1 captures one byte-identical snapshot per migrated workflow under
``tests/skill_layout/snapshots/<skill-id>.tpl.xml``. Snapshots are produced
by compiling each workflow against a hermetic fixture project (the same
shape each workflow's own ``test_*_e2e.py`` already builds) with all
non-deterministic inputs frozen:

* ``date.today()`` and ``datetime.now()`` are pinned to ``2026-01-15``.
* Per-LLM transforms are stubbed to no-ops (the conftest's
  ``disable_patch_compilation`` fixture already does this).
* Git diff capture returns ``""`` because the tmp project is not a git
  repository.

Phase 7.2 mechanically inlines each compiler. Every inlined compiler
must reproduce its snapshot byte-for-byte; the snapshots committed in
Phase 7.1 are the golden baseline.

To refresh: ``UPDATE_SNAPSHOTS=1 pytest tests/skill_layout/test_snapshots.py``.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from datetime import date as _date_cls
from datetime import datetime as _datetime_cls
from pathlib import Path
from typing import Any

import pytest

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.types import CompilerContext
from tests.skill_layout._snapshot_helpers import assert_compile_matches_snapshot

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "src" / "bmad_assist" / "skills"

# Stable parent for per-skill project roots. Lives under the system
# temp dir so each test session starts from the same absolute path,
# making project-root substitution in SKILL.md byte-deterministic.
_SNAPSHOT_PROJECT_ROOT = Path(tempfile.gettempdir()) / "bmad-assist-snapshot-projects"

# Frozen clock for snapshot determinism. Workflows that read date.today()
# or datetime.now() at compile time get this fixed value.
FROZEN_DATE = _date_cls(2026, 1, 15)
FROZEN_DATETIME = _datetime_cls(2026, 1, 15, 12, 0, 0)


# --------------------------------------------------------------------------- #
# Per-skill fixture helpers                                                   #
# --------------------------------------------------------------------------- #


def _install_skill(project_root: Path, skill_id: str) -> Path:
    target = project_root / ".claude" / "skills" / skill_id
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SKILLS_ROOT / skill_id, target)
    scripts_dir = project_root / "_bmad" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "resolve_customization.py").write_text("# test marker\n")
    return target


def _seed_docs(project_root: Path) -> Path:
    docs = project_root / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "project_context.md").write_text("# Project Context\n\nMinimal context.\n")
    (docs / "prd.md").write_text("# PRD\n\nProject requirements.\n")
    (docs / "architecture.md").write_text("# Architecture\n\nLayered.\n")
    return docs


def _seed_epic(docs: Path, *, epic_filename: str = "epic-10-test.md") -> None:
    epics_dir = docs / "epics"
    epics_dir.mkdir(exist_ok=True)
    (epics_dir / epic_filename).write_text(
        "# Epic 10: Test Epic\n\n## Story 10.1: Initial Setup\n\nContent.\n"
    )


def _seed_sprint_status(docs: Path, status: str) -> Path:
    sprint = docs / "sprint-artifacts"
    sprint.mkdir(exist_ok=True)
    (sprint / "sprint-status.yaml").write_text(
        f"development_status:\n  10-1-initial-setup: {status}\n"
    )
    return sprint


def _seed_story(sprint: Path, status: str, *, with_file_list: bool = False) -> None:
    body = (
        "# Story 10.1: Initial Setup\n\n"
        f"## Status\n\n{status}\n\n"
        "## Acceptance Criteria\n\n- [ ] AC1\n\n"
        "## Tasks/Subtasks\n\n- [ ] Task 1\n\n"
        "## Dev Notes\n\nMinimal notes.\n"
    )
    if with_file_list:
        body += "\n## File List\n\n- src/example.py\n"
    (sprint / "10-1-initial-setup.md").write_text(body)


# --- Per-skill fixture builders ----------------------------------------- #


def _fx_create_story(proj: Path) -> CompilerContext:
    docs = _seed_docs(proj)
    _seed_epic(docs)
    _seed_sprint_status(docs, "backlog")
    return CompilerContext(
        project_root=proj,
        output_folder=docs,
        project_knowledge=docs,
        resolved_variables={"epic_num": 10, "story_num": 1},
    )


def _fx_dev_story(proj: Path) -> CompilerContext:
    docs = _seed_docs(proj)
    _seed_epic(docs)
    sprint = _seed_sprint_status(docs, "ready-for-dev")
    _seed_story(sprint, "ready-for-dev")
    return CompilerContext(
        project_root=proj,
        output_folder=docs,
        project_knowledge=docs,
        resolved_variables={"epic_num": 10, "story_num": 1},
    )


def _fx_validate_story(proj: Path) -> CompilerContext:
    docs = _seed_docs(proj)
    _seed_epic(docs)
    sprint = _seed_sprint_status(docs, "ready-for-validation")
    _seed_story(sprint, "ready-for-validation")
    return CompilerContext(
        project_root=proj,
        output_folder=docs,
        project_knowledge=docs,
        resolved_variables={"epic_num": 10, "story_num": 1},
    )


def _fx_code_review(proj: Path) -> CompilerContext:
    docs = _seed_docs(proj)
    _seed_epic(docs)
    sprint = _seed_sprint_status(docs, "ready-for-review")
    _seed_story(sprint, "ready-for-review", with_file_list=True)
    return CompilerContext(
        project_root=proj,
        output_folder=docs,
        project_knowledge=docs,
        resolved_variables={"epic_num": 10, "story_num": 1},
    )


def _fx_retrospective(proj: Path) -> CompilerContext:
    docs = _seed_docs(proj)
    _seed_epic(docs, epic_filename="epic-10.md")
    sprint = _seed_sprint_status(docs, "done")
    _seed_story(sprint, "done")
    # Mark epic as complete so retrospective passes its precondition.
    (sprint / "sprint-status.yaml").write_text(
        "development_status:\n"
        "  10-1-initial-setup: done\n"
        "  epic-10: complete\n"
        "  epic-10-retrospective: pending\n"
    )
    return CompilerContext(
        project_root=proj,
        output_folder=docs,
        project_knowledge=docs,
        resolved_variables={"epic_num": 10},
    )


def _fx_security_review(proj: Path) -> CompilerContext:
    docs = _seed_docs(proj)
    return CompilerContext(
        project_root=proj,
        output_folder=docs,
        project_knowledge=docs,
    )


def _fx_qa_plan_execute(proj: Path) -> CompilerContext:
    output_folder = proj / "out"
    qa = output_folder / "qa-artifacts"
    plans = qa / "test-plans"
    plans.mkdir(parents=True)
    (plans / "epic-10-e2e-plan.md").write_text(
        "# E2E Test Plan - Epic 10\n\n"
        '## Setup\n```bash\nexport PROJECT_ROOT="$(pwd)"\n```\n\n'
        "## Master Checklist\n\n| ID | Test | Cat | Status |\n|----|------|-----|--------|\n"
        "| E10-A01 | Smoke | A | pending |\n\n"
        "## Category A Tests\n\n### E10-A01: Smoke\n```bash\necho ok\n```\n\n"
        "<!-- QA_PLAN_END -->\n"
    )
    return CompilerContext(
        project_root=proj,
        output_folder=output_folder,
        project_knowledge=output_folder,
        resolved_variables={"epic_num": 10},
    )


def _fx_qa_plan_generate(proj: Path) -> CompilerContext:
    docs = proj / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "prd.md").write_text("# PRD\n\nFR-1: example.\nNFR-1: example.\n")
    (docs / "architecture.md").write_text("# Architecture\n\nLayered.\n")
    epics = docs / "epics"
    epics.mkdir(exist_ok=True)
    (epics / "epic-10.md").write_text(
        "# Epic 10: Test Epic\n\nObjectives.\n\n## Story 10.1\n\nContent.\n"
    )
    (docs / "ux-elements.md").write_text(
        '# UX Elements\n\n- `[data-testid="main-panel"]`\n- `[data-testid="submit-btn"]`\n'
    )
    impl_stories = proj / "implementation-artifacts" / "stories"
    impl_stories.mkdir(parents=True)
    (impl_stories / "10-1-initial-setup.md").write_text(
        "# Story 10.1\n\n## Acceptance Criteria\n- AC-1: example\n"
    )
    return CompilerContext(
        project_root=proj,
        output_folder=proj,
        project_knowledge=proj / "docs",
        resolved_variables={"epic_num": 10},
    )


def _fx_testarch(proj: Path) -> CompilerContext:
    docs = _seed_docs(proj)
    _seed_epic(docs)
    sprint = _seed_sprint_status(docs, "ready-for-dev")
    _seed_story(sprint, "ready-for-dev")
    return CompilerContext(
        project_root=proj,
        output_folder=docs,
        project_knowledge=docs,
        resolved_variables={"epic_num": 10, "story_num": 1},
    )


def _fx_validate_story_synthesis(proj: Path) -> CompilerContext:
    from bmad_assist.validation.anonymizer import AnonymizedValidation

    docs = proj / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "project_context.md").write_text("# Project Context\n\nMinimal context.\n")
    sprint = docs / "sprint-artifacts"
    sprint.mkdir(exist_ok=True)
    (sprint / "10-1-initial-setup.md").write_text(
        "# Story 10.1: Initial Setup\n\n"
        "## Status\n\nready-for-validation\n\n"
        "## Acceptance Criteria\n\n- [ ] AC1\n"
    )
    return CompilerContext(
        project_root=proj,
        output_folder=docs,
        project_knowledge=docs,
        resolved_variables={
            "epic_num": 10,
            "story_num": 1,
            "session_id": "test-session",
            "anonymized_validations": [
                AnonymizedValidation(
                    validator_id="Validator A",
                    content="<!-- VALIDATION_REPORT_START -->\n"
                    "Found 1 critical issue: missing AC2.\n"
                    "<!-- VALIDATION_REPORT_END -->",
                    original_ref="ref-a",
                ),
                AnonymizedValidation(
                    validator_id="Validator B",
                    content="<!-- VALIDATION_REPORT_START -->\n"
                    "Found 1 critical issue: missing AC2.\n"
                    "<!-- VALIDATION_REPORT_END -->",
                    original_ref="ref-b",
                ),
            ],
        },
    )


def _fx_code_review_synthesis(proj: Path) -> CompilerContext:
    from bmad_assist.validation.anonymizer import AnonymizedValidation

    docs = proj / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "project_context.md").write_text("# Project Context\n\nMinimal context.\n")
    sprint = docs / "sprint-artifacts"
    sprint.mkdir(exist_ok=True)
    (sprint / "10-1-initial-setup.md").write_text(
        "# Story 10.1: Initial Setup\n\n"
        "## Status\n\nready-for-review\n\n"
        "## Acceptance Criteria\n\n- [x] AC1\n\n"
        "## File List\n\n- src/example.py\n"
    )
    return CompilerContext(
        project_root=proj,
        output_folder=docs,
        project_knowledge=docs,
        resolved_variables={
            "epic_num": 10,
            "story_num": 1,
            "session_id": "test-session",
            "anonymized_reviews": [
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
            ],
        },
    )


# --- Skill registry ------------------------------------------------------ #

# Maps skill_id -> fixture builder producing a CompilerContext from the
# tmp_path project root.
_SKILL_FIXTURES: dict[str, Callable[[Path], CompilerContext]] = {
    "bmad-create-story": _fx_create_story,
    "bmad-dev-story": _fx_dev_story,
    "bmad-validate-story": _fx_validate_story,
    "bmad-code-review": _fx_code_review,
    "bmad-retrospective": _fx_retrospective,
    "bmad-security-review": _fx_security_review,
    "bmad-qa-plan-execute": _fx_qa_plan_execute,
    "bmad-qa-plan-generate": _fx_qa_plan_generate,
    "bmad-testarch-atdd": _fx_testarch,
    "bmad-testarch-automate": _fx_testarch,
    "bmad-testarch-ci": _fx_testarch,
    "bmad-testarch-framework": _fx_testarch,
    "bmad-testarch-nfr": _fx_testarch,
    "bmad-testarch-test-design": _fx_testarch,
    "bmad-testarch-test-review": _fx_testarch,
    "bmad-testarch-trace": _fx_testarch,
    "bmad-validate-story-synthesis": _fx_validate_story_synthesis,
    "bmad-code-review-synthesis": _fx_code_review_synthesis,
}


# --------------------------------------------------------------------------- #
# Determinism helpers                                                         #
# --------------------------------------------------------------------------- #


@pytest.fixture
def freeze_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin every ``date.today()`` / ``datetime.now()`` site to FROZEN_*.

    Workflows compute ``date`` and ``timestamp`` at compile time. Without
    this, snapshots would drift every day. The patch targets the
    specific module attributes that each compiler imports so changes
    don't ripple beyond the compile pipeline.
    """

    class _FrozenDate(_date_cls):
        @classmethod
        def today(cls) -> _date_cls:
            return FROZEN_DATE

    class _FrozenDatetime(_datetime_cls):
        @classmethod
        def now(cls, tz: Any = None) -> _datetime_cls:
            return FROZEN_DATETIME

    # Compiler-side date/timestamp computation lives in
    # bmad_assist.compiler.variables.epic_story.
    import bmad_assist.compiler.variables.epic_story as epic_story_mod

    monkeypatch.setattr(epic_story_mod, "date", _FrozenDate)
    monkeypatch.setattr(epic_story_mod, "datetime", _FrozenDatetime)

    # Synthesis compilers compute date directly via `from datetime import date`.
    # Patch both the legacy compiler modules and the inlined skill-layout
    # compiler modules so Phase 7 inlined compilers stay deterministic.
    import bmad_assist.compiler.skills.bmad_validate_story_synthesis as inlined_vss_mod
    import bmad_assist.compiler.workflows.code_review_synthesis as crs_mod
    import bmad_assist.compiler.workflows.validate_story_synthesis as vss_mod

    monkeypatch.setattr(vss_mod, "date", _FrozenDate)
    monkeypatch.setattr(crs_mod, "date", _FrozenDate)
    monkeypatch.setattr(inlined_vss_mod, "date", _FrozenDate)

    # validate_story uses datetime.now() for a timestamp variable.
    import bmad_assist.compiler.workflows.validate_story as vs_mod

    monkeypatch.setattr(vs_mod, "datetime", _FrozenDatetime)


# --------------------------------------------------------------------------- #
# The capture / verification test                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("skill_id", sorted(_SKILL_FIXTURES.keys()))
def test_compiled_output_matches_snapshot(
    skill_id: str,
    freeze_clock: None,
) -> None:
    """Each migrated skill compiles to a byte-identical pinned snapshot.

    The snapshot is the Phase-7 baseline: Brief 7.1 captures it from the
    current delegation-based compilers; Brief 7.2 inlines each compiler
    and must reproduce the same bytes.

    The project root is built under a deterministic tmp directory keyed
    by ``skill_id`` so SKILL.md substitution (which embeds the absolute
    project path) emits the same bytes regardless of test ordering.

    Snapshot refresh: ``UPDATE_SNAPSHOTS=1 pytest tests/skill_layout/test_snapshots.py``.
    """
    proj = _SNAPSHOT_PROJECT_ROOT / skill_id / "proj"
    if proj.exists():
        shutil.rmtree(proj)
    proj.mkdir(parents=True)
    _install_skill(proj, skill_id)
    builder = _SKILL_FIXTURES[skill_id]
    ctx = builder(proj)

    result = compile_workflow(skill_id, ctx, skill_layout="new")
    assert_compile_matches_snapshot(skill_id, result.context)
