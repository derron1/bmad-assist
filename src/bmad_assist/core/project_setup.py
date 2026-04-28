"""Project setup utilities for bmad-assist.

Provides shared logic for ``init`` and ``run`` commands to bootstrap
the BMAD v6.4+ skill layout into a project (and to manage the
gitignore warnings shown to the user).

Phase 6 removed the legacy ``_bmad/bmm/workflows/...`` bootstrap path
and its supporting helpers (``copy_bundled_workflows``,
``reset_project_cache``, ``sync_bundled_cache``,
``_create_bmad_config``, ``_has_legacy_install``, plus the
overwrite-prompt machinery used only by the legacy copier). The
remaining surface is intentionally narrow: bootstrap skills, atomically
copy files, validate paths.
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from bmad_assist.core.config import Config
from bmad_assist.core.exceptions import BmadAssistError
from bmad_assist.git import check_gitignore

logger = logging.getLogger(__name__)


class SetupError(BmadAssistError):
    """Error during project setup."""


@dataclass
class SetupResult:
    """Result of project setup operation."""

    dirs_created: list[Path] = field(default_factory=list)
    config_created: bool = False
    workflows_copied: list[str] = field(default_factory=list)
    workflows_skipped: list[str] = field(default_factory=list)
    gitignore_updated: bool = False
    layout: str = "new"  # Phase 6: always "new"; field kept for compat.
    skills_bootstrapped: list[str] = field(default_factory=list)
    skills_skipped: list[str] = field(default_factory=list)

    @property
    def has_skipped(self) -> bool:
        """Return True if any workflows were skipped due to differences."""
        return len(self.workflows_skipped) > 0


def _validate_path_safe(base_path: Path, target_path: Path) -> bool:
    """Ensure target_path is within base_path (no traversal).

    Args:
        base_path: The base directory that target must be within.
        target_path: The path to validate.

    Returns:
        True if target_path is safely within base_path.

    """
    try:
        base_resolved = base_path.resolve()
        target_resolved = target_path.resolve()
        return target_resolved.is_relative_to(base_resolved)
    except (ValueError, OSError):
        return False


def _atomic_copy_file(src: Path, dst: Path) -> None:
    """Copy file atomically with secure temp file permissions.

    Args:
        src: Source file to copy.
        dst: Destination path.

    Raises:
        SetupError: If copy fails or source is a symlink.

    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dst.with_suffix(dst.suffix + ".tmp")

    try:
        # Read source (don't follow symlinks - F5-SEC)
        if src.is_symlink():
            raise SetupError(f"Cannot copy symlink: {src}")
        content = src.read_bytes()

        # Write to temp with standard permissions (readable by group/other)
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(fd, content)
        finally:
            os.close(fd)

        # Atomic replace
        os.replace(temp_path, dst)
    except OSError as e:
        if temp_path.exists():
            temp_path.unlink()
        raise SetupError(f"Failed to copy {src} to {dst}: {e}") from e


def check_gitignore_warning(
    project_path: Path,
    config: Config | None,
    console: Console,
) -> None:
    """Show gitignore warning if needed, respecting suppress config.

    Args:
        project_path: Project root directory.
        config: Loaded config (may be None).
        console: Rich console for output.

    """
    if config and config.warnings and config.warnings.suppress_gitignore:
        return  # Suppressed

    all_present, missing = check_gitignore(project_path)
    if all_present:
        return

    console.print("\n[yellow]⚠️  .gitignore not configured for bmad-assist[/yellow]")
    console.print(f"   Missing patterns: {', '.join(missing)}")
    console.print("\n   To fix: [bold]bmad-assist init[/bold]")
    console.print("   To suppress: Add to [bold]bmad-assist.yaml[/bold]:")
    console.print("     [dim]warnings:[/dim]")
    console.print("     [dim]  suppress_gitignore: true[/dim]\n")


