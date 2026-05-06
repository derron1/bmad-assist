"""Bootstrap-time validation of bundled skill step chains.

Walks every tri-modal entry step (``steps-?/step-01*.md``) inside an
installed skill directory and confirms each ``nextStepFile`` link
resolves to a real file. Composes :func:`build_step_chain` rather than
duplicating chain-traversal logic — the resolver fix that introduced
``{skill-root}`` substitution lives there and is snapshot-tested.

The motivating regression: a bug in the chain walker silently truncated
8 TEA workflows for weeks because no install-time check exercised the
chains. This validator runs on every bootstrap so a broken bundled
skill aborts the install loudly instead of leaking through to runtime.

Public API:
    ChainValidationError: dataclass describing a single broken link.
    validate_skill_chains: validate one installed skill directory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from bmad_assist.compiler.step_chain import (
    _substitute_skill_root,
    build_step_chain,
)
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChainValidationError:
    """A single broken link discovered while walking a skill's step chains.

    Attributes:
        skill_dir: Installed skill root (the directory containing SKILL.md).
        entry_step: The ``step-01-*.md`` file the walk started from.
        broken_step: The step file holding the bad ``nextStepFile`` ref
            (i.e. the last step the walker reached before failing).
        next_step_ref: The raw ``nextStepFile`` string, as written in
            frontmatter (pre-substitution).
        reason: Human-readable explanation of the failure.

    """

    skill_dir: Path
    entry_step: Path
    broken_step: Path | None
    next_step_ref: str | None
    reason: str

    def format(self) -> str:
        """Render a single-line summary suitable for ERROR-level logs."""
        broken = self.broken_step.name if self.broken_step else "<entry>"
        ref = self.next_step_ref or "<n/a>"
        return (
            f"skill={self.skill_dir.name} "
            f"entry={self.entry_step.name} "
            f"broken_at={broken} "
            f"nextStepFile={ref!r} "
            f"reason={self.reason}"
        )


def _iter_entry_steps(skill_root: Path) -> list[Path]:
    """Yield the tri-modal entry steps (``steps-?/step-01*.md``) for a skill.

    Only the three canonical mode directories are inspected
    (``steps-c``, ``steps-e``, ``steps-v``). Skills with no such
    directory (single-file SKILL.md skills like ``bmad-create-story``)
    return an empty list — they are not chain-walked at all.
    """
    entries: list[Path] = []
    for mode_dir_name in ("steps-c", "steps-e", "steps-v"):
        mode_dir = skill_root / mode_dir_name
        if not mode_dir.is_dir():
            continue
        # ``step-01*.md`` matches both ``step-01-foo.md`` and ``step-01.md``.
        # Sort for deterministic ordering across runs.
        entries.extend(sorted(mode_dir.glob("step-01*.md")))
    return entries


def _validate_chain_terminus(
    skill_root: Path,
    entry: Path,
    chain: list,  # list[StepIR] — avoid runtime import cycle in annotations
) -> ChainValidationError | None:
    """Detect chains truncated by the "Next step not found" warning.

    ``build_step_chain`` raises ``CompilerError`` for unresolved tokens
    (handled by the caller) but only logs a warning + returns early when
    the resolved path simply doesn't exist on disk. To turn that silent
    truncation into a hard error we re-resolve the last step's
    ``nextStepFile`` after the walk: if it points at a missing file,
    the chain is broken; if it's None, the chain ended legitimately.
    """
    if not chain:
        return ChainValidationError(
            skill_dir=skill_root,
            entry_step=entry,
            broken_step=None,
            next_step_ref=None,
            reason="empty chain (entry step parsed to nothing)",
        )

    last = chain[-1]
    next_ref = last.next_step_file
    if not next_ref:
        # Genuine end of chain.
        return None

    substituted = _substitute_skill_root(next_ref, last.path)
    # Unresolved tokens after substitution would have raised in
    # build_step_chain — if we reach here with a brace still present,
    # that's a defensive belt-and-braces case worth flagging.
    if "{" in substituted:
        return ChainValidationError(
            skill_dir=skill_root,
            entry_step=entry,
            broken_step=last.path,
            next_step_ref=next_ref,
            reason=f"unresolved token after substitution: {substituted!r}",
        )

    candidate = Path(substituted)
    next_path = candidate if candidate.is_absolute() else (last.path.parent / candidate)
    next_path = next_path.resolve()

    if not next_path.exists():
        return ChainValidationError(
            skill_dir=skill_root,
            entry_step=entry,
            broken_step=last.path,
            next_step_ref=next_ref,
            reason=f"next step file does not exist: {next_path}",
        )

    # build_step_chain succeeded, the link resolves, AND we don't return
    # here — fine. (If next_path exists but the walker stopped early for
    # some other reason, that would indicate a bug in the walker, not in
    # the bundled content; it's not in scope for this validator.)
    return None


def validate_skill_chains(skill_root: Path) -> list[ChainValidationError]:
    """Validate every tri-modal step chain under ``skill_root``.

    For each tri-modal entry step (``step-01*.md`` inside ``steps-c/``,
    ``steps-e/``, or ``steps-v/``), walks the chain via
    :func:`build_step_chain` and collects any failures. Returns all
    discovered errors so the caller can decide how to surface them
    (typically: log every one then raise).

    Args:
        skill_root: Installed skill directory (the one containing
            ``SKILL.md``). Skills without any ``steps-?/`` directories
            are skipped — they are single-file SKILL.md skills with no
            chain to validate.

    Returns:
        List of :class:`ChainValidationError`. Empty when all chains
        resolve cleanly.

    """
    errors: list[ChainValidationError] = []
    if not skill_root.is_dir():
        # Defensive: caller should never invoke us on a non-directory
        # but a missing dir isn't a chain-validation concern.
        return errors

    for entry in _iter_entry_steps(skill_root):
        try:
            chain = build_step_chain(entry)
        except CompilerError as e:
            errors.append(
                ChainValidationError(
                    skill_dir=skill_root,
                    entry_step=entry,
                    broken_step=None,
                    next_step_ref=None,
                    reason=str(e).splitlines()[0],
                )
            )
            continue

        terminus_error = _validate_chain_terminus(skill_root, entry, chain)
        if terminus_error is not None:
            errors.append(terminus_error)

    return errors
