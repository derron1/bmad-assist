"""A/B test LLM-powered analysis report generator.

Collects all artifacts from both variants and invokes the master LLM
to produce a structured analysis report comparing the results.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bmad_assist.experiments.ab.config import ABTestConfig

logger = logging.getLogger(__name__)

# Budget limits
MAX_TOTAL_CHARS = 400_000  # ~100K tokens
MAX_FILE_CHARS = 15_000  # Truncate individual files beyond this
ANALYSIS_TIMEOUT = 600  # 10 minutes for LLM analysis


@dataclass
class _ArtifactFile:
    """A collected artifact file with metadata."""

    path: Path
    relative_name: str
    content: str
    priority: int  # Lower = more important (kept when budget exceeded)


@dataclass
class _StoryArtifacts:
    """All artifacts collected for one story in one variant."""

    story_id: str
    mappings: list[_ArtifactFile] = field(default_factory=list)
    artifacts: list[_ArtifactFile] = field(default_factory=list)
    benchmarks: list[_ArtifactFile] = field(default_factory=list)


def _read_file_truncated(path: Path, max_chars: int = MAX_FILE_CHARS) -> str:
    """Read file content, truncating if too large."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > max_chars:
            return content[:max_chars] + "\n\n[...truncated at {max_chars} chars...]"
        return content
    except Exception:
        logger.warning("Failed to read artifact: %s", path)
        return ""


def _collect_variant_artifacts(
    variant_dir: Path,
) -> list[_StoryArtifacts]:
    """Walk variant directory and collect all story artifacts.

    Expected structure:
        variant-{a,b}/
            story-{id}/
                artifacts/          -> .md files (reviews, validations, syntheses)
                artifacts/benchmarks/ -> .yaml files (per-reviewer metrics)
                bmad-assist/cache/  -> *-mapping-*.json files only

    """
    results: list[_StoryArtifacts] = []

    # Find all story-* directories
    story_dirs = sorted(
        [d for d in variant_dir.iterdir() if d.is_dir() and d.name.startswith("story-")],
        key=lambda d: d.name,
    )

    for story_dir in story_dirs:
        story_id = story_dir.name.removeprefix("story-")
        story = _StoryArtifacts(story_id=story_id)

        # Collect mapping files (highest priority - needed for deanonymization)
        cache_dir = story_dir / "bmad-assist" / "cache"
        if cache_dir.is_dir():
            for f in sorted(cache_dir.iterdir()):
                if f.is_file() and "mapping" in f.name and f.suffix == ".json":
                    content = _read_file_truncated(f)
                    if content:
                        story.mappings.append(
                            _ArtifactFile(
                                path=f,
                                relative_name=f.name,
                                content=content,
                                priority=0,
                            )
                        )

        # Collect artifact files (.md - reviews, validations, syntheses)
        artifacts_dir = story_dir / "artifacts"
        if artifacts_dir.is_dir():
            for f in sorted(artifacts_dir.rglob("*.md")):
                if f.is_file():
                    # Syntheses are higher priority than individual reviews
                    priority = 1 if "synthesis" in f.name else 2
                    content = _read_file_truncated(f)
                    if content:
                        story.artifacts.append(
                            _ArtifactFile(
                                path=f,
                                relative_name=str(f.relative_to(artifacts_dir)),
                                content=content,
                                priority=priority,
                            )
                        )

        # Collect benchmark files (.yaml)
        if artifacts_dir.is_dir():
            for f in sorted(artifacts_dir.rglob("*.yaml")):
                if f.is_file():
                    content = _read_file_truncated(f)
                    if content:
                        story.benchmarks.append(
                            _ArtifactFile(
                                path=f,
                                relative_name=str(f.relative_to(artifacts_dir)),
                                content=content,
                                priority=3,
                            )
                        )

        results.append(story)

    return results


