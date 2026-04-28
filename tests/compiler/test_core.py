"""Tests for the compiler core module after the 0.6.0 refactor.

The dispatch table accepts only canonical ``bmad-`` prefixed ids;
legacy short aliases (``"create-story"``, ``"testarch-atdd"``, ...)
were dropped. Every accepted name resolves to a v6.4+ skill-layout
compiler under :mod:`bmad_assist.compiler.skills`.

These tests cover:

* Module structure / public API exports.
* Dispatch through :func:`get_workflow_compiler`.
* Validation of workflow names.
"""

from __future__ import annotations

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

    def test_workflow_registry_only_canonical_ids(self) -> None:
        """Every registry entry is a canonical ``bmad-`` prefixed self-mapping."""
        assert WORKFLOW_REGISTRY, "WORKFLOW_REGISTRY must not be empty"
        for key, value in WORKFLOW_REGISTRY.items():
            assert key.startswith("bmad-"), f"Registry key '{key}' is not bmad-prefixed"
            assert value == key, f"Registry entry '{key}' must self-map (got '{value}')"


class TestDispatch:
    """Dispatch from :func:`get_workflow_compiler`."""

    def test_dispatch_routes_canonical_id(self) -> None:
        """Canonical ``bmad-`` prefixed id dispatches to the skill-layout compiler."""
        compiler = get_workflow_compiler("bmad-create-story")
        assert compiler.workflow_name == "bmad-create-story"
        assert type(compiler).__module__.startswith("bmad_assist.compiler.skills.")

    def test_dispatch_normalises_underscores(self) -> None:
        """Underscored module-style names (``bmad_create_story``) resolve too."""
        compiler = get_workflow_compiler("bmad_create_story")
        assert compiler.workflow_name == "bmad-create-story"

    def test_dispatch_strips_whitespace(self) -> None:
        """Surrounding whitespace is tolerated."""
        compiler = get_workflow_compiler("  bmad-create-story  ")
        assert compiler.workflow_name == "bmad-create-story"

    def test_legacy_short_alias_auto_prefixes(self) -> None:
        """An un-prefixed name auto-prepends ``bmad-`` for internal dispatch.

        The 0.6.0 registry simplification dropped explicit alias entries,
        but production code still routes via the Phase enum (which emits
        un-prefixed names like ``"create-story"``). The dispatcher
        auto-prepends ``bmad-`` as a fallback so internal callers don't
        need a sweeping rewrite.
        """
        compiler = get_workflow_compiler("create-story")
        assert compiler.workflow_name == "bmad-create-story"

    def test_unknown_unprefixed_name_still_raises(self) -> None:
        """Auto-prefix only fires for *known* skill ids; unknowns still raise."""
        with pytest.raises(CompilerError):
            get_workflow_compiler("definitely-not-a-workflow")

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


class TestBackwardsCompatKwargs:
    """Pre-0.6.0 callers may pass ``skill_layout=...`` — accept and ignore."""

    def test_skill_layout_kwarg_silently_ignored(self) -> None:
        """``skill_layout`` is swallowed by ``**_legacy_kwargs`` without error."""
        compiler = get_workflow_compiler("bmad-create-story", skill_layout="new")
        assert compiler.workflow_name == "bmad-create-story"

    def test_compile_workflow_skill_layout_kwarg_silently_ignored(self, tmp_path) -> None:
        """``compile_workflow(..., skill_layout=...)`` still accepts the kwarg.

        The compile call itself fails (no skill installed in tmp_path),
        but the failure must NOT be a ``TypeError`` from an unexpected
        keyword argument — that would mean the back-compat shim broke.
        """
        ctx = CompilerContext(
            project_root=tmp_path,
            output_folder=tmp_path,
            project_knowledge=tmp_path,
        )
        try:
            compile_workflow("bmad-create-story", ctx, skill_layout="auto")
        except TypeError as exc:  # pragma: no cover — guard against regression
            pytest.fail(f"skill_layout kwarg should be swallowed, got TypeError: {exc}")
        except Exception:  # noqa: BLE001 — any other failure is fine
            pass


class TestProtocolCompliance:
    """The dispatched compiler implements the public protocol."""

    def test_implements_protocol(self) -> None:
        """Dispatched compiler satisfies the WorkflowCompiler protocol."""
        compiler = get_workflow_compiler("bmad-create-story")
        assert isinstance(compiler, WorkflowCompiler)

    def test_required_methods_callable(self) -> None:
        """All five protocol members are bound on the dispatched compiler."""
        compiler = get_workflow_compiler("bmad-create-story")
        assert callable(compiler.get_required_files)
        assert callable(compiler.get_variables)
        assert callable(compiler.validate_context)
        assert callable(compiler.compile)
        assert isinstance(compiler.workflow_name, str)
