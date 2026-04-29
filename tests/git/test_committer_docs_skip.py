"""Tests for the docs-only short-circuit in auto_commit_phase.

Per-phase auto-commits often touch only sprint-status.yaml,
.bundle-version stamps, or story markdown — yet the pre-commit
fix pass (`_run_precommit_fix`) runs ESLint + ``tsc`` across the
whole project, costing ~10 s per phase. We short-circuit when the
diff has no JS/TS files.
"""

from __future__ import annotations

from bmad_assist.git.committer import _has_lint_relevant_changes


def test_docs_only_diff_skips_precommit() -> None:
    docs_only = [
        "_bmad-output/implementation-artifacts/sprint-status.yaml",
        ".claude/skills/bmad-create-story/.bundle-version",
        "_bmad-output/implementation-artifacts/12-3-foo.md",
        "docs/configuration.md",
        "package.json",  # config, not lintable per current hook setup
    ]
    assert _has_lint_relevant_changes(docs_only) is False


def test_ts_diff_runs_precommit() -> None:
    assert _has_lint_relevant_changes(["src/foo.ts"]) is True
    assert _has_lint_relevant_changes(["apps/storefront/src/routes/classes.tsx"]) is True


def test_js_jsx_mjs_cjs_diff_runs_precommit() -> None:
    assert _has_lint_relevant_changes(["scripts/build.js"]) is True
    assert _has_lint_relevant_changes(["src/Component.jsx"]) is True
    assert _has_lint_relevant_changes(["scripts/setup.mjs"]) is True
    assert _has_lint_relevant_changes(["scripts/legacy.cjs"]) is True


def test_vue_svelte_diff_runs_precommit() -> None:
    assert _has_lint_relevant_changes(["src/App.vue"]) is True
    assert _has_lint_relevant_changes(["src/App.svelte"]) is True


def test_mixed_diff_runs_precommit() -> None:
    """A single code file in a docs-heavy diff still triggers the fix pass."""
    mixed = [
        "_bmad-output/implementation-artifacts/sprint-status.yaml",
        "docs/foo.md",
        "apps/medusa/src/admin/routes/classes/page.tsx",  # the code change
    ]
    assert _has_lint_relevant_changes(mixed) is True


def test_python_files_do_not_trigger_precommit() -> None:
    """The current pre-commit hook handles ESLint + tsc only — Python
    isn't in scope. If a project's hook setup were to change, this test
    would need to update alongside ``_LINT_RELEVANT_EXTENSIONS``.
    """
    assert _has_lint_relevant_changes(["src/foo.py"]) is False


def test_empty_diff_returns_false() -> None:
    assert _has_lint_relevant_changes([]) is False
