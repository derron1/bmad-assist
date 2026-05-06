"""Experiment subcommand group for bmad-assist CLI.

Commands for experiment framework execution and management.
"""

import logging
import os
import sys
from pathlib import Path

import typer

from bmad_assist.cli_utils import (
    EXIT_CONFIG_ERROR,
    EXIT_ERROR,
    EXIT_SUCCESS,
    _error,
    _setup_logging,
    _success,
    _validate_project_path,
    _warning,
    console,
    format_duration_cli,
)
from bmad_assist.core.exceptions import ConfigError

logger = logging.getLogger(__name__)

experiment_app = typer.Typer(
    name="experiment",
    help="Experiment framework commands",
    no_args_is_help=True,
)


def _get_experiments_dir(project_path: Path) -> Path:
    """Get experiments directory path.

    Args:
        project_path: Project root path.

    Returns:
        Path to experiments directory.

    Raises:
        typer.Exit: If experiments directory doesn't exist.

    """
    experiments_dir = project_path / "experiments"
    if not experiments_dir.exists():
        _error("experiments/ directory not found")
        _error(f"  Expected at: {experiments_dir}")
        _error("  Create the directory structure manually or specify correct --project path.")
        _error("  Required structure: experiments/{configs,loops,patch-sets,fixtures,runs}/")
        raise typer.Exit(code=EXIT_ERROR)
    return experiments_dir


def _validate_run_exists(runs_dir: Path, run_id: str) -> Path:
    """Validate run directory exists.

    Args:
        runs_dir: Base runs directory.
        run_id: Run identifier.

    Returns:
        Path to run directory.

    Raises:
        typer.Exit: If run doesn't exist.

    """
    run_dir = runs_dir / run_id
    if not run_dir.exists():
        _error(f"Run not found: {run_id}")
        _error(f"  Expected at: {run_dir}")
        raise typer.Exit(code=EXIT_ERROR)
    return run_dir


