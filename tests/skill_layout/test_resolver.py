"""Unit tests for ``bmad_assist.skill_layout.resolver``.

These exercise the merge primitives and the file-loading wrappers in
isolation. Cross-validation against the live BMAD scripts lives in
``test_resolver_parity.py``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from bmad_assist.skill_layout import (
    ResolverError,
    deep_merge,
    merge_arrays,
    resolve_central_config,
    resolve_customization,
)
from bmad_assist.skill_layout.resolver import _find_project_root, extract_key

FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- #
# deep_merge / merge_arrays primitives                                        #
# --------------------------------------------------------------------------- #


class TestDeepMerge:
    """Structural rules from the BMAD resolver."""

    def test_dict_dict_recursive_merge(self) -> None:
        """Two dicts deep-merge per-key."""
        base = {"a": {"x": 1, "y": 2}}
        override = {"a": {"y": 20, "z": 30}}
        assert deep_merge(base, override) == {"a": {"x": 1, "y": 20, "z": 30}}

    def test_scalar_override_wins(self) -> None:
        """Scalar overrides replace base values."""
        assert deep_merge({"x": 1}, {"x": 99}) == {"x": 99}
        assert deep_merge("base", "override") == "override"
        assert deep_merge(True, False) is False

    def test_type_mismatch_override_wins(self) -> None:
        """Whenever shapes disagree, the override wins."""
        assert deep_merge({"a": 1}, [1, 2]) == [1, 2]
        assert deep_merge([1, 2], {"a": 1}) == {"a": 1}
        assert deep_merge(1, {"a": 1}) == {"a": 1}

    def test_keyed_merge_by_code(self) -> None:
        """Arrays of tables sharing ``code`` merge by that key."""
        base = [
            {"code": "a", "label": "Alpha"},
            {"code": "b", "label": "Beta"},
        ]
        override = [
            {"code": "a", "label": "Alpha v2"},
            {"code": "c", "label": "Gamma"},
        ]
        assert merge_arrays(base, override) == [
            {"code": "a", "label": "Alpha v2"},
            {"code": "b", "label": "Beta"},
            {"code": "c", "label": "Gamma"},
        ]

    def test_keyed_merge_by_id_when_no_code(self) -> None:
        """Arrays of tables sharing ``id`` merge by ``id`` when ``code`` is absent."""
        base = [{"id": "x", "v": 1}]
        override = [{"id": "x", "v": 2}, {"id": "y", "v": 3}]
        assert merge_arrays(base, override) == [
            {"id": "x", "v": 2},
            {"id": "y", "v": 3},
        ]

    def test_unkeyed_lists_concatenate(self) -> None:
        """Arrays without ``code``/``id`` simply concatenate."""
        assert merge_arrays([1, 2], [3, 4]) == [1, 2, 3, 4]
        assert merge_arrays(["a"], ["b", "c"]) == ["a", "b", "c"]

    def test_mixed_identifier_keys_falls_back_to_concat(self) -> None:
        """Mixing ``code`` and ``id`` items falls back to concat (per upstream docstring)."""
        base = [{"code": "a"}]
        override = [{"id": "x"}]
        assert merge_arrays(base, override) == [{"code": "a"}, {"id": "x"}]

    def test_partial_keyed_array_falls_back_to_concat(self) -> None:
        """Keyed merge requires every item to carry the identifier."""
        base = [{"code": "a"}, {"label": "no code"}]
        override = [{"code": "a", "label": "Alpha"}]
        assert merge_arrays(base, override) == [
            {"code": "a"},
            {"label": "no code"},
            {"code": "a", "label": "Alpha"},
        ]

    def test_array_with_non_dict_items_concats(self) -> None:
        """Arrays of scalars always concat."""
        assert merge_arrays(["a", "b"], ["c"]) == ["a", "b", "c"]

    def test_empty_arrays(self) -> None:
        """Empty inputs are handled symmetrically."""
        assert merge_arrays([], []) == []
        assert merge_arrays([], [{"code": "x"}]) == [{"code": "x"}]
        assert merge_arrays([{"code": "x"}], []) == [{"code": "x"}]


# --------------------------------------------------------------------------- #
# extract_key                                                                 #
# --------------------------------------------------------------------------- #


class TestExtractKey:
    """Dotted-path lookups used by the ``--key`` filter."""

    def test_dotted_path(self) -> None:
        """Multi-segment paths walk nested dicts."""
        data = {"a": {"b": {"c": 99}}}
        assert extract_key(data, "a.b.c") == 99

    def test_top_level(self) -> None:
        """Single-segment paths look up the top-level key."""
        assert extract_key({"x": 1}, "x") == 1

    def test_missing_returns_sentinel(self) -> None:
        """Missing paths return the module-level sentinel value."""
        from bmad_assist.skill_layout.resolver import _MISSING

        assert extract_key({"a": 1}, "b") is _MISSING
        assert extract_key({"a": {"b": 1}}, "a.c") is _MISSING


# --------------------------------------------------------------------------- #
# resolve_customization                                                       #
# --------------------------------------------------------------------------- #


class TestResolveCustomization:
    """File-loading resolver tests for the three-layer customization stack."""

    def test_defaults_only_returns_defaults(self, tmp_path: Path) -> None:
        """Without overrides we return the defaults verbatim."""
        skill = _make_skill(tmp_path / "bmad-foo", {"a": 1, "b": [1, 2]})
        merged = resolve_customization(skill, project_root=tmp_path)
        assert merged == {"a": 1, "b": [1, 2]}

    def test_team_overrides_apply(self, tmp_path: Path) -> None:
        """Team overrides modify the defaults."""
        skill = _make_skill(tmp_path / "bmad-foo", {"a": 1, "b": 2})
        custom_dir = tmp_path / "_bmad" / "custom"
        custom_dir.mkdir(parents=True)
        (custom_dir / "bmad-foo.toml").write_text("a = 100\n", encoding="utf-8")
        merged = resolve_customization(skill, project_root=tmp_path)
        assert merged == {"a": 100, "b": 2}

    def test_user_overrides_beat_team(self, tmp_path: Path) -> None:
        """User overrides win over team overrides."""
        skill = _make_skill(tmp_path / "bmad-foo", {"a": 1})
        custom_dir = tmp_path / "_bmad" / "custom"
        custom_dir.mkdir(parents=True)
        (custom_dir / "bmad-foo.toml").write_text("a = 10\n", encoding="utf-8")
        (custom_dir / "bmad-foo.user.toml").write_text("a = 99\n", encoding="utf-8")
        merged = resolve_customization(skill, project_root=tmp_path)
        assert merged["a"] == 99

    def test_missing_required_customize_toml_raises(self, tmp_path: Path) -> None:
        """An absent ``customize.toml`` raises :class:`ResolverError`."""
        skill_dir = tmp_path / "bmad-foo"
        skill_dir.mkdir()
        with pytest.raises(ResolverError, match="customization file not found"):
            resolve_customization(skill_dir, project_root=tmp_path)

    def test_malformed_required_raises(self, tmp_path: Path) -> None:
        """Unparseable ``customize.toml`` raises :class:`ResolverError`."""
        skill_dir = tmp_path / "bmad-foo"
        skill_dir.mkdir()
        (skill_dir / "customize.toml").write_text("a = = =\n", encoding="utf-8")
        with pytest.raises(ResolverError, match="failed to parse"):
            resolve_customization(skill_dir, project_root=tmp_path)

    def test_malformed_optional_logs_warning_and_skips(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A malformed override file logs a warning and is silently skipped."""
        skill = _make_skill(tmp_path / "bmad-foo", {"a": 1})
        custom_dir = tmp_path / "_bmad" / "custom"
        custom_dir.mkdir(parents=True)
        (custom_dir / "bmad-foo.toml").write_text("a = ! ! !\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="bmad_assist.skill_layout.resolver"):
            merged = resolve_customization(skill, project_root=tmp_path)
        assert merged == {"a": 1}
        assert any("failed to parse" in record.getMessage() for record in caplog.records)

    def test_keys_filter_returns_only_present(self, tmp_path: Path) -> None:
        """Missing ``keys`` entries are silently omitted (BMAD ``_MISSING`` parity)."""
        skill = _make_skill(tmp_path / "bmad-foo", {"workflow": {"a": 1}, "agent": {"x": 2}})
        merged = resolve_customization(
            skill,
            project_root=tmp_path,
            keys=["workflow", "missing"],
        )
        assert merged == {"workflow": {"a": 1}}

    def test_keys_filter_supports_dotted_paths(self, tmp_path: Path) -> None:
        """Dotted keys are looked up via the ``extract_key`` walker."""
        skill = _make_skill(
            tmp_path / "bmad-foo",
            {"workflow": {"on_complete": "do thing"}},
        )
        merged = resolve_customization(
            skill,
            project_root=tmp_path,
            keys=["workflow.on_complete"],
        )
        assert merged == {"workflow.on_complete": "do thing"}

    def test_keep_bmad_prefix_in_skill_name(self, tmp_path: Path) -> None:
        """The override filename keeps the ``bmad-`` prefix."""
        skill = _make_skill(tmp_path / "bmad-foo", {"a": 1})
        custom_dir = tmp_path / "_bmad" / "custom"
        custom_dir.mkdir(parents=True)
        # foo.toml (no prefix) must be ignored
        (custom_dir / "foo.toml").write_text("a = 999\n", encoding="utf-8")
        # bmad-foo.toml (full name) must be applied
        (custom_dir / "bmad-foo.toml").write_text("a = 5\n", encoding="utf-8")
        merged = resolve_customization(skill, project_root=tmp_path)
        assert merged["a"] == 5

    def test_no_project_root_when_overrides_absent(self, tmp_path: Path) -> None:
        """A standalone skill outside any project still resolves to its defaults."""
        skill = _make_skill(tmp_path / "standalone" / "bmad-foo", {"a": 1})
        merged = resolve_customization(skill, project_root=None)
        assert merged == {"a": 1}

    def test_array_of_tables_keyed_merge(self, tmp_path: Path) -> None:
        """Array-of-tables overrides merge by ``code``."""
        skill_dir = tmp_path / "bmad-foo"
        skill_dir.mkdir()
        (skill_dir / "customize.toml").write_text(
            '[[items]]\ncode = "a"\nlabel = "Alpha"\n'
            '[[items]]\ncode = "b"\nlabel = "Beta"\n',
            encoding="utf-8",
        )
        custom_dir = tmp_path / "_bmad" / "custom"
        custom_dir.mkdir(parents=True)
        (custom_dir / "bmad-foo.toml").write_text(
            '[[items]]\ncode = "a"\nlabel = "Alpha v2"\n'
            '[[items]]\ncode = "c"\nlabel = "Gamma"\n',
            encoding="utf-8",
        )
        merged = resolve_customization(skill_dir, project_root=tmp_path)
        assert merged["items"] == [
            {"code": "a", "label": "Alpha v2"},
            {"code": "b", "label": "Beta"},
            {"code": "c", "label": "Gamma"},
        ]

    def test_empty_customize_toml(self, tmp_path: Path) -> None:
        """An empty file resolves to ``{}``."""
        skill_dir = tmp_path / "bmad-foo"
        skill_dir.mkdir()
        (skill_dir / "customize.toml").write_text("", encoding="utf-8")
        merged = resolve_customization(skill_dir, project_root=tmp_path)
        assert merged == {}


# --------------------------------------------------------------------------- #
# resolve_central_config                                                      #
# --------------------------------------------------------------------------- #


class TestResolveCentralConfig:
    """File-loading resolver tests for the four-layer central config."""

    def test_full_four_layer_merge(self) -> None:
        """All four layers contribute to the merged result."""
        merged = resolve_central_config(FIXTURES / "sample_central_config")
        assert merged["core"]["edition"] == "user-custom"
        # base ["en"] + base.user ["fr"] + custom.user ["jp"] = concat per BMAD rules
        assert merged["core"]["languages"] == ["en", "fr", "jp"]
        assert merged["core"]["project_name"] == "sample"
        assert merged["user_prefs"] == {"theme": "dark"}

    def test_keyed_merge_through_layers(self) -> None:
        """Keyed array merges traverse layers correctly."""
        merged = resolve_central_config(FIXTURES / "sample_central_config")
        menu = merged["agents"]["menu"]
        amelia = next(item for item in menu if item["code"] == "amelia")
        assert amelia["description"] == "Senior dev (team override)"
        assert amelia["seniority"] == "principal"
        assert any(item["code"] == "winston" for item in menu)

    def test_missing_required_raises(self, tmp_path: Path) -> None:
        """An absent ``_bmad/config.toml`` raises :class:`ResolverError`."""
        (tmp_path / "_bmad").mkdir()
        with pytest.raises(ResolverError, match="config file not found"):
            resolve_central_config(tmp_path)

    def test_keys_filter(self) -> None:
        """The ``keys`` filter mirrors the customization resolver."""
        merged = resolve_central_config(
            FIXTURES / "sample_central_config",
            keys=["core", "missing.path"],
        )
        assert "core" in merged
        assert "missing.path" not in merged


# --------------------------------------------------------------------------- #
# _find_project_root                                                          #
# --------------------------------------------------------------------------- #


class TestFindProjectRoot:
    """Walk-up project-root discovery."""

    def test_finds_via_bmad_marker(self, tmp_path: Path) -> None:
        """A ``_bmad/`` directory marks the project root."""
        (tmp_path / "_bmad").mkdir()
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        assert _find_project_root(nested) == tmp_path.resolve()

    def test_finds_via_git_marker(self, tmp_path: Path) -> None:
        """A ``.git/`` directory also marks the project root."""
        (tmp_path / ".git").mkdir()
        nested = tmp_path / "deep"
        nested.mkdir()
        assert _find_project_root(nested) == tmp_path.resolve()

    def test_returns_none_or_outer_marker(self, tmp_path: Path) -> None:
        """Without a marker we either get ``None`` or an outer marker hit."""
        # tmp_path may live under a real project, so we accept either
        # outcome — but if non-None, the result must carry a marker.
        nested = tmp_path / "a"
        nested.mkdir()
        result = _find_project_root(nested)
        if result is not None:
            assert (result / "_bmad").exists() or (result / ".git").exists()


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _make_skill(skill_dir: Path, customize: dict[str, object]) -> Path:
    """Materialize a skill directory with a ``customize.toml`` of the given content."""
    skill_dir.mkdir(parents=True)
    lines: list[str] = []
    for key, value in customize.items():
        lines.append(_toml_line(key, value))
    (skill_dir / "customize.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return skill_dir


def _toml_line(key: str, value: object) -> str:
    """Tiny TOML emitter for the simple test inputs used here."""
    if isinstance(value, str):
        return f'{key} = "{value}"'
    if isinstance(value, bool):
        return f"{key} = {'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f"{key} = {value}"
    if isinstance(value, list):
        return f"{key} = {value!r}".replace("'", '"')
    if isinstance(value, dict):
        body = ", ".join(_inline(k, v) for k, v in value.items())
        return f"{key} = {{ {body} }}"
    raise TypeError(f"unsupported test value for TOML emit: {type(value).__name__}")


def _inline(key: str, value: object) -> str:
    """Encode a value as part of an inline TOML table."""
    if isinstance(value, str):
        return f'{key} = "{value}"'
    if isinstance(value, bool):
        return f"{key} = {'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f"{key} = {value}"
    if isinstance(value, list):
        rendered = ", ".join(
            (f'"{item}"' if isinstance(item, str) else str(item)) for item in value
        )
        return f"{key} = [{rendered}]"
    raise TypeError(f"unsupported inline value for TOML emit: {type(value).__name__}")
