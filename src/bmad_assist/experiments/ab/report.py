"""A/B test comparison report generator.

Produces markdown comparison reports showing metric differences
between variant A and variant B.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from bmad_assist.experiments.ab.config import ABTestConfig

if TYPE_CHECKING:
    from bmad_assist.experiments.ab.runner import ABVariantResult

logger = logging.getLogger(__name__)


def generate_ab_comparison(
    config: ABTestConfig,
    variant_a: ABVariantResult,
    variant_b: ABVariantResult,
    output_path: Path,
) -> Path:
    """Generate a markdown comparison report for A/B test results.

    Args:
        config: Original test configuration.
        variant_a: Results for variant A.
        variant_b: Results for variant B.
        output_path: Where to write the markdown report.

    Returns:
        Path to the generated report file.

    """
    lines: list[str] = []

    lines.append(f"# A/B Test Report: {config.name}")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now(UTC).isoformat()}")
    lines.append(f"**Fixture:** {config.fixture}")
    lines.append(f"**Stories:** {', '.join(s.id for s in config.stories)}")
    lines.append(f"**Phases:** {', '.join(config.phases)}")
    lines.append("")

    # Variant summary table
    lines.append("## Results Summary")
    lines.append("")
    lines.append(
        f"| Metric | Variant A ({variant_a.label}) | Variant B ({variant_b.label}) | Delta |"
    )
    lines.append("|--------|:---:|:---:|:---:|")

    lines.append(f"| Status | {variant_a.status.value} | {variant_b.status.value} | - |")

    delta_completed = variant_b.stories_completed - variant_a.stories_completed
    delta_str = f"+{delta_completed}" if delta_completed > 0 else str(delta_completed)
    lines.append(
        f"| Stories Completed | {variant_a.stories_completed} "
        f"| {variant_b.stories_completed} | {delta_str} |"
    )

    delta_failed = variant_b.stories_failed - variant_a.stories_failed
    delta_str = f"+{delta_failed}" if delta_failed > 0 else str(delta_failed)
    lines.append(
        f"| Stories Failed | {variant_a.stories_failed} "
        f"| {variant_b.stories_failed} | {delta_str} |"
    )

    # Duration
    delta_dur = variant_b.duration_seconds - variant_a.duration_seconds
    pct = (delta_dur / variant_a.duration_seconds * 100) if variant_a.duration_seconds > 0 else 0
    lines.append(
        f"| Duration | {variant_a.duration_seconds:.1f}s "
        f"| {variant_b.duration_seconds:.1f}s "
        f"| {delta_dur:+.1f}s ({pct:+.1f}%) |"
    )
    lines.append("")

    # Configuration differences
    lines.append("## Configuration")
    lines.append("")
    lines.append("| Axis | Variant A | Variant B |")
    lines.append("|------|-----------|-----------|")

    a_cfg = config.variant_a
    b_cfg = config.variant_b
    lines.append(f"| Config | {a_cfg.config} | {b_cfg.config} |")
    lines.append(f"| Patch-Set | {a_cfg.patch_set} | {b_cfg.patch_set} |")
    if a_cfg.customize_set or b_cfg.customize_set:
        lines.append(
            f"| Customize-Set | {a_cfg.customize_set or '-'} | {b_cfg.customize_set or '-'} |"
        )
    if a_cfg.template_set or b_cfg.template_set:
        lines.append(
            f"| Template-Set | {a_cfg.template_set or '-'} | {b_cfg.template_set or '-'} |"
        )
    lines.append("")

    # Errors
    if variant_a.error or variant_b.error:
        lines.append("## Errors")
        lines.append("")
        if variant_a.error:
            lines.append(f"**Variant A ({variant_a.label}):** {variant_a.error}")
            lines.append("")
        if variant_b.error:
            lines.append(f"**Variant B ({variant_b.label}):** {variant_b.error}")
            lines.append("")

    content = "\n".join(lines) + "\n"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(".md.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(temp_path, output_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise

    logger.info("Comparison report written to: %s", output_path)
    return output_path