@experiment_app.command("run")
def experiment_run(
    fixture: str = typer.Option(
        ...,
        "--fixture",
        "-f",
        help="Fixture ID from registry",
    ),
    config: str = typer.Option(
        ...,
        "--config",
        "-c",
        help="Config template name",
    ),
    patch_set: str = typer.Option(
        ...,
        "--patch-set",
        "-P",
        help="Patch-set manifest name",
    ),
    loop: str = typer.Option(
        ...,
        "--loop",
        "-l",
        help="Loop template name",
    ),
    output_dir: str | None = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="Custom output directory",
    ),
    project: str = typer.Option(
        ".",
        "--project",
        "-p",
        help="Path to project directory",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose output",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Validate configuration without execution",
    ),
    qa: bool = typer.Option(
        False,
        "--qa",
        help="Include Playwright tests (Category B) in QA. Without --qa, only CLI tests (A) run.",
    ),
    fail_fast: bool = typer.Option(
        False,
        "--fail-fast",
        help="Stop immediately on first story failure instead of continuing",
    ),
) -> None:
    """Execute a single experiment with specified configuration.

    Iterates through all stories in the fixture's current epic, executing the
    full loop sequence for each story. After the last story completes, runs
    retrospective automatically.

    Examples:
        bmad-assist experiment run -f minimal -c opus-solo -P baseline -l standard
        bmad-assist experiment run --fixture complex --config haiku --patch-set experimental -l atdd
        bmad-assist experiment run -f simple-portfolio -c opus-solo -P baseline -l standard --qa
        bmad-assist experiment run -f simple-portfolio -c opus-solo -P baseline --fail-fast

    """
    from bmad_assist.experiments import (
        ConfigRegistry,
        ExperimentInput,
        ExperimentRunner,
        FixtureManager,
        LoopRegistry,
        PatchSetRegistry,
    )

    # Setup logging (always enabled, verbose controls DEBUG vs INFO level)
    _setup_logging(verbose=verbose, quiet=False)

    # Validate project path
    project_path = _validate_project_path(project)
    experiments_dir = _get_experiments_dir(project_path)

    # Initialize registries for validation
    config_registry = ConfigRegistry(experiments_dir / "configs", project_path)
    loop_registry = LoopRegistry(experiments_dir / "loops")
    patchset_registry = PatchSetRegistry(experiments_dir / "patch-sets", project_path)
    fixture_manager = FixtureManager(experiments_dir / "fixtures")

    # Validate all inputs exist
    validation_errors = []

    try:
        config_registry.get(config)
    except ConfigError as e:
        validation_errors.append(str(e))
    try:
        loop_registry.get(loop)
    except ConfigError as e:
        validation_errors.append(str(e))
    try:
        patchset_registry.get(patch_set)
    except ConfigError as e:
        validation_errors.append(str(e))
    try:
        fixture_manager.get(fixture)
    except ConfigError as e:
        validation_errors.append(str(e))

    if validation_errors:
        for err in validation_errors:
            _error(err)
        raise typer.Exit(code=EXIT_CONFIG_ERROR)

    # Dry run mode
    if dry_run:
        console.print("[yellow][Dry run][/yellow] Would execute experiment:")
        console.print(f"  Fixture: {fixture} [green]✓[/green]")
        console.print(f"  Config: {config} [green]✓[/green]")
        console.print(f"  Patch-Set: {patch_set} [green]✓[/green]")
        console.print(f"  Loop: {loop} [green]✓[/green]")
        console.print(f"  QA category: [green]{'A + B (all)' if qa else 'A (CLI only)'}[/green]")
        if fail_fast:
            console.print("  Fail-fast: [green]enabled[/green]")
        console.print()
        console.print("Configuration valid. Run without --dry-run to execute.")
        raise typer.Exit(code=EXIT_SUCCESS)

    # Create input
    # QA always runs: "A" = safe CLI tests, "all" = CLI + Playwright (--qa flag)
    exp_input = ExperimentInput(
        fixture=fixture,
        config=config,
        patch_set=patch_set,
        loop=loop,
        qa_category="all" if qa else "A",
        fail_fast=fail_fast,
    )

    # Run experiment
    runner = ExperimentRunner(experiments_dir, project_path)

    _success("Starting experiment...")
    console.print(f"  Fixture: {fixture}")
    console.print(f"  Config: {config}")
    console.print(f"  Patch-Set: {patch_set}")
    console.print(f"  Loop: {loop}")
    console.print(f"  QA category: {'A + B (all)' if qa else 'A (CLI only)'}")
    if fail_fast:
        console.print("  Fail-fast: enabled")
    console.print()

    try:
        result = runner.run(exp_input)
    except ConfigError as e:
        _error(str(e))
        raise typer.Exit(code=EXIT_CONFIG_ERROR) from None
    except Exception as e:
        _error(f"Experiment failed: {e}")
        if verbose:
            console.print_exception()
        raise typer.Exit(code=EXIT_ERROR) from None

    # Display result
    console.print()
    if result.status.value == "completed":
        _success("Experiment completed")
    else:
        _error(f"Experiment {result.status.value}")
        if result.error:
            console.print(f"  Error: {result.error}")

    console.print(f"  Run ID: {result.run_id}")
    console.print(f"  Duration: {format_duration_cli(result.duration_seconds)}")
    completed = result.stories_completed
    attempted = result.stories_attempted
    failed = result.stories_failed
    console.print(f"  Stories: {completed}/{attempted} completed, {failed} failed")
    if result.retrospective_completed:
        console.print("  Retrospective: [green]completed[/green]")
    if result.qa_completed:
        qa_cat = "A + B" if qa else "A"
        console.print(f"  QA phases ({qa_cat}): [green]completed[/green]")
    else:
        qa_cat = "A + B" if qa else "A"
        console.print(f"  QA phases ({qa_cat}): [yellow]not completed[/yellow]")
    console.print(f"  Output: {experiments_dir / 'runs' / result.run_id}/")

    if result.status.value != "completed":
        raise typer.Exit(code=EXIT_ERROR)


