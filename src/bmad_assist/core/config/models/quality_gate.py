"""Quality-gate configuration.

The quality gate runs project pre-commit hooks (or any configured hook
command) against changed files BEFORE a phase declares success. Hard-fail
by design — the phase fails if any hook fails. No LLM fix-retry.

See :mod:`bmad_assist.quality_gate` for the runner implementation; this
module only defines the user-facing config surface that maps onto its
keyword arguments.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class QualityGateConfig(BaseModel):
    """Quality-gate phase-success configuration.

    Attributes:
        enabled: Master switch. When False the gate never runs.
        phases: Phase names that run the gate before declaring success.
            Defaults to ``["dev_story", "create_story"]`` — the two phases
            that produce file mutations a human would normally lint at
            commit time.
        hook_command: Shell command template. ``{files}`` is substituted
            with the space-separated list of changed file paths by the
            runner.
        skip_if_no_config: Skip (don't fail) when the project has no
            ``.pre-commit-config.yaml``. Lets the default ``enabled=True``
            be a safe no-op for projects without pre-commit.
        timeout_seconds: Wall-clock cap on the hook subprocess.

    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(
        default=True,
        description="Gate phase success on pre-commit hooks",
        json_schema_extra={"security": "safe", "ui_widget": "toggle"},
    )
    phases: list[str] = Field(
        default_factory=lambda: ["dev_story", "create_story"],
        description="Phases that run the quality gate before declaring success",
        json_schema_extra={"security": "safe", "ui_widget": "text"},
    )
    hook_command: str = Field(
        default="pre-commit run --files {files}",
        description="Shell command to invoke. {files} is substituted with changed file paths.",
        json_schema_extra={"security": "dangerous", "ui_widget": "text"},
    )
    skip_if_no_config: bool = Field(
        default=True,
        description="Skip the gate (don't fail) if .pre-commit-config.yaml is missing",
        json_schema_extra={"security": "safe", "ui_widget": "toggle"},
    )
    timeout_seconds: int = Field(
        default=300,
        ge=10,
        description="Wall-clock cap on the hook subprocess (seconds)",
        json_schema_extra={"security": "safe", "ui_widget": "number", "unit": "s"},
    )

    def is_enabled_for_phase(self, phase: str) -> bool:
        """Return True iff the gate is enabled for ``phase``.

        Phase names use the underscore form (e.g. ``"dev_story"``) — the
        same form ``BaseHandler.phase_name`` returns.
        """
        return self.enabled and phase in self.phases
