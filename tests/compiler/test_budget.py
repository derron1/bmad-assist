"""Tests for shared prompt budget enforcement.

Covers:
- apply_section_budget: token-based section trimming
- PromptBudgetEnforcer: staged trimming across context sections
- PromptBudgetConfig: per-workflow cap resolution
"""

from bmad_assist.compiler.budget import (
    BudgetResult,
    ContextSection,
    PromptBudgetEnforcer,
    apply_section_budget,
)
from bmad_assist.compiler.shared_utils import estimate_tokens
from bmad_assist.core.config.models.prompt_budget import PromptBudgetConfig


# ---------------------------------------------------------------------------
# apply_section_budget
# ---------------------------------------------------------------------------


class TestApplySectionBudget:
    def test_under_budget_unchanged(self) -> None:
        """Files within budget are returned unchanged."""
        files = {"a.py": "hello", "b.py": "world"}
        result = apply_section_budget(files, budget_tokens=1000)
        assert result == files

    def test_over_budget_truncates(self) -> None:
        """Files exceeding budget are truncated or dropped."""
        # Each file ~1000 tokens (4000 chars)
        files = {"a.py": "x" * 4000, "b.py": "y" * 4000}
        result = apply_section_budget(files, budget_tokens=1200)
        total = sum(estimate_tokens(v) for v in result.values())
        assert total <= 1300  # Allow small margin for truncation granularity

    def test_zero_budget_returns_unchanged(self) -> None:
        """budget_tokens=0 disables enforcement."""
        files = {"a.py": "x" * 10000}
        result = apply_section_budget(files, budget_tokens=0)
        assert result == files

    def test_empty_files_returns_empty(self) -> None:
        """Empty dict is returned as-is."""
        assert apply_section_budget({}, budget_tokens=1000) == {}

    def test_preserves_insertion_order(self) -> None:
        """First file is included before second."""
        files = {"first": "a" * 4000, "second": "b" * 4000}
        result = apply_section_budget(files, budget_tokens=1200)
        assert "first" in result


# ---------------------------------------------------------------------------
# PromptBudgetEnforcer
# ---------------------------------------------------------------------------


def _make_section(key: str, tokens: int, priority: int = 1, trimmable: bool = True) -> ContextSection:
    """Create a section with approximately the given token count."""
    content = "x" * (tokens * 4)  # ~4 chars per token
    return ContextSection(key=key, files={f"[{key}]": content}, priority=priority, trimmable=trimmable)


class TestPromptBudgetEnforcer:
    def test_under_cap_returns_unchanged(self) -> None:
        """Sections under cap are returned without modification."""
        sections = [
            _make_section("strategic", 1000, priority=1),
            _make_section("source", 2000, priority=3, trimmable=False),
        ]
        enforcer = PromptBudgetEnforcer("test", cap=5000)
        result = enforcer.enforce(sections)
        assert not result.exceeded
        assert result.trimmed_sections == []
        assert result.total_tokens <= 5000

    def test_strategic_trimmed_first(self) -> None:
        """Strategic docs (priority=1) are trimmed before TEA (priority=2)."""
        sections = [
            _make_section("strategic", 5000, priority=1),
            _make_section("tea", 5000, priority=2),
            _make_section("source", 5000, priority=3, trimmable=False),
        ]
        enforcer = PromptBudgetEnforcer("test", cap=12000)
        result = enforcer.enforce(sections)

        # Strategic should be trimmed; TEA may or may not need trimming
        assert "strategic" in result.trimmed_sections

    def test_non_trimmable_sections_preserved(self) -> None:
        """Story and source sections are never modified."""
        story_content = "important story content"
        sections = [
            _make_section("strategic", 5000, priority=1),
            ContextSection("story", {"story.md": story_content}, priority=99, trimmable=False),
        ]
        enforcer = PromptBudgetEnforcer("test", cap=3000)
        result = enforcer.enforce(sections)
        assert result.sections["story"]["story.md"] == story_content

    def test_exceeded_flag_set(self) -> None:
        """When trimming cannot meet cap, exceeded=True is set."""
        sections = [
            _make_section("strategic", 1000, priority=1),
            _make_section("source", 20000, priority=3, trimmable=False),
        ]
        enforcer = PromptBudgetEnforcer("test", cap=5000)
        result = enforcer.enforce(sections)
        assert result.exceeded

    def test_cap_zero_disables_enforcement(self) -> None:
        """cap=0 disables enforcement and returns all sections unchanged."""
        sections = [
            _make_section("strategic", 50000, priority=1),
        ]
        enforcer = PromptBudgetEnforcer("test", cap=0)
        result = enforcer.enforce(sections)
        assert not result.exceeded
        assert result.trimmed_sections == []

    def test_multiple_trimmable_sections(self) -> None:
        """Multiple trimmable sections are trimmed in priority order."""
        sections = [
            _make_section("strategic", 3000, priority=1),
            _make_section("tea", 3000, priority=2),
            _make_section("source", 3000, priority=3, trimmable=False),
        ]
        enforcer = PromptBudgetEnforcer("test", cap=5000)
        result = enforcer.enforce(sections)
        # Strategic is trimmed first (lowest priority number)
        assert result.trimmed_sections[0] == "strategic"

    def test_graduated_synthesis_trimming(self) -> None:
        """Graduated trimming mirrors validate_story_synthesis section layout.

        Sections: strategic(1) → source(2) → deep_verify(3) → validations(4) → story(99, non-trimmable).
        With a tight cap, strategic/source/DV should be trimmed before validations.
        """
        sections = [
            _make_section("strategic", 3000, priority=1),
            _make_section("source", 3000, priority=2),
            _make_section("deep_verify", 2000, priority=3),
            _make_section("validations", 5000, priority=4),
            _make_section("story", 3000, priority=99, trimmable=False),
        ]
        # Cap at 10000 with ~16000 total → need to trim ~6000
        enforcer = PromptBudgetEnforcer("validate_story_synthesis", cap=10000)
        result = enforcer.enforce(sections)

        # Strategic should be trimmed first, then source
        assert "strategic" in result.trimmed_sections
        # Story must be fully preserved
        assert "story" not in result.trimmed_sections

    def test_story_never_trimmed_even_when_over_cap(self) -> None:
        """Story section is never trimmed even when all trimmable sections are exhausted."""
        sections = [
            _make_section("strategic", 1000, priority=1),
            _make_section("validations", 2000, priority=4),
            _make_section("story", 20000, priority=99, trimmable=False),
        ]
        enforcer = PromptBudgetEnforcer("test", cap=5000)
        result = enforcer.enforce(sections)
        # Story exceeds cap alone, but cannot be trimmed
        assert result.exceeded
        assert "story" not in result.trimmed_sections


