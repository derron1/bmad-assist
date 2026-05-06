"""Git worktree management for A/B testing.

Creates and manages isolated git worktrees from fixture repositories.
Each variant gets its own worktree at the specified git ref.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from bmad_assist.core.exceptions import IsolationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorktreeInfo:
    """Information about a created worktree."""

    path: Path
    ref: str
    variant_label: str


def _run_git(args: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result.

    Args:
        args: Git subcommand and arguments.
        cwd: Working directory for the command.
        check: If True, raise on non-zero exit.

    Returns:
        CompletedProcess with stdout/stderr.

    Raises:
        IsolationError: If git command fails and check=True.

    """
    cmd = ["git"] + args
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if check and result.returncode != 0:
            raise IsolationError(
                f"Git command failed: {' '.join(cmd)}\n"
                f"stderr: {result.stderr.strip()}\n"
                f"stdout: {result.stdout.strip()}",
                source_path=cwd,
                snapshot_path=cwd,
            )
        return result
    except subprocess.TimeoutExpired as e:
        raise IsolationError(
            f"Git command timed out: {' '.join(cmd)}",
            source_path=cwd,
            snapshot_path=cwd,
        ) from e
    except FileNotFoundError as e:
        raise IsolationError(
            "git executable not found",
            source_path=cwd,
            snapshot_path=cwd,
        ) from e


def validate_fixture_is_git_repo(fixture_path: Path) -> None:
    """Validate that the fixture directory is a git repository.

    Raises:
        IsolationError: If not a git repo.

    """
    git_dir = fixture_path / ".git"
    if not git_dir.exists():
        raise IsolationError(
            f"Fixture is not a git repository: {fixture_path} (no .git directory)",
            source_path=fixture_path,
            snapshot_path=fixture_path,
        )


def validate_ref_exists(fixture_path: Path, ref: str) -> None:
    """Validate that a git ref exists in the fixture repo.

    Raises:
        IsolationError: If ref does not exist.

    """
    result = _run_git(
        ["rev-parse", "--verify", f"{ref}^{{commit}}"],
        cwd=fixture_path,
        check=False,
    )
    if result.returncode != 0:
        raise IsolationError(
            f"Git ref '{ref}' not found in fixture {fixture_path.name}",
            source_path=fixture_path,
            snapshot_path=fixture_path,
        )


def create_worktree(
    fixture_path: Path,
    worktree_path: Path,
    ref: str,
    variant_label: str,
) -> WorktreeInfo:
    """Create a git worktree from a fixture at a specific ref.

    Creates a detached HEAD worktree at the specified commit/tag.
    The fixture repo itself is not modified.

    Args:
        fixture_path: Path to the source git repository (fixture).
        worktree_path: Desired path for the new worktree.
        ref: Git ref to checkout (commit SHA, tag, branch name).
        variant_label: Label for this variant (for logging).

    Returns:
        WorktreeInfo with worktree details.

    Raises:
        IsolationError: If worktree creation fails.

    """
    if worktree_path.exists():
        raise IsolationError(
            f"Worktree path already exists: {worktree_path}",
            source_path=fixture_path,
            snapshot_path=worktree_path,
        )

    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Creating worktree for variant '%s' at %s (ref: %s)",
        variant_label,
        worktree_path,
        ref,
    )

    _run_git(
        ["worktree", "add", "--detach", str(worktree_path), ref],
        cwd=fixture_path,
    )

    logger.info("Worktree created: %s", worktree_path)

    return WorktreeInfo(
        path=worktree_path.resolve(),
        ref=ref,
        variant_label=variant_label,
    )


