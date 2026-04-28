"""Multi-location workflow discovery for compiler.

Discovery probes:

1. Project override (``.bmad-assist/workflows/<name>/``)
2. v6.4+ skill mirror (``.claude/skills/<bmad-id>/`` /
   ``.agents/skills/<bmad-id>/``)
3. Legacy ``_bmad/...`` install on the user's machine (still
   consulted by the ``bmad-assist patch`` dev tool — runtime routing
   no longer touches it)

There is no bundled-source fallback for the legacy layout — projects
without a ``_bmad/...`` install must rely on the bundled *skill*
sources (under :mod:`bmad_assist.skills`) reached via
:func:`bmad_assist.skill_layout.find_skill`.
"""

import logging
from pathlib import Path

from bmad_assist.compiler.core import WORKFLOW_REGISTRY
from bmad_assist.compiler.types import WorkflowSource

logger = logging.getLogger(__name__)

# Search locations for user's BMAD installation (checked in order).
# Note: .bmad-assist/workflows is checked separately as override, not here.
BMAD_SEARCH_PATHS = [
    "_bmad/bmm/workflows/4-implementation",
    "_bmad/bmm/workflows/testarch",
    "_bmad/tea/workflows/testarch",
]

# Mapping from canonical bmad-prefixed workflow id to BMAD directory
# structure. testarch workflows use shorter names in BMAD without the
# ``testarch-`` prefix.
WORKFLOW_TO_BMAD_DIR = {
    "bmad-testarch-atdd": "atdd",
    "bmad-testarch-trace": "trace",
    "bmad-testarch-test-review": "test-review",
    "bmad-testarch-automate": "automate",
    "bmad-testarch-ci": "ci",
    "bmad-testarch-framework": "framework",
    "bmad-testarch-nfr": "nfr-assess",
    "bmad-testarch-test-design": "test-design",
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


def _resolve_skill_id(workflow_name: str) -> str | None:
    """Return the canonical skill id for ``workflow_name`` (or ``None``)."""
    return WORKFLOW_REGISTRY.get(workflow_name)


def _probe_new_layout(
    workflow_name: str,
    project_root: Path,
) -> WorkflowSource | None:
    """Probe the v6.4+ skill layout for ``workflow_name``."""
    skill_id = _resolve_skill_id(workflow_name)
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
    """Probe the legacy ``_bmad/...`` workflow directories.

    Falls back through both the canonical ``bmad-`` prefixed name and
    the legacy short name (e.g. ``bmad-testarch-atdd`` → ``atdd``)
    so projects that pre-date the canonical naming continue to resolve.
    """
    bmad_dir_name = WORKFLOW_TO_BMAD_DIR.get(workflow_name, workflow_name)
    candidates = [bmad_dir_name]
    if bmad_dir_name != workflow_name:
        candidates.append(workflow_name)
    # Strip the ``bmad-`` prefix as a final fallback for legacy installs.
    if workflow_name.startswith("bmad-"):
        legacy_short = workflow_name[len("bmad-") :]
        if legacy_short not in candidates:
            candidates.append(legacy_short)

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
) -> WorkflowSource | None:
    """Discover a workflow's source directory.

    Probe order:

    1. **Project override** (``.bmad-assist/workflows/<name>/``) wins
       unconditionally. Returns ``layout="override"``.
    2. **v6.4+ skill mirror** under ``.claude/skills`` /
       ``.agents/skills``. Returns ``layout="new"``.
    3. **Legacy ``_bmad/...`` install** on the user's machine. Returns
       ``layout="old"``. Only consulted by the ``bmad-assist patch``
       dev tool; runtime routing never reaches this branch.

    There is no bundled-source fallback for the legacy layout —
    workflows without an installed source path must be reached via the
    skill-layout compilers (which read SKILL.md from
    :mod:`bmad_assist.skills`).

    Args:
        workflow_name: Canonical ``bmad-`` prefixed workflow id.
        project_root: Project root directory.

    Returns:
        A :class:`WorkflowSource`, or ``None`` if no source could be
        located.

    """
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
            skill_id=_resolve_skill_id(workflow_name),
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
    skill_id = _resolve_skill_id(workflow_name)
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
