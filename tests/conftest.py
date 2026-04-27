"""Pytest configuration and fixtures for bmad-assist tests."""

import contextlib
from pathlib import Path
from unittest.mock import patch

import pytest

# Skill-layout compiler modules whose ``apply_llm_transforms`` import
# must be stubbed by ``disable_patch_compilation``. Each module
# re-exports the function at top level so existing tests can also
# monkeypatch it directly via ``setattr(skill_mod, "apply_llm_transforms", ...)``.
# Phase 3.2 should append new entries here when adding more skill
# compilers.
_SKILL_COMPILER_MODULES: tuple[str, ...] = (
    "bmad_assist.compiler.skills.bmad_code_review",
    "bmad_assist.compiler.skills.bmad_create_story",
    "bmad_assist.compiler.skills.bmad_dev_story",
    "bmad_assist.compiler.skills.bmad_retrospective",
)


@pytest.fixture(autouse=True)
def reset_paths_singleton():
    """Reset paths singleton before and after each test.

    This ensures tests don't leak path configuration between each other.
    Tests that need paths must explicitly initialize them.
    """
    from bmad_assist.core.paths import _reset_paths

    _reset_paths()
    yield
    _reset_paths()


@pytest.fixture(autouse=True)
def reset_and_load_minimal_config(request):
    """Reset config singleton and load minimal config for tests.

    This fixture:
    1. Resets config singleton before test
    2. Loads minimal valid config (unless marked with @pytest.mark.no_auto_config)
    3. Resets config singleton after test

    Tests that need NO config (e.g., testing config loading itself) can use:
        @pytest.mark.no_auto_config
    """
    from bmad_assist.core.config import _reset_config, load_config

    # Reset before test
    _reset_config()

    # Load minimal config unless explicitly disabled
    if not request.node.get_closest_marker("no_auto_config"):
        # Minimal valid config
        minimal_config = {
            "providers": {
                "master": {
                    "provider": "claude",
                    "model": "opus",
                }
            }
        }
        try:
            load_config(minimal_config)
        except Exception:
            # Config already loaded or other issue - that's ok
            pass

    yield

    # Reset after test
    _reset_config()


@pytest.fixture(autouse=True)
def reset_loop_config_singleton():
    """Reset loop config singleton to DEFAULT_LOOP_CONFIG before and after each test.

    This ensures tests don't leak loop configuration between each other.
    Without this, tests could pick up the project's bmad-assist.yaml loop config
    which has TEA phases enabled.
    """
    from bmad_assist.core.config.loop_config import _reset_loop_config, set_loop_config
    from bmad_assist.core.config.models.loop import DEFAULT_LOOP_CONFIG

    _reset_loop_config()
    # Explicitly set to DEFAULT to avoid loading from project bmad-assist.yaml
    set_loop_config(DEFAULT_LOOP_CONFIG)
    yield
    _reset_loop_config()


@pytest.fixture
def init_test_paths(tmp_path):
    """Initialize paths singleton for a test with temp directory.

    Usage:
        def test_something(init_test_paths):
            # paths are now initialized with tmp_path as project root
            from bmad_assist.core.paths import get_paths
            paths = get_paths()
    """
    from bmad_assist.core.paths import init_paths

    paths = init_paths(tmp_path)
    paths.ensure_directories()
    return paths


@pytest.fixture
def cli_isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Create isolated environment for CLI tests with minimal config.

    This fixture:
    1. Changes CWD to tmp_path (isolates from real bmad-assist.yaml)
    2. Creates minimal bmad-assist.yaml in tmp_path
    3. Returns tmp_path for test use

    Usage:
        def test_cli_command(cli_isolated_env):
            result = runner.invoke(app, ["compile", ...])
            # Uses config from cli_isolated_env (tmp_path)
    """
    monkeypatch.chdir(tmp_path)

    # Create minimal config file
    config_content = """\
