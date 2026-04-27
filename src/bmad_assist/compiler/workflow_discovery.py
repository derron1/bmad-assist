"""Multi-location workflow discovery for compiler.

Hybrid + layout-aware discovery strategy:

- CUSTOM workflows (validate-story, *-synthesis, code-review): Always
  use bundled (user's BMAD doesn't have these).
- STANDARD workflows (create-story, dev-story, retrospective, testarch-*):
  Prefer the user's BMAD installation, fall back to bundled.

Phase 4 introduces :func:`discover_workflow_source`, a layout-aware
variant that returns :class:`WorkflowSource` carrying the resolved
path together with the originating layout flavour
(``"new"`` | ``"old"`` | ``"bundled"`` | ``"override"``) and the
canonical ``bmad-`` skill id when discovery hit the v6.4+ layout.
The legacy :func:`discover_workflow_dir` is preserved as a thin
backward-compatible wrapper that returns just the path.
"""

import logging
from pathlib import Path

from bmad_assist.compiler.types import WorkflowSource
from bmad_assist.skill_layout import detect_layout
from bmad_assist.workflows import get_bundled_workflow_dir

logger = logging.getLogger(__name__)

# Workflows that are custom/modified by bmad-assist - ALWAYS use bundled
CUSTOM_WORKFLOWS = {
    "validate-story",  # Not in standard BMAD
    "validate-story-synthesis",  # Multi-LLM consolidation
    "code-review",  # Modified (2x larger than original)
    "code-review-synthesis",  # Multi-LLM consolidation
    "qa-plan-generate",  # QA module - not in standard BMAD
    "qa-plan-execute",  # QA module - not in standard BMAD
    "security-review",  # Security agent - CWE-based analysis
}

# Standard BMAD workflows - prefer user's installation, fallback to bundled
STANDARD_WORKFLOWS = {
    "create-story",
    "dev-story",
    "retrospective",
    # Testarch module (standard BMAD, not custom) - all 8 TEA workflows
    "testarch-atdd",
    "testarch-trace",
    "testarch-test-review",
    "testarch-automate",
    "testarch-ci",
    "testarch-framework",
    "testarch-nfr-assess",
    "testarch-test-design",
}

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