def _build_variant_section(
    label: str,
    stories: list[_StoryArtifacts],
) -> str:
    """Build XML section for one variant's artifacts."""
    lines: list[str] = []
    lines.append(f'<variant label="{label}">')

    for story in stories:
        lines.append(f'  <story id="{story.story_id}">')

        if story.mappings:
            lines.append("    <mappings>")
            for m in story.mappings:
                lines.append(f'      <file name="{m.relative_name}">')
                lines.append(m.content)
                lines.append("      </file>")
            lines.append("    </mappings>")

        if story.artifacts:
            lines.append("    <artifacts>")
            for a in story.artifacts:
                lines.append(f'      <file name="{a.relative_name}">')
                lines.append(a.content)
                lines.append("      </file>")
            lines.append("    </artifacts>")

        if story.benchmarks:
            lines.append("    <benchmarks>")
            for b in story.benchmarks:
                lines.append(f'      <file name="{b.relative_name}">')
                lines.append(b.content)
                lines.append("      </file>")
            lines.append("    </benchmarks>")

        lines.append("  </story>")

    lines.append("</variant>")
    return "\n".join(lines)


def _estimate_chars(stories: list[_StoryArtifacts]) -> int:
    """Estimate total character count for a variant's artifacts."""
    total = 0
    for story in stories:
        for group in (story.mappings, story.artifacts, story.benchmarks):
            for f in group:
                total += len(f.content) + 100  # overhead for tags
    return total


_ANALYSIS_TEMPLATE = """\
# A/B Test Analysis: {test_name}

## Test Setup
Describe what changed between variants. List the reviewer/validator fleet \
with actual model names (deanonymized from mapping files). \
Note the synthesizer model if applicable.

## Deanonymized Results by Model
For each story, create a table mapping anonymized labels (Validator A, B, etc.) \
to actual models. Include synthesis scores, verdicts, evidence scores, and durations.

## Model-level Comparison
Compare how each unique model performed across variants. \
Group by model, show score deltas and verdict changes.

## Key Findings
Numbered findings (3-7) analyzing:
- Which models were stable vs volatile across variants
- Whether the variant change improved, degraded, or had no effect per model
- Any surprising patterns (e.g., a model getting confused by new instructions)
- Quality differences in synthesis outputs

## Duration Analysis
Total and per-phase timing breakdown. Identify which components caused \
the biggest duration deltas.

## Verdict
One-paragraph overall conclusion: did the variant change achieve its goal? \
Was it beneficial, neutral, or harmful?

## Recommendations
3-5 actionable next steps based on the findings.
"""

_SYSTEM_PROMPT = """\
You are an expert data analyst specializing in A/B test analysis for LLM workflow experiments.

You will receive all artifacts from an A/B test run — test definition, comparison summary, \
and per-variant per-story artifacts including code reviews/validations, synthesis reports, \
benchmark metrics, and model deanonymization mappings.

Your task: produce a thorough, data-driven analysis report following the provided template. \
Be specific — cite actual scores, model names, durations, and quote relevant excerpts. \
Do NOT be vague or generic. If the data is insufficient for a section, say so explicitly.

Key rules:
- Always deanonymize models using the mapping files (Validator A -> actual model name)
- Compare apples-to-apples: same model in variant A vs variant B
- Note when variance is likely noise vs a real signal
- Be honest about sample size limitations
- Output ONLY the markdown report, no preamble or meta-commentary
"""


def _build_prompt(
    config: ABTestConfig,
    result_dir: Path,
    variant_a_stories: list[_StoryArtifacts],
    variant_b_stories: list[_StoryArtifacts],
) -> str:
    """Build the full analysis prompt with all artifacts."""
    parts: list[str] = []
    parts.append("<ab-test-analysis>")

    # Test definition
    test_def_path = result_dir / "test-definition.yaml"
    if test_def_path.exists():
        parts.append("<test-definition>")
        parts.append(test_def_path.read_text(encoding="utf-8"))
        parts.append("</test-definition>")

    # Comparison summary
    comparison_path = result_dir / "comparison.md"
    if comparison_path.exists():
        parts.append("<comparison-summary>")
        parts.append(comparison_path.read_text(encoding="utf-8"))
        parts.append("</comparison-summary>")

    # Variant artifacts
    parts.append(_build_variant_section(config.variant_a.label, variant_a_stories))
    parts.append(_build_variant_section(config.variant_b.label, variant_b_stories))

    # Analysis template
    parts.append("<analysis-template>")
    parts.append(_ANALYSIS_TEMPLATE.format(test_name=config.name))
    parts.append("</analysis-template>")

    parts.append("</ab-test-analysis>")

    return "\n\n".join(parts)


