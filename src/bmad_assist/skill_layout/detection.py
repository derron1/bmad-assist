"""Decide whether a project uses the BMAD v6.4+ "skill" layout.

Phase 4 expands the heuristic from Phase 0's single signal to two:

1. Strong signal: ``_bmad/scripts/resolve_customization.py`` exists.
   This file is shipped by v6.4+ BMAD installers and is absent from
   older "workflow" layouts.
2. Bootstrapped signal: any ``bmad-`` prefixed skill exists under
   ``.claude/skills/<id>/customize.toml``. This catches projects we
   bootstrapped via ``bmad-assist init`` without a full BMAD install.

Either signal classifies the project as ``"new"``. Otherwise we fall
back to ``"old"``.
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
    # Strong signal: BMAD's installer wrote the resolver script.
    resolver = project_root / "_bmad" / "scripts" / "resolve_customization.py"
    if resolver.exists():
        return "new"

    # Bootstrapped signal: any v6.4+ skill installed (with customize.toml).
    # Check for the canonical bmad-prefixed skill ids we bundle.
    skills_dir = project_root / ".claude" / "skills"
    if skills_dir.is_dir():
        try:
            for entry in skills_dir.iterdir():
                if (
                    entry.is_dir()
                    and entry.name.startswith("bmad-")
                    and (entry / "customize.toml").is_file()
                ):
                    return "new"
        except OSError:
            # Permission errors or transient filesystem issues — fall through.
            pass

    return "old"