# Mapping from workflow name to v6.4+ skill id. Most skills follow the
# ``bmad-<workflow_name>`` convention; the few exceptions are listed
# explicitly.
WORKFLOW_TO_SKILL_ID = {
    "create-story": "bmad-create-story",
    "dev-story": "bmad-dev-story",
    "retrospective": "bmad-retrospective",
    "code-review": "bmad-code-review",
    "testarch-atdd": "bmad-testarch-atdd",
    "testarch-trace": "bmad-testarch-trace",
    "testarch-test-review": "bmad-testarch-test-review",
    "testarch-automate": "bmad-testarch-automate",
    "testarch-ci": "bmad-testarch-ci",
    "testarch-framework": "bmad-testarch-framework",
    "testarch-nfr-assess": "bmad-testarch-nfr",
    "testarch-test-design": "bmad-testarch-test-design",
    # Phase 3.5 orphans — bmad-assist-authored skill-layout ports.
    "validate-story": "bmad-validate-story",
    "validate-story-synthesis": "bmad-validate-story-synthesis",
    "qa-plan-generate": "bmad-qa-plan-generate",
    "qa-plan-execute": "bmad-qa-plan-execute",
    "code-review-synthesis": "bmad-code-review-synthesis",
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

    # Either workflow.yaml or workflow.md (or both) makes it valid
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
    """Probe the v6.4+ skill layout for ``workflow_name``.

    Returns a :class:`WorkflowSource` with ``layout="new"`` if a skill
    directory is found under ``.claude/skills`` or ``.agents/skills``.
    Returns ``None`` when no installed mirror exists.
    """
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
    """Discover a workflow's source directory with layout awareness.

    Probe order:

    1. **Project override** (``.bmad-assist/workflows/<name>/``) wins
       unconditionally for all workflows. Returns ``layout="override"``.
    2. **CUSTOM** workflows skip project probes and use the bundled
       copy. Returns ``layout="bundled"``.
    3. **STANDARD** workflows:
       - When the project's effective layout is ``"new"``, probe the
         v6.4+ skill mirrors first. On hit, returns ``layout="new"``.
       - Always also probe the legacy ``_bmad/...`` locations. On hit,
         returns ``layout="old"``.
       - Finally, fall back to the bundled copy. Returns
         ``layout="bundled"``.

    Args:
        workflow_name: Workflow name (e.g. ``"dev-story"``).
        project_root: Project root directory.
        layout: Optional layout override. When ``None`` (the default),
            :func:`detect_layout` is consulted to decide whether to
            probe the new layout. Pass ``"new"`` / ``"old"`` to force
            a particular branch (used by tests and the run command's
            ``--skill-layout`` flag).

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

    # 2. CUSTOM workflows: always bundled.
    if workflow_name in CUSTOM_WORKFLOWS:
        bundled = get_bundled_workflow_dir(workflow_name)
        if bundled is not None:
            logger.debug("Using bundled custom workflow: %s", workflow_name)
            return WorkflowSource(
                workflow_name=workflow_name,
                skill_id=None,
                layout="bundled",
                path=bundled,
            )
        logger.error("Bundled workflow missing: %s", workflow_name)
        return None

    # 3. STANDARD workflows: layout-aware probes.
    effective_layout = layout if layout is not None else detect_layout(project_root)

    if effective_layout == "new":
        new_source = _probe_new_layout(workflow_name, project_root)
        if new_source is not None:
            return new_source

    legacy = _probe_legacy_user_install(workflow_name, project_root)
    if legacy is not None:
        return WorkflowSource(
            workflow_name=workflow_name,
            skill_id=WORKFLOW_TO_SKILL_ID.get(workflow_name),
            layout="old",
            path=legacy,
        )

    bundled = get_bundled_workflow_dir(workflow_name)
    if bundled is not None:
        logger.info(
            "Using bundled fallback for %s (no BMAD installation found)",
            workflow_name,
        )
        return WorkflowSource(
            workflow_name=workflow_name,
            skill_id=WORKFLOW_TO_SKILL_ID.get(workflow_name),
            layout="bundled",
            path=bundled,
        )

    return None


def discover_workflow_dir(
    workflow_name: str,
    project_root: Path,
) -> Path | None:
    """Discover workflow directory using the legacy probe ordering.

    Backward-compatible wrapper around :func:`discover_workflow_source`
    that **always asks for the legacy layout**. This preserves the
    contract for existing callers (legacy compilers in
    ``bmad_assist.compiler.workflows.*``) which rely on the returned
    path containing ``workflow.yaml`` / ``workflow.md``. The v6.4+
    skill compilers do not use this function — they probe via
    :func:`bmad_assist.skill_layout.find_skill` directly.

    For CUSTOM workflows (validate-story, *-synthesis, code-review):
    - Always use bundled (user's BMAD doesn't have these)

    For STANDARD workflows:
    1. User's legacy BMAD installation (``_bmad/bmm/workflows/...``).
    2. Bundled fallback.

    Args:
        workflow_name: Workflow name (e.g., 'dev-story').
        project_root: Project root directory.

    Returns:
        Path to legacy workflow directory (containing ``workflow.yaml``
        / ``workflow.md``), or None if not found.

    """
    source = discover_workflow_source(workflow_name, project_root, layout="old")
    if source is None:
        return None
    return source.path


def get_workflow_not_found_message(workflow_name: str, project_root: Path) -> str:
    """Generate helpful error message when workflow not found."""
    is_custom = workflow_name in CUSTOM_WORKFLOWS

    if is_custom:
        return (
            f"Bundled workflow '{workflow_name}' not found!\n\n"
            f"This is a bmad-assist custom workflow that should be bundled.\n"
            f"Please reinstall: pip install -e .\n"
        )

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
    checked.append("(bundled package fallback)")

    return (
        f"Workflow '{workflow_name}' not found!\n\n"
        f"Checked locations:\n" + "\n".join(f"  - {loc}" for loc in checked) + "\n\n"
        f"To fix:\n"
        f"  1. Reinstall bmad-assist: pip install -e .\n"
        f"  2. Or install BMAD: Copy _bmad/ from github.com/bmad-code-org/BMAD-METHOD\n"
        f"  3. Or create override: .bmad-assist/workflows/{workflow_name}/"
    )
