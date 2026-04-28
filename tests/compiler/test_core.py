"""Tests for the compiler core module after the Phase 6 refactor.

Phase 6 collapsed the dispatch table — every accepted name now resolves
to a v6.4+ skill-layout compiler under
:mod:`bmad_assist.compiler.skills`. The legacy compilers in
:mod:`bmad_assist.compiler.workflows` remain as private delegation
targets only.

These tests cover:

* Module structure / public API exports.
* Dispatch through :func:`get_workflow_compiler`.
* Validation of workflow names.
* Backwards-compat behaviour for the deprecated ``skill_layout``
  argument (no-op + ``DeprecationWarning``).
"""

from __future__ import annotations

import warnings

import pytest

from bmad_assist.compiler import (
    CompiledWorkflow,
    CompilerContext,
    CompilerError,
    WorkflowCompiler,
    WorkflowIR,
    compile_workflow,
    get_workflow_compiler,
    parse_workflow,
)
from bmad_assist.compiler.core import WORKFLOW_REGISTRY


class TestModuleStructure:
    """Compiler module exports the documented public API."""

    def test_compiler_module_structure(self) -> None:
        """Compiler module exports the documented dispatch helpers."""
        from bmad_assist import compiler

        assert hasattr(compiler, "compile_workflow")
        assert hasattr(compiler, "CompilerError")
        assert hasattr(compiler, "WorkflowCompiler")
        assert hasattr(compiler, "get_workflow_compiler")

    def test_compiler_all_exports(self) -> None:
        """``__all__`` matches the actually-bound module attributes."""
        from bmad_assist import compiler

        for name in compiler.__all__:
            assert hasattr(compiler, name), f"Missing export: {name}"

    def test_types_importable(self) -> None:
        """Public type aliases are importable from the compiler module."""
        assert CompilerContext is not None
        assert CompiledWorkflow is not None
        assert WorkflowIR is not None
        assert parse_workflow is not None
        assert compile_workflow is not None

    def test_workflow_registry_alias_count(self) -> None:
        """Every registry entry maps to a bmad-prefixed canonical id."""
        assert WORKFLOW_REGISTRY, "WORKFLOW_REGISTRY must not be empty"
        for value in WORKFLOW_REGISTRY.values():
            assert value.startswith("bmad-"), f"Registry value '{value}' is not bmad-prefixed"

    def test_workflow_registry_canonical_self_aliases(self) -> None:
        """Every canonical id maps to itself in the registry."""
        canonical_ids = set(WORKFLOW_REGISTRY.values())
        for canonical in canonical_ids:
            assert WORKFLOW_REGISTRY.get(canonical) == canonical, f"{canonical} must map to itself"


class TestDispatch:
    """Dispatch from :func:`get_workflow_compiler`."""

    def test_dispatch_routes_legacy_alias(self) -> None:
        """A legacy alias resolves to the bmad-prefixed compiler."""
        compiler = get_workflow_compiler("create-story")
        assert compiler.workflow_name == "bmad-create-story"
        assert type(compiler).__module__.startswith("bmad_assist.compiler.skills.")

    def test_dispatch_routes_canonical_id(self) -> None:
        """Canonical ``bmad-`` prefixed id also dispatches."""
        compiler = get_workflow_compiler("bmad-create-story")
        assert compiler.workflow_name == "bmad-create-story"

    def test_dispatch_normalises_underscores(self) -> None:
        """Underscored aliases (legacy module names) resolve too."""
        compiler = get_workflow_compiler("create_story")
        assert compiler.workflow_name == "bmad-create-story"

    def test_dispatch_strips_whitespace(self) -> None:
        """Surrounding whitespace is tolerated."""
        compiler = get_workflow_compiler("  create-story  ")
        assert compiler.workflow_name == "bmad-create-story"

    def test_dispatch_unknown_workflow_raises(self) -> None:
        """Unknown workflows raise :class:`CompilerError` with a hint."""
        with pytest.raises(CompilerError) as exc_info:
            get_workflow_compiler("nonexistent-workflow")
        msg = str(exc_info.value)
        assert "nonexistent-workflow" in msg
        assert "WORKFLOW_REGISTRY" in msg

    def test_dispatch_empty_name_raises(self) -> None:
        """Empty / whitespace-only names raise."""
        with pytest.raises(CompilerError) as exc_info:
            get_workflow_compiler("")
        assert "empty" in str(exc_info.value).lower()
        with pytest.raises(CompilerError) as exc_info:
            get_workflow_compiler("   ")
        assert "empty" in str(exc_info.value).lower()

    def test_dispatch_invalid_name_dot(self) -> None:
        """Names with dots are rejected (import path security)."""
        with pytest.raises(CompilerError) as exc_info:
            get_workflow_compiler("create.story")
        assert "invalid workflow name" in str(exc_info.value).lower()

    def test_dispatch_invalid_name_slash(self) -> None:
        """Path-traversal-style names are rejected."""
        with pytest.raises(CompilerError) as exc_info:
            get_workflow_compiler("../malicious")
        assert "invalid workflow name" in str(exc_info.value).lower()

    def test_dispatch_invalid_name_uppercase(self) -> None:
        """Uppercase names are rejected."""
        with pytest.raises(CompilerError) as exc_info:
            get_workflow_compiler("CreateStory")
        assert "invalid workflow name" in str(exc_info.value).lower()

    def test_dispatch_invalid_name_starts_with_digit(self) -> None:
        """Names must begin with a letter."""
        with pytest.raises(CompilerError) as exc_info:
            get_workflow_compiler("123-workflow")
        assert "invalid workflow name" in str(exc_info.value).lower()


class TestSkillLayoutDeprecation:
    """The ``skill_layout`` parameter is a no-op-with-warning since Phase 6."""

    def setup_method(self) -> None:
        """Reset the per-process dedup flag so each test sees a fresh warning."""
        import bmad_assist.compiler.core as core

        core._SKILL_LAYOUT_FLAG_WARNING_EMITTED = False

    def test_default_does_not_warn(self) -> None:
        """``skill_layout="auto"`` (default) emits no DeprecationWarning."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            get_workflow_compiler("create-story")
        deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        skill_layout_warnings = [w for w in deprecations if "skill-layout" in str(w.message)]
        assert not skill_layout_warnings

    def test_explicit_value_warns_once(self) -> None:
        """Passing ``"old"`` triggers exactly one DeprecationWarning."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            get_workflow_compiler("create-story", skill_layout="old")
            get_workflow_compiler("create-story", skill_layout="old")
        deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        skill_layout_warnings = [w for w in deprecations if "skill-layout" in str(w.message)]
        assert len(skill_layout_warnings) == 1

    def test_explicit_value_still_dispatches(self) -> None:
        """Even with ``"old"`` we still get a skill-layout compiler."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            compiler = get_workflow_compiler("create-story", skill_layout="old")
        assert type(compiler).__module__.startswith("bmad_assist.compiler.skills.")


class TestProtocolCompliance:
    """The dispatched compiler implements the public protocol."""

    def test_implements_protocol(self) -> None:
        """Dispatched compiler satisfies the WorkflowCompiler protocol."""
        compiler = get_workflow_compiler("create-story")
        assert isinstance(compiler, WorkflowCompiler)

    def test_required_methods_callable(self) -> None:
        """All five protocol members are bound on the dispatched compiler."""
        compiler = get_workflow_compiler("create-story")
        assert callable(compiler.get_required_files)
        assert callable(compiler.get_variables)
        assert callable(compiler.validate_context)
        assert callable(compiler.compile)
        assert isinstance(compiler.workflow_name, str)