@experiment_app.command("batch")
def experiment_batch(
    fixtures: str = typer.Option(
        ...,
        "--fixtures",
        help="Comma-separated fixture IDs",
    ),
    configs: str = typer.Option(
        ...,
        "--configs",
        help="Comma-separated config template names",
    ),
    patch_set: str = typer.Option(
        ...,
        "--patch-set",
        help="Patch-set manifest name (applies to all)",
    ),
    loop: str = typer.Option(
        ...,
        "--loop",
        help="Loop template name (applies to all)",
    ),
    parallel: int = typer.Option(
        1,
        "--parallel",
        "-j",
        help="Number of concurrent runs (default: 1, max: 4) - MVP: sequential only",
        min=1,
        max=4,
    ),
    project: str = typer.Option(
        ".",
        "--project",
        "-p",
        help="Path to project directory",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose output",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Show combinations without execution",
    ),
) -> None:
    """Run multiple experiment combinations.

    Generates cartesian product of fixtures × configs, all using the same
    patch-set and loop template.

    Examples:
        bmad-assist experiment batch -F minimal,complex -C opus-solo,haiku -P baseline -l standard

    """
    from bmad_assist.experiments import (
        ConfigRegistry,
        ExperimentInput,
        ExperimentRunner,
        ExperimentStatus,
        FixtureManager,
        LoopRegistry,
        PatchSetRegistry,
    )

    # Setup logging (always enabled, verbose controls DEBUG vs INFO level)
    _setup_logging(verbose=verbose, quiet=False)

    # Validate project path
    project_path = _validate_project_path(project)
    experiments_dir = _get_experiments_dir(project_path)

    # Parse comma-separated lists
    fixture_list = [f.strip() for f in fixtures.split(",") if f.strip()]
    config_list = [c.strip() for c in configs.split(",") if c.strip()]

    if not fixture_list:
        _error("No fixtures specified")
        raise typer.Exit(code=EXIT_CONFIG_ERROR)
    if not config_list:
        _error("No configs specified")
        raise typer.Exit(code=EXIT_CONFIG_ERROR)

    # Initialize registries for validation
    config_registry = ConfigRegistry(experiments_dir / "configs", project_path)
    loop_registry = LoopRegistry(experiments_dir / "loops")
    patchset_registry = PatchSetRegistry(experiments_dir / "patch-sets", project_path)
    fixture_manager = FixtureManager(experiments_dir / "fixtures")

    # Validate all inputs exist
    validation_errors = []

    for f in fixture_list:
        try:
            fixture_manager.get(f)
        except ConfigError as e:
            validation_errors.append(str(e))
    for c in config_list:
        try:
            config_registry.get(c)
        except ConfigError as e:
            validation_errors.append(str(e))
    try:
        loop_registry.get(loop)
    except ConfigError as e:
        validation_errors.append(str(e))
    try:
        patchset_registry.get(patch_set)
    except ConfigError as e:
        validation_errors.append(str(e))

    if validation_errors:
        for err in validation_errors:
            _error(err)
        raise typer.Exit(code=EXIT_CONFIG_ERROR)

    # Generate cartesian product combinations
    combinations = [(f, c) for f in fixture_list for c in config_list]
    total = len(combinations)

    nf, nc = len(fixture_list), len(config_list)
    console.print(f"[bold]Batch: {total} experiments ({nf} fixtures × {nc} configs)[/bold]")
    console.print()

    # Dry run mode
    if dry_run:
        console.print("[yellow][Dry run][/yellow] Would execute experiments:")
        for i, (f, c) in enumerate(combinations, 1):
            console.print(f"  {i}. {f} + {c}")
        console.print()
        console.print("Run without --dry-run to execute.")
        raise typer.Exit(code=EXIT_SUCCESS)

    # MVP: Sequential execution only, --parallel flag accepted but ignored
    if parallel > 1:
        console.print(
            "[dim]Note: --parallel > 1 not yet implemented (MVP). Running sequentially.[/dim]"
        )
        console.print()

    # Track results
    succeeded = 0
    failed_runs: list[str] = []

    runner = ExperimentRunner(experiments_dir, project_path)

    for i, (f, c) in enumerate(combinations, 1):
        console.print(f"[{i}/{total}] {f} + {c}: ", end="")

        exp_input = ExperimentInput(
            fixture=f,
            config=c,
            patch_set=patch_set,
            loop=loop,
        )

        try:
            result = runner.run(exp_input)
            if result.status == ExperimentStatus.COMPLETED:
                succeeded += 1
                console.print(
                    f"[green]completed[/green] ({format_duration_cli(result.duration_seconds)})"
                )
            else:
                failed_runs.append(result.run_id)
                console.print(f"[red]{result.status.value}[/red]")
                if result.error and verbose:
                    console.print(f"    Error: {result.error}")
        except Exception as e:
            failed_runs.append(f"{f}+{c}")
            console.print(f"[red]error[/red]: {e}")
            if verbose:
                console.print_exception()

    # Summary
    console.print()
    if failed_runs:
        _warning(f"Batch completed: {succeeded}/{total} succeeded, {len(failed_runs)} failed")
        console.print("  Failed runs:")
        for run_id in failed_runs:
            console.print(f"    - {run_id}")
        raise typer.Exit(code=EXIT_ERROR)
    else:
        _success(f"Batch completed: {total}/{total} succeeded")