def _copy_skill_tree(
    src_dir: Path,
    dst_dir: Path,
    _is_root: bool = True,
    *,
    preserve_customizations: bool = True,
) -> None:
    """Copy a bundled skill directory tree.

    Mirrors :func:`_copy_workflow_tree` but is named separately so the
    intent is clear at the call site (skill bootstrap vs workflow
    install). Performs the same atomic-write + rollback discipline.

    Args:
        src_dir: Source skill directory (under ``bmad_assist.skills``).
        dst_dir: Destination skill directory.
        _is_root: Internal flag - True for top-level call (enables rollback).
        preserve_customizations: When True (default), skip copying
            ``customize.toml`` files when the destination already exists.
            ``customize.toml`` is the v6.4+ user-override surface; the
            non-destructive ``--reset-workflows`` flow must not clobber
            it. Set False for the destructive ``--reset-skills-force``
            path.

    Raises:
        SetupError: If any copy operation fails. Cleans up partial copy.

    """
    import shutil

    created_dst = not dst_dir.exists()
    dst_dir.mkdir(parents=True, exist_ok=True)

    try:
        for item in src_dir.iterdir():
            if item.is_symlink():
                logger.warning("Skipping symlink in skill: %s", item)
                continue
            # Skip Python bytecode artefacts that may be co-located
            # under the bundled skills package.
            if item.name == "__pycache__" or item.name.endswith(".pyc"):
                continue
            if item.is_dir():
                _copy_skill_tree(
                    item,
                    dst_dir / item.name,
                    _is_root=False,
                    preserve_customizations=preserve_customizations,
                )
            else:
                target = dst_dir / item.name
                # Preserve user customize.toml overrides on non-destructive
                # re-copies (Phase 5 --reset-workflows semantics).
                if preserve_customizations and item.name == "customize.toml" and target.exists():
                    logger.debug(
                        "Preserving user customize.toml override at %s",
                        target,
                    )
                    continue
                _atomic_copy_file(item, target)
    except (SetupError, OSError) as e:
        if _is_root and created_dst and dst_dir.exists():
            logger.warning("Rolling back partial skill copy: %s", dst_dir)
            shutil.rmtree(dst_dir, ignore_errors=True)
        raise SetupError(f"Failed to copy skill tree {src_dir}: {e}") from e


def bootstrap_new_layout(
    project_path: Path,
    force: bool,
    console: Console,
    *,
    preserve_customizations: bool = True,
) -> tuple[list[str], list[str]]:
    """Bootstrap the BMAD v6.4+ skill layout from bundled sources.

    Copies each bundled skill (under :mod:`bmad_assist.skills`) into
    both ``<project>/.claude/skills/<id>/`` and
    ``<project>/.agents/skills/<id>/`` (byte-identical mirrors).

    No-clobber semantics: existing destination directories are left
    untouched unless ``force=True``.

    Args:
        project_path: Project root directory.
        force: If True, re-copy existing skill directories from the
            bundled versions. If False, skip already-present skills.
        console: Rich console for progress output.
        preserve_customizations: When True (default), per-skill
            ``customize.toml`` files are preserved during re-copy. The
            destructive ``--reset-skills-force`` path passes False to
            also overwrite ``customize.toml``. Only meaningful when
            ``force=True`` (no-clobber mode never copies over existing
            files anyway).

    Returns:
        Tuple of ``(bootstrapped_skill_ids, skipped_skill_ids)``.

    """
    from bmad_assist.skills import get_bundled_skill_dir, list_bundled_skills

    skills = list_bundled_skills()
    if not skills:
        console.print("  [yellow]No bundled skills available to bootstrap[/yellow]")
        return [], []

    console.print(f"\n[bold]Bootstrapping {len(skills)} bundled skills...[/bold]")
    if force and preserve_customizations:
        console.print(
            "  [dim]Re-copy mode: preserving any existing customize.toml overrides.[/dim]"
        )
    elif force and not preserve_customizations:
        console.print(
            "  [yellow]Destructive reset: customize.toml overrides will be replaced.[/yellow]"
        )

    bootstrapped: list[str] = []
    skipped: list[str] = []

    for i, skill_id in enumerate(skills, 1):
        src_dir = get_bundled_skill_dir(skill_id)
        if src_dir is None:
            console.print(f"  [{i}/{len(skills)}] {skill_id}... [red]NOT FOUND[/red]")
            continue

        console.print(f"  [{i}/{len(skills)}] {skill_id}...", end=" ")

        any_action = False
        for mirror_prefix in (".claude/skills", ".agents/skills"):
            dst_dir = project_path / mirror_prefix / skill_id
            if not _validate_path_safe(project_path, dst_dir):
                console.print(f"[red]SKIPPED {mirror_prefix} (invalid path)[/red]", end=" ")
                continue
            if dst_dir.exists():
                if force:
                    # Re-copy in place rather than rmtree → copy. The
                    # _copy_skill_tree call honours preserve_customizations
                    # (which we want to keep customize.toml intact for the
                    # default --reset-workflows UX). For the destructive
                    # --reset-skills-force path, preserve=False allows
                    # customize.toml to be overwritten too.
                    _copy_skill_tree(
                        src_dir,
                        dst_dir,
                        preserve_customizations=preserve_customizations,
                    )
                    any_action = True
                # else: no-clobber — leave it alone
            else:
                _copy_skill_tree(
                    src_dir,
                    dst_dir,
                    preserve_customizations=preserve_customizations,
                )
                any_action = True

        if any_action:
            console.print("[green]ok[/green]")
            bootstrapped.append(skill_id)
        else:
            console.print("[dim]exists[/dim]")
            skipped.append(skill_id)

    return bootstrapped, skipped


