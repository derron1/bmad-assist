"""Tests for A/B comparison report generator.

Tests cover:
- Markdown report generation
- Delta calculations (positive, negative, zero)
- Duration percentage calculations
- Error section rendering
- Atomic write behavior
"""

from pathlib import Path

from bmad_assist.experiments.ab.config import ABTestConfig, ABVariantConfig, StoryRef
from bmad_assist.experiments.ab.report import generate_ab_comparison
from bmad_assist.experiments.ab.runner import ABVariantResult
from bmad_assist.experiments.runner import ExperimentStatus


def _make_config(**overrides: object) -> ABTestConfig:
    """Build an ABTestConfig with sensible defaults."""
    defaults: dict[str, object] = {
        "name": "test-ab",
        "fixture": "minimal",
        "stories": [StoryRef(id="3.1", ref="abc"), StoryRef(id="3.2", ref="def")],
        "phases": ["create-story"],
        "variant_a": ABVariantConfig(label="baseline", config="c1", patch_set="p1"),
        "variant_b": ABVariantConfig(label="experiment", config="c2", patch_set="p2"),
    }
    defaults.update(overrides)
    return ABTestConfig(**defaults)  # type: ignore[arg-type]


def _make_result(
    label: str = "baseline",
    status: ExperimentStatus = ExperimentStatus.COMPLETED,
    completed: int = 2,
    failed: int = 0,
    duration: float = 10.0,
    error: str | None = None,
) -> ABVariantResult:
    """Build an ABVariantResult with sensible defaults."""
    return ABVariantResult(
        label=label,
        status=status,
        stories_attempted=completed + failed,
        stories_completed=completed,
        stories_failed=failed,
        duration_seconds=duration,
        worktree_path=Path("/tmp/fake"),
        result_dir=Path("/tmp/fake-results"),
        error=error,
    )


