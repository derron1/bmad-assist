"""Run manifest system for experiment framework.

This module provides the manifest system for tracking experiment runs,
including all configuration resolved values, execution results, and metrics.

Usage:
    from bmad_assist.experiments import (
        RunManifest,
        ManifestInput,
        ManifestResolved,
        ManifestResults,
        ManifestMetrics,
        ManifestPhaseResult,
        ManifestManager,
    )

    # Create manifest manager for a run
    manager = ManifestManager(run_dir)

    # Create manifest at start
    manifest = manager.create(input, resolved, started, run_id)

    # Update during execution
    manager.update_status(ExperimentStatus.RUNNING)
    manager.add_phase_result(phase_result)

    # Finalize on completion
    manager.finalize(ExperimentStatus.COMPLETED, completed)

"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
    field_validator,
)

from bmad_assist.core.exceptions import ConfigError, ManifestError
from bmad_assist.experiments.runner import ExperimentStatus

logger = logging.getLogger(__name__)

# Terminal statuses that lock the manifest
TERMINAL_STATUSES: frozenset[ExperimentStatus] = frozenset(
    {
        ExperimentStatus.COMPLETED,
        ExperimentStatus.FAILED,
        ExperimentStatus.CANCELLED,
    }
)

__all__ = [
    # Models
    "ManifestInput",
    "ResolvedFixture",
    "ResolvedConfig",
    "ResolvedPatchSet",
    "ResolvedLoop",
    "ManifestResolved",
    "ManifestPhaseResult",
    "ManifestResults",
    "ManifestMetrics",
    "RunManifest",
    # Manager
    "ManifestManager",
    # Helper functions
    "build_resolved_fixture",
    "build_resolved_config",
    "build_resolved_patchset",
    "build_resolved_loop",
    # Constants
    "TERMINAL_STATUSES",
]


# =============================================================================
# Manifest Input Models (frozen - never changes after creation)
# =============================================================================


class ManifestInput(BaseModel):
    """Input configuration that was requested for the experiment run.

    Captures the four axis names exactly as specified by the user.
    This section never changes after manifest creation.

    Attributes:
        fixture: Fixture ID requested.
        config: Config template name requested.
        patch_set: Patch-set manifest name requested.
        loop: Loop template name requested.

    """

    model_config = ConfigDict(frozen=True)

    fixture: str = Field(..., description="Fixture ID requested")
    config: str = Field(..., description="Config template name requested")
    patch_set: str = Field(..., description="Patch-set manifest name requested")
    customize_set: str | None = Field(
        default=None, description="Customize-set name requested (v6.4 customize.toml overlays)"
    )
    loop: str = Field(..., description="Loop template name requested")


# =============================================================================
# Resolved Section Models (frozen - snapshot of actual values used)
# =============================================================================


class ResolvedFixture(BaseModel):
    """Resolved fixture configuration snapshot.

    Captures the actual fixture source and snapshot location.

    Attributes:
        name: Fixture name from registry.
        source: Original fixture path.
        snapshot: Relative path to snapshot in run dir.

    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., description="Fixture name from registry")
    source: str = Field(..., description="Original fixture path")
    snapshot: str = Field(..., description="Relative path to snapshot in run dir")


class ResolvedConfig(BaseModel):
    """Resolved config template snapshot.

    Captures the actual provider configuration used.

    Attributes:
        name: Config template name.
        source: Config YAML file path.
        providers: Full provider configuration dict.

    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., description="Config template name")
    source: str = Field(..., description="Config YAML file path")
    providers: dict[str, Any] = Field(..., description="Full provider configuration dict")


class ResolvedPatchSet(BaseModel):
    """Resolved patch-set manifest snapshot.

    Captures the actual patch files and workflow overrides used.

    Attributes:
        name: Patch-set manifest name.
        source: Patch-set YAML file path.
        workflow_overrides: Workflow name to override directory path.
        patches: Workflow name to resolved patch file path.

    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., description="Patch-set manifest name")
    source: str = Field(..., description="Patch-set YAML file path")
    workflow_overrides: dict[str, str] = Field(
        default_factory=dict,
        description="Workflow name to override directory path",
    )
    patches: dict[str, str | None] = Field(
        default_factory=dict,
        description="Workflow name to resolved patch file path",
    )


