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

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

import bmad_assist
from bmad_assist.core.config import Config
from bmad_assist.core.exceptions import BmadAssistError
from bmad_assist.git import check_gitignore

logger = logging.getLogger(__name__)

# Stamp file written into each installed skill directory recording the
# bmad-assist version and bundled customize.toml hash that produced the
# copy. Used by bootstrap to detect stale installs after a release that
# changes skill content/layout, and to distinguish unmodified bundled
# customize.toml files from consumer-edited overrides. Not shipped in
# the bundle — written post-copy by ``bootstrap_new_layout``.
_BUNDLE_VERSION_FILE = ".bundle-version"
_LEGACY_VERSION_LABEL = "legacy"


@dataclass(frozen=True)
class BundleStamp:
    """Metadata describing the bundled skill content last installed."""

    version: str | None
    customize_toml_hash: str | None = None


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


def _hash_file(path: Path) -> str | None:
    """Return the SHA-256 hash for ``path``, or ``None`` if unreadable."""
    try:
        content = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(content).hexdigest()


def _read_installed_bundle_stamp(skill_dir: Path) -> BundleStamp:
    """Read bundled skill provenance metadata from an installed skill dir.

    New installs write JSON with both the bmad-assist version and the
    bundled ``customize.toml`` hash. Older installs wrote a single
    version string; those are treated as version-only legacy stamps.
    """
    stamp = skill_dir / _BUNDLE_VERSION_FILE
    try:
        content = stamp.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return BundleStamp(version=None)

    if not content:
        return BundleStamp(version=None)

    if content.startswith("{"):
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("Invalid bundle stamp JSON in %s", stamp)
            return BundleStamp(version=None)
        if not isinstance(data, dict):
            logger.warning("Invalid bundle stamp payload in %s", stamp)
            return BundleStamp(version=None)
        version = data.get("version")
        customize_hash = data.get("customize_toml_hash")
        return BundleStamp(
            version=version if isinstance(version, str) and version else None,
            customize_toml_hash=customize_hash
            if isinstance(customize_hash, str) and customize_hash
            else None,
        )

    return BundleStamp(version=content)


def _read_installed_bundle_version(skill_dir: Path) -> str | None:
    """Read the stamped bundle version from an installed skill dir."""
    return _read_installed_bundle_stamp(skill_dir).version


def _write_bundle_version(
    skill_dir: Path,
    version: str,
    customize_toml_hash: str | None,
) -> None:
    """Write bundled skill provenance metadata into an installed skill dir."""
    payload = {
        "version": version,
        "customize_toml_hash": customize_toml_hash,
    }
    content = json.dumps(payload, sort_keys=True) + "\n"
    (skill_dir / _BUNDLE_VERSION_FILE).write_text(content, encoding="utf-8")


def _installed_customize_hash(skill_dir: Path) -> str | None:
    """Hash the customize.toml that is actually on disk in an installed skill.

    The stamp's invariant is that ``customize_toml_hash`` records the
    bundled default the on-disk file was last synced from. ``_copy_skill_tree``
    may PRESERVE an existing customize.toml (no copy) when it diverges from the
    previously stamped default, so callers must stamp the hash of what is
    *actually on disk* rather than the current bundled hash — otherwise the
    stamp falsely certifies stale content as current-bundled and every future
    run's stamp-is-current fast-path skips the skill forever (see BOOT-001 in
    docs/known-issues.md).
    """
    return _hash_file(skill_dir / "customize.toml")


