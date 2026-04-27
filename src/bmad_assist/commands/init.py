"""Init command for bmad-assist CLI.

Initializes a project for bmad-assist usage.
"""

from pathlib import Path
from typing import TYPE_CHECKING

import typer

from bmad_assist.cli_utils import (
    EXIT_ERROR,
    EXIT_WARNING,
    _error,
    _success,
    console,
)

if TYPE_CHECKING:
    from rich.console import Console


def _validate_bundled_workflows(console_obj: "Console") -> None:
    """Validate bundled workflows are properly installed."""
    from bmad_assist.workflows import list_bundled_workflows

    expected_custom = {
        "validate-story",
        "validate-story-synthesis",
        "code-review",
        "code-review-synthesis",
        "qa-plan-generate",
        "qa-plan-execute",
    }
    expected_standard = {
        "create-story",
        "dev-story",
        "retrospective",
        "testarch-atdd",
        "testarch-trace",
        "testarch-test-review",
    }
    expected = expected_custom | expected_standard

    bundled = set(list_bundled_workflows())
    missing = expected - bundled

    if missing:
        console_obj.print(f"  [red]Missing bundled workflows:[/red] {', '.join(sorted(missing))}")
        console_obj.print("  [yellow]Fix: reinstall bmad-assist with `pip install -e .`[/yellow]")
    else:
        console_obj.print(f"  [green]All {len(bundled)} bundled workflows available[/green]")


