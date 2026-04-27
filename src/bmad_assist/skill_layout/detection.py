"""Decide whether a project uses the BMAD v6.4+ "skill" layout.

Phase 0 settled on a single signal: the presence of
``_bmad/scripts/resolve_customization.py``. That file is shipped by
v6.4+ installers and absent from older "workflow" layouts.
"""

from __future__ import annotations

from pathlib import Path

from .types import Layout


def detect_layout(project_root: Path) -> Layout:
    """Return ``"new"`` for v6.4+ skill layouts, ``"old"`` otherwise.

    Args:
        project_root: Project root to inspect.

    Returns:
        The detected layout flavor.

    """
    marker = project_root / "_bmad" / "scripts" / "resolve_customization.py"
    return "new" if marker.exists() else "old"
