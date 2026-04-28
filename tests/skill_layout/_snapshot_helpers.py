"""Snapshot helpers for skill-layout compiler output.

Phase 7.1 introduces byte-identical output snapshots as the proof
contract for the inlining migration. The snapshots live under
``tests/skill_layout/snapshots/<skill-id>.tpl.xml``. Each one captures
the compiled body produced by the current (delegation-based) compiler.
Phase 7.2 inlines each compiler one-by-one; every inlined compiler
must reproduce its snapshot byte-for-byte.

The capture protocol uses ``UPDATE_SNAPSHOTS=1`` (or the explicit
``update=True`` argument) to refresh the on-disk snapshot. Snapshots
are committed to git so refreshes show up as reviewable diffs.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


def assert_compile_matches_snapshot(
    skill_id: str,
    compiled_body: str,
    *,
    update: bool = False,
) -> None:
    """Compare a compiled body to its pinned snapshot.

    Args:
        skill_id: Canonical bmad-prefixed id (e.g.
            ``"bmad-validate-story-synthesis"``). Determines the
            on-disk snapshot file name.
        compiled_body: The compiled workflow body to compare. Typically
            ``compile_workflow(...).context``.
        update: If ``True``, write the snapshot instead of comparing.
            ``UPDATE_SNAPSHOTS=1`` in the environment has the same
            effect — useful for refreshing every snapshot in one run.

    Raises:
        AssertionError: When the compiled body diverges from the
            snapshot. The error message reports the byte counts on both
            sides so the diff is easy to skim in CI logs.

    """
    snapshot_path = SNAPSHOT_DIR / f"{skill_id}.tpl.xml"
    if update or os.getenv("UPDATE_SNAPSHOTS"):
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(compiled_body, encoding="utf-8")
        return
    if not snapshot_path.exists():
        pytest.fail(f"Snapshot missing: {snapshot_path}. Run with UPDATE_SNAPSHOTS=1 to create.")
    expected = snapshot_path.read_text(encoding="utf-8")
    assert compiled_body == expected, (
        f"Compiled output diverges from snapshot for {skill_id}.\n"
        f"  Compiled: {len(compiled_body)} chars\n"
        f"  Snapshot: {len(expected)} chars\n"
        f"  Run with UPDATE_SNAPSHOTS=1 to refresh after intentional change."
    )


__all__ = ["SNAPSHOT_DIR", "assert_compile_matches_snapshot"]
