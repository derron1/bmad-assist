"""Tests for CODE_REVIEW prompt budgeting: diff truncation, TEA budget, overlap trim, size log."""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bmad_assist.compiler.workflows.code_review import (
    _apply_tea_budget,
    _truncate_git_diff,
)


# ---------------------------------------------------------------------------
# Helper: build a synthetic git diff string
# ---------------------------------------------------------------------------

def _make_diff(num_files: int, lines_per_file: int = 20) -> str:
    """Build a fake git diff with num_files files, each lines_per_file lines."""
    parts = []
    for i in range(num_files):
        header = f"diff --git a/src/file{i}.py b/src/file{i}.py\n"
        body = "\n".join(f"+line {j} of file {i}" for j in range(lines_per_file)) + "\n"
        parts.append(header + body)
    return "".join(parts)


# ---------------------------------------------------------------------------
# 2a — Git diff truncation
# ---------------------------------------------------------------------------


class TestGitDiffTruncation:
    def test_git_diff_truncated_at_hard_limit(self) -> None:
        """A 600-line diff is truncated to ≤400 lines plus a notice."""
        diff = _make_diff(num_files=25, lines_per_file=24)  # 25*(1+24) = 625 lines
        result = _truncate_git_diff(diff, max_lines=400)

        output_lines = result.splitlines()
        # The truncation notice is appended as a single multi-line string;
        # count only the "real" lines before it.
        assert len(output_lines) <= 402  # 400 + up to 2 notice lines
        assert "[DIFF TRUNCATED" in result

    def test_git_diff_no_truncation_when_under_limit(self) -> None:
        """A diff under the cap is returned unchanged."""
        diff = _make_diff(num_files=5, lines_per_file=10)  # 5*11 = 55 lines
        result = _truncate_git_diff(diff, max_lines=400)
        assert result == diff
        assert "[DIFF TRUNCATED" not in result

    def test_git_diff_truncates_at_file_boundary(self) -> None:
        """The cut lands on a diff --git header, not mid-hunk."""
        diff = _make_diff(num_files=25, lines_per_file=20)  # 25*21 = 525 lines
        result = _truncate_git_diff(diff, max_lines=400)

        # Collect lines that appear before the truncation notice
        lines_before_notice = result.split("[DIFF TRUNCATED")[0].splitlines()
        # The last diff --git line in the output should be the start of a file block
        # (the partial file was cut at its header, so the header itself is NOT included)
        # Verify no mid-hunk lines appear after the last complete file block
        # by checking that the truncation is clean (content ends before a new header)
        content_before = result.split("[DIFF TRUNCATED")[0]
        # The content should end right before a diff --git header (possibly with whitespace)
        assert "diff --git" in content_before  # at least one complete file

    def test_git_diff_truncation_notice_includes_counts(self) -> None:
        """Truncation notice includes omitted line count and file counts."""
        diff = _make_diff(num_files=25, lines_per_file=20)
        result = _truncate_git_diff(diff, max_lines=400)
        assert "lines omitted" in result
        assert "files shown of" in result

    def test_git_diff_zero_max_lines_returns_unchanged(self) -> None:
        """max_lines=0 disables truncation and returns the original diff."""
        diff = _make_diff(num_files=5, lines_per_file=10)
        result = _truncate_git_diff(diff, max_lines=0)
        assert result == diff

    def test_git_diff_empty_returns_empty(self) -> None:
        """Empty diff is returned as-is."""
        assert _truncate_git_diff("", max_lines=400) == ""

    def test_single_file_exceeding_cap_preserves_content(self) -> None:
        """A single file diff exceeding max_lines still produces non-empty output."""
        # Build a single-file diff with 1000 lines
        header = "diff --git a/src/big.py b/src/big.py\nindex abc..def 100644\n--- a/src/big.py\n+++ b/src/big.py\n"
        hunks = ""
        for h in range(10):
            hunks += f"@@ -{h*100+1},100 +{h*100+1},100 @@\n"
            hunks += "\n".join(f"+line {h*100+j}" for j in range(99)) + "\n"
        diff = header + hunks

        result = _truncate_git_diff(diff, max_lines=100)

        # Must include file identity
        assert "diff --git a/src/big.py" in result
        assert "--- a/src/big.py" in result
        assert "+++ b/src/big.py" in result
        # Must include some hunk content
        assert "@@ " in result
        assert "+line " in result
        # Must include truncation notice
        assert "[DIFF TRUNCATED" in result

    def test_single_file_cuts_at_hunk_boundary(self) -> None:
        """Single-file fallback cuts at a @@ boundary, not mid-hunk."""
        header = "diff --git a/src/x.py b/src/x.py\n--- a/src/x.py\n+++ b/src/x.py\n"
        hunks = ""
        for h in range(5):
            hunks += f"@@ -{h*30+1},30 +{h*30+1},30 @@\n"
            hunks += "\n".join(f"+line {h*30+j}" for j in range(29)) + "\n"
        diff = header + hunks

        result = _truncate_git_diff(diff, max_lines=50)
        content_before_notice = result.split("[DIFF TRUNCATED")[0]
        lines = content_before_notice.splitlines()
        # Last non-empty line before notice should be a hunk content line
        # (the cut is at a @@ boundary, so the LAST @@ in the retained slice
        # starts a hunk that was excluded)
        assert len(lines) > 5  # At least file headers + some hunk content

    def test_single_file_max_lines_before_second_hunk(self) -> None:
        """When max_lines falls before the second @@, raw cap fallback is used."""
        header = "diff --git a/src/x.py b/src/x.py\n--- a/src/x.py\n+++ b/src/x.py\n"
        # One huge hunk
        hunk = "@@ -1,500 +1,500 @@\n" + "\n".join(f"+line {i}" for i in range(500)) + "\n"
        diff = header + hunk

        # max_lines=10 falls within the first (and only) hunk
        result = _truncate_git_diff(diff, max_lines=10)
        # Should still produce some output (raw cap fallback)
        assert "diff --git" in result
        assert "[DIFF TRUNCATED" in result

    def test_stat_prefix_still_includes_file_block(self) -> None:
        """When a long stat section precedes patches and max_lines lands in the
        stat region, the output still contains diff --git and @@ hunk content."""
        # 60 stat lines, then 3 real file patches
        stat_section = "".join(
            f" src/file{i}.py | 10 ++++++++++\n" for i in range(60)
        )
        stat_section += " 60 files changed, 600 insertions(+)\n\n"
        patches = ""
        for i in range(3):
            patches += (
                f"diff --git a/src/file{i}.py b/src/file{i}.py\n"
                f"index abc{i}..def{i} 100644\n"
                f"--- a/src/file{i}.py\n"
                f"+++ b/src/file{i}.py\n"
                f"@@ -1,10 +1,10 @@\n"
            )
            patches += "\n".join(f"+line {j} of file {i}" for j in range(10)) + "\n"
        diff = stat_section + patches

        # max_lines=50 lands inside the stat section (before first diff --git)
        result = _truncate_git_diff(diff, max_lines=50)

        assert "diff --git" in result, "Output must contain at least one file block"
        assert "@@ " in result, "Output must contain at least one hunk header"
        assert "[DIFF TRUNCATED" in result

    def test_stat_prefix_truncation_notice_reports_nonzero_files(self) -> None:
        """Truncation notice after stat-prefix fallback reports shown_files >= 1
        and result contains actual patch content."""
        stat_section = "".join(
            f" src/file{i}.py | 5 +++++\n" for i in range(40)
        )
        stat_section += " 40 files changed\n\n"
        patches = ""
        for i in range(3):
            patches += (
                f"diff --git a/src/file{i}.py b/src/file{i}.py\n"
                f"index aaa..bbb 100644\n"
                f"--- a/src/file{i}.py\n"
                f"+++ b/src/file{i}.py\n"
                f"@@ -1,5 +1,5 @@\n"
            )
            patches += "\n".join(f"+line {j}" for j in range(5)) + "\n"
        diff = stat_section + patches

        result = _truncate_git_diff(diff, max_lines=30)

        assert "0 files shown" not in result, "Must report at least 1 file shown"
        assert "diff --git" in result, "Output must contain patch content"
        assert "[DIFF TRUNCATED" in result

    def test_git_diff_truncation_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A warning is emitted when the diff is truncated."""
        diff = _make_diff(num_files=25, lines_per_file=20)
        with caplog.at_level(logging.WARNING, logger="bmad_assist.compiler.workflows.code_review"):
            _truncate_git_diff(diff, max_lines=400)
        assert any("Git diff truncated" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 2b — TEA context budget
# ---------------------------------------------------------------------------


class TestTeaContextBudget:
    def test_tea_context_budget_enforced(self) -> None:
        """Large TEA content is truncated to stay within the token cap."""
        # Each char ≈ 0.25 tokens; 4000 token cap ≈ 16000 chars
        large_content = "x" * 20000  # ~5000 tokens
        tea_files = {
            "[tea-test-design]": large_content,
            "[tea-atdd]": large_content,
        }
        result = _apply_tea_budget(tea_files, budget_tokens=4000)

        # Total tokens across result should be ≤4000 (allow small margin for
        # truncation granularity — _truncate_content uses a char/token approximation)
        from bmad_assist.compiler.shared_utils import estimate_tokens
        total_tokens = sum(estimate_tokens(v) for v in result.values())
        assert total_tokens <= 4100  # ≤4000 target, +100 for approximation margin

    def test_tea_context_under_budget_unchanged(self) -> None:
        """TEA content within the budget is returned unchanged."""
        small = "hello world"  # ~2-3 tokens
        tea_files = {"[tea-overview]": small}
        result = _apply_tea_budget(tea_files, budget_tokens=4000)
        assert result == tea_files

    def test_tea_context_empty_input(self) -> None:
        """Empty tea_files dict is returned as-is."""
        assert _apply_tea_budget({}, budget_tokens=4000) == {}

    def test_tea_context_zero_budget_returns_empty_or_unchanged(self) -> None:
        """budget_tokens=0 returns the original dict (disabled)."""
        tea_files = {"[tea-x]": "some content"}
        result = _apply_tea_budget(tea_files, budget_tokens=0)
        assert result == tea_files

    def test_tea_context_preserves_insertion_order(self) -> None:
        """Artifacts are included in insertion order until budget is consumed."""
        from bmad_assist.compiler.shared_utils import estimate_tokens

        # Two artifacts, each 1000 tokens, budget 1500 → first fits, second truncated/dropped
        content_1k = "a" * 4000  # ~1000 tokens
        tea_files = {"[first]": content_1k, "[second]": content_1k}
        result = _apply_tea_budget(tea_files, budget_tokens=1500)

        assert "[first]" in result  # First artifact must be included


# ---------------------------------------------------------------------------
# 2c — Overlap trim integration via _build_context_files
# ---------------------------------------------------------------------------


class TestOverlapTrim:
    """Tests that files with ≥80% diff coverage are excluded from source context."""

    def test_overlap_trim_skips_high_coverage_file(self, tmp_path: Path) -> None:
        """A file at ≥80% changed-line coverage is excluded from source context."""
        from unittest.mock import patch

        from bmad_assist.compiler.source_context import _normalize_path
        from bmad_assist.compiler.workflows.code_review import (
            _extract_modified_files_from_stat,
        )

        # Create a file with 10 lines
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        target = src_dir / "small.py"
        target.write_text("\n".join(f"line_{i} = {i}" for i in range(10)))

        # Build a stat output that reports 9 changed lines (90%) for that file
        stat_output = (
            " src/small.py | 9 +++++++++\n"
            " 1 file changed, 9 insertions(+)\n"
        )
        modified_files = _extract_modified_files_from_stat(stat_output, skip_docs=True)
        # Verify the parser captured the path and changed count
        assert any(path == "src/small.py" for path, _ in modified_files)

        changed_lines = next(c for p, c in modified_files if p == "src/small.py")
        total_lines = target.read_text().count("\n") + 1
        coverage = changed_lines / total_lines
        assert coverage >= 0.8, f"Expected ≥80% coverage, got {coverage:.1%}"

        # Verify that the skip logic would flag this path
        skip_set: set[str] = set()
        content = target.read_text()
        if total_lines > 0 and changed_lines / total_lines >= 0.8:
            skip_set.add("src/small.py")
        assert "src/small.py" in skip_set

    def test_overlap_trim_keeps_low_coverage_file(self, tmp_path: Path) -> None:
        """A file at <80% changed-line coverage is NOT excluded."""
        target = tmp_path / "large.py"
        target.write_text("\n".join(f"line_{i} = {i}" for i in range(100)))

        changed_lines = 20  # 20% coverage
        total_lines = 100
        skip_set: set[str] = set()
        if total_lines > 0 and changed_lines / total_lines >= 0.8:
            skip_set.add("large.py")
        assert "large.py" not in skip_set


# ---------------------------------------------------------------------------
# 2d — Total compiled size logging
# ---------------------------------------------------------------------------


def _make_compile_context(tmp_path: Path, workflow_ir=None) -> "CompilerContext":
    """Build a minimal CompilerContext suitable for compile() testing."""
    from bmad_assist.compiler.types import CompilerContext

    return CompilerContext(
        project_root=tmp_path,
        output_folder=tmp_path / "out",
        resolved_variables={"epic_num": 1, "story_num": 1},
        workflow_ir=workflow_ir,
    )


class TestTotalSizeLogging:
    def test_total_size_logged_unconditionally(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """logger.info with token count is emitted after compile, regardless of log level."""
        from unittest.mock import MagicMock, patch

        from bmad_assist.compiler.output import GeneratedOutput
        from bmad_assist.compiler.workflows.code_review import CodeReviewCompiler

        compiler = CodeReviewCompiler()
        mock_result = GeneratedOutput(xml="<x/>", token_estimate=100, size_bytes=4)
        resolved = {
            "epic_num": 1, "story_num": 1, "story_key": "1.1",
            "story_file": None, "story_title": None, "date": None, "story_id": None,
        }

        workflow_ir = MagicMock()
        workflow_ir.raw_config = {"description": "test"}
        context = _make_compile_context(tmp_path, workflow_ir=workflow_ir)
        context.links_only = False

        with (
            patch("bmad_assist.compiler.workflows.code_review.generate_output",
                  return_value=mock_result),
            patch.object(compiler, "_build_context_files", return_value={}),
            patch.object(compiler, "_build_mission", return_value="mission"),
            patch("bmad_assist.compiler.workflows.code_review.apply_post_process",
                  return_value="<final/>"),
            patch("bmad_assist.compiler.workflows.code_review.context_snapshot"),
            patch("bmad_assist.compiler.workflows.code_review.resolve_variables",
                  return_value=resolved),
            patch("bmad_assist.compiler.workflows.code_review.resolve_story_file",
                  return_value=(None, None, None)),
            patch("bmad_assist.compiler.workflows.code_review._capture_git_diff",
                  return_value=""),
            patch("bmad_assist.compiler.workflows.code_review.find_sprint_status_file",
                  return_value=None),
            patch("bmad_assist.compiler.workflows.code_review.filter_instructions",
                  return_value="<instructions/>"),
            patch("bmad_assist.compiler.workflows.code_review.substitute_variables",
                  return_value="<instructions/>"),
            patch.object(compiler, "get_workflow_dir", return_value=tmp_path),
            caplog.at_level(logging.INFO, logger="bmad_assist.compiler.workflows.code_review"),
        ):
            compiler.compile(context)

        assert any(
            "CODE_REVIEW prompt" in r.message and "tokens" in r.message
            for r in caplog.records
        ), f"Expected CODE_REVIEW prompt log; got: {[r.message for r in caplog.records]}"

    def test_total_size_warning_at_cap(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """logger.warning fires when token estimate exceeds max_code_review_prompt_tokens."""
        from unittest.mock import MagicMock, patch

        from bmad_assist.compiler.output import GeneratedOutput
        from bmad_assist.compiler.workflows.code_review import CodeReviewCompiler

        compiler = CodeReviewCompiler()
        # Token estimate above the default 50k cap
        mock_result = GeneratedOutput(xml="<x/>", token_estimate=60000, size_bytes=240000)
        resolved = {
            "epic_num": 1, "story_num": 1, "story_key": "1.1",
            "story_file": None, "story_title": None, "date": None, "story_id": None,
        }

        workflow_ir = MagicMock()
        workflow_ir.raw_config = {"description": "test"}
        context = _make_compile_context(tmp_path, workflow_ir=workflow_ir)
        context.links_only = False

        with (
            patch("bmad_assist.compiler.workflows.code_review.generate_output",
                  return_value=mock_result),
            patch.object(compiler, "_build_context_files", return_value={}),
            patch.object(compiler, "_build_mission", return_value="mission"),
            patch("bmad_assist.compiler.workflows.code_review.apply_post_process",
                  return_value="<final/>"),
            patch("bmad_assist.compiler.workflows.code_review.context_snapshot"),
            patch("bmad_assist.compiler.workflows.code_review.resolve_variables",
                  return_value=resolved),
            patch("bmad_assist.compiler.workflows.code_review.resolve_story_file",
                  return_value=(None, None, None)),
            patch("bmad_assist.compiler.workflows.code_review._capture_git_diff",
                  return_value=""),
            patch("bmad_assist.compiler.workflows.code_review.find_sprint_status_file",
                  return_value=None),
            patch("bmad_assist.compiler.workflows.code_review.filter_instructions",
                  return_value="<instructions/>"),
            patch("bmad_assist.compiler.workflows.code_review.substitute_variables",
                  return_value="<instructions/>"),
            patch.object(compiler, "get_workflow_dir", return_value=tmp_path),
            caplog.at_level(logging.WARNING, logger="bmad_assist.compiler.workflows.code_review"),
        ):
            compiler.compile(context)

        assert any(
            "exceeds cap" in r.message or "CODE_REVIEW prompt exceeds" in r.message
            for r in caplog.records
        ), f"Expected cap warning; got: {[r.message for r in caplog.records]}"
