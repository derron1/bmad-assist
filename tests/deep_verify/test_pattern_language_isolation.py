"""Regression tests for pattern-library language isolation.

These tests pin Deep Verify's P1 fix: when the pattern matcher is asked for
patterns scoped to one language, it must NOT return patterns tagged with a
different language. The original bug let Go-idiom patterns (e.g.
``CQ-002-CODE-GO`` "defer inside loop") fire on Python files just because
they contained a ``for`` loop, because batch mode never propagated the
language hint and ``get_patterns(language=None)`` returned the full library.

Two contracts are exercised:

1. ``get_patterns(language="<lang>")`` returns only spec patterns
   (``pattern.language is None``) plus code patterns whose
   ``pattern.language == <lang>``. Patterns tagged with any other language
   are excluded.
2. ``get_patterns(language=None)`` returns spec-only patterns. Code patterns
   for any language are excluded — callers without a language hint must not
   accidentally match code patterns across languages. (Callers wanting the
   raw, unfiltered library should use ``get_all_patterns()``.)
"""

from __future__ import annotations

import pytest

from bmad_assist.deep_verify.patterns.library import (
    PatternLibrary,
    get_default_pattern_library,
)


@pytest.fixture(scope="module")
def default_library() -> PatternLibrary:
    """Default Deep Verify pattern library (cached singleton).

    Uses the real shipped library so the regression actually pins the
    behaviour seen in production rather than a synthetic fixture.
    """
    return get_default_pattern_library()


class TestPythonLanguageIsolation:
    """When language='python', no other-language code patterns leak in."""

    def test_no_go_patterns_returned_for_python(self, default_library: PatternLibrary) -> None:
        patterns = default_library.get_patterns(language="python")
        go_patterns = [p for p in patterns if p.language == "go"]
        assert go_patterns == [], (
            "get_patterns(language='python') leaked Go-tagged patterns: "
            f"{[p.id for p in go_patterns]}"
        )

    def test_no_javascript_patterns_returned_for_python(
        self, default_library: PatternLibrary
    ) -> None:
        patterns = default_library.get_patterns(language="python")
        js_patterns = [p for p in patterns if p.language == "javascript"]
        assert js_patterns == [], (
            "get_patterns(language='python') leaked JavaScript-tagged "
            f"patterns: {[p.id for p in js_patterns]}"
        )

    def test_no_typescript_patterns_returned_for_python(
        self, default_library: PatternLibrary
    ) -> None:
        patterns = default_library.get_patterns(language="python")
        ts_patterns = [p for p in patterns if p.language == "typescript"]
        assert ts_patterns == [], (
            "get_patterns(language='python') leaked TypeScript-tagged "
            f"patterns: {[p.id for p in ts_patterns]}"
        )

    def test_only_python_or_spec_patterns_returned_for_python(
        self, default_library: PatternLibrary
    ) -> None:
        patterns = default_library.get_patterns(language="python")
        assert patterns, "expected at least the spec patterns to be returned"
        for p in patterns:
            assert p.language is None or p.language == "python", (
                f"unexpected language={p.language!r} on pattern {p.id} when filtering for python"
            )

    def test_spec_patterns_still_included_for_python(self, default_library: PatternLibrary) -> None:
        """Language-agnostic patterns (pattern.language is None) MUST remain.

        Spec patterns drive the bulk of useful matches and are language
        independent. Filtering for a specific language should narrow the
        code patterns, not strip spec patterns.
        """
        patterns = default_library.get_patterns(language="python")
        spec_patterns = [p for p in patterns if p.language is None]
        assert spec_patterns, (
            "get_patterns(language='python') returned no spec (language=None) "
            "patterns; spec patterns should always be included"
        )


class TestGoLanguageIsolation:
    """Symmetric check from the Go side — no Python/JS leakage either way."""

    def test_no_python_patterns_returned_for_go(self, default_library: PatternLibrary) -> None:
        patterns = default_library.get_patterns(language="go")
        py_patterns = [p for p in patterns if p.language == "python"]
        assert py_patterns == [], (
            "get_patterns(language='go') leaked Python-tagged patterns: "
            f"{[p.id for p in py_patterns]}"
        )

    def test_only_go_or_spec_patterns_returned_for_go(
        self, default_library: PatternLibrary
    ) -> None:
        patterns = default_library.get_patterns(language="go")
        for p in patterns:
            assert p.language is None or p.language == "go", (
                f"unexpected language={p.language!r} on pattern {p.id} when filtering for go"
            )


class TestNoLanguageHintReturnsSpecOnly:
    """get_patterns(language=None) must NOT include language-tagged patterns.

    This is the second half of the P1 fix: code paths that legitimately have
    no language hint (e.g. a future caller, a doc/spec artifact) must not
    accidentally trip code patterns from any language.
    """

    def test_language_none_excludes_go_code_patterns(self, default_library: PatternLibrary) -> None:
        patterns = default_library.get_patterns(language=None)
        go_patterns = [p for p in patterns if p.language == "go"]
        assert go_patterns == [], (
            "get_patterns(language=None) leaked Go-tagged code patterns: "
            f"{[p.id for p in go_patterns]}"
        )

    def test_language_none_excludes_python_code_patterns(
        self, default_library: PatternLibrary
    ) -> None:
        patterns = default_library.get_patterns(language=None)
        py_patterns = [p for p in patterns if p.language == "python"]
        assert py_patterns == [], (
            "get_patterns(language=None) leaked Python-tagged code patterns: "
            f"{[p.id for p in py_patterns]}"
        )

    def test_language_none_returns_only_spec_patterns(
        self, default_library: PatternLibrary
    ) -> None:
        patterns = default_library.get_patterns(language=None)
        assert patterns, "expected at least one spec pattern in default library"
        for p in patterns:
            assert p.language is None, (
                f"get_patterns(language=None) returned non-spec pattern "
                f"{p.id} with language={p.language!r}"
            )

    def test_get_all_patterns_still_returns_full_library(
        self, default_library: PatternLibrary
    ) -> None:
        """Sanity check: get_all_patterns() is the escape hatch.

        Callers that genuinely want every pattern (e.g. introspection, full
        library dumps) must still get them via get_all_patterns(); the
        narrowed get_patterns(language=None) contract is intentional.
        """
        all_patterns = default_library.get_all_patterns()
        spec_only = default_library.get_patterns(language=None)
        # All-patterns must be a strict superset of spec-only when the
        # default library actually ships code patterns.
        has_code_patterns = any(p.language is not None for p in all_patterns)
        if has_code_patterns:
            assert len(all_patterns) > len(spec_only), (
                "get_all_patterns() should include language-tagged code "
                "patterns; got the same count as get_patterns(language=None)"
            )