def init_command(
    project: str = typer.Option(
        ".",
        "--project",
        "-p",
        help="Path to project directory to initialize",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Show what would be done without making changes",
    ),
    reset_workflows: bool = typer.Option(
        False,
        "--reset-workflows",
        help="Overwrite all workflows with bundled versions (destructive!)",
    ),
    wizard: bool = typer.Option(
        False,
        "--wizard",
        "-w",
        help="Run interactive configuration wizard after initialization",
    ),
    skill_layout: str = typer.Option(
        "auto",
        "--skill-layout",
        help=(
            "Skill layout mode for setup: 'auto' (detect), 'new' (force "
            "bootstrap of .claude/skills + .agents/skills from bundled "
            "skills), 'old' (force legacy _bmad/bmm/workflows copy). "
            "Default: auto."
        ),
    ),
) -> None:
    """Initialize a project for bmad-assist.

    Sets up the project with required configuration:
    - Creates .bmad-assist/ directory for state and cache
    - Copies bundled BMAD workflows to _bmad/bmm/workflows/
    - Adds required patterns to .gitignore to prevent committing artifacts
    - Creates minimal BMAD config if missing

    This command is idempotent - safe to run multiple times.

    Examples:
        bmad-assist init                    # Initialize current directory
        bmad-assist init -p ./my-project    # Initialize specific project
        bmad-assist init --wizard           # Initialize and configure interactively
        bmad-assist init --dry-run          # Preview changes without applying
        bmad-assist init --reset-workflows  # Restore bundled workflow versions

    """
    from rich.prompt import Confirm

    from bmad_assist.core.project_setup import ensure_project_setup

    project_path = Path(project).resolve()

    if not project_path.exists():
        _error(f"Project directory does not exist: {project_path}")
        raise typer.Exit(code=EXIT_ERROR)

    if not project_path.is_dir():
        _error(f"Path is not a directory: {project_path}")
        raise typer.Exit(code=EXIT_ERROR)

    # Validate --skill-layout value early.
    if skill_layout not in ("auto", "new", "old"):
        _error(
            f"Invalid --skill-layout value '{skill_layout}'. "
            "Allowed: 'auto', 'new', 'old'."
        )
        raise typer.Exit(code=EXIT_ERROR)

    # Run interactive config wizard FIRST if requested (before any other setup)
    if wizard and not dry_run:
        from bmad_assist.core.config_generator import run_config_wizard

        try:
            run_config_wizard(project_path, console)
        except KeyboardInterrupt:
            console.print("\n[yellow]Wizard cancelled[/yellow]")
            raise typer.Exit(code=130) from None

        console.print()

    # Confirm destructive reset operation
    if reset_workflows:
        console.print(
            "[yellow]⚠️  WARNING: --reset-workflows will overwrite ALL local workflow customizations![/yellow]"
        )  # noqa: E501
        if not Confirm.ask("Continue?", default=False):
            console.print("[dim]Cancelled.[/dim]")
            raise typer.Exit(code=0)

    console.print(f"[bold]Initializing bmad-assist in:[/bold] {project_path}")
    console.print()

    if dry_run:
        console.print("[yellow]Dry run mode - no changes will be made[/yellow]")
        console.print()
        # For dry run, just show what would happen
        from bmad_assist.git import check_gitignore
        from bmad_assist.skill_layout import detect_layout
        from bmad_assist.skills import list_bundled_skills
        from bmad_assist.workflows import list_bundled_workflows

        if wizard:
            console.print("  [dim]Would run configuration wizard[/dim]")

        bmad_dir = project_path / ".bmad-assist"
        if not bmad_dir.exists():
            console.print(f"  [dim]Would create:[/dim] {bmad_dir}/")

        # Determine effective layout for the dry-run preview.
        if skill_layout == "auto":
            detected = detect_layout(project_path)
            legacy_present = (
                project_path / "_bmad" / "bmm" / "workflows"
            ).is_dir()
            if detected == "new":
                effective = "existing-new"
            elif legacy_present:
                effective = "existing-old"
            else:
                effective = "fresh"
        elif skill_layout == "new":
            effective = "fresh"
        else:
            effective = "existing-old"

        if effective == "existing-new":
            console.print(
                "  [dim]Detected v6.4+ skill layout — would leave installed skills alone[/dim]"
            )
        elif effective == "fresh":
            skills = list_bundled_skills()
            console.print(
                f"  [dim]Would bootstrap {len(skills)} bundled skills into "
                f".claude/skills/ and .agents/skills/[/dim]"
            )
        else:
            config_file = project_path / "_bmad" / "bmm" / "config.yaml"
            if not config_file.exists():
                console.print(
                    f"  [dim]Would create:[/dim] {config_file.relative_to(project_path)}"
                )
            workflows = list_bundled_workflows()
            console.print(f"  [dim]Would copy {len(workflows)} bundled workflows[/dim]")

        all_present, missing = check_gitignore(project_path)
        if not all_present:
            console.print(f"  [dim]Would update .gitignore:[/dim] {', '.join(missing)}")

        console.print()
        console.print("[yellow]Dry run - no changes made. Run without --dry-run to apply.[/yellow]")
        return

    # Run the actual setup
    result = ensure_project_setup(
        project_path,
        include_gitignore=True,
        force=reset_workflows,
        console=console,
        skill_layout=skill_layout,
    )

    # Verify bundled workflows (legacy validation — only meaningful when
    # we actually installed legacy workflows for this project).
    if result.layout == "old":
        console.print()
        console.print("[bold]Workflow validation:[/bold]")
        _validate_bundled_workflows(console)

    # Summary
    console.print()
    any_changes = (
        result.workflows_copied
        or result.config_created
        or result.gitignore_updated
        or result.dirs_created
        or result.skills_bootstrapped
    )
    if any_changes:
        _success("Project initialized successfully")
        if result.skills_bootstrapped:
            console.print(
                f"  Skills bootstrapped (v6.4+ layout): {len(result.skills_bootstrapped)}"
            )
        if result.workflows_copied:
            console.print(f"  Workflows copied: {len(result.workflows_copied)}")
    else:
        console.print("[green]Project already initialized - no changes needed.[/green]")

    # Exit with warning code if workflows were skipped (legacy path only —
    # the new-layout bootstrap intentionally no-clobbers).
    if result.has_skipped:
        console.print(
            f"\n[yellow]⚠️  {len(result.workflows_skipped)} workflow(s) skipped (local differs from bundled)[/yellow]"
        )  # noqa: E501
        console.print("[yellow]   Use --reset-workflows to restore bundled versions[/yellow]")
        raise typer.Exit(code=EXIT_WARNING)