class ResolvedLoop(BaseModel):
    """Resolved loop template snapshot.

    Captures the actual workflow sequence used.

    Attributes:
        name: Loop template name.
        source: Loop YAML file path.
        sequence: Ordered list of workflow names.

    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., description="Loop template name")
    source: str = Field(..., description="Loop YAML file path")
    sequence: list[str] = Field(..., description="Ordered list of workflow names")


class ManifestResolved(BaseModel):
    """Complete resolved configuration for the experiment run.

    This section captures the actual values used, not just the names.
    It provides full reproducibility information.

    Attributes:
        fixture: Resolved fixture configuration.
        config: Resolved config template.
        patch_set: Resolved patch-set manifest.
        loop: Resolved loop template.

    """

    model_config = ConfigDict(frozen=True)

    fixture: ResolvedFixture
    config: ResolvedConfig
    patch_set: ResolvedPatchSet
    loop: ResolvedLoop


# =============================================================================
# Results Models (NOT frozen - updated incrementally during run)
# =============================================================================


class ManifestPhaseResult(BaseModel):
    """Result of a single phase execution.

    Named ManifestPhaseResult to avoid collision with core/loop/types.py:PhaseResult.
    Tracks the outcome of each workflow phase in the experiment.

    Attributes:
        phase: Phase/workflow name.
        story: Story ID if applicable; None for epic-level phases.
        epic: Epic ID if applicable; useful for multi-epic experiments.
        status: Phase outcome (completed, failed, skipped).
        duration_seconds: Phase duration in seconds.
        tokens: Total tokens used (input + output); None if not tracked.
        cost: API cost in USD; None if not tracked.
        error: Error message if failed.

    """

    model_config = ConfigDict(frozen=True)

    phase: str = Field(..., description="Phase/workflow name")
    story: str | None = Field(
        None, description="Story ID if applicable; None for epic-level phases"
    )
    epic: int | str | None = Field(
        None, description="Epic ID if applicable; useful for multi-epic experiments"
    )
    status: Literal["completed", "failed", "skipped"] = Field(..., description="Phase outcome")
    duration_seconds: float = Field(..., description="Phase duration in seconds")
    tokens: int | None = Field(None, description="Total tokens used (input + output)")
    cost: float | None = Field(None, description="API cost in USD")
    error: str | None = Field(None, description="Error message if failed")


class ManifestResults(BaseModel):
    """Execution results tracking.

    Updated incrementally as phases complete. Not frozen because
    it accumulates results during the run.

    Attributes:
        stories_attempted: Number of stories started.
        stories_completed: Number of stories succeeded.
        stories_failed: Number of stories failed.
        retrospective_completed: Whether retrospective was executed successfully.
        qa_completed: Whether QA phases completed (if --qa was used).
        phases: Per-phase result breakdown.

    """

    stories_attempted: int = Field(0, description="Number of stories started")
    stories_completed: int = Field(0, description="Number of stories succeeded")
    stories_failed: int = Field(0, description="Number of stories failed")
    retrospective_completed: bool = Field(
        False, description="Whether retrospective was executed successfully"
    )
    qa_completed: bool = Field(
        False, description="Whether QA phases completed (always runs, --qa adds Playwright tests)"
    )
    phases: list[ManifestPhaseResult] = Field(
        default_factory=list,
        description="Per-phase result breakdown",
    )


class ManifestMetrics(BaseModel):
    """Aggregated metrics for the experiment run.

    Placeholder for Story 18.8. Fields are optional since they're
    populated after run completion by metrics collection.

    Attributes:
        total_cost: Total API cost.
        total_tokens: Total tokens used.
        total_duration_seconds: Total run duration.
        avg_tokens_per_phase: Average tokens per phase.
        avg_cost_per_phase: Average cost per phase.

    """

    total_cost: float | None = Field(None, description="Total API cost")
    total_tokens: int | None = Field(None, description="Total tokens used")
    total_duration_seconds: float | None = Field(None, description="Total run duration")
    avg_tokens_per_phase: float | None = Field(None, description="Average tokens per phase")
    avg_cost_per_phase: float | None = Field(None, description="Average cost per phase")


# =============================================================================
# Root Manifest Model
# =============================================================================


class RunManifest(BaseModel):
    """Complete experiment run manifest.

    The root model that composes all manifest sections. Not frozen
    because status and results are updated during the run lifecycle.

    Attributes:
        run_id: Unique run identifier.
        started: UTC start timestamp.
        completed: UTC completion timestamp.
        status: Current run status.
        schema_version: Manifest schema version.
        input: Requested configuration.
        resolved: Actual values used.
        results: Execution results (populated during run).
        metrics: Aggregated metrics (populated by Story 18.8).

    """

    run_id: str = Field(..., description="Unique run identifier")
    started: datetime = Field(..., description="UTC start timestamp")
    completed: datetime | None = Field(None, description="UTC completion timestamp")
    status: ExperimentStatus = Field(..., description="Current run status")
    schema_version: str = Field("1.0", description="Manifest schema version")

    input: ManifestInput = Field(..., description="Requested configuration")
    resolved: ManifestResolved = Field(..., description="Actual values used")
    results: ManifestResults | None = Field(
        None, description="Execution results (populated during run)"
    )
    metrics: ManifestMetrics | None = Field(
        None, description="Aggregated metrics (populated by Story 18.8)"
    )

    @field_validator("started", "completed", mode="before")
    @classmethod
    def parse_datetime(cls, v: str | datetime | None) -> datetime | None:
        """Parse datetime from ISO 8601 string (for YAML loading)."""
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        return datetime.fromisoformat(v)

    @field_validator("status", mode="before")
    @classmethod
    def parse_status(cls, v: str | ExperimentStatus) -> ExperimentStatus:
        """Parse status from string (for YAML loading)."""
        if isinstance(v, ExperimentStatus):
            return v
        return ExperimentStatus(v)

    @field_serializer("started", "completed")
    def serialize_datetime(self, dt: datetime | None) -> str | None:
        """Serialize datetime to ISO 8601 with UTC timezone."""
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.isoformat()

    @field_serializer("status")
    def serialize_status(self, status: ExperimentStatus) -> str:
        """Serialize status enum to string value."""
        return status.value


# =============================================================================
# ManifestManager Class
# =============================================================================


class ManifestManager:
    """Manages manifest lifecycle for experiment runs.

    Handles creation, loading, updating, and finalization of run manifests
    with immutability enforcement after terminal status.

    Usage:
        manager = ManifestManager(run_dir)
        manifest = manager.create(input, resolved, started, run_id)
        manager.update_status(ExperimentStatus.RUNNING)
        manager.add_phase_result(phase_result)
        manager.finalize(ExperimentStatus.COMPLETED, completed_time)

    """

    def __init__(self, run_dir: Path) -> None:
        """Initialize the manager.

        Args:
            run_dir: Path to the experiment run directory.

        """
        self._run_dir = run_dir
        self._manifest_path = run_dir / "manifest.yaml"
        self._manifest: RunManifest | None = None
        self._finalized: bool = False

    @property
    def is_finalized(self) -> bool:
        """Return True if manifest has been finalized."""
        return self._finalized

    @property
    def manifest(self) -> RunManifest | None:
        """Return the current manifest, or None if not loaded."""
        return self._manifest

    def create(
        self,
        input: ManifestInput,
        resolved: ManifestResolved,
        started: datetime,
        run_id: str,
    ) -> RunManifest:
        """Create a new manifest for the run.

        Args:
            input: The input configuration requested.
            resolved: The resolved configuration values.
            started: UTC start timestamp.
            run_id: Unique run identifier.

        Returns:
            The created RunManifest.

        """
        self._manifest = RunManifest(
            run_id=run_id,
            started=started,
            completed=None,
            status=ExperimentStatus.PENDING,
            input=input,
            resolved=resolved,
            results=None,
            metrics=None,
        )
        self._save()
        logger.debug("Created manifest for run %s at %s", run_id, self._manifest_path)
        return self._manifest

    def load(self) -> RunManifest:
        """Load an existing manifest from the run directory.

        Returns:
            The loaded RunManifest.

        Raises:
            ConfigError: If manifest file not found or invalid.

        """
        if not self._manifest_path.exists():
            raise ConfigError(f"Manifest not found: {self._manifest_path}")

        try:
            with self._manifest_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(f"Invalid YAML in manifest: {e}") from e
        except OSError as e:
            raise ConfigError(f"Cannot read manifest: {e}") from e

        try:
            self._manifest = RunManifest.model_validate(data)
        except ValidationError as e:
            raise ConfigError(f"Manifest validation failed: {e}") from e

        # Check if already finalized
        if self._manifest.status in TERMINAL_STATUSES:
            self._finalized = True

        logger.debug("Loaded manifest for run %s", self._manifest.run_id)
        return self._manifest

    def update_status(
        self,
        status: ExperimentStatus,
        completed: datetime | None = None,
    ) -> None:
        """Update the manifest status.

        Args:
            status: New status to set.
            completed: Completion timestamp (required for terminal statuses).

        Raises:
            ManifestError: If manifest is finalized or status transition invalid.

        """
        self._ensure_not_finalized("update_status")
        self._ensure_loaded()

        is_terminal = status in TERMINAL_STATUSES
        if is_terminal:
            if completed is None:
                completed = datetime.now(UTC)
            self._manifest.completed = completed  # type: ignore[union-attr]

        self._manifest.status = status  # type: ignore[union-attr]
        self._save()

        # Set finalized AFTER successful save to avoid inconsistent state
        if is_terminal:
            self._finalized = True

        logger.debug(
            "Updated manifest status to %s for run %s",
            status.value,
            self._manifest.run_id,  # type: ignore[union-attr]
        )

    def add_phase_result(self, result: ManifestPhaseResult) -> None:
        """Add a phase result to the manifest.

        Args:
            result: Phase result to add.

        Raises:
            ManifestError: If manifest is finalized or status is not RUNNING.

        """
        self._ensure_not_finalized("add_phase_result")
        self._ensure_loaded()

        # AC7: Only status=running allows modifications via add_phase_result()
        if self._manifest.status != ExperimentStatus.RUNNING:  # type: ignore[union-attr]
            raise ManifestError(
                f"Cannot add phase result: status is {self._manifest.status.value}, "  # type: ignore[union-attr]
                "must be running",
                run_id=self._manifest.run_id,  # type: ignore[union-attr]
            )

        # Initialize results if needed
        if self._manifest.results is None:  # type: ignore[union-attr]
            self._manifest.results = ManifestResults()  # type: ignore[union-attr]

        self._manifest.results.phases.append(result)  # type: ignore[union-attr]

        # Skipped phases do not increment stories_attempted
        if result.status != "skipped":
            self._manifest.results.stories_attempted += 1  # type: ignore[union-attr]

        if result.status == "completed":
            self._manifest.results.stories_completed += 1  # type: ignore[union-attr]
        elif result.status == "failed":
            self._manifest.results.stories_failed += 1  # type: ignore[union-attr]

        self._save()
        logger.debug(
            "Added phase result '%s' (%s) to manifest for run %s",
            result.phase,
            result.status,
            self._manifest.run_id,  # type: ignore[union-attr]
        )

    def update_metrics(self, metrics: ManifestMetrics) -> None:
        """Update the manifest's metrics field.

        This is the only modification allowed after finalization,
        as metrics are collected by Story 18.8 after run completion.

        Args:
            metrics: The metrics to set.

        """
        self._ensure_loaded()
        self._manifest.metrics = metrics  # type: ignore[union-attr]
        self._save()
        logger.debug(
            "Updated metrics for manifest run %s",
            self._manifest.run_id,  # type: ignore[union-attr]
        )

    def finalize(
        self,
        status: ExperimentStatus,
        completed: datetime,
    ) -> RunManifest:
        """Finalize the manifest, making it immutable.

        Args:
            status: Final status (must be terminal).
            completed: Completion timestamp.

        Returns:
            The finalized manifest.

        Raises:
            ManifestError: If manifest is already finalized or status not terminal.

        """
        self._ensure_not_finalized("finalize")
        self._ensure_loaded()

        if status not in TERMINAL_STATUSES:
            raise ManifestError(
                f"Cannot finalize with non-terminal status: {status}",
                run_id=self._manifest.run_id,  # type: ignore[union-attr]
            )

        self._manifest.status = status  # type: ignore[union-attr]
        self._manifest.completed = completed  # type: ignore[union-attr]
        self._finalized = True
        self._save()

        logger.info(
            "Finalized manifest for run %s with status %s",
            self._manifest.run_id,  # type: ignore[union-attr]
            status.value,
        )
        return self._manifest  # type: ignore[return-value]

    def _ensure_not_finalized(self, operation: str) -> None:
        """Raise ManifestError if manifest is finalized."""
        if self._finalized:
            run_id = self._manifest.run_id if self._manifest else "unknown"
            raise ManifestError(
                f"Cannot {operation}: manifest is finalized",
                run_id=run_id,
            )

    def _ensure_loaded(self) -> None:
        """Ensure manifest is loaded."""
        if self._manifest is None:
            raise ManifestError(
                "Manifest not loaded. Call create() or load() first.",
                run_id="unknown",
            )

    def _save(self) -> None:
        """Save manifest to file using atomic write pattern."""
        if self._manifest is None:
            return

        temp_path = self._manifest_path.with_suffix(".yaml.tmp")

        try:
            # Ensure directory exists
            self._run_dir.mkdir(parents=True, exist_ok=True)

            # Serialize to dict
            data = self._manifest.model_dump(mode="json")

            # Write to temp file
            with temp_path.open("w", encoding="utf-8") as f:
                yaml.dump(
                    data,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                    allow_unicode=True,
                    width=120,
                )

            # Atomic rename
            os.replace(temp_path, self._manifest_path)

        except Exception as e:
            # Clean up temp file on any failure (OSError, YAMLError, etc.)
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                logger.warning("Failed to clean up temp file: %s", temp_path)
            raise ManifestError(
                f"Failed to save manifest: {e}",
                run_id=self._manifest.run_id,
            ) from e


# =============================================================================
# Helper Functions for Building Resolved Section
# =============================================================================


def build_resolved_fixture(
    entry: FixtureEntry,
    isolation_result: IsolationResult,
    run_dir: Path,
) -> ResolvedFixture:
    """Build resolved fixture from entry and isolation result.

    Args:
        entry: Fixture registry entry.
        isolation_result: Result from fixture isolation.
        run_dir: Run directory for relative path calculation.

    Returns:
        ResolvedFixture with actual values.

    """
    # Import here to avoid circular imports

    # Calculate relative snapshot path
    try:
        snapshot_rel = isolation_result.snapshot_path.relative_to(run_dir)
    except ValueError:
        snapshot_rel = isolation_result.snapshot_path

    return ResolvedFixture(
        name=entry.id,
        source=str(isolation_result.source_path),
        snapshot=f"./{snapshot_rel}",
    )


def build_resolved_config(
    template: ConfigTemplate,
    source_path: Path,
) -> ResolvedConfig:
    """Build resolved config from template.

    Args:
        template: Config template.
        source_path: Path to source YAML file.

    Returns:
        ResolvedConfig with provider details.

    """
    # Import here to avoid circular imports

    providers: dict[str, Any] = {}
    if template.providers is not None:
        providers = {
            "master": {
                "provider": template.providers.master.provider,
                "model": template.providers.master.model,
            },
            "multi": [{"provider": m.provider, "model": m.model} for m in template.providers.multi],
        }
    return ResolvedConfig(
        name=template.name,
        source=str(source_path),
        providers=providers,
    )


def build_resolved_patchset(
    manifest: PatchSetManifest,
    source_path: Path,
) -> ResolvedPatchSet:
    """Build resolved patch-set from manifest.

    Args:
        manifest: Patch-set manifest.
        source_path: Path to source YAML file.

    Returns:
        ResolvedPatchSet with resolved paths.

    """
    # Import here to avoid circular imports

    return ResolvedPatchSet(
        name=manifest.name,
        source=str(source_path),
        workflow_overrides=dict(manifest.workflow_overrides),
        patches=dict(manifest.patches),
    )


def build_resolved_loop(
    template: LoopTemplate,
    source_path: Path,
) -> ResolvedLoop:
    """Build resolved loop from template.

    Args:
        template: Loop template.
        source_path: Path to source YAML file.

    Returns:
        ResolvedLoop with workflow sequence.

    """
    # Import here to avoid circular imports

    return ResolvedLoop(
        name=template.name,
        source=str(source_path),
        sequence=[step.workflow for step in template.sequence],
    )


# Type hints for forward references (used in helper functions)
if TYPE_CHECKING:
    from bmad_assist.experiments.config import ConfigTemplate
    from bmad_assist.experiments.fixture import FixtureEntry
    from bmad_assist.experiments.isolation import IsolationResult
    from bmad_assist.experiments.loop import LoopTemplate
    from bmad_assist.experiments.patchset import PatchSetManifest