def _load_analysis_config(
    config: ABTestConfig,
    experiments_dir: Path,
) -> None:
    """Load config singleton from experiment template for LLM invocation.

    Uses variant_a's config template to populate the config singleton,
    matching the same logic as ABTestRunner._build_config_dict().

    Args:
        config: A/B test configuration (uses variant_a.config).
        experiments_dir: Path to experiments directory.

    """
    from bmad_assist.core.config import Config, ProviderConfig
    from bmad_assist.core.config.loaders import load_config
    from bmad_assist.experiments.config import ConfigRegistry

    registry = ConfigRegistry(experiments_dir / "configs")
    template = registry.get(config.variant_a.config)

    if template.raw_config:
        config_dict = dict(template.raw_config)
        for key in ("name", "config_name", "description"):
            config_dict.pop(key, None)
    elif template.providers is not None:
        config_dict = Config(
            providers=ProviderConfig(
                master=template.providers.master,
                multi=template.providers.multi,
            ),
        ).model_dump()
    else:
        config_dict = {}

    load_config(config_dict)


def generate_ab_analysis(
    config: ABTestConfig,
    result_dir: Path,
    experiments_dir: Path | None = None,
) -> Path | None:
    """Generate an LLM-powered analysis report for A/B test results.

    Collects all artifacts from both variant directories, builds a
    structured prompt, invokes claude-sdk with opus, and writes
    the analysis to result_dir/analysis.md.

    Args:
        config: A/B test configuration.
        result_dir: Root result directory containing variant subdirs.
        experiments_dir: Path to experiments directory. When provided,
            self-loads the config singleton from the experiment template
            (needed when called after runner resets singletons or from
            standalone CLI).

    Returns:
        Path to generated analysis.md, or None if generation failed.

    """
    logger.info("Generating A/B analysis report via LLM...")

    # Self-load config if experiments_dir provided (standalone or post-reset)
    if experiments_dir is not None:
        _load_analysis_config(config, experiments_dir)

    # Collect artifacts from both variants
    variant_a_dir = result_dir / "variant-a"
    variant_b_dir = result_dir / "variant-b"

    variant_a_stories = _collect_variant_artifacts(variant_a_dir)
    variant_b_stories = _collect_variant_artifacts(variant_b_dir)

    total_artifacts = sum(
        len(s.mappings) + len(s.artifacts) + len(s.benchmarks)
        for s in variant_a_stories + variant_b_stories
    )
    if total_artifacts == 0:
        logger.warning("No artifacts found for analysis, skipping")
        return None

    # Check token budget
    total_chars = _estimate_chars(variant_a_stories) + _estimate_chars(variant_b_stories)
    if total_chars > MAX_TOTAL_CHARS:
        logger.warning(
            "Artifact total (%d chars) exceeds budget (%d), some files may be truncated",
            total_chars,
            MAX_TOTAL_CHARS,
        )

    # Build prompt
    prompt = _build_prompt(config, result_dir, variant_a_stories, variant_b_stories)

    full_prompt = f"{_SYSTEM_PROMPT}\n\n{prompt}"

    logger.info(
        "Analysis prompt: %d chars, %d artifacts (A: %d stories, B: %d stories)",
        len(full_prompt),
        total_artifacts,
        len(variant_a_stories),
        len(variant_b_stories),
    )

    # Invoke master LLM from config
    from bmad_assist.core.config import get_config
    from bmad_assist.providers.registry import get_provider

    analysis_config = get_config()
    master = analysis_config.providers.master
    provider = get_provider(master.provider)
    result = provider.invoke(
        full_prompt,
        model=master.model,
        display_model=master.display_model,
        timeout=ANALYSIS_TIMEOUT,
        settings_file=master.settings_path,
        disable_tools=True,
    )
    analysis_text = provider.parse_output(result)

    if not analysis_text or not analysis_text.strip():
        logger.error("LLM returned empty analysis")
        return None

    # Write analysis report
    output_path = result_dir / "analysis.md"
    temp_path = output_path.with_suffix(".md.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(analysis_text.strip() + "\n")
        os.replace(temp_path, output_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise

    logger.info(
        "Analysis report written: %s (%d chars)",
        output_path,
        len(analysis_text),
    )
    return output_path