@experiment_app.command("list")
def experiment_list(
    status: str | None = typer.Option(
        None,
        "--status",
        "-s",
        help="Filter by status (completed, failed, cancelled, running, pending)",
    ),
    fixture: str | None = typer.Option(
        None,
        "--fixture",
        "-f",
        help="Filter by fixture name",
    ),
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Filter by config name",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        "-n",
        help="Maximum number of runs to display",
    ),
    project: str = typer.Option(
        ".",
        "--project",
        "-p",
        help="Path to project directory",
    ),
) -> None:
    """List completed experiment runs with optional filters.

    Examples:
        bmad-assist experiment list
        bmad-assist experiment list --status completed --fixture minimal

    """
    from rich.table import Table

    from bmad_assist.experiments import ManifestManager

    # Validate project path
    project_path = _validate_project_path(project)
    experiments_dir = _get_experiments_dir(project_path)
    runs_dir = experiments_dir / "runs"

    if not runs_dir.exists():
        console.print("[dim]No runs found (runs directory doesn't exist)[/dim]")
        raise typer.Exit(code=EXIT_SUCCESS)

    # Collect run data
    runs_data = []

    for run_dir in sorted(runs_dir.iterdir(), reverse=True):
        if not run_dir.is_dir():
            continue

        manifest_path = run_dir / "manifest.yaml"
        if not manifest_path.exists():
            continue

        try:
            manager = ManifestManager(run_dir)
            manifest = manager.load()

            # Apply filters
            if status and manifest.status.value != status:
                continue
            if fixture and manifest.input.fixture != fixture:
                continue
            if config and manifest.input.config != config:
                continue

            # Calculate duration
            if manifest.completed and manifest.started:
                duration = (manifest.completed - manifest.started).total_seconds()
                duration_str = format_duration_cli(duration)
            else:
                duration_str = "-"

            # Format date
            date_str = manifest.started.strftime("%Y-%m-%d") if manifest.started else "-"

            runs_data.append(
                {
                    "run_id": manifest.run_id,
                    "status": manifest.status.value,
                    "fixture": manifest.input.fixture,
                    "config": manifest.input.config,
                    "duration": duration_str,
                    "date": date_str,
                }
            )

            if len(runs_data) >= limit:
                break

        except Exception as e:
            logger.debug("Failed to load manifest for %s: %s", run_dir.name, e)
            continue

    if not runs_data:
        console.print("[dim]No runs found matching filters[/dim]")
        raise typer.Exit(code=EXIT_SUCCESS)

    # Create table
    table = Table(title="Experiment Runs")
    table.add_column("Run ID", style="cyan")
    table.add_column("Status")
    table.add_column("Fixture", style="dim")
    table.add_column("Config", style="dim")
    table.add_column("Duration", style="dim")
    table.add_column("Date", style="dim")

    for run in runs_data:
        status_style = {
            "completed": "[green]completed[/green]",
            "failed": "[red]failed[/red]",
            "cancelled": "[yellow]cancelled[/yellow]",
            "running": "[blue]running[/blue]",
            "pending": "[dim]pending[/dim]",
        }.get(run["status"], run["status"])

        table.add_row(
            run["run_id"],
            status_style,
            run["fixture"],
            run["config"],
            run["duration"],
            run["date"],
        )

    console.print(table)


