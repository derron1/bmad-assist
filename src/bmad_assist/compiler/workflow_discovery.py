"""Multi-location workflow discovery for compiler.

Phase 6 simplification: the bundled-legacy fallback was removed when
``src/bmad_assist/workflows/<name>/`` was deleted. Discovery now only
probes:

1. Project override (``.bmad-assist/workflows/<name>/``)
2. v6.4+ skill mirror (``.claude/skills/<bmad-id>/`` /
   ``.agents/skills/<bmad-id>/``)
3. Legacy ``_bmad/...`` install on the user's machine

There is no longer a bundled-source fallback for the legacy layout —
projects without a ``_bmad/...`` install must rely on the bundled
*skill* sources (under :mod:`bmad_assist.skills`) reached via
:func:`bmad_assist.skill_layout.find_skill`.
"""

import logging
from pathlib import Path

from bmad_assist.compiler.types import WorkflowSource

logger = logging.getLogger(__name__)

# Re-exported from compiler.core for callers that historically imported
# the dispatch table from this module.
from bmad_assist.compiler.core import WORKFLOW_REGISTRY as WORKFLOW_TO_SKILL_ID  # noqa: E402,F401

# Search locations for user's BMAD installation (checked in order)
# Note: .bmad-assist/workflows is checked separately as override, not here
BMAD_SEARCH_PATHS = [
    "_bmad/bmm/workflows/4-implementation",
    "_bmad/bmm/workflows/testarch",
    "_bmad/tea/workflows/testarch",
]

# Mapping from workflow name to BMAD directory structure
# testarch workflows use different naming in BMAD (without 'testarch-' prefix)
WORKFLOW_TO_BMAD_DIR = {
    "testarch-atdd": "atdd",
    "testarch-trace": "trace",
    "testarch-test-review": "test-review",
    "testarch-automate": "automate",
    "testarch-ci": "ci",
    "testarch-framework": "framework",
    "testarch-nfr-assess": "nfr-assess",
    "testarch-test-design": "test-design",
}

# Search prefixes for v6.4+ skill mirrors (relative to project root).
NEW_LAYOUT_SKILL_ROOTS = (".claude/skills", ".agents/skills")


def _is_valid_workflow_dir(path: Path) -> bool:
    """Check if path is a valid workflow directory.

    Valid if directory contains workflow.yaml OR workflow.md (or both).
    Tri-modal workflows may have only workflow.md without workflow.yaml.
    """
    if not path.is_dir():
        return False

    has_yaml = (path / "workflow.yaml").is_file()
    has_md = (path / "workflow.md").is_file()

    return has_yaml or has_md


def _is_valid_skill_dir(path: Path) -> bool:
    """Check if a path is a v6.4+ skill directory (contains SKILL.md)."""
    return path.is_dir() and (path / "SKILL.md").is_file()


def _probe_new_layout(
    workflow_name: str,
    project_root: Path,
) -> WorkflowSource | None:
    """Probe the v6.4+ skill layout for ``workflow_name``."""
    skill_id = WORKFLOW_TO_SKILL_ID.get(workflow_name)
    if skill_id is None:
        return None

    for prefix in NEW_LAYOUT_SKILL_ROOTS:
        candidate = project_root / prefix / skill_id
        if _is_valid_skill_dir(candidate):
            logger.debug("Discovered new-layout skill: %s", candidate)
            return WorkflowSource(
                workflow_name=workflow_name,
                skill_id=skill_id,
                layout="new",
                path=candidate,
            )

    return None


def _probe_legacy_user_install(
    workflow_name: str,
    project_root: Path,
) -> Path | None:
    """Probe the legacy ``_bmad/...`` workflow directories."""
    bmad_dir_name = WORKFLOW_TO_BMAD_DIR.get(workflow_name, workflow_name)
    candidates = [bmad_dir_name]
    if bmad_dir_name != workflow_name:
        candidates.append(workflow_name)

    for search_path in BMAD_SEARCH_PATHS:
        for candidate_name in candidates:
            candidate = project_root / search_path / candidate_name
            if _is_valid_workflow_dir(candidate):
                logger.debug("Using user's BMAD workflow: %s", candidate)
                return candidate
    return None


def discover_workflow_source(
    workflow_name: str,
    project_root: Path,
    *,
    layout: str | None = None,
) -> WorkflowSource | None:
    """Discover a workflow's source directory.

    Probe order:

    1. **Project override** (``.bmad-assist/workflows/<name>/``) wins
       unconditionally. Returns ``layout="override"``.
    2. **v6.4+ skill mirror** under ``.claude/skills`` /
       ``.agents/skills``. Returns ``layout="new"``.
    3. **Legacy ``_bmad/...`` install** on the user's machine. Returns
       ``layout="old"``.

    Phase 6 removed the bundled-source fallback: workflows without an
    installed source path must be reached via the skill-layout
    compilers (which read SKILL.md from :mod:`bmad_assist.skills`).

    Args:
        workflow_name: Workflow name (e.g. ``"dev-story"``).
        project_root: Project root directory.
        layout: Accepted for backwards compatibility with Phase 4
            callers but no longer functional — every probe runs in the
            documented order regardless of the value. Will be removed
            in the next major release.

    Returns:
        A :class:`WorkflowSource`, or ``None`` if no source could be
        located.

    """
    del layout  # No longer consulted; kept for signature compat.
    # 1. Project-level override always wins.
    override = project_root / ".bmad-assist" / "workflows" / workflow_name
    if _is_valid_workflow_dir(override):
        logger.debug("Using project override: %s", override)
        return WorkflowSource(
            workflow_name=workflow_name,
            skill_id=None,
            layout="override",
            path=override,
        )

    # 2. v6.4+ skill mirror.
    new_source = _probe_new_layout(workflow_name, project_root)
    if new_source is not None:
        return new_source

    # 3. Legacy ``_bmad/...`` install on the user's machine.
    legacy = _probe_legacy_user_install(workflow_name, project_root)
    if legacy is not None:
        return WorkflowSource(
            workflow_name=workflow_name,
            skill_id=WORKFLOW_TO_SKILL_ID.get(workflow_name),
            layout="old",
            path=legacy,
        )

    return None


def get_workflow_not_found_message(workflow_name: str, project_root: Path) -> str:
    """Generate helpful error message when workflow not found."""
    bmad_dir_name = WORKFLOW_TO_BMAD_DIR.get(workflow_name, workflow_name)
    candidates = [bmad_dir_name]
    if bmad_dir_name != workflow_name:
        candidates.append(workflow_name)
    checked = []
    skill_id = WORKFLOW_TO_SKILL_ID.get(workflow_name)
    if skill_id is not None:
        for prefix in NEW_LAYOUT_SKILL_ROOTS:
            checked.append(str(project_root / prefix / skill_id))
    for p in BMAD_SEARCH_PATHS:
        for candidate_name in candidates:
            checked.append(str(project_root / p / candidate_name))

    return (
        f"Workflow '{workflow_name}' not found!\n\n"
        f"Checked locations:\n" + "\n".join(f"  - {loc}" for loc in checked) + "\n\n"
        f"To fix:\n"
        f"  1. Run `bmad-assist init` to bootstrap the v6.4+ skill layout.\n"
        f"  2. Or install BMAD: copy _bmad/ from github.com/bmad-code-org/BMAD-METHOD\n"
        f"  3. Or create override: .bmad-assist/workflows/{workflow_name}/"
    )
