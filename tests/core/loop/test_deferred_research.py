"""Tests for deferred-research scaffolding (Step 4 of rework-resilience).

Covers:
- Classification heuristic across all four buckets.
- deferred-work.md parsing (latest section, missing brackets, multi-file
  brackets, line ranges).
- End-to-end scaffolding for empirical findings.
- Idempotency: existing target dirs are not overwritten.
- deferred-work.md follow-up append (not replace).
- Mixed batches preserve per-bucket counts.
- Real-data regression: algo project's round-9 fixture (story-4.4).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bmad_assist.core.loop.deferred_research import (
    FindingRef,
    ScaffoldSummary,
    _classify,
    _extract_first_file_and_line,
    _parse_deferred_work,
    _slugify,
    scaffold_deferred_research,
)


# ---------------------------------------------------------------------------
# Fixtures: real algo round-9 deferred-work.md content
# ---------------------------------------------------------------------------


# Verbatim from /Users/derron.carr/Documents/Folder/algo/_bmad-output/
# implementation-artifacts/deferred-work.md (rounds 7, 8, 9). The whole
# point of carrying all three rounds is to exercise the "use the LAST
# section" code-path — anything else would be a regression.
_ALGO_DEFERRED_WORK_ROUNDS_7_8_9 = textwrap.dedent(
    """\
    ## Deferred from: code review of story-4.4 (2026-05-06, round 7)

    - Permutation-FST methodology defect (carry-forward, round 7) [app/core/p_hacking/permutation_fst.py, app/schemas/p_hacking.py:201-207] — CRITICAL, research-blocked: `_annualized_sharpe(perm_returns)` is order-invariant under block-permutation, producing a degenerate null distribution where `p_value == 1.0` for every nonzero-variance run. Tier 2 unconditionally flags every strategy, inflating `flagged_tier_count` by 1. Resolution requires research authority to specify an order-sensitive test statistic (block bootstrap of Sharpe-of-trade-equity, sign-flip permutation, or original López de Prado FST), then patch the test statistic + `VALIDATION_PROVENANCE` + AC 15 together in a follow-up story. Defect documented at `permutation_fst.py:15-39` with "DO NOT PATCH WITHOUT RESEARCH AUTHORITY" guard. Tier 2 still runs to preserve wiring; diagnostic surface is informational only (`is_default_gate=False`).
    - Tab re-renders trigger full 3-tier computation per summary tab click (carry-forward) [app/dashboard/callbacks/optimization/results.py:217-232, app/dashboard/callbacks/optimization_results_callbacks.py:793-796] — same as round 6 deferral. UI-performance follow-up.
    - Block permutation drops tail returns (carry-forward) [app/core/p_hacking/permutation_fst.py:67-72] — same as round 6 deferral. Resolves naturally with AC-15 methodology fix.
    - CPCV leaves tail returns out of every OOS fold (carry-forward) [app/core/p_hacking/cpcv.py:90,102] — same as round 6 deferral. Architectural; resolves with data-length-aware `n_splits` or `np.array_split` rewrite.
    - Manual verification logs for AC 20 still missing (carry-forward) — same as round 5/6 deferral. Process-only gap; track as checklist item before promoting story to `done`.

    ## Deferred from: code review of story-4.4 (2026-05-07, round 8)

    - Permutation-FST methodology defect (carry-forward, round 8) [app/core/p_hacking/permutation_fst.py, app/schemas/p_hacking.py:201-207] — CRITICAL, research-blocked. Sixth re-confirmation by both validators of the same order-invariance defect: `_annualized_sharpe(perm_returns)` produces a degenerate null distribution where `p_value == 1.0` for every nonzero-variance run, so Tier 2 unconditionally flags every strategy. `VALIDATION_PROVENANCE` still cites "Permutation-FST F1=0.833" measured against an order-sensitive statistic. Resolution path unchanged: research authority must specify an order-sensitive test statistic (block bootstrap of Sharpe-of-trade-equity, sign-flip permutation, or original López de Prado FST), then patch the test statistic + provenance + AC 15 together in a follow-up story. Defect documented at `permutation_fst.py:15-39`. Diagnostic surface is informational only (`is_default_gate=False`); no scoring/aggregator/deployment depends on the signal.
    - Tab re-renders trigger full 3-tier computation (carry-forward, round 8) [app/dashboard/callbacks/optimization/results.py:217-232, app/dashboard/callbacks/optimization_results_callbacks.py:793-796] — UI-performance follow-up; `audit_log=False` already prevents the original tab-inflation audit-table-churn bug.
    - Block permutation drops tail returns (carry-forward, round 8) [app/core/p_hacking/permutation_fst.py:67-72] — minor variance bias when `len(returns) % block_size != 0`. Resolves naturally with the AC-15 methodology fix.
    - CPCV leaves tail returns out of every OOS fold (carry-forward, round 8) [app/core/p_hacking/cpcv.py:90,102] — architectural floor-division; resolves with data-length-aware `n_splits` or `np.array_split` rewrite.
    - Manual verification logs for AC 20 still missing (carry-forward, round 8) — process-only gap; track as checklist item before promoting story to `done`.

    ## Deferred from: code review of story-4.4 (2026-05-07, round 9)

    - Permutation-FST methodology defect (carry-forward, round 9) [app/core/p_hacking/permutation_fst.py, app/schemas/p_hacking.py:201-207] — CRITICAL, research-blocked. Seventh re-confirmation: `_annualized_sharpe(perm_returns)` is order-invariant under block-permutation, producing a degenerate null distribution where `p_value == 1.0` for every nonzero-variance run, so Tier 2 unconditionally flags every strategy. `VALIDATION_PROVENANCE` still cites "Permutation-FST F1=0.833" against an order-sensitive statistic. Resolution path unchanged: research authority must specify an order-sensitive test statistic (block bootstrap of Sharpe-of-trade-equity, sign-flip permutation, or original López de Prado FST), then patch the test statistic + provenance + AC 15 together in a follow-up story. Defect documented at `permutation_fst.py:15-39` with the "DO NOT PATCH WITHOUT RESEARCH AUTHORITY" guard. Diagnostic surface is informational only (`is_default_gate=False`); no scoring/aggregator/deployment depends on the signal.
    - Tab re-renders trigger full 3-tier computation (carry-forward, round 9) [app/dashboard/callbacks/optimization/results.py:217-232, app/dashboard/callbacks/optimization_results_callbacks.py:793-796] — UI-performance follow-up; `audit_log=False` already prevents the original tab-inflation audit-table-churn bug.
    - Block permutation drops tail returns (carry-forward, round 9) [app/core/p_hacking/permutation_fst.py:67-72] — minor variance bias when `len(returns) % block_size != 0`. Resolves naturally with the AC-15 methodology fix.
    - CPCV leaves tail returns out of every OOS fold (carry-forward, round 9) [app/core/p_hacking/cpcv.py:90,102] — architectural floor-division; resolves with data-length-aware `n_splits` or `np.array_split` rewrite.
    - Manual verification logs for AC 20 still missing (carry-forward, round 9) — process-only gap; track as checklist item before promoting story to `done`.
    """
)


def _write_deferred_work(project: Path, body: str) -> Path:
    """Helper: drop deferred-work.md into a project at the canonical path."""
    path = project / "_bmad-output" / "implementation-artifacts" / "deferred-work.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


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
# First-file / line extraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "blob,expected_file,expected_line",
    [
        ("src/algo.py:42", "src/algo.py", 42),
        ("src/algo.py", "src/algo.py", None),
        ("src/a.py:201-207", "src/a.py", 201),
        ("src/a.py:90,102", "src/a.py", 90),  # comma inside path bracket → second is dropped
        ("first.py:10, second.py:20", "first.py", 10),  # multi-file: take the first
        (
            "app/core/p_hacking/permutation_fst.py, app/schemas/p_hacking.py:201-207",
            "app/core/p_hacking/permutation_fst.py",
            None,
        ),
    ],
)
def test_extract_first_file_and_line(
    blob: str, expected_file: str | None, expected_line: int | None
) -> None:
    assert _extract_first_file_and_line(blob) == (expected_file, expected_line)


# ---------------------------------------------------------------------------
# deferred-work.md parser
# ---------------------------------------------------------------------------


def test_finds_latest_round_section() -> None:
    """Append-only: the LAST matching section heading is the most recent round."""
    findings = _parse_deferred_work(_ALGO_DEFERRED_WORK_ROUNDS_7_8_9, 4, 4)
    # Round 9 has 5 bullets.
    assert len(findings) == 5
    # First bullet's title carries the round-9 marker, not 7 or 8.
    assert "round 9" in findings[0].title


def test_handles_missing_brackets() -> None:
    """A bullet without [file:line] still parses; files becomes None."""
    findings = _parse_deferred_work(_ALGO_DEFERRED_WORK_ROUNDS_7_8_9, 4, 4)
    # The "Manual verification logs" bullet has no bracket.
    manual = next(f for f in findings if "Manual verification logs" in f.title)
    assert manual.file is None
    assert manual.line is None
    assert "process-only gap" in manual.reason
    assert _classify(manual) == "out_of_scope"


def test_handles_multi_file_brackets() -> None:
    """Two files in brackets → take the first; range like 201-207 → 201."""
    findings = _parse_deferred_work(_ALGO_DEFERRED_WORK_ROUNDS_7_8_9, 4, 4)
    perm_fst = next(f for f in findings if f.title.startswith("Permutation-FST"))
    # Two files in the bracket. Parser takes the first, which has no :line.
    assert perm_fst.file == "app/core/p_hacking/permutation_fst.py"
    assert perm_fst.line is None
    # The "Block permutation" bullet's bracket is single-file with a range.
    block_perm = next(f for f in findings if f.title.startswith("Block permutation"))
    assert block_perm.file == "app/core/p_hacking/permutation_fst.py"
    assert block_perm.line == 67


def test_no_matching_section_returns_empty() -> None:
    """No heading for the requested story → empty list, no errors."""
    text = "## Deferred from: code review of story-1.1 (2026-01-01)\n\n- Foo — bar\n"
    assert _parse_deferred_work(text, 99, 99) == []


# ---------------------------------------------------------------------------
# Real-data regression: algo round-9 classification
# ---------------------------------------------------------------------------


def test_classifies_round9_algo_data(tmp_path: Path) -> None:
    """Round 9 of algo story-4.4: 5 items, classification = (1, 0, 0, 4)."""
    project = tmp_path / "project"
    project.mkdir()
    _write_deferred_work(project, _ALGO_DEFERRED_WORK_ROUNDS_7_8_9)

    summary = scaffold_deferred_research(
        project_path=project,
        epic_num=4,
        story_num=4,
        deferred_critical=1,
        deferred_high=2,
    )

    assert isinstance(summary, ScaffoldSummary)
    # 1 empirical: Permutation-FST methodology defect (research-blocked)
    assert len(summary.empirical) == 1, f"empirical={[e.finding.title for e in summary.empirical]}"
    assert summary.empirical[0].finding.title.startswith("Permutation-FST")
    # 0 narrative: no literature triggers in any of the 5 bullets
    assert len(summary.narrative) == 0
    # 0 architectural: CPCV's "architectural floor-division" doesn't match any
    # exact-substring trigger (architectural decision / design decision / etc.)
    assert len(summary.architectural) == 0, (
        f"architectural={[f.title for f in summary.architectural]}"
    )
    # 4 out_of_scope: Tab re-renders, Block permutation, CPCV, AC 20 manual logs
    assert len(summary.out_of_scope) == 4


def test_scaffolds_permutation_fst_for_real_data(tmp_path: Path) -> None:
    """End-to-end: algo round-9 → permutation-fst slug + 5 template files."""
    project = tmp_path / "project"
    project.mkdir()
    _write_deferred_work(project, _ALGO_DEFERRED_WORK_ROUNDS_7_8_9)

    summary = scaffold_deferred_research(
        project_path=project,
        epic_num=4,
        story_num=4,
        deferred_critical=1,
        deferred_high=2,
    )

    assert summary.errors == [], f"errors={summary.errors}"
    assert len(summary.empirical) == 1
    entry = summary.empirical[0]
    assert entry.slug.startswith("permutation-fst-")

    target = Path(entry.target_path)
    assert target.is_dir()
    expected_files = {
        "experiment.py",
        "benchmark.py",
        "program.md",
        "scorecard.json",
        "README.md",
    }
    actual_files = {p.name for p in target.iterdir() if p.is_file()}
    assert expected_files <= actual_files

    # Follow-up appended to deferred-work.md
    deferred_path = project / "_bmad-output" / "implementation-artifacts" / "deferred-work.md"
    contents = deferred_path.read_text(encoding="utf-8")
    assert "## Empirical research follow-up:" in contents
    assert "Source story**: 4.4" in contents


# ---------------------------------------------------------------------------
# End-to-end scaffolding (smaller fixtures)
# ---------------------------------------------------------------------------


def _build_section(epic: int, story: int, bullets: list[str], date: str = "2026-05-06") -> str:
    """Build a minimal `## Deferred from:` section with the given bullets."""
    bullet_block = "\n".join(bullets)
    return f"## Deferred from: code review of story-{epic}.{story} ({date})\n\n{bullet_block}\n"


def test_scaffold_empirical_creates_harness(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    section = _build_section(
        4,
        2,
        ["- Permutation-FST degenerate [src/algo.py:42] — research-methodology defect"],
    )
    _write_deferred_work(project, section)

    summary = scaffold_deferred_research(
        project_path=project,
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
    section = _build_section(
        1,
        1,
        ["- Existing harness [src/x.py:1] — empirical validation required"],
    )
    _write_deferred_work(project, section)

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
    section = _build_section(
        3,
        7,
        ["- Method choice [src/m.py:5] — research authority required for benchmark"],
    )
    # Pre-existing content in the file followed by the section.
    body = "# Existing content\n\nPrior bullet.\n\n" + section
    deferred_work = _write_deferred_work(project, body)

    summary = scaffold_deferred_research(
        project_path=project,
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
    section = _build_section(
        2,
        5,
        [
            "- Empirical thing [a.py:1] — empirical validation needed",
            "- Narrative thing [b.py:2] — literature review pending",
            "- Architectural thing [c.py:3] — requires architectural decision",
            "- Out of scope thing [d.py:4] — pre-existing low-priority",
        ],
    )
    _write_deferred_work(project, section)

    summary = scaffold_deferred_research(
        project_path=project,
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


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    """No deferred-work.md → empty summary, no errors, info-logged."""
    project = tmp_path / "project"
    project.mkdir()

    summary = scaffold_deferred_research(
        project_path=project,
        epic_num=1,
        story_num=1,
        deferred_critical=1,
        deferred_high=0,
    )

    assert summary.empirical == []
    assert summary.errors == []


def test_no_matching_section_in_existing_file_returns_empty(tmp_path: Path) -> None:
    """File exists but no heading for this story → empty summary, no errors."""
    project = tmp_path / "project"
    project.mkdir()
    # Section is for a different story.
    _write_deferred_work(
        project,
        _build_section(1, 1, ["- Some other story bullet — out-of-scope"]),
    )

    summary = scaffold_deferred_research(
        project_path=project,
        epic_num=99,
        story_num=99,
        deferred_critical=1,
        deferred_high=0,
    )

    assert summary.empirical == []
    assert summary.errors == []


def test_scaffold_duplicate_empirical_titles_dedup_slugs(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    section = _build_section(
        1,
        1,
        [
            "- Same title [a.py:1] — empirical validation needed",
            "- Same title [b.py:2] — research-methodology defect",
        ],
    )
    _write_deferred_work(project, section)

    summary = scaffold_deferred_research(
        project_path=project,
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