@experiment_app.command("show")
def experiment_show(
    run_id: str = typer.Argument(
        ...,
        help="Run identifier (e.g., run-2026-01-09-001)",
    ),
    project: str = typer.Option(
        ".",
        "--project",
        "-p",
        help="Path to project directory",
    ),
) -> None:
    """Display detailed information for a specific run.

    Examples:
        bmad-assist experiment show run-2026-01-09-001

    """
    from bmad_assist.experiments import ManifestManager, MetricsCollector

    # Validate project path
    project_path = _validate_project_path(project)
    experiments_dir = _get_experiments_dir(project_path)
    runs_dir = experiments_dir / "runs"

    run_dir = _validate_run_exists(runs_dir, run_id)

    # Load manifest
    try:
        manager = ManifestManager(run_dir)
        manifest = manager.load()
    except ConfigError as e:
        _error(str(e))
        raise typer.Exit(code=EXIT_ERROR) from None

    # Load metrics (optional)
    metrics = None
    try:
        collector = MetricsCollector(run_dir)
        metrics_file = collector.load()
        metrics = metrics_file.summary
    except Exception as e:
        logger.debug("Optional metrics loading failed for %s: %s", run_id, e)

    # Display header
    console.print(f"[bold]# Run: {manifest.run_id}[/bold]")
    console.print()

    # Status section
    console.print("[bold]## Status[/bold]")
    status_style = {
        "completed": "[green]completed[/green]",
        "failed": "[red]failed[/red]",
        "cancelled": "[yellow]cancelled[/yellow]",
        "running": "[blue]running[/blue]",
        "pending": "[dim]pending[/dim]",
    }.get(manifest.status.value, manifest.status.value)
    console.print(f"Status: {status_style}")
    console.print(f"Started: {manifest.started.isoformat() if manifest.started else 'N/A'}")
    console.print(f"Completed: {manifest.completed.isoformat() if manifest.completed else 'N/A'}")
    if manifest.completed and manifest.started:
        duration = (manifest.completed - manifest.started).total_seconds()
        console.print(f"Duration: {format_duration_cli(duration)}")
    console.print()

    # Configuration section
    console.print("[bold]## Configuration[/bold]")
    console.print(f"Fixture: {manifest.input.fixture} ({manifest.resolved.fixture.source})")
    console.print(f"Config: {manifest.input.config} ({manifest.resolved.config.source})")
    console.print(f"Patch-Set: {manifest.input.patch_set} ({manifest.resolved.patch_set.source})")
    console.print(f"Loop: {manifest.input.loop} ({manifest.resolved.loop.source})")
    console.print()

    # Results section
    console.print("[bold]## Results[/bold]")
    if manifest.results:
        console.print(f"Stories Attempted: {manifest.results.stories_attempted}")
        console.print(f"Stories Completed: {manifest.results.stories_completed}")
        console.print(f"Stories Failed: {manifest.results.stories_failed}")
    else:
        console.print("[dim]No results recorded[/dim]")
    console.print()

    # Metrics section
    console.print("[bold]## Metrics[/bold]")
    if metrics:
        if metrics.total_cost is not None:
            console.print(f"Total Cost: ${metrics.total_cost:.2f}")
        if metrics.total_tokens is not None:
            console.print(f"Total Tokens: {metrics.total_tokens:,}")
        if metrics.avg_tokens_per_phase is not None:
            console.print(f"Avg Tokens/Phase: {int(metrics.avg_tokens_per_phase):,}")
    else:
        console.print("[dim]No metrics available[/dim]")
    console.print()

    # Phases section
    if manifest.results and manifest.results.phases:
        from rich.table import Table

        console.print("[bold]## Phases[/bold]")
        table = Table()
        table.add_column("Phase")
        table.add_column("Story")
        table.add_column("Status")
        table.add_column("Duration")

        for phase in manifest.results.phases:
            status_style = {
                "completed": "[green]completed[/green]",
                "failed": "[red]failed[/red]",
                "skipped": "[dim]skipped[/dim]",
            }.get(phase.status, phase.status)

            table.add_row(
                phase.phase,
                phase.story or "-",
                status_style,
                format_duration_cli(phase.duration_seconds),
            )

        console.print(table)