providers:
  master:
    provider: claude
    model: opus
"""
    (tmp_path / "bmad-assist.yaml").write_text(config_content)
    return tmp_path


@pytest.fixture(autouse=True)
def disable_notifications():
    """Reset notification dispatcher before and after each test.

    Prevents any real notifications from being sent during tests.
    Tests that need real notifications should mock the providers explicitly.
    """
    from bmad_assist.notifications.dispatcher import reset_dispatcher

    reset_dispatcher()
    yield
    reset_dispatcher()


@pytest.fixture(autouse=True)
def disable_patch_compilation(request):
    """Skip patch compilation during tests to avoid LLM calls.

    Patch compilation uses the Master LLM provider to transform workflows,
    which is slow (~7s per attempt × 3 retries = 21s). Tests should use
    original workflow files instead.

    Tests that need real patch compilation can use:
        @pytest.mark.real_patch_compilation
    """
    if request.node.get_closest_marker("real_patch_compilation"):
        yield None
        return

    from bmad_assist.core.exceptions import PatchError

    def _raise(*args, **kwargs):
        raise PatchError("Patch compilation disabled in tests")

    # Disable BOTH the legacy compile_patch entry-point AND the
    # extracted apply_llm_transforms shared by every skill-layout
    # compiler. Without the second set of mocks, Phase 2.5+ tests
    # would issue real LLM calls (skill compilers bypass compile_patch
    # and talk to apply_llm_transforms directly).
    #
    # Phase 3.1: replaced the hardcoded bmad_create_story patch with a
    # registry sweep over every skill-compiler module. Adding a new
    # skill compiler is now a one-line change in
    # ``_SKILL_COMPILER_MODULES`` above. Each module is patched
    # individually because tests reach into the subclass module
    # directly (``monkeypatch.setattr(skill_mod, "apply_llm_transforms",
    # fake)``) and the base class re-resolves the attribute on every
    # call from the subclass's own module.
    with contextlib.ExitStack() as stack:
        mock = stack.enter_context(patch("bmad_assist.compiler.patching.compiler.compile_patch"))
        mock.side_effect = _raise

        skill_mocks: dict[str, object] = {}
        for module_name in _SKILL_COMPILER_MODULES:
            try:
                skill_mock = stack.enter_context(
                    patch(
                        f"{module_name}.apply_llm_transforms",
                        side_effect=_raise,
                    )
                )
            except (AttributeError, ImportError):
                # Defensive: if a registered skill compiler hasn't been
                # imported yet (or doesn't re-export apply_llm_transforms)
                # we skip it rather than fail the whole test session.
                continue
            skill_mocks[module_name] = skill_mock

        # Expose the per-module apply_llm_transforms stubs on the
        # primary mock for tests that want to assert against them.
        # Backwards-compat alias — tests previously checked
        # ``mock.apply_llm_transforms`` (the bmad_create_story stub).
        mock.skill_apply_llm_transforms = skill_mocks
        legacy_alias = skill_mocks.get("bmad_assist.compiler.skills.bmad_create_story")
        if legacy_alias is not None:
            mock.apply_llm_transforms = legacy_alias
        yield mock


@pytest.fixture(autouse=True)
def disable_debug_json_logger(request):
    """Globally disable DebugJsonLogger during tests.

    Prevents tests from writing to ~/.bmad-assist/debug/json/
    which would create large files (especially for tests with
    10MB output like test_output_capture.py).

    Tests that need the real logger can use:
        @pytest.mark.real_debug_logger
    """
    # Skip this fixture for tests marked with real_debug_logger
    if request.node.get_closest_marker("real_debug_logger"):
        yield None
        return

    with patch("bmad_assist.core.debug_logger.DebugJsonLogger") as mock:
        # Create a mock that does nothing
        instance = mock.return_value
        instance.enabled = False
        instance.append.return_value = None
        instance.close.return_value = None
        instance.path = None
        yield mock
