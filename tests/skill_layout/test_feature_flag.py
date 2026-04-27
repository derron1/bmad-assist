"""Tests for the ``skill_layout`` feature flag plumbing.

Phase 2 consumes the flag: ``compile_workflow(..., skill_layout="new")``
now routes ``bmad-create-story`` (and the legacy ``create-story``
alias) through the v6.4+ skill-layout compiler. End-to-end coverage of
that route lives in
:mod:`tests.skill_layout.test_create_story_e2e` and
:mod:`tests.skill_layout.test_create_story_compat`. The tests in this
module focus on the config model surface and the CLI flag plumbing.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from bmad_assist.cli import app
from bmad_assist.core.config.models.main import Config
from bmad_assist.core.config.models.providers import MasterProviderConfig, ProviderConfig


def _minimal_config(**overrides: object) -> Config:
    """Build the smallest possible Config for unit testing."""
    base: dict[str, object] = {
        "providers": ProviderConfig(
            master=MasterProviderConfig(provider="claude-subprocess", model="opus"),
        ),
    }
    base.update(overrides)
    return Config(**base)


def test_config_default_skill_layout_is_auto() -> None:
    """The default value is ``'auto'`` when nothing is supplied."""
    config = _minimal_config()
    assert config.skill_layout == "auto"


@pytest.mark.parametrize("value", ["auto", "new", "old"])
def test_config_accepts_each_valid_value(value: str) -> None:
    """All three documented values must validate cleanly."""
    config = _minimal_config(skill_layout=value)
    assert config.skill_layout == value


def test_config_rejects_invalid_value() -> None:
    """Any value outside the literal set raises ``ValidationError``."""
    with pytest.raises(ValidationError):
        _minimal_config(skill_layout="legacy")


def test_cli_run_command_advertises_skill_layout_flag() -> None:
    """The ``run`` command's ``--help`` lists ``--skill-layout``."""
    runner = CliRunner()
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--skill-layout" in result.stdout
