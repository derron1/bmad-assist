"""Tests for ``resolve_tea_index_path`` and schema-tolerant parser.

Phase 4 introduced layout-aware tea-index resolution and a parser that
accepts both 5- and 6-column schemas.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad_assist.core.exceptions import ParserError
from bmad_assist.testarch.knowledge.index import parse_index
from bmad_assist.testarch.knowledge.loader import (
    NEW_LAYOUT_AGENTS_INDEX_PATH,
    NEW_LAYOUT_CLAUDE_INDEX_PATH,
    resolve_tea_index_path,
)

# --- Path resolution ---------------------------------------------------------


def test_resolve_prefers_new_layout_claude(tmp_path: Path) -> None:
    """A v6.4+ install with .claude/skills/bmad-tea wins over legacy."""
    claude = tmp_path / NEW_LAYOUT_CLAUDE_INDEX_PATH
    claude.parent.mkdir(parents=True)
    claude.write_text("id,name,description,tags,tier,fragment_file\n", encoding="utf-8")

    legacy = tmp_path / "_bmad/tea/testarch/tea-index.csv"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("id,name,description,tags,fragment_file\n", encoding="utf-8")

    resolved = resolve_tea_index_path(tmp_path)
    assert resolved is not None
    assert resolved == claude.resolve() or resolved == claude


def test_resolve_uses_agents_when_claude_missing(tmp_path: Path) -> None:
    """Falls through to the .agents/skills mirror when .claude/skills is absent."""
    agents = tmp_path / NEW_LAYOUT_AGENTS_INDEX_PATH
    agents.parent.mkdir(parents=True)
    agents.write_text("id,name,description,tags,tier,fragment_file\n", encoding="utf-8")

    resolved = resolve_tea_index_path(tmp_path)
    assert resolved is not None
    assert resolved == agents or resolved == agents.resolve()


def test_resolve_falls_back_to_legacy_layout(tmp_path: Path) -> None:
    """Legacy ``_bmad/tea/...`` is honoured when no new-layout install exists."""
    legacy = tmp_path / "_bmad/tea/testarch/tea-index.csv"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("id,name,description,tags,fragment_file\n", encoding="utf-8")

    resolved = resolve_tea_index_path(tmp_path)
    assert resolved is not None
    assert resolved == legacy or resolved == legacy.resolve()


def test_resolve_falls_back_to_bmm_legacy(tmp_path: Path) -> None:
    """``_bmad/bmm/testarch/tea-index.csv`` resolves when ``_bmad/tea/...`` missing."""
    legacy = tmp_path / "_bmad/bmm/testarch/tea-index.csv"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("id,name,description,tags,fragment_file\n", encoding="utf-8")

    resolved = resolve_tea_index_path(tmp_path)
    assert resolved is not None
    assert resolved == legacy or resolved == legacy.resolve()


def test_resolve_falls_back_to_bundled(tmp_path: Path) -> None:
    """A bare project resolves to the bundled fallback (always present in repo)."""
    resolved = resolve_tea_index_path(tmp_path)
    # Bundled is always available in the repo's installed package.
    assert resolved is not None
    assert resolved.name == "tea-index.csv"


# --- Schema-tolerant parser ---------------------------------------------------


def _five_col_csv() -> str:
    return (
        "id,name,description,tags,fragment_file\n"
        "alpha,Alpha,Desc,tag1,knowledge/alpha.md\n"
        "beta,Beta,Desc,tag1,knowledge/beta.md\n"
    )


def _six_col_csv() -> str:
    return (
        "id,name,description,tags,tier,fragment_file\n"
        "alpha,Alpha,Desc,tag1,core,knowledge/alpha.md\n"
        "beta,Beta,Desc,tag1,extended,knowledge/beta.md\n"
    )


def test_parse_legacy_5col_schema_defaults_tier(tmp_path: Path) -> None:
    """5-col rows produce fragments with ``tier='core'`` by default."""
    path = tmp_path / "tea-index.csv"
    path.write_text(_five_col_csv(), encoding="utf-8")

    fragments = parse_index(path)
    assert len(fragments) == 2
    for f in fragments:
        assert f.tier == "core"


def test_parse_v64_6col_schema_preserves_tier(tmp_path: Path) -> None:
    """6-col rows preserve their tier values verbatim."""
    path = tmp_path / "tea-index.csv"
    path.write_text(_six_col_csv(), encoding="utf-8")

    fragments = parse_index(path)
    assert len(fragments) == 2
    assert fragments[0].tier == "core"
    assert fragments[1].tier == "extended"


def test_parse_consistent_shape_between_schemas(tmp_path: Path) -> None:
    """Both schemas produce the same id/name/file structure."""
    p5 = tmp_path / "five.csv"
    p6 = tmp_path / "six.csv"
    p5.write_text(_five_col_csv(), encoding="utf-8")
    p6.write_text(_six_col_csv(), encoding="utf-8")

    f5 = parse_index(p5)
    f6 = parse_index(p6)
    assert [f.id for f in f5] == [f.id for f in f6]
    assert [f.fragment_file for f in f5] == [f.fragment_file for f in f6]


def test_parse_unexpected_column_count_raises(tmp_path: Path) -> None:
    """A 4-column schema (missing fragment_file) raises ParserError."""
    path = tmp_path / "broken.csv"
    path.write_text(
        "id,name,description,tags\n"
        "alpha,Alpha,Desc,tag1\n",
        encoding="utf-8",
    )
    with pytest.raises(ParserError, match="missing required columns"):
        parse_index(path)


def test_parse_extra_unknown_column_raises(tmp_path: Path) -> None:
    """A 7-column schema with an unknown column raises ParserError."""
    path = tmp_path / "extra.csv"
    path.write_text(
        "id,name,description,tags,tier,fragment_file,extra\n"
        "alpha,Alpha,Desc,tag1,core,knowledge/alpha.md,bonus\n",
        encoding="utf-8",
    )
    with pytest.raises(ParserError, match="unexpected schema"):
        parse_index(path)


def test_parse_5col_with_extra_column_raises(tmp_path: Path) -> None:
    """A 5-col schema where the 5th column is unknown (no fragment_file) raises."""
    path = tmp_path / "weird.csv"
    path.write_text(
        "id,name,description,tags,bogus\n"
        "alpha,Alpha,Desc,tag1,xx\n",
        encoding="utf-8",
    )
    with pytest.raises(ParserError, match="missing required columns"):
        parse_index(path)


def test_parse_bundled_index_succeeds() -> None:
    """The refreshed bundled v6.4+ index parses cleanly."""
    from bmad_assist.testarch.knowledge_base import get_bundled_index_path

    bundled = get_bundled_index_path()
    assert bundled is not None
    fragments = parse_index(bundled)
    assert len(fragments) >= 50  # v6.4+ ships 51 rows
    # Spot-check tier values are populated
    tiers = {f.tier for f in fragments}
    assert "core" in tiers
    assert "extended" in tiers