def remove_worktree(fixture_path: Path, worktree_path: Path) -> None:
    """Remove a git worktree and clean up.

    Args:
        fixture_path: Path to the source git repository.
        worktree_path: Path to the worktree to remove.

    """
    if not worktree_path.exists():
        logger.debug("Worktree already removed: %s", worktree_path)
        return

    logger.info("Removing worktree: %s", worktree_path)

    result = _run_git(
        ["worktree", "remove", "--force", str(worktree_path)],
        cwd=fixture_path,
        check=False,
    )

    if result.returncode != 0:
        logger.warning(
            "git worktree remove failed, falling back to manual cleanup: %s",
            result.stderr.strip(),
        )
        try:
            shutil.rmtree(worktree_path)
        except OSError as e:
            logger.error("Failed to remove worktree directory %s: %s", worktree_path, e)

        _run_git(["worktree", "prune"], cwd=fixture_path, check=False)


def checkout_ref(worktree_path: Path, ref: str) -> None:
    """Checkout a specific ref in an existing worktree.

    Used for per-story ref switching during A/B tests.
    Detaches HEAD at the specified commit.  Purges all runtime
    artifacts so each story starts from a clean snapshot of the
    ref commit.  Artifacts are already snapshotted by the caller
    before this function is called.

    Args:
        worktree_path: Path to the worktree.
        ref: Git ref to checkout (commit SHA, tag, branch).

    Raises:
        IsolationError: If checkout fails.

    """
    logger.info("Checking out ref %s in %s", ref, worktree_path.name)

    # Purge runtime artifact directories (gitignored, so git clean skips them)
    for artifact_dir in ("_bmad-output", ".bmad-assist/prompts"):
        path = worktree_path / artifact_dir
        if path.is_dir():
            shutil.rmtree(path)
            logger.debug("Purged artifact directory: %s", artifact_dir)

    # Discard tracked file changes left by the previous story's dev phase
    _run_git(["checkout", "."], cwd=worktree_path)
    _run_git(["clean", "-fd"], cwd=worktree_path)
    _run_git(["checkout", "--detach", ref], cwd=worktree_path)


def create_ab_worktrees(
    fixture_path: Path,
    base_dir: Path,
    ref: str,
    test_name: str,
) -> tuple[WorktreeInfo, WorktreeInfo]:
    """Create worktrees for both A/B variants.

    Args:
        fixture_path: Path to the fixture git repository.
        base_dir: Base directory for worktrees.
        ref: Git ref to checkout for both variants.
        test_name: Test name for directory naming.

    Returns:
        Tuple of (worktree_a, worktree_b).

    Raises:
        IsolationError: If worktree creation fails.

    """
    validate_fixture_is_git_repo(fixture_path)
    validate_ref_exists(fixture_path, ref)

    worktree_a = create_worktree(
        fixture_path=fixture_path,
        worktree_path=base_dir / "worktree-a",
        ref=ref,
        variant_label="variant-a",
    )

    try:
        worktree_b = create_worktree(
            fixture_path=fixture_path,
            worktree_path=base_dir / "worktree-b",
            ref=ref,
            variant_label="variant-b",
        )
    except IsolationError:
        remove_worktree(fixture_path, worktree_a.path)
        raise

    return worktree_a, worktree_b


def cleanup_ab_worktrees(
    fixture_path: Path,
    worktree_a: WorktreeInfo | None,
    worktree_b: WorktreeInfo | None,
    base_dir: Path | None = None,
) -> None:
    """Clean up both worktrees and optionally the base directory.

    Args:
        fixture_path: Path to the fixture git repository.
        worktree_a: Worktree A info (or None if not created).
        worktree_b: Worktree B info (or None if not created).
        base_dir: Optional base directory to remove after worktrees.

    """
    if worktree_b is not None:
        remove_worktree(fixture_path, worktree_b.path)
    if worktree_a is not None:
        remove_worktree(fixture_path, worktree_a.path)

    if base_dir is not None and base_dir.exists():
        try:
            shutil.rmtree(base_dir)
            logger.debug("Removed AB test base directory: %s", base_dir)
        except OSError as e:
            logger.warning("Failed to remove base directory %s: %s", base_dir, e)