class TestGenerateABComparison:
    """Tests for generate_ab_comparison."""

    def test_basic_report(self, tmp_path: Path) -> None:
        """Generate a basic comparison report."""
        cfg = _make_config()
        va = _make_result(label="baseline", completed=2, duration=10.0)
        vb = _make_result(label="experiment", completed=2, duration=8.0)
        out = tmp_path / "report.md"

        result = generate_ab_comparison(cfg, va, vb, out)

        assert result == out
        assert out.exists()
        content = out.read_text()
        assert "# A/B Test Report: test-ab" in content
        assert "baseline" in content
        assert "experiment" in content

    def test_header_metadata(self, tmp_path: Path) -> None:
        """Report header includes fixture, ref, stories, phases."""
        cfg = _make_config()
        va = _make_result(label="baseline")
        vb = _make_result(label="experiment")
        out = tmp_path / "report.md"
        generate_ab_comparison(cfg, va, vb, out)
        content = out.read_text()

        assert "**Fixture:** minimal" in content
        assert "**Stories:** 3.1, 3.2" in content
        assert "**Phases:** create-story" in content

    def test_positive_delta(self, tmp_path: Path) -> None:
        """Positive delta shown when B completes more stories."""
        cfg = _make_config()
        va = _make_result(label="a", completed=1, failed=1)
        vb = _make_result(label="b", completed=2, failed=0)
        out = tmp_path / "report.md"
        generate_ab_comparison(cfg, va, vb, out)
        content = out.read_text()
        assert "+1" in content  # +1 completed

    def test_negative_delta(self, tmp_path: Path) -> None:
        """Negative delta shown when B completes fewer stories."""
        cfg = _make_config()
        va = _make_result(label="a", completed=2)
        vb = _make_result(label="b", completed=1)
        out = tmp_path / "report.md"
        generate_ab_comparison(cfg, va, vb, out)
        content = out.read_text()
        assert "| -1 |" in content

    def test_zero_delta(self, tmp_path: Path) -> None:
        """Zero delta shown when both variants match."""
        cfg = _make_config()
        va = _make_result(label="a", completed=2)
        vb = _make_result(label="b", completed=2)
        out = tmp_path / "report.md"
        generate_ab_comparison(cfg, va, vb, out)
        content = out.read_text()
        assert "| 0 |" in content

    def test_duration_delta_and_pct(self, tmp_path: Path) -> None:
        """Duration delta shows seconds and percentage."""
        cfg = _make_config()
        va = _make_result(label="a", duration=100.0)
        vb = _make_result(label="b", duration=120.0)
        out = tmp_path / "report.md"
        generate_ab_comparison(cfg, va, vb, out)
        content = out.read_text()
        assert "100.0s" in content
        assert "120.0s" in content
        assert "+20.0s" in content
        assert "+20.0%" in content

    def test_duration_zero_a_no_crash(self, tmp_path: Path) -> None:
        """Zero duration for A does not cause division by zero."""
        cfg = _make_config()
        va = _make_result(label="a", duration=0.0)
        vb = _make_result(label="b", duration=5.0)
        out = tmp_path / "report.md"
        generate_ab_comparison(cfg, va, vb, out)
        content = out.read_text()
        assert "0.0s" in content

    def test_errors_section_both(self, tmp_path: Path) -> None:
        """Errors section appears when both variants have errors."""
        cfg = _make_config()
        va = _make_result(label="a", error="boom A")
        vb = _make_result(label="b", error="boom B")
        out = tmp_path / "report.md"
        generate_ab_comparison(cfg, va, vb, out)
        content = out.read_text()
        assert "## Errors" in content
        assert "boom A" in content
        assert "boom B" in content

    def test_errors_section_one(self, tmp_path: Path) -> None:
        """Errors section appears when only one variant has error."""
        cfg = _make_config()
        va = _make_result(label="a", error="only A failed")
        vb = _make_result(label="b")
        out = tmp_path / "report.md"
        generate_ab_comparison(cfg, va, vb, out)
        content = out.read_text()
        assert "## Errors" in content
        assert "only A failed" in content

    def test_no_errors_section(self, tmp_path: Path) -> None:
        """No errors section when neither variant has errors."""
        cfg = _make_config()
        va = _make_result(label="a")
        vb = _make_result(label="b")
        out = tmp_path / "report.md"
        generate_ab_comparison(cfg, va, vb, out)
        content = out.read_text()
        assert "## Errors" not in content

    def test_config_table(self, tmp_path: Path) -> None:
        """Configuration table shows config and patch-set names."""
        cfg = _make_config()
        va = _make_result(label="a")
        vb = _make_result(label="b")
        out = tmp_path / "report.md"
        generate_ab_comparison(cfg, va, vb, out)
        content = out.read_text()
        assert "## Configuration" in content
        assert "c1" in content  # variant_a config name
        assert "c2" in content  # variant_b config name
        assert "p1" in content  # variant_a patch_set
        assert "p2" in content  # variant_b patch_set

    def test_customize_set_in_config_table(self, tmp_path: Path) -> None:
        """Customize-Set row appears when either variant has customize_set."""
        cfg = _make_config(
            variant_a=ABVariantConfig(
                label="baseline",
                config="c1",
                patch_set="p1",
                customize_set="agents-team-frame",
            ),
            variant_b=ABVariantConfig(
                label="experiment",
                config="c2",
                patch_set="p2",
            ),
        )
        va = _make_result(label="baseline")
        vb = _make_result(label="experiment")
        out = tmp_path / "report.md"
        generate_ab_comparison(cfg, va, vb, out)
        content = out.read_text()
        assert "| Customize-Set | agents-team-frame | - |" in content

    def test_template_set_in_config_table(self, tmp_path: Path) -> None:
        """Template-Set row appears when either variant has template_set."""
        cfg = _make_config(
            variant_a=ABVariantConfig(
                label="baseline",
                config="c1",
                patch_set="p1",
            ),
            variant_b=ABVariantConfig(
                label="experiment",
                config="c2",
                patch_set="p2",
                template_set="opt-v1",
            ),
        )
        va = _make_result(label="baseline")
        vb = _make_result(label="experiment")
        out = tmp_path / "report.md"
        generate_ab_comparison(cfg, va, vb, out)
        content = out.read_text()
        assert "| Template-Set | - | opt-v1 |" in content

    def test_no_set_rows_when_both_none(self, tmp_path: Path) -> None:
        """Customize-Set and Template-Set rows omitted when both are None."""
        cfg = _make_config()
        va = _make_result(label="a")
        vb = _make_result(label="b")
        out = tmp_path / "report.md"
        generate_ab_comparison(cfg, va, vb, out)
        content = out.read_text()
        assert "Customize-Set" not in content
        assert "Template-Set" not in content

    def test_both_sets_in_config_table(self, tmp_path: Path) -> None:
        """Both Customize-Set and Template-Set rows when both variants have them."""
        cfg = _make_config(
            variant_a=ABVariantConfig(
                label="baseline",
                config="c1",
                patch_set="p1",
                customize_set="cs-a",
                template_set="tpl-a",
            ),
            variant_b=ABVariantConfig(
                label="experiment",
                config="c2",
                patch_set="p2",
                customize_set="cs-b",
                template_set="tpl-b",
            ),
        )
        va = _make_result(label="baseline")
        vb = _make_result(label="experiment")
        out = tmp_path / "report.md"
        generate_ab_comparison(cfg, va, vb, out)
        content = out.read_text()
        assert "| Customize-Set | cs-a | cs-b |" in content
        assert "| Template-Set | tpl-a | tpl-b |" in content

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """Create parent directories when writing report."""
        cfg = _make_config()
        va = _make_result(label="a")
        vb = _make_result(label="b")
        out = tmp_path / "nested" / "deep" / "report.md"
        result = generate_ab_comparison(cfg, va, vb, out)
        assert result.exists()

    def test_no_temp_file_left(self, tmp_path: Path) -> None:
        """Temp file (.md.tmp) does not remain after write."""
        cfg = _make_config()
        va = _make_result(label="a")
        vb = _make_result(label="b")
        out = tmp_path / "report.md"
        generate_ab_comparison(cfg, va, vb, out)
        assert not out.with_suffix(".md.tmp").exists()
