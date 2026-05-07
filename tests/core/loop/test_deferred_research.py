"""Tests for deferred-research scaffolding (Step 4 of rework-resilience).

Covers:
- Classification heuristic across all four buckets.
- End-to-end scaffolding for empirical findings.
- Idempotency: existing target dirs are not overwritten.
- deferred-work.md follow-up append (not replace).
- Mixed batches preserve per-bucket counts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad_assist.core.loop.deferred_research import (
    FindingRef,
    ScaffoldSummary,
    _classify,
    _parse_defer_findings,
    _slugify,
    scaffold_deferred_research,
)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,reason,expected",
    [
        # empirical triggers
        ("Permutation-FST degenerate", "research-methodology defect", "empirical"),
        ("Pick best stat test", "research authority needed", "empirical"),
        ("Outlier rejection", "empirical validation required", "empirical"),
        # narrative triggers
        ("Caching strategy", "literature review of LRU vs LFU", "narrative"),
        ("Distance metric", "candidate methods unknown", "narrative"),
        # architectural triggers
        ("Auth integration", "requires architectural decision", "architectural"),
        ("Deployment topology", "design decision pending", "architectural"),
        ("Platform choice", "escalated to architect for review", "architectural"),
        # out_of_scope (default)
        ("Pre-existing typo", "deferred from AI review", "out_of_scope"),
        ("Low-priority lint", "noisy stylistic preference", "out_of_scope"),
    ],
)
def test_classify_routes_correctly(title: str, reason: str, expected: str) -> None:
    finding = FindingRef(title=title, file=None, line=None, reason=reason)
    assert _classify(finding) == expected


def test_classify_first_match_wins_empirical_over_narrative() -> None:
    # Body contains both 'methodology defect' (empirical) and 'literature review'
    # (narrative). First-match-wins → empirical.
    finding = FindingRef(
        title="Statistical test choice",
        file=None,
        line=None,
        reason="methodology defect; literature review of candidates pending",
    )
    assert _classify(finding) == "empirical"


def test_classify_case_insensitive() -> None:
    finding = FindingRef(
        title="Foo Bar",
        file=None,
        line=None,
        reason="REQUIRES ARCHITECTURAL DECISION before changing flow",
    )
    assert _classify(finding) == "architectural"


# ---------------------------------------------------------------------------
# Slug derivation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Permutation-FST degenerate", "permutation-fst-degenerate"),
        ("Has  multiple   spaces", "has-multiple-spaces"),
        ("--leading and trailing--", "leading-and-trailing"),
        ("All non-alnum: !@#$%", "all-non-alnum"),
        ("", "deferred-finding"),
        ("!@#", "deferred-finding"),
    ],
)
def test_slugify_basic_cases(title: str, expected: str) -> None:
    assert _slugify(title) == expected


def test_slugify_truncates_to_max_len() -> None:
    long_title = "this-is-a-very-long-title-that-should-be-truncated-cleanly"
    slug = _slugify(long_title, max_len=20)
    assert len(slug) <= 20
    assert not slug.endswith("-")


# ---------------------------------------------------------------------------
# Defer-line parser
# ---------------------------------------------------------------------------


def test_parse_defer_lines_with_and_without_file_loc() -> None:
    report = (
        "## Action items\n\n"
        "- [x] [Review][Defer] Permutation-FST degenerate [src/algo.py:42] "
        "— research-methodology defect\n"
        "- [ ] [Review][Defer] Outlier rejection threshold "
        "— literature review of candidates pending\n"
        "- [x] [Review][Defer] Pre-existing typo [README.md] — pre-existing, low-priority\n"
        "- [ ] [Review][Patch] Should not match — different marker\n"
    )
    findings = _parse_defer_findings(report)
    assert len(findings) == 3
    assert findings[0].title == "Permutation-FST degenerate"
    assert findings[0].file == "src/algo.py"
    assert findings[0].line == 42
    assert findings[0].reason == "research-methodology defect"
    assert findings[1].file is None
    assert findings[1].line is None
    assert findings[2].file == "README.md"
    assert findings[2].line is None


# ---------------------------------------------------------------------------
# End-to-end scaffolding
# ---------------------------------------------------------------------------


def _write_synthesis_report(tmp_path: Path, lines: list[str]) -> Path:
    """Helper: write a minimal synthesis report containing the given Defer lines."""
    report = tmp_path / "synthesis-report.md"
    body = "## Action items\n\n" + "\n".join(lines) + "\n"
    report.write_text(body, encoding="utf-8")
    return report


def test_scaffold_empirical_creates_harness(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    report = _write_synthesis_report(
        tmp_path,
        [
            "- [x] [Review][Defer] Permutation-FST degenerate [src/algo.py:42] "
            "— research-methodology defect",
        ],
    )

    summary = scaffold_deferred_research(
        project_path=project,
        synthesis_report_path=report,
        epic_num=4,
        story_num=2,
        deferred_critical=1,
        deferred_high=0,
    )

    assert isinstance(summary, ScaffoldSummary)
    assert len(summary.empirical) == 1
    assert summary.errors == []

    entry = summary.empirical[0]
    assert entry.slug == "permutation-fst-degenerate"
    target = Path(entry.target_path)
    assert target.is_dir()

    # Verify all five template files copied through.
    expected_files = {
        "experiment.py",
        "benchmark.py",
        "program.md",
        "scorecard.json",
        "README.md",
    }
    actual_files = {p.name for p in target.iterdir() if p.is_file()}
    assert expected_files <= actual_files


def test_scaffold_idempotent_when_target_exists(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    report = _write_synthesis_report(
        tmp_path,
        [
            "- [x] [Review][Defer] Existing harness [src/x.py:1] — empirical validation required",
        ],
    )

    # Pre-create the target dir with a sentinel file.
    target = (
        project
        / "_bmad-output"
        / "planning-artifacts"
        / "research"
        / "existing-harness_autoresearch"
    )
    target.mkdir(parents=True)
    sentinel = target / "do_not_overwrite.txt"
    sentinel.write_text("preserved", encoding="utf-8")

    summary = scaffold_deferred_research(
        project_path=project,
        synthesis_report_path=report,
        epic_num=1,
        story_num=1,
        deferred_critical=1,
        deferred_high=0,
    )

    assert summary.empirical == []
    assert any("already exists" in err for err in summary.errors)
    # Sentinel is preserved; no template files were dropped on top of it.
    assert sentinel.read_text(encoding="utf-8") == "preserved"


def test_scaffold_appends_followup_to_deferred_work(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    deferred_work = project / "_bmad-output" / "implementation-artifacts" / "deferred-work.md"
    deferred_work.parent.mkdir(parents=True)
    deferred_work.write_text("# Existing content\n\nPrior bullet.\n", encoding="utf-8")

    report = _write_synthesis_report(
        tmp_path,
        [
            "- [x] [Review][Defer] Method choice [src/m.py:5] "
            "— research authority required for benchmark",
        ],
    )

    summary = scaffold_deferred_research(
        project_path=project,
        synthesis_report_path=report,
        epic_num=3,
        story_num=7,
        deferred_critical=1,
        deferred_high=0,
    )

    assert len(summary.empirical) == 1
    contents = deferred_work.read_text(encoding="utf-8")
    # Existing content preserved (append, not replace).
    assert "# Existing content" in contents
    assert "Prior bullet." in contents
    # Follow-up block appended.
    assert "## Empirical research follow-up:" in contents
    assert "method-choice" in contents
    assert "Source story**: 3.7" in contents
    assert "research authority required" in contents


def test_scaffold_mixed_batch_routes_each_bucket(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    report = _write_synthesis_report(
        tmp_path,
        [
            "- [x] [Review][Defer] Empirical thing [a.py:1] — empirical validation needed",
            "- [x] [Review][Defer] Narrative thing [b.py:2] — literature review pending",
            "- [x] [Review][Defer] Architectural thing [c.py:3] — requires architectural decision",
            "- [x] [Review][Defer] Out of scope thing [d.py:4] — pre-existing low-priority",
        ],
    )

    summary = scaffold_deferred_research(
        project_path=project,
        synthesis_report_path=report,
        epic_num=2,
        story_num=5,
        deferred_critical=2,
        deferred_high=2,
    )

    assert len(summary.empirical) == 1
    assert len(summary.narrative) == 1
    assert len(summary.architectural) == 1
    assert len(summary.out_of_scope) == 1
    # Only the empirical finding produces a research dir.
    research_root = project / "_bmad-output" / "planning-artifacts" / "research"
    research_dirs = [p for p in research_root.iterdir() if p.is_dir()]
    assert len(research_dirs) == 1
    assert research_dirs[0].name == "empirical-thing_autoresearch"


def test_scaffold_handles_missing_report(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    missing_report = tmp_path / "does-not-exist.md"

    summary = scaffold_deferred_research(
        project_path=project,
        synthesis_report_path=missing_report,
        epic_num=1,
        story_num=1,
        deferred_critical=1,
        deferred_high=0,
    )

    assert summary.empirical == []
    assert any("Could not read synthesis report" in err for err in summary.errors)


def test_scaffold_handles_no_defer_lines(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    report = tmp_path / "synthesis-report.md"
    report.write_text(
        "## Action items\n\n- [ ] [Review][Patch] Some patch — fix it\n",
        encoding="utf-8",
    )

    summary = scaffold_deferred_research(
        project_path=project,
        synthesis_report_path=report,
        epic_num=1,
        story_num=1,
        deferred_critical=1,
        deferred_high=0,
    )

    assert summary.empirical == []
    # Non-fatal: error recorded explaining the mismatch.
    assert any("No [Review][Defer] lines parsed" in err for err in summary.errors)


def test_scaffold_duplicate_empirical_titles_dedup_slugs(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    report = _write_synthesis_report(
        tmp_path,
        [
            "- [x] [Review][Defer] Same title [a.py:1] — empirical validation needed",
            "- [x] [Review][Defer] Same title [b.py:2] — research-methodology defect",
        ],
    )

    summary = scaffold_deferred_research(
        project_path=project,
        synthesis_report_path=report,
        epic_num=1,
        story_num=1,
        deferred_critical=2,
        deferred_high=0,
    )

    assert len(summary.empirical) == 2
    slugs = {entry.slug for entry in summary.empirical}
    assert slugs == {"same-title", "same-title-2"}
    research_root = project / "_bmad-output" / "planning-artifacts" / "research"
    dirs = sorted(p.name for p in research_root.iterdir() if p.is_dir())
    assert dirs == ["same-title-2_autoresearch", "same-title_autoresearch"]