def ensure_project_setup(
    project_path: Path,
    include_gitignore: bool = False,
    force: bool = False,
    console: Console | None = None,
    *,
    preserve_customizations: bool = True,
    **_legacy_kwargs: object,
) -> SetupResult:
    """Ensure project is set up for bmad-assist.

    Every project — fresh or pre-existing — bootstraps the v6.4+ skill
    layout into ``.claude/skills/<id>/`` and ``.agents/skills/<id>/``
    from the bundled :mod:`bmad_assist.skills` package.

    Args:
        project_path: Project root directory.
        include_gitignore: If True, also update .gitignore (init only).
        force: If True, re-bootstrap (re-copy installed skills).
        console: Rich console for output (None = no output).
        preserve_customizations: When True (default), the v6.4+
            re-bootstrap preserves any per-skill ``customize.toml``
            overrides. The destructive ``--reset-skills-force`` path
            wires this to False to also overwrite ``customize.toml``.
        **_legacy_kwargs: Swallows deprecated keyword arguments
            (``skill_layout``) that pre-0.6.0 callers may still pass.

    Returns:
        SetupResult with status and details. ``layout`` is always
        ``"new"``.

    """
    del _legacy_kwargs  # kept for backwards-compat signature.

    from bmad_assist.git import setup_gitignore

    console = console or Console(quiet=True)
    result = SetupResult()
    result.layout = "new"

    # 1. Create .bmad-assist/ directory
    bmad_assist_dir = project_path / ".bmad-assist"
    if not bmad_assist_dir.exists():
        bmad_assist_dir.mkdir(parents=True)
        (bmad_assist_dir / "cache").mkdir()
        result.dirs_created.append(bmad_assist_dir)
        console.print(f"  [green]Created:[/green] {bmad_assist_dir.relative_to(project_path)}/")
    else:
        # Ensure cache subdir exists
        cache_dir = bmad_assist_dir / "cache"
        if not cache_dir.exists():
            cache_dir.mkdir()

    # 2. Bootstrap v6.4+ skill layout. ``force`` re-copies installed
    # skills; default is no-clobber so existing customisations win.
    bootstrapped, skipped = bootstrap_new_layout(
        project_path,
        force=force,
        console=console,
        preserve_customizations=preserve_customizations,
    )
    result.skills_bootstrapped = bootstrapped
    result.skills_skipped = skipped

    # 3. Setup gitignore (init only)
    if include_gitignore:
        changed, msg = setup_gitignore(project_path)
        result.gitignore_updated = changed
        if changed:
            console.print(f"  [green].gitignore:[/green] {msg}")

    return result
