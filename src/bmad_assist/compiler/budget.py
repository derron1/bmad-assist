"""Shared prompt-budget enforcement for workflow compilers.

Provides staged enforcement of total prompt token caps after all context
sections are assembled. Trims strategic and TEA sections first, leaving
source files and story content untouched (those have their own budgets
via ``SourceContextService``).

Usage::

    from bmad_assist.compiler.budget import (
        ContextSection,
        PromptBudgetEnforcer,
        apply_section_budget,
    )

    enforcer = PromptBudgetEnforcer("code_review", cap=50000)
    sections = [
        ContextSection("strategic", strategic_files, priority=1, trimmable=True),
        ContextSection("tea", tea_files, priority=2, trimmable=True),
        ContextSection("source", source_files, priority=3, trimmable=False),
        ContextSection("story", story_files, priority=99, trimmable=False),
    ]
    result = enforcer.enforce(sections)
    if result.exceeded:
        logger.warning("Prompt exceeds cap: %d tokens", result.total_tokens)

"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from bmad_assist.compiler.shared_utils import estimate_tokens
from bmad_assist.compiler.strategic_context import _truncate_content

__all__ = [
    "BudgetResult",
    "ContextSection",
    "PromptBudgetEnforcer",
    "apply_section_budget",
]

logger = logging.getLogger(__name__)


@dataclass
class ContextSection:
    """A labeled section of compiled context with trimming metadata.

    Attributes:
        key: Section identifier (e.g., "strategic", "tea", "source").
        files: Path-to-content mapping for this section.
        priority: Lower = trim first (strategic=1, tea=2, source=3, story=99).
        trimmable: Whether the enforcer may trim this section.

    """

    key: str
    files: dict[str, str]
    priority: int
    trimmable: bool

    @property
    def token_count(self) -> int:
        """Estimate total tokens across all files in this section."""
        return sum(estimate_tokens(v) for v in self.files.values())


@dataclass
class BudgetResult:
    """Result of prompt budget enforcement.

    Attributes:
        sections: Rebuilt section maps (key → files dict).
        total_tokens: Final total token estimate after enforcement.
        trimmed_sections: Keys of sections that were trimmed.
        exceeded: True if still over budget after all trimming.

    """

    sections: dict[str, dict[str, str]] = field(default_factory=dict)
    total_tokens: int = 0
    trimmed_sections: list[str] = field(default_factory=list)
    exceeded: bool = False


def apply_section_budget(
    files: dict[str, str],
    budget_tokens: int,
) -> dict[str, str]:
    """Enforce a token budget over a dict of context files.

    Files are included in insertion order. When the total exceeds
    ``budget_tokens``, each file is truncated using ``_truncate_content()``
    until the budget is satisfied. Files that do not fit after truncation
    are dropped.

    Generalized version of ``_apply_tea_budget`` from code_review.py.

    Args:
        files: Mapping of name → content.
        budget_tokens: Maximum total tokens to retain (0 = disabled).

    Returns:
        Budget-enforced mapping (subset of input).

    """
    if budget_tokens <= 0 or not files:
        return files

    total_before = sum(estimate_tokens(v) for v in files.values())
    if total_before <= budget_tokens:
        return files

    result: dict[str, str] = {}
    remaining = budget_tokens
    for key, content in files.items():
        if remaining <= 0:
            break
        content_tokens = estimate_tokens(content)
        if content_tokens <= remaining:
            result[key] = content
            remaining -= content_tokens
        else:
            truncated, used_tokens = _truncate_content(content, remaining)
            if truncated:
                result[key] = truncated
                remaining -= used_tokens

    total_after = sum(estimate_tokens(v) for v in result.values())
    logger.debug(
        "Section budget enforced: %d → %d tokens (cap=%d)",
        total_before,
        total_after,
        budget_tokens,
    )
    return result


class PromptBudgetEnforcer:
    """Staged prompt budget enforcer.

    Trims context sections in priority order to meet a total token cap.
    Only trimmable sections (strategic, TEA) are modified; source files,
    git diffs, and story content are left untouched.

    Algorithm:
        1. Measure total tokens. If under cap → return immediately.
        2. Sort trimmable sections by priority (lowest first).
        3. For each trimmable section, proportionally reduce its budget.
        4. If still over → set exceeded=True, log warning.

    """

    def __init__(self, workflow_name: str, cap: int) -> None:
        """Initialize enforcer.

        Args:
            workflow_name: Name of the workflow for logging.
            cap: Total prompt token cap (0 = disabled).

        """
        self.workflow_name = workflow_name
        self.cap = cap

    @classmethod
    def from_config(cls, workflow_name: str) -> PromptBudgetEnforcer:
        """Create enforcer from global config.

        Falls back to default PromptBudgetConfig if config not loaded.
        """
        from bmad_assist.core.config.models.prompt_budget import PromptBudgetConfig

        try:
            from bmad_assist.core.config import get_config

            config = get_config()
            prompt_budget = config.compiler.prompt_budget
        except Exception:
            prompt_budget = PromptBudgetConfig()

        cap = prompt_budget.get_cap(workflow_name)
        return cls(workflow_name, cap)

    def enforce(self, sections: list[ContextSection]) -> BudgetResult:
        """Apply staged trimming to meet the total prompt cap.

        Args:
            sections: List of context sections to enforce budget over.

        Returns:
            BudgetResult with rebuilt section maps and enforcement metadata.

        """
        if self.cap <= 0:
            # Budget enforcement disabled
            return BudgetResult(
                sections={s.key: s.files for s in sections},
                total_tokens=sum(s.token_count for s in sections),
            )

        total = sum(s.token_count for s in sections)
        if total <= self.cap:
            # Under budget — fast path
            return BudgetResult(
                sections={s.key: s.files for s in sections},
                total_tokens=total,
            )

        logger.info(
            "%s prompt budget: %d tokens exceeds cap %d — starting staged trimming",
            self.workflow_name,
            total,
            self.cap,
        )

        # Sort trimmable sections by priority (lowest = trim first)
        trimmable = sorted(
            [s for s in sections if s.trimmable and s.files],
            key=lambda s: s.priority,
        )

        overage = total - self.cap
        trimmed_keys: list[str] = []

        for section in trimmable:
            if overage <= 0:
                break

            current_tokens = section.token_count
            if current_tokens <= 0:
                continue

            # Proportional reduction: trim this section to remove up to the overage
            target_tokens = max(0, current_tokens - overage)
            if target_tokens <= 0:
                # Remove the entire section
                saved = current_tokens
                section.files = {}
            else:
                # Re-enforce section at reduced budget
                section.files = apply_section_budget(section.files, target_tokens)
                saved = current_tokens - section.token_count

            if saved > 0:
                overage -= saved
                trimmed_keys.append(section.key)
                logger.debug(
                    "%s: trimmed section '%s' by %d tokens",
                    self.workflow_name,
                    section.key,
                    saved,
                )

        final_total = sum(s.token_count for s in sections)
        exceeded = final_total > self.cap

        if exceeded:
            logger.warning(
                "%s prompt still exceeds cap after trimming: %d tokens (cap=%d)",
                self.workflow_name,
                final_total,
                self.cap,
            )
        else:
            logger.info(
                "%s prompt trimmed to %d tokens (cap=%d, trimmed: %s)",
                self.workflow_name,
                final_total,
                self.cap,
                ", ".join(trimmed_keys) or "none",
            )

        return BudgetResult(
            sections={s.key: s.files for s in sections},
            total_tokens=final_total,
            trimmed_sections=trimmed_keys,
            exceeded=exceeded,
        )