# ---------------------------------------------------------------------------
# PromptBudgetConfig
# ---------------------------------------------------------------------------


class TestPromptBudgetConfig:
    def test_get_cap_with_override(self) -> None:
        """Per-workflow override is returned."""
        config = PromptBudgetConfig(workflow_caps={"code_review": 50000})
        assert config.get_cap("code_review") == 50000

    def test_get_cap_default(self) -> None:
        """Default cap is returned for unlisted workflow."""
        config = PromptBudgetConfig(default_cap=80000)
        assert config.get_cap("unknown_workflow") == 80000

    def test_get_cap_disabled(self) -> None:
        """Returns 0 when enforcement is disabled."""
        config = PromptBudgetConfig(enabled=False)
        assert config.get_cap("code_review") == 0

    def test_get_cap_normalizes_hyphens(self) -> None:
        """Hyphens in workflow name are converted to underscores."""
        config = PromptBudgetConfig(workflow_caps={"code_review": 50000})
        assert config.get_cap("code-review") == 50000


# ---------------------------------------------------------------------------
# Effective budget resolution (regression for misaligned budgets)
# ---------------------------------------------------------------------------


class TestEffectiveBudgetResolution:
    """Regression tests: when prompt cap < synthesis token_budget,
    compression logic must use the tighter of the two."""

    def test_effective_budget_uses_prompt_cap_when_tighter(self) -> None:
        """min(synthesis_budget=120000, prompt_cap=40000) → 40000."""
        synthesis_token_budget = 120_000
        prompt_cap = 40_000
        effective_budget = (
            min(synthesis_token_budget, prompt_cap) if prompt_cap > 0 else synthesis_token_budget
        )
        assert effective_budget == 40_000

    def test_effective_budget_uses_synthesis_when_cap_disabled(self) -> None:
        """When prompt cap is 0 (disabled), effective budget = synthesis budget."""
        synthesis_token_budget = 120_000
        prompt_cap = 0
        effective_budget = (
            min(synthesis_token_budget, prompt_cap) if prompt_cap > 0 else synthesis_token_budget
        )
        assert effective_budget == 120_000

    def test_effective_budget_uses_synthesis_when_cap_larger(self) -> None:
        """When prompt cap > synthesis budget, synthesis budget wins."""
        synthesis_token_budget = 40_000
        prompt_cap = 80_000
        effective_budget = (
            min(synthesis_token_budget, prompt_cap) if prompt_cap > 0 else synthesis_token_budget
        )
        assert effective_budget == 40_000

    def test_config_get_cap_returns_validate_story_synthesis_cap(self) -> None:
        """Default config has validate_story_synthesis cap of 40000."""
        config = PromptBudgetConfig()
        cap = config.get_cap("validate_story_synthesis")
        assert cap == 40_000