@experiment_app.command("compare")
def experiment_compare(
    run_ids: list[str] = typer.Argument(  # noqa: B008
        ...,
        help="Run IDs to compare (2-10 required)",
    ),
    output: str | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Output file path (default: stdout)",
    ),
    format_: str = typer.Option(
        "markdown",
        "--format",
        "-f",
        help="Output format: markdown or json",
    ),
    project: str = typer.Option(
        ".",
        "--project",
        "-p",
        help="Path to project directory",
    ),
) -> None:
    """Generate comparison report for multiple runs.

    Examples:
        bmad-assist experiment compare run-001 run-002
        bmad-assist experiment compare run-001 run-002 run-003 --output comparison.md

    """
    import json

    from bmad_assist.experiments.comparison import MAX_COMPARISON_RUNS, ComparisonGenerator

    # Validate run count
    if len(run_ids) < 2:
        _error("At least 2 runs required for comparison")
        raise typer.Exit(code=EXIT_CONFIG_ERROR)
    if len(run_ids) > MAX_COMPARISON_RUNS:
        _error(f"Maximum {MAX_COMPARISON_RUNS} runs allowed for comparison")
        raise typer.Exit(code=EXIT_CONFIG_ERROR)

    # Validate format
    format_lower = format_.lower()
    if format_lower not in ("markdown", "json"):
        _error(f"Invalid --format: {format_}. Use 'markdown' or 'json'.")
        raise typer.Exit(code=EXIT_CONFIG_ERROR)

    project_path = _validate_project_path(project)
    experiments_dir = _get_experiments_dir(project_path)
    runs_dir = experiments_dir / "runs"

    # Validate all runs exist
    for rid in run_ids:
        _validate_run_exists(runs_dir, rid)

    # Generate comparison
    generator = ComparisonGenerator(runs_dir)

    try:
        report = generator.compare(run_ids)
    except ConfigError as e:
        _error(str(e))
        raise typer.Exit(code=EXIT_ERROR) from None
    except ValueError as e:
        _error(str(e))
        raise typer.Exit(code=EXIT_CONFIG_ERROR) from None

    # Format output based on --format option
    if format_lower == "json":
        content = json.dumps(report.model_dump(mode="json"), indent=2)
    else:
        content = generator.generate_markdown(report)

    # Output to file or stdout
    if output:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write
        temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        try:
            temp_path.write_text(content, encoding="utf-8")
            os.replace(temp_path, output_path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise
        _success(f"Comparison report written to: {output_path}")
    else:
        sys.stdout.write(content)


@experiment_app.command("templates")
def experiment_templates(
    type_: str | None = typer.Option(
        None,
        "--type",
        "-t",
        help="Filter by type: config, loop, patch-set, fixture",
    ),
    project: str = typer.Option(
        ".",
        "--project",
        "-p",
        help="Path to project directory",
    ),
) -> None:
    """List available templates across all four axes.

    Examples:
        bmad-assist experiment templates
        bmad-assist experiment templates --type config

    """
    from rich.table import Table

    from bmad_assist.experiments import (
        ConfigRegistry,
        FixtureManager,
        LoopRegistry,
        PatchSetRegistry,
    )

    # Validate project path
    project_path = _validate_project_path(project)
    experiments_dir = _get_experiments_dir(project_path)

    # Validate type filter
    valid_types = {"config", "loop", "patch-set", "fixture"}
    if type_ and type_.lower() not in valid_types:
        _error(f"Invalid --type: {type_}. Valid types: {', '.join(valid_types)}")
        raise typer.Exit(code=EXIT_CONFIG_ERROR)

    show_all = type_ is None
    type_lower = type_.lower() if type_ else None

    # Config templates
    if show_all or type_lower == "config":
        console.print("[bold]Config Templates:[/bold]")
        try:
            config_registry = ConfigRegistry(experiments_dir / "configs", project_path)
            config_names = config_registry.list()

            if config_names:
                table = Table()
                table.add_column("Name", style="cyan")
                table.add_column("Description")
                table.add_column("Master Model", style="dim")

                for name in config_names:
                    try:
                        cfg = config_registry.get(name)
                        if cfg.providers is not None:
                            master_model = (
                                f"{cfg.providers.master.provider}/{cfg.providers.master.model}"
                            )
                        else:
                            master_model = "(full config)"
                        table.add_row(cfg.name, cfg.description or "-", master_model)
                    except Exception as e:
                        table.add_row(name, f"[red]Error: {e}[/red]", "-")
                console.print(table)
            else:
                console.print("[dim]  No config templates found[/dim]")
        except Exception as e:
            console.print(f"[dim]  Error loading configs: {e}[/dim]")
        console.print()

    # Loop templates
    if show_all or type_lower == "loop":
        console.print("[bold]Loop Templates:[/bold]")
        try:
            loop_registry = LoopRegistry(experiments_dir / "loops")
            loop_names = loop_registry.list()

            if loop_names:
                table = Table()
                table.add_column("Name", style="cyan")
                table.add_column("Description")
                table.add_column("Steps", style="dim")

                for name in loop_names:
                    try:
                        lp = loop_registry.get(name)
                        table.add_row(lp.name, lp.description or "-", str(len(lp.sequence)))
                    except Exception as e:
                        table.add_row(name, f"[red]Error: {e}[/red]", "-")
                console.print(table)
            else:
                console.print("[dim]  No loop templates found[/dim]")
        except Exception as e:
            console.print(f"[dim]  Error loading loops: {e}[/dim]")
        console.print()

    # Patch-set manifests
    if show_all or type_lower == "patch-set":
        console.print("[bold]Patch-Set Manifests:[/bold]")
        try:
            patchset_registry = PatchSetRegistry(experiments_dir / "patch-sets", project_path)
            patchset_names = patchset_registry.list()

            if patchset_names:
                table = Table()
                table.add_column("Name", style="cyan")
                table.add_column("Description")
                table.add_column("Workflows", style="dim")

                for name in patchset_names:
                    try:
                        ps = patchset_registry.get(name)
                        workflow_count = len(ps.patches) + len(ps.workflow_overrides)
                        table.add_row(ps.name, ps.description or "-", str(workflow_count))
                    except Exception as e:
                        table.add_row(name, f"[red]Error: {e}[/red]", "-")
                console.print(table)
            else:
                console.print("[dim]  No patch-set manifests found[/dim]")
        except Exception as e:
            console.print(f"[dim]  Error loading patch-sets: {e}[/dim]")
        console.print()

    # Fixtures
    if show_all or type_lower == "fixture":
        console.print("[bold]Fixtures:[/bold]")
        try:
            fixture_manager = FixtureManager(experiments_dir / "fixtures")
            fixture_ids = fixture_manager.list()

            if fixture_ids:
                table = Table()
                table.add_column("ID", style="cyan")
                table.add_column("Name")
                table.add_column("Difficulty", style="dim")
                table.add_column("Est. Cost", style="dim")

                for fx_id in fixture_ids:
                    try:
                        fx = fixture_manager.get(fx_id)
                        table.add_row(
                            fx.id,
                            fx.name,
                            fx.difficulty or "-",
                            fx.estimated_cost or "-",
                        )
                    except Exception as e:
                        table.add_row(fx_id, f"[red]Error: {e}[/red]", "-", "-")
                console.print(table)
            else:
                console.print("[dim]  No fixtures found[/dim]")
        except Exception as e:
            console.print(f"[dim]  Error loading fixtures: {e}[/dim]")
        console.print()


@experiment_app.command("ab")
def experiment_ab(
    test_file: str = typer.Argument(
        ...,
        help="Path to A/B test definition YAML file",
    ),
    project: str = typer.Option(
        ".",
        "--project",
        "-p",
        help="Path to project directory",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose output",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Validate configuration without execution",
    ),
) -> None:
    """Run an A/B workflow test from a YAML definition file.

    Creates git worktrees for each variant, runs the specified phases
    through both, and generates a comparison report.

    Examples:
        bmad-assist experiment ab experiments/ab-tests/prompt-v2-test.yaml
        bmad-assist experiment ab my-test.yaml --dry-run

    """
    from bmad_assist.experiments.ab import ABTestRunner, load_ab_test_config

    _setup_logging(verbose=verbose, quiet=False)
    project_path = _validate_project_path(project)
    experiments_dir = _get_experiments_dir(project_path)

    test_path = Path(test_file)
    if not test_path.is_absolute():
        test_path = project_path / test_path

    try:
        config = load_ab_test_config(test_path)
    except ConfigError as e:
        _error(str(e))
        raise typer.Exit(code=EXIT_CONFIG_ERROR) from None

    console.print(f"[bold]A/B Test: {config.name}[/bold]")
    console.print(f"  Fixture: {config.fixture}")
    console.print(f"  Stories: {', '.join(s.id for s in config.stories)}")
    for s in config.stories:
        console.print(f"    {s.id} @ {s.ref}")
    console.print(f"  Phases: {', '.join(config.phases)}")
    console.print(
        f"  Variant A: {config.variant_a.label} "
        f"(config={config.variant_a.config}, patch={config.variant_a.patch_set})"
    )
    if config.variant_a.customize_set:
        console.print(f"    customize_set={config.variant_a.customize_set}")
    if config.variant_a.template_set:
        console.print(f"    template_set={config.variant_a.template_set}")
    console.print(
        f"  Variant B: {config.variant_b.label} "
        f"(config={config.variant_b.config}, patch={config.variant_b.patch_set})"
    )
    if config.variant_b.customize_set:
        console.print(f"    customize_set={config.variant_b.customize_set}")
    if config.variant_b.template_set:
        console.print(f"    template_set={config.variant_b.template_set}")
    console.print(f"  Scorecard: {'yes' if config.scorecard else 'no'}")
    console.print()

    if dry_run:
        runner = ABTestRunner(experiments_dir, project_path)
        try:
            runner._ensure_registries()
            runner._validate_inputs(config)
        except ConfigError as e:
            _error(str(e))
            raise typer.Exit(code=EXIT_CONFIG_ERROR) from None

        console.print(
            "[yellow][Dry run][/yellow] Configuration valid. Run without --dry-run to execute."
        )
        raise typer.Exit(code=EXIT_SUCCESS)

    runner = ABTestRunner(experiments_dir, project_path)
    try:
        result = runner.run(config)
    except ConfigError as e:
        _error(str(e))
        raise typer.Exit(code=EXIT_CONFIG_ERROR) from None
    except Exception as e:
        _error(f"A/B test failed: {e}")
        if verbose:
            console.print_exception()
        raise typer.Exit(code=EXIT_ERROR) from None

    console.print()
    console.print("[bold]Results:[/bold]")
    for label, vr in [("A", result.variant_a), ("B", result.variant_b)]:
        status_color = "green" if vr.status.value == "completed" else "red"
        console.print(
            f"  Variant {label} ({vr.label}): "
            f"[{status_color}]{vr.status.value}[/{status_color}] "
            f"({vr.stories_completed}/{vr.stories_attempted} stories, "
            f"{format_duration_cli(vr.duration_seconds)})"
        )
        if vr.error:
            console.print(f"    Error: {vr.error}")

    if result.comparison_path:
        console.print(f"  Comparison: {result.comparison_path}")
    if result.scorecard_a_path:
        console.print(f"  Scorecard A: {result.scorecard_a_path}")
    if result.scorecard_b_path:
        console.print(f"  Scorecard B: {result.scorecard_b_path}")

    console.print(f"  Output: {result.result_dir}/")

    both_ok = (
        result.variant_a.status.value == "completed"
        and result.variant_b.status.value == "completed"
    )
    if both_ok:
        _success("A/B test completed successfully")
    else:
        raise typer.Exit(code=EXIT_ERROR)


@experiment_app.command("ab-analysis")
def experiment_ab_analysis(
    result_dir: str = typer.Argument(
        ...,
        help="Path to A/B test result directory (contains test-definition.yaml)",
    ),
    project: str = typer.Option(
        ".",
        "--project",
        "-p",
        help="Path to project directory",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose output",
    ),
) -> None:
    """Re-run LLM analysis on existing A/B test results.

    Loads the test definition from the result directory and generates
    a new analysis.md using the master LLM from the variant A config.

    Examples:
        bmad-assist experiment ab-analysis experiments/ab-results/my-test-20260208-082603
        bmad-assist experiment ab-analysis ./experiments/ab-results/my-test -p /path/to/project

    """
    from bmad_assist.experiments.ab import load_ab_test_config
    from bmad_assist.experiments.ab.analysis import generate_ab_analysis

    _setup_logging(verbose=verbose, quiet=False)
    project_path = _validate_project_path(project)
    experiments_dir = _get_experiments_dir(project_path)

    result_path = Path(result_dir)
    if not result_path.is_absolute():
        result_path = project_path / result_path

    # Validate result directory
    if not result_path.is_dir():
        _error(f"Result directory not found: {result_path}")
        raise typer.Exit(code=EXIT_ERROR)

    test_def_path = result_path / "test-definition.yaml"
    if not test_def_path.exists():
        _error(f"test-definition.yaml not found in {result_path}")
        raise typer.Exit(code=EXIT_ERROR)

    # Load A/B test config from saved definition
    try:
        config = load_ab_test_config(test_def_path)
    except ConfigError as e:
        _error(str(e))
        raise typer.Exit(code=EXIT_CONFIG_ERROR) from None

    # Check comparison exists (analysis needs it)
    comparison_path = result_path / "comparison.md"
    if not comparison_path.exists():
        _warning("comparison.md not found — analysis may be incomplete")

    console.print(f"[bold]A/B Analysis: {config.name}[/bold]")
    console.print(f"  Result dir: {result_path}")
    console.print(f"  Config (variant A): {config.variant_a.config}")
    console.print()

    try:
        analysis_path = generate_ab_analysis(
            config=config,
            result_dir=result_path,
            experiments_dir=experiments_dir,
        )
    except ConfigError as e:
        _error(str(e))
        raise typer.Exit(code=EXIT_CONFIG_ERROR) from None
    except Exception as e:
        _error(f"Analysis generation failed: {e}")
        if verbose:
            console.print_exception()
        raise typer.Exit(code=EXIT_ERROR) from None

    if analysis_path is not None:
        _success(f"Analysis written: {analysis_path}")
    else:
        _error("Analysis returned no output (empty artifacts or LLM failure)")
        raise typer.Exit(code=EXIT_ERROR)
