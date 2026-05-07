"""Deferred-research scaffolding for the CODE_REVIEW_SYNTHESIS phase.

When code-review-synthesis emits ``deferred_critical + deferred_high > 0``,
each ``[Review][Defer]`` finding in the synthesis report is classified into
one of four buckets — empirical, narrative, architectural, or out-of-scope —
based on its body text. Empirical findings get an autoresearch harness
scaffolded under ``{project}/_bmad-output/planning-artifacts/research/``;
the other three are logged-only.

Step 4 of the rework-resilience trilogy. See:
- ``docs/recipes/autoresearch.md`` for the recipe and iteration contract
- ``src/bmad_assist/skills/bmad-code-review-synthesis/SKILL.md`` for the
  ``[Review][Defer]`` marker semantics
- prior commits ``12d5410``, ``9358d38``, ``3c272e3`` for Steps 1-3

The scaffold is **non-blocking**: callers wrap invocations in a broad
``except`` and log warnings rather than failing the synthesis phase. This
module's public functions never raise on a normal scaffold path; they
record problems in :attr:`ScaffoldSummary.errors` and continue.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from bmad_assist.templates.resolver import autoresearch_template_dir

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FindingRef:
    """A reference to a single ``[Review][Defer]`` finding parsed from the report."""

    title: str
    file: str | None
    line: int | None
    reason: str


@dataclass(frozen=True)
class ScaffoldEntry:
    """Bookkeeping for one empirical finding that was scaffolded."""

    slug: str
    target_path: str  # absolute path as string, JSON-serialisable
    finding: FindingRef


@dataclass(frozen=True)
class ScaffoldSummary:
    """Result of a scaffold pass over a synthesis report.

    Attributes:
        empirical: Findings routed to the autoresearch harness.
        narrative: Findings classified as needing literature work (log-only).
        architectural: Findings classified as design decisions (log-only).
        out_of_scope: Findings that didn't match any heuristic (no extra log;
            synthesis already wrote them to deferred-work.md).
        errors: Non-fatal problems encountered during scaffolding (e.g.
            target dir already exists, template missing).

    """

    empirical: list[ScaffoldEntry] = field(default_factory=list)
    narrative: list[FindingRef] = field(default_factory=list)
    architectural: list[FindingRef] = field(default_factory=list)
    out_of_scope: list[FindingRef] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Classification heuristic
# ---------------------------------------------------------------------------


# First-match-wins. Order matters: empirical → narrative → architectural →
# out-of-scope (default). Substrings are matched case-insensitively against
# the finding's full body text (title + reason).
_EMPIRICAL_TRIGGERS: tuple[str, ...] = (
    "methodology defect",
    "methodology change",
    "research authority",
    "research-blocked",
    "research-methodology",
    "research methodology",
    "which method works",
    "validate against benchmark",
    "which candidate",
    "empirical validation",
)

_NARRATIVE_TRIGGERS: tuple[str, ...] = (
    "literature review",
    "literature gap",
    "candidate methods unknown",
    "survey of methods",
    "prior art needed",
)

_ARCHITECTURAL_TRIGGERS: tuple[str, ...] = (
    "requires architectural decision",
    "architectural decision",
    "design decision",
    "requires design",
    "escalated to architect",
    "escalated to pm",
)


def _classify(finding: FindingRef) -> str:
    """Classify a finding into one of: empirical, narrative, architectural, out_of_scope.

    Substring/regex match against the joined ``title + " " + reason`` text,
    case-insensitive. First match wins; default is ``out_of_scope``.
    """
    body = f"{finding.title} {finding.reason}".lower()
    if any(trigger in body for trigger in _EMPIRICAL_TRIGGERS):
        return "empirical"
    if any(trigger in body for trigger in _NARRATIVE_TRIGGERS):
        return "narrative"
    if any(trigger in body for trigger in _ARCHITECTURAL_TRIGGERS):
        return "architectural"
    return "out_of_scope"


# ---------------------------------------------------------------------------
# deferred-work.md parser
# ---------------------------------------------------------------------------


# Matches lines like:
#   - Permutation-FST methodology defect (round 9) [app/x.py, app/y.py:201-207] — CRITICAL, ...
#   - Manual verification logs missing — process-only gap
# Bracketed file/line is OPTIONAL — some bullets carry no [...] block at all.
# The em-dash separator (—, U+2014) matches the format step 6.6 of the
# bmad-code-review-synthesis SKILL emits to deferred-work.md.
_DEFERRED_BULLET_RE = re.compile(
    r"^- (?P<title>.+?)\s*(?:\[(?P<files>[^\]]+)\]\s*)?—\s*(?P<reason>.+)$"
)


# Matches the per-review section heading. We capture epic.story so we can pick
# only the bullets that belong to the story being synthesised. Append-only file
# semantics mean the LAST matching heading is the most recent.
def _section_heading_re(epic_num: int | str, story_num: int | str) -> re.Pattern[str]:
    return re.compile(
        rf"^## Deferred from: code review of story-{re.escape(str(epic_num))}\."
        rf"{re.escape(str(story_num))}\b.*$",
        re.MULTILINE,
    )


# Match ANY ## section heading — used to find where the chosen story's section ends.
_ANY_SECTION_HEADING_RE = re.compile(r"^## ", re.MULTILINE)


def _extract_first_file_and_line(files_blob: str) -> tuple[str | None, int | None]:
    """From a comma-separated bracketed files string, take the first file.

    Splits on ``,`` and trims whitespace. The first entry is then split on
    ``:`` to extract an optional line number; ranges like ``201-207`` collapse
    to the leading integer.
    """
    first = files_blob.split(",", 1)[0].strip()
    if not first:
        return None, None
    if ":" in first:
        raw_path, _, line_part = first.partition(":")
        path: str | None = raw_path.strip() or None
        line_part = line_part.strip()
        # Range like "201-207" → first integer.
        line_token = line_part.split("-", 1)[0].strip()
        try:
            line_num = int(line_token) if line_token else None
        except ValueError:
            line_num = None
        return path, line_num
    return first, None


def _parse_deferred_work(text: str, epic_num: int | str, story_num: int | str) -> list[FindingRef]:
    """Parse the latest ``## Deferred from: code review of story-{e}.{s}`` section.

    Returns every ``- ...`` bullet inside the section parsed into
    ``FindingRef``s. Returns an empty list if no matching section exists. The
    file is append-only, so the LAST occurrence of the heading is the most
    recent round.
    """
    section_re = _section_heading_re(epic_num, story_num)
    matches = list(section_re.finditer(text))
    if not matches:
        return []

    # Use the last (most recent) section.
    last = matches[-1]
    section_start = last.end()  # after the heading line
    # Find the next ## heading after this one (or EOF).
    next_heading = _ANY_SECTION_HEADING_RE.search(text, pos=section_start)
    section_end = next_heading.start() if next_heading else len(text)
    body = text[section_start:section_end]

    findings: list[FindingRef] = []
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        if not line.startswith("- "):
            continue
        m = _DEFERRED_BULLET_RE.match(line)
        if not m:
            continue
        files_blob = m.group("files")
        if files_blob is not None:
            file_path, line_num = _extract_first_file_and_line(files_blob)
        else:
            file_path, line_num = None, None
        findings.append(
            FindingRef(
                title=m.group("title").strip(),
                file=file_path,
                line=line_num,
                reason=m.group("reason").strip(),
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Slug derivation
# ---------------------------------------------------------------------------

_SLUG_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _slugify(title: str, max_len: int = 40) -> str:
    """Lowercase, alnum-only, dash-separated slug — max ``max_len`` chars.

    Strips leading/trailing dashes after truncation. Returns ``"deferred-finding"``
    if the input has no alphanumerics.
    """
    cleaned = _SLUG_NORMALIZE_RE.sub("-", title.lower()).strip("-")
    if not cleaned:
        return "deferred-finding"
    truncated = cleaned[:max_len].rstrip("-")
    return truncated or "deferred-finding"


# ---------------------------------------------------------------------------
# deferred-work.md follow-up writer
# ---------------------------------------------------------------------------


def _append_empirical_followup(
    deferred_work_path: Path,
    *,
    slug: str,
    epic_num: int | str,
    story_num: int | str,
    finding: FindingRef,
    today: str,
) -> None:
    """Append an empirical-research follow-up block to deferred-work.md."""
    relative_harness = f"_bmad-output/planning-artifacts/research/{slug}_autoresearch/"
    iteration_cmd = (
        f"cd {relative_harness} && "
        "python benchmark.py --synthetic-only --samples-per-class 30 --permutations 100"
    )
    block = (
        f"\n\n## Empirical research follow-up: {slug} ({today})\n\n"
        f"- **Source story**: {epic_num}.{story_num}\n"
        f"- **Source finding**: {finding.title}\n"
        f"- **Harness path**: `{relative_harness}`\n"
        f"- **Iteration command**: `{iteration_cmd}`\n"
        f"- **Originating reason**: {finding.reason}\n"
        "- **Status**: scaffolded; awaiting operator iteration. "
        "See [docs/recipes/autoresearch.md](../../docs/recipes/autoresearch.md) "
        "for the iteration contract.\n"
    )
    deferred_work_path.parent.mkdir(parents=True, exist_ok=True)
    with deferred_work_path.open("a", encoding="utf-8") as f:
        f.write(block)


def _append_logonly_followup(
    deferred_work_path: Path,
    *,
    classification: str,
    finding: FindingRef,
    today: str,
) -> None:
    """Append a one-line follow-up bullet for narrative/architectural items."""
    file_loc = (
        f" [{finding.file}:{finding.line}]"
        if finding.file and finding.line
        else (f" [{finding.file}]" if finding.file else "")
    )
    line = (
        f"\n- [ ] {classification.title()} follow-up needed: "
        f"{finding.title}{file_loc} ({today}) — {finding.reason}\n"
    )
    deferred_work_path.parent.mkdir(parents=True, exist_ok=True)
    with deferred_work_path.open("a", encoding="utf-8") as f:
        f.write(line)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scaffold_deferred_research(
    *,
    project_path: Path,
    epic_num: int | str,
    story_num: int | str,
    deferred_critical: int,
    deferred_high: int,
) -> ScaffoldSummary:
    """Classify deferred-work.md findings and scaffold empirical harnesses.

    Reads ``{project}/_bmad-output/implementation-artifacts/deferred-work.md``
    (the canonical structured ledger of deferrals — see step 6.6 of
    ``bmad-code-review-synthesis/SKILL.md``). Locates the LATEST section
    heading ``## Deferred from: code review of story-{epic}.{story}`` and
    parses every bullet underneath it.

    Args:
        project_path: Consumer project root. Harnesses land under
            ``{project_path}/_bmad-output/planning-artifacts/research/``.
        epic_num: Originating epic — used to locate the section and recorded
            in the follow-up block.
        story_num: Originating story — used to locate the section and recorded
            in the follow-up block.
        deferred_critical: Count from the synthesis resolution_data block;
            included in the args for symmetry with the call-site contract,
            but not used for branching here (the caller already gated).
        deferred_high: As above.

    Returns:
        A :class:`ScaffoldSummary` describing what was scaffolded, classified,
        and what (if anything) failed. Non-fatal: even if everything fails,
        the summary is returned with errors populated rather than raising.

    Notes:
        Failures are recorded in ``summary.errors``; this function does not
        raise on normal control-flow problems (template missing, target dir
        exists, parse error, malformed report). Truly catastrophic failures
        (e.g. unwritable filesystem) propagate and the caller's broad
        ``except`` is the safety net.

    """
    summary = ScaffoldSummary()

    deferred_work_path = (
        project_path / "_bmad-output" / "implementation-artifacts" / "deferred-work.md"
    )

    # Missing deferred-work.md → benign empty summary. The synthesis prompt's
    # step 6.6 may legitimately not have written one yet (e.g., no deferrals
    # were emitted in this round despite resolution_data carrying counts from
    # a prior round). Logged at info, not flagged as error.
    if not deferred_work_path.exists():
        logger.info(
            "deferred-work.md not found at %s; nothing to scaffold "
            "(deferred_critical=%d deferred_high=%d)",
            deferred_work_path,
            deferred_critical,
            deferred_high,
        )
        return summary

    try:
        text = deferred_work_path.read_text(encoding="utf-8")
    except OSError as e:
        summary.errors.append(f"Could not read deferred-work.md: {e}")
        return summary

    findings = _parse_deferred_work(text, epic_num, story_num)
    if not findings:
        # No matching ## heading for this story (or the heading exists but
        # carried no parseable bullets). Benign — info-log and return empty.
        logger.info(
            "No deferred-work.md section found for story-%s.%s "
            "(deferred_critical=%d deferred_high=%d)",
            epic_num,
            story_num,
            deferred_critical,
            deferred_high,
        )
        return summary

    # Resolve template directory once. If missing, every empirical item will
    # be recorded as an error but classification still runs.
    try:
        template_dir = autoresearch_template_dir()
    except FileNotFoundError as e:
        summary.errors.append(f"Template dir unresolved: {e}")
        template_dir = None

    research_root = project_path / "_bmad-output" / "planning-artifacts" / "research"
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    used_slugs: set[str] = set()

    for finding in findings:
        bucket = _classify(finding)

        if bucket == "narrative":
            summary.narrative.append(finding)
            try:
                _append_logonly_followup(
                    deferred_work_path,
                    classification="narrative",
                    finding=finding,
                    today=today,
                )
            except OSError as e:
                summary.errors.append(f"Could not append narrative follow-up: {e}")
            continue

        if bucket == "architectural":
            summary.architectural.append(finding)
            try:
                _append_logonly_followup(
                    deferred_work_path,
                    classification="architectural",
                    finding=finding,
                    today=today,
                )
            except OSError as e:
                summary.errors.append(f"Could not append architectural follow-up: {e}")
            continue

        if bucket == "out_of_scope":
            summary.out_of_scope.append(finding)
            # No extra deferred-work.md write — synthesis step 6.6 already
            # logged it under its per-review heading.
            continue

        # Empirical — scaffold a harness.
        if template_dir is None:
            summary.errors.append(
                f"Cannot scaffold empirical finding '{finding.title}': template missing"
            )
            continue

        slug = _slugify(finding.title)
        # Guard against duplicate slugs within a single batch.
        suffix = 2
        unique_slug = slug
        while unique_slug in used_slugs:
            unique_slug = f"{slug}-{suffix}"
            suffix += 1
        used_slugs.add(unique_slug)

        target_dir = research_root / f"{unique_slug}_autoresearch"

        if target_dir.exists():
            summary.errors.append(f"Target dir already exists, refusing to overwrite: {target_dir}")
            continue

        try:
            research_root.mkdir(parents=True, exist_ok=True)
            shutil.copytree(template_dir, target_dir, dirs_exist_ok=False)
        except OSError as e:
            summary.errors.append(f"Could not copy template to {target_dir}: {e}")
            continue

        try:
            _append_empirical_followup(
                deferred_work_path,
                slug=unique_slug,
                epic_num=epic_num,
                story_num=story_num,
                finding=finding,
                today=today,
            )
        except OSError as e:
            summary.errors.append(
                f"Scaffolded {target_dir.name} but could not append follow-up: {e}"
            )

        summary.empirical.append(
            ScaffoldEntry(
                slug=unique_slug,
                target_path=str(target_dir),
                finding=finding,
            )
        )

    return summary
