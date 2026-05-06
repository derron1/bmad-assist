"""Init command for bmad-assist CLI.

Initializes a project for bmad-assist usage by bootstrapping the
v6.4+ skill layout into ``.claude/skills/`` and ``.agents/skills/``.
"""

from pathlib import Path

import typer

from bmad_assist.cli_utils import (
    EXIT_ERROR,
    EXIT_WARNING,
    _error,
    _success,
    console,
)


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
        help=(
            "Re-copy bundled skill files. Unmodified customize.toml files are "
            "updated; modified overrides are preserved."
        ),
    ),
    reset_skills_force: bool = typer.Option(
        False,
        "--reset-skills-force",
        help=(
            "Re-copy ALL bundled skill files including customize.toml "
            "(destroys any user customize.toml overrides). Implies "
            "--reset-workflows. Use only when you want a completely "
            "clean slate."
        ),
    ),
    wizard: bool = typer.Option(
        False,
        "--wizard",
        "-w",
        help="Run interactive configuration wizard after initialization",
    ),
) -> None:
    """Initialize a project for bmad-assist.

    Sets up the project with required configuration:
    - Creates .bmad-assist/ directory for state and cache
    - Bootstraps bundled v6.4+ skills into .claude/skills/ and
      .agents/skills/
    - Adds required patterns to .gitignore to prevent committing
      artifacts

    This command is idempotent - safe to run multiple times.

    Examples:
        bmad-assist init                       # Initialize current directory
        bmad-assist init -p ./my-project       # Initialize specific project
        bmad-assist init --wizard              # Initialize and configure interactively
        bmad-assist init --dry-run             # Preview changes without applying
        bmad-assist init --reset-workflows     # Re-copy bundled skill files
                                               # (updates unmodified customize.toml)
        bmad-assist init --reset-skills-force  # Destructive reset including customize.toml

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

    # Run interactive config wizard FIRST if requested (before any other setup)
    if wizard and not dry_run:
        from bmad_assist.core.config_generator import run_config_wizard

        try:
            run_config_wizard(project_path, console)
        except KeyboardInterrupt:
            console.print("\n[yellow]Wizard cancelled[/yellow]")
            raise typer.Exit(code=130) from None

        console.print()

    # --reset-skills-force implies --reset-workflows (force-copy from bundled).
    if reset_skills_force and not reset_workflows:
        reset_workflows = True

    # Confirm destructive reset operations.
    if reset_skills_force:
        console.print(
            "[red]WARNING: --reset-skills-force will overwrite EVERY bundled skill "
            "file including customize.toml — your per-skill overrides will be lost![/red]"
        )
        if not Confirm.ask("Continue?", default=False):
            console.print("[dim]Cancelled.[/dim]")
            raise typer.Exit(code=0)
    elif reset_workflows:
        console.print(
            "[yellow]--reset-workflows will re-copy bundled skill files. "
            "Unmodified customize.toml files are updated; modified overrides "
            "are preserved.[/yellow]"
        )
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
        from bmad_assist.skills import list_bundled_skills

        if wizard:
            console.print("  [dim]Would run configuration wizard[/dim]")

        bmad_dir = project_path / ".bmad-assist"
        if not bmad_dir.exists():
            console.print(f"  [dim]Would create:[/dim] {bmad_dir}/")

        skills = list_bundled_skills()
        console.print(
            f"  [dim]Would bootstrap {len(skills)} bundled skills into "
            f".claude/skills/ and .agents/skills/[/dim]"
        )

        all_present, missing = check_gitignore(project_path)
        if not all_present:
            console.print(f"  [dim]Would update .gitignore:[/dim] {', '.join(missing)}")

        console.print()
        console.print("[yellow]Dry run - no changes made. Run without --dry-run to apply.[/yellow]")
        return

    # Run the actual setup.
    #
    # --reset-workflows: force=True, preserve_customizations=True
    #   (re-copy SKILL.md / template.md / etc.; update unmodified
    #   customize.toml files and preserve modified overrides)
    # --reset-skills-force: force=True, preserve_customizations=False
    #   (destructive — overwrite customize.toml too)
    # neither: force=False (no-clobber bootstrap only)
    result = ensure_project_setup(
        project_path,
        include_gitignore=True,
        force=reset_workflows,
        console=console,
        preserve_customizations=not reset_skills_force,
    )

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
    else:
        console.print("[green]Project already initialized - no changes needed.[/green]")

    # Exit with warning code if workflows were skipped (Phase 6: this
    # only fires for callers that pre-populated workflows_skipped; the
    # default skill bootstrap never sets it).
    if result.has_skipped:
        console.print(f"\n[yellow]{len(result.workflows_skipped)} workflow(s) skipped[/yellow]")
        raise typer.Exit(code=EXIT_WARNING)
