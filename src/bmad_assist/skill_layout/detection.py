"""Layout detector — Phase 6 reduced to a permanent ``"new"`` answer.

The legacy "workflow" layout was removed when the bundled
``src/bmad_assist/workflows/<name>/`` tree was deleted and routing
became single-path through the v6.4+ skill compilers. This shim is
preserved as a compatibility surface for callers that still import
``detect_layout`` (CLI flags, tests, etc.); it now always returns
``"new"``.

# TODO: remove in next major release once all call sites have been
# updated to drop the layout-detection branch.
"""

from __future__ import annotations

from pathlib import Path

from .types import Layout


def detect_layout(project_root: Path) -> Layout:
    """Always return ``"new"``.

    Phase 6 collapsed the routing onto the v6.4+ skill layout. The
    project_root argument is accepted for signature compatibility but
    is no longer inspected.
    """
    del project_root  # No longer consulted.
    return "new"