def _copy_skill_tree(
    src_dir: Path,
    dst_dir: Path,
    _is_root: bool = True,
    *,
    preserve_customizations: bool = True,
    previous_customize_toml_hash: str | None = None,
) -> None:
    """Copy a bundled skill directory tree.

    Mirrors :func:`_copy_workflow_tree` but is named separately so the
    intent is clear at the call site (skill bootstrap vs workflow
    install). Performs the same atomic-write + rollback discipline.

    Args:
        src_dir: Source skill directory (under ``bmad_assist.skills``).
        dst_dir: Destination skill directory.
        _is_root: Internal flag - True for top-level call (enables rollback).
        preserve_customizations: When True (default), overwrite an
            existing ``customize.toml`` only when its content hash still
            matches the previously installed bundled hash. Modified
            files are preserved. Set False for the destructive
            ``--reset-skills-force`` path.
        previous_customize_toml_hash: Hash recorded when this skill was
            last installed. Used to distinguish unmodified bundled
            defaults from consumer customizations.

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
            # The version stamp is written by bootstrap_new_layout, never shipped.
            if item.name == _BUNDLE_VERSION_FILE:
                continue
            if item.is_dir():
                _copy_skill_tree(
                    item,
                    dst_dir / item.name,
                    _is_root=False,
                    preserve_customizations=preserve_customizations,
                    previous_customize_toml_hash=previous_customize_toml_hash,
                )
            else:
                target = dst_dir / item.name
                if preserve_customizations and item.name == "customize.toml" and target.exists():
                    installed_hash = _hash_file(target)
                    if (
                        previous_customize_toml_hash is not None
                        and installed_hash == previous_customize_toml_hash
                    ):
                        logger.debug(
                            "Updating unmodified bundled customize.toml at %s",
                            target,
                        )
                    else:
                        logger.warning(
                            "Preserving modified customize.toml at %s; "
                            "bundled defaults were not overwritten",
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
    untouched while their bundle stamp is current unless ``force=True``.

    Args:
        project_path: Project root directory.
        force: If True, re-copy existing skill directories from the
            bundled versions. If False, skip already-present skills.
        console: Rich console for progress output.
        preserve_customizations: When True (default), existing
            ``customize.toml`` files are overwritten only when their
            content hash still matches the previously installed bundled
            default. Divergent files are preserved as consumer edits.
            The destructive ``--reset-skills-force`` path passes False
            to overwrite ``customize.toml`` regardless of provenance.

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
            "  [dim]Re-copy mode: updating unmodified customize.toml files; "
            "preserving modified overrides.[/dim]"
        )
    elif force and not preserve_customizations:
        console.print(
            "  [yellow]Destructive reset: customize.toml overrides will be replaced.[/yellow]"
        )

    bootstrapped: list[str] = []
    skipped: list[str] = []
    current_version = bmad_assist.__version__

    for i, skill_id in enumerate(skills, 1):
        src_dir = get_bundled_skill_dir(skill_id)
        if src_dir is None:
            console.print(f"  [{i}/{len(skills)}] {skill_id}... [red]NOT FOUND[/red]")
            continue
        current_customize_hash = _hash_file(src_dir / "customize.toml")

        console.print(f"  [{i}/{len(skills)}] {skill_id}...", end=" ")

        # Track the most "active" status across both mirrors so the
        # rendered message reflects what actually happened. Priority:
        # fresh > forced > refreshed > skipped.
        skill_status: str | None = None
        from_version: str | None = None

        for mirror_prefix in (".claude/skills", ".agents/skills"):
            dst_dir = project_path / mirror_prefix / skill_id
            if not _validate_path_safe(project_path, dst_dir):
                console.print(f"[red]SKIPPED {mirror_prefix} (invalid path)[/red]", end=" ")
                continue

            if not dst_dir.exists():
                _copy_skill_tree(
                    src_dir,
                    dst_dir,
                    preserve_customizations=preserve_customizations,
                )
                _write_bundle_version(dst_dir, current_version, _installed_customize_hash(dst_dir))
                if skill_status not in ("fresh",):
                    skill_status = "fresh"
                continue

            installed_stamp = _read_installed_bundle_stamp(dst_dir)

            if force:
                # Re-copy in place rather than rmtree → copy. The
                # _copy_skill_tree call updates customize.toml only when
                # provenance shows the destination still matches the
                # previous bundled default. For the destructive
                # --reset-skills-force path, preserve=False overwrites it.
                _copy_skill_tree(
                    src_dir,
                    dst_dir,
                    preserve_customizations=preserve_customizations,
                    previous_customize_toml_hash=installed_stamp.customize_toml_hash,
                )
                _write_bundle_version(dst_dir, current_version, _installed_customize_hash(dst_dir))
                if skill_status != "fresh":
                    skill_status = "forced"
                continue

            if (
                installed_stamp.version == current_version
                and installed_stamp.customize_toml_hash is None
            ):
                # Upgrade legacy one-line stamps in place without
                # changing an otherwise current no-clobber install.
                _write_bundle_version(dst_dir, current_version, _installed_customize_hash(dst_dir))
                if skill_status is None:
                    skill_status = "skipped"
                continue

            stamp_is_current = (
                installed_stamp.version == current_version
                and installed_stamp.customize_toml_hash == current_customize_hash
            )
            if not stamp_is_current:
                # Stale, unstamped legacy, or bundled customize.toml
                # changed — auto-refresh. customize.toml is overwritten
                # only when its content still matches the previously
                # stamped bundled default; consumer edits are preserved.
                _copy_skill_tree(
                    src_dir,
                    dst_dir,
                    preserve_customizations=preserve_customizations,
                    previous_customize_toml_hash=installed_stamp.customize_toml_hash,
                )
                _write_bundle_version(dst_dir, current_version, _installed_customize_hash(dst_dir))
                if skill_status not in ("fresh", "forced"):
                    skill_status = "refreshed"
                    if from_version is None:
                        from_version = installed_stamp.version or _LEGACY_VERSION_LABEL
                continue

            # Stamp matches — fully up to date.
            if skill_status is None:
                skill_status = "skipped"

        if skill_status == "skipped":
            console.print("[dim]exists[/dim]")
            skipped.append(skill_id)
        elif skill_status == "refreshed":
            label = from_version or _LEGACY_VERSION_LABEL
            console.print(f"[green]updated {label} → {current_version}[/green]")
            bootstrapped.append(skill_id)
        elif skill_status in ("fresh", "forced"):
            console.print("[green]ok[/green]")
            bootstrapped.append(skill_id)
        else:
            # All mirrors had invalid paths (or skill list was empty).
            console.print("[dim](no-op)[/dim]")
            skipped.append(skill_id)

    # Post-bootstrap chain validation: walk every installed skill's
    # tri-modal step chains and abort the install if any link is broken.
    # Guards against silent regressions like the {skill-root} truncation
    # bug — which shipped for weeks because nothing checked chain
    # integrity at install time. Run against both mirrors so we catch a
    # corrupted copy in either tree.
    _validate_installed_skill_chains(project_path, skills, console)

    return bootstrapped, skipped


def _validate_installed_skill_chains(
    project_path: Path,
    skill_ids: list[str],
    console: Console,
) -> None:
    """Walk every installed skill's step chains; raise on any broken link.

    Aggregates errors across all skills + both mirrors so the user sees
    every problem at once instead of fixing them one at a time.

    Raises:
        SetupError: If any chain has a broken ``nextStepFile`` link.

    """
    # Local import to avoid pulling the compiler graph into module-level
    # imports of project_setup (which is loaded by the CLI entrypoint).
    from bmad_assist.skill_layout import (
        ChainValidationError,
        validate_skill_chains,
    )

    all_errors: list[ChainValidationError] = []
    for skill_id in skill_ids:
        for mirror_prefix in (".claude/skills", ".agents/skills"):
            skill_dir = project_path / mirror_prefix / skill_id
            if not skill_dir.is_dir():
                continue
            all_errors.extend(validate_skill_chains(skill_dir))

    if not all_errors:
        return

    console.print(
        f"\n[red]Chain validation failed: {len(all_errors)} broken "
        f"step link(s) detected in installed skills.[/red]"
    )
    for err in all_errors:
        logger.error("broken skill chain: %s", err.format())
        console.print(f"  [red]•[/red] {err.format()}")

    raise SetupError(
        f"Bootstrap aborted: {len(all_errors)} broken step chain link(s) "
        f"detected in installed skills. See ERROR logs for details."
    )


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
            re-bootstrap preserves per-skill ``customize.toml`` only
            when it has diverged from the previously installed bundled
            default. The destructive ``--reset-skills-force`` path wires
            this to False to overwrite ``customize.toml`` regardless of
            provenance.
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
