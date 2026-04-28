"""Workflow notification label resolution system.

This module provides workflow label and icon resolution for notification displays.
It supports explicit configuration from workflow YAML files, predefined labels for
known workflows, and intelligent fallback via pattern matching and smart truncation.

Resolution order:
1. Predefined registry (hardcoded, O(1) lookup)
2. v6.4+ ``SKILL.md`` ``description`` frontmatter (probed in
   ``.claude/skills/bmad-<name>/`` → ``.agents/skills/bmad-<name>/`` →
   bundled ``src/bmad_assist/skills/bmad-<name>/``)
3. Legacy workflow YAML ``notification.icon`` / ``notification.label``
   fields (still consulted for projects with a ``_bmad/...`` install)
4. Pattern-based default icons (matching workflow name patterns)
5. Smart-truncated name with neutral icon (fallback)

Example:
    >>> from bmad_assist.notifications import (
    ...     get_workflow_icon,
    ...     get_workflow_label,
    ...     get_workflow_notification_config,
    ... )
    >>> get_workflow_icon("create-story")
    '📝'
    >>> get_workflow_label("create-story")
    'Create'
    >>> get_workflow_label("very-long-workflow-name")
    'Very-long-w…'

"""

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# v6.4+ skill mirror prefixes (relative to project root). Probed in
# order before the bundled fallback under ``src/bmad_assist/skills``.
_SKILL_LAYOUT_PROJECT_PREFIXES = (".claude/skills", ".agents/skills")

__all__ = [
    "WorkflowNotificationConfig",
    "get_workflow_icon",
    "get_workflow_label",
    "get_workflow_notification_config",
    "clear_workflow_label_cache",
]

# Maximum label length (including ellipsis if truncated)
MAX_LABEL_LENGTH = 16

# Default icon for workflows with no pattern match
DEFAULT_ICON = "📋"

# Icon patterns - order matters, first match wins.
# CRITICAL: "synth" must be before "dev" to correctly match *synthesis* workflows
ICON_PATTERNS: list[tuple[str, str]] = [
    ("create", "📝"),
    ("valid", "🔍"),
    ("synth", "🔄"),  # Must be before "dev" to catch *synthesis*
    ("dev", "💻"),
    ("review", "👀"),
    ("test", "🧪"),
    ("plan", "📋"),
    ("retro", "📊"),
]

# Predefined labels for known workflows (from Epic 21 spec)
# These provide O(1) lookup without YAML parsing
PREDEFINED_LABELS: dict[str, "WorkflowNotificationConfig"] = {}


@dataclass(frozen=True)
class WorkflowNotificationConfig:
    """Notification display configuration for a workflow.

    Attributes:
        icon: Single emoji for visual identification.
        label: Short display name (max 16 chars).

    """

    icon: str
    label: str


def _initialize_predefined_labels() -> None:
    """Initialize predefined labels registry."""
    global PREDEFINED_LABELS

    predefined = {
        # Core workflows
        "create-story": WorkflowNotificationConfig("📝", "Create"),
        "validate-story": WorkflowNotificationConfig("🔍", "Validate"),
        "validate-story-synthesis": WorkflowNotificationConfig("🔍", "Val-Synth"),
        "dev-story": WorkflowNotificationConfig("💻", "Develop"),
        "code-review": WorkflowNotificationConfig("👀", "Review"),
        "code-review-synthesis": WorkflowNotificationConfig("👀", "Rev-Synth"),
        "retrospective": WorkflowNotificationConfig("📊", "Retro"),
        "sprint-planning": WorkflowNotificationConfig("📋", "Sprint"),
        # Story lifecycle events (from EventType)
        "story-started": WorkflowNotificationConfig("🚀", "Story"),
        "story-completed": WorkflowNotificationConfig("✅", "Story"),
        # TEA module workflows (keys match Phase enum values after _phase_to_workflow_name conversion)
        "tea-framework": WorkflowNotificationConfig("🧪", "Framework"),
        "tea-nfr-assess": WorkflowNotificationConfig("🧪", "NFR"),
        "tea-test-design": WorkflowNotificationConfig("🧪", "TestDesign"),
        "tea-ci": WorkflowNotificationConfig("🧪", "CI"),
        "tea-trace": WorkflowNotificationConfig("🧪", "Trace"),
        "tea-automate": WorkflowNotificationConfig("🧪", "Automate"),
        "atdd": WorkflowNotificationConfig("🧪", "ATDD"),
        "test-review": WorkflowNotificationConfig("🧪", "TestReview"),
    }

    PREDEFINED_LABELS = predefined


# Initialize on module load
_initialize_predefined_labels()

# Module-level cache for workflow notification configs
_label_cache: dict[str, WorkflowNotificationConfig] = {}
_cache_lock = threading.Lock()


def _match_icon_pattern(workflow_name: str) -> str:
    """Match workflow name against icon patterns.

    Patterns are matched in order (first match wins). The matching is
    case-insensitive and uses substring matching.

    Args:
        workflow_name: Workflow identifier (case-insensitive).

    Returns:
        Matched icon or DEFAULT_ICON if no pattern matches.

    Examples:
        >>> _match_icon_pattern("create-story")
        '📝'
        >>> _match_icon_pattern("code-review-synthesis")
        '🔄'
        >>> _match_icon_pattern("unknown-workflow")
        '🔷'

    """
    name_lower = workflow_name.lower()
    for pattern, icon in ICON_PATTERNS:
        if pattern in name_lower:
            return icon
    return DEFAULT_ICON


def _strip_module_prefix(workflow_name: str) -> str:
    """Strip module prefix from workflow name.

    Module prefixes are separated by ":" (e.g., "testarch:nfr").
    Only the first ":" is considered as the separator.

    Args:
        workflow_name: Full workflow name (e.g., "testarch:nfr").

    Returns:
        Name without prefix (e.g., "nfr").

    Examples:
        >>> _strip_module_prefix("testarch:nfr")
        'nfr'
        >>> _strip_module_prefix("create-story")
        'create-story'
        >>> _strip_module_prefix("a:b:c")
        'b:c'

    """
    if ":" in workflow_name:
        return workflow_name.split(":", 1)[1]
    return workflow_name


def _smart_truncate_label(name: str, max_len: int = MAX_LABEL_LENGTH) -> str:
    """Smart truncate and format label.

    Processing steps:
    1. Strip module prefix (if present)
    2. Capitalize first letter (preserve rest as-is)
    3. Truncate with ellipsis if exceeds max_len

    Args:
        name: Raw workflow name.
        max_len: Maximum label length (default 16).

    Returns:
        Formatted display label (max max_len chars).

    Examples:
        >>> _smart_truncate_label("create-story")
        'Create-story'
        >>> _smart_truncate_label("testarch:nfr")
        'Nfr'
        >>> _smart_truncate_label("very-long-workflow-name")
        'Very-long-w…'

    """
    # Strip module prefix
    base_name = _strip_module_prefix(name)

    # Handle empty base name edge case
    if not base_name:
        return ""

    # Capitalize first letter (preserve rest as-is)
    label = base_name[0].upper() + base_name[1:]

    # Truncate if needed (max_len - 1 chars + ellipsis)
    if len(label) > max_len:
        return label[: max_len - 1] + "…"

    return label


def _find_workflow_yaml_paths(workflow_name: str) -> list[Path]:
    """Find possible workflow.yaml paths for a workflow.

    Searches in standard BMAD workflow locations:
    - _bmad/bmm/workflows/4-implementation/{workflow}/workflow.yaml
    - _bmad/bmm/workflows/{workflow}/workflow.yaml
    - .bmad/bmm/workflows/4-implementation/{workflow}/workflow.yaml (legacy)

    For module workflows (e.g., "testarch:nfr"):
    - _bmad/bmm/workflows/{module}/{workflow}/workflow.yaml

    Args:
        workflow_name: Workflow identifier.

    Returns:
        List of possible Path objects to workflow.yaml files.

    """
    # Use CWD as project root - standard pattern in bmad-assist
    # YAML discovery is a fallback for custom workflows, so CWD is appropriate
    project_root = Path.cwd()

    # Handle module prefixed workflows (e.g., "testarch:nfr" -> module=testarch, name=nfr)
    if ":" in workflow_name:
        module, name = workflow_name.split(":", 1)
        search_paths = [
            project_root / "_bmad" / "bmm" / "workflows" / module / name / "workflow.yaml",
            project_root / ".bmad" / "bmm" / "workflows" / module / name / "workflow.yaml",
        ]
    else:
        # Standard workflow search
        impl = "4-implementation"
        search_paths = [
            project_root / "_bmad" / "bmm" / "workflows" / impl / workflow_name / "workflow.yaml",
            project_root / "_bmad" / "bmm" / "workflows" / workflow_name / "workflow.yaml",
            project_root / ".bmad" / "bmm" / "workflows" / impl / workflow_name / "workflow.yaml",
            project_root / ".bmad" / "bmm" / "workflows" / workflow_name / "workflow.yaml",
        ]

    return search_paths


def _load_workflow_notification_config(workflow_name: str) -> WorkflowNotificationConfig | None:
    """Attempt to load notification config from workflow.yaml.

    This is a fallback for workflows not in the predefined registry.

    Args:
        workflow_name: Workflow identifier.

    Returns:
        WorkflowNotificationConfig if found in YAML, None otherwise.
        Returns None on any error (malformed YAML, file not found, permission denied).

    """
    search_paths = _find_workflow_yaml_paths(workflow_name)

    for workflow_yaml in search_paths:
        try:
            if not workflow_yaml.exists():
                continue

            with open(workflow_yaml) as f:
                data = yaml.safe_load(f)

            if not data or not isinstance(data, dict):
                continue

            notification = data.get("notification", {})
            if not notification or not isinstance(notification, dict):
                continue

            # Check label (with short as alias for backward compatibility)
            label = notification.get("label") or notification.get("short")
            icon = notification.get("icon")

            if not label and not icon:
                continue

            # Enforce max label length per AC1 (truncate YAML-provided labels)
            if label and len(label) > MAX_LABEL_LENGTH:
                label = label[: MAX_LABEL_LENGTH - 1] + "…"

            # Build config with fallbacks for missing fields
            return WorkflowNotificationConfig(
                icon=icon or _match_icon_pattern(workflow_name),
                label=label or _smart_truncate_label(workflow_name),
            )

        except yaml.YAMLError as e:
            logger.warning(f"Failed to parse workflow YAML for {workflow_name}: {e}")
            continue
        except OSError as e:
            logger.warning(f"Failed to read workflow file for {workflow_name}: {e}")
            continue
        except PermissionError as e:
            logger.warning(f"Permission denied reading workflow for {workflow_name}: {e}")
            continue

    return None


def _canonical_skill_id(workflow_name: str) -> str:
    """Map a workflow name to its canonical ``bmad-`` skill id.

    Module-prefixed names (``"testarch:nfr"``) collapse to their
    base name before prefixing. Names that already start with
    ``bmad-`` are returned unchanged.
    """
    base = _strip_module_prefix(workflow_name)
    if base.startswith("bmad-"):
        return base
    return f"bmad-{base}"


def _find_skill_md_paths(workflow_name: str) -> list[Path]:
    """Find possible ``SKILL.md`` paths for a v6.4+ workflow.

    Probes (in order):

    1. ``<cwd>/.claude/skills/bmad-<name>/SKILL.md``
    2. ``<cwd>/.agents/skills/bmad-<name>/SKILL.md``
    3. Bundled ``src/bmad_assist/skills/bmad-<name>/SKILL.md``

    Args:
        workflow_name: Workflow identifier (e.g. ``"create-story"``).

    Returns:
        Ordered list of candidate ``SKILL.md`` paths. Non-existent
        paths are still returned — the caller filters them.

    """
    project_root = Path.cwd()
    skill_id = _canonical_skill_id(workflow_name)

    candidates = [
        project_root / prefix / skill_id / "SKILL.md" for prefix in _SKILL_LAYOUT_PROJECT_PREFIXES
    ]

    # Bundled fallback — always last so project installs win.
    try:
        from importlib.resources import files

        bundled = Path(str(files("bmad_assist.skills"))) / skill_id / "SKILL.md"
    except (ModuleNotFoundError, TypeError, ValueError):
        # Defensive fallback if importlib.resources is unhappy
        # (e.g. namespace packages, frozen builds).
        bundled = Path(__file__).parent.parent / "skills" / skill_id / "SKILL.md"
    candidates.append(bundled)

    return candidates


def _load_skill_md_notification_config(
    workflow_name: str,
) -> WorkflowNotificationConfig | None:
    """Resolve a notification config from a v6.4+ ``SKILL.md`` description.

    Uses :func:`bmad_assist.skill_layout.parse_skill` to read the
    structured frontmatter. The ``description`` field becomes the
    label (smart-truncated to the configured maximum); the icon is
    derived via pattern match on the workflow name. Returns ``None``
    on any error (missing file, malformed frontmatter, missing
    description).

    """
    from bmad_assist.skill_layout import parse_skill
    from bmad_assist.skill_layout.errors import MalformedSkill

    for candidate in _find_skill_md_paths(workflow_name):
        if not candidate.is_file():
            continue
        try:
            doc = parse_skill(candidate)
        except MalformedSkill as exc:
            logger.debug("SKILL.md probe skipped %s: %s", candidate, exc)
            continue

        description = doc.frontmatter.description.strip()
        if not description:
            continue

        # The frontmatter description is a free-form sentence; we
        # smart-truncate it the same way fallback labels are formatted
        # so the notification line stays bounded.
        label = _smart_truncate_label(description)
        if not label:
            continue

        return WorkflowNotificationConfig(
            icon=_match_icon_pattern(workflow_name),
            label=label,
        )

    return None


def _compute_config(workflow_name: str) -> WorkflowNotificationConfig:
    """Compute notification config for a workflow.

    Resolution order:
    1. Predefined registry (``PREDEFINED_LABELS``)
    2. v6.4+ ``SKILL.md`` frontmatter description
    3. Legacy workflow YAML ``notification.*`` fields
    4. Pattern matching + truncation fallback

    Args:
        workflow_name: Workflow identifier.

    Returns:
        WorkflowNotificationConfig with resolved icon and label.

    """
    # 1. Check predefined registry first (O(1) lookup)
    if workflow_name in PREDEFINED_LABELS:
        return PREDEFINED_LABELS[workflow_name]

    # 2. v6.4+ SKILL.md description (the post-Phase-7 truth for v6.4+
    # users; the bundled fallback covers fresh installs that haven't
    # run ``bmad-assist init`` yet).
    skill_config = _load_skill_md_notification_config(workflow_name)
    if skill_config is not None:
        return skill_config

    # 3. Try to load from legacy workflow YAML (``_bmad/...`` installs).
    yaml_config = _load_workflow_notification_config(workflow_name)
    if yaml_config is not None:
        return yaml_config

    # 4. Fallback: pattern matching + smart truncation
    return WorkflowNotificationConfig(
        icon=_match_icon_pattern(workflow_name),
        label=_smart_truncate_label(workflow_name),
    )


def _get_or_compute_config(workflow_name: str) -> WorkflowNotificationConfig:
    """Get config from cache or compute and cache it.

    Thread-safe implementation using double-checked locking pattern.

    Args:
        workflow_name: Workflow identifier.

    Returns:
        WorkflowNotificationConfig from cache or freshly computed.

    """
    # Fast path: check cache without lock
    if workflow_name in _label_cache:
        return _label_cache[workflow_name]

    # Slow path: acquire lock, check again, compute if needed
    with _cache_lock:
        # Double-check after acquiring lock
        if workflow_name in _label_cache:
            return _label_cache[workflow_name]

        config = _compute_config(workflow_name)
        _label_cache[workflow_name] = config
        return config


def get_workflow_icon(workflow_name: str) -> str:
    """Get notification icon for workflow.

    Resolution order:
    1. Predefined registry
    2. Workflow YAML notification.icon
    3. Pattern match on name
    4. Default neutral icon (🔷)

    Uses caching for O(1) lookups after first access.

    Args:
        workflow_name: Workflow identifier (e.g., "create-story", "testarch:nfr").

    Returns:
        Single emoji icon.

    Examples:
        >>> get_workflow_icon("create-story")
        '📝'
        >>> get_workflow_icon("code-review-synthesis")
        '🔄'
        >>> get_workflow_icon("unknown-workflow")
        '🔷'

    """
    return _get_or_compute_config(workflow_name).icon


def get_workflow_label(workflow_name: str) -> str:
    """Get notification label for workflow.

    Resolution order:
    1. Predefined registry
    2. Workflow YAML notification.label (or notification.short alias)
    3. Smart-truncated name (16 chars max with "…" if truncated)

    Uses caching for O(1) lookups after first access.

    Args:
        workflow_name: Workflow identifier (e.g., "create-story", "testarch:nfr").

    Returns:
        Display label (max 16 chars, may have ellipsis).

    Examples:
        >>> get_workflow_label("create-story")
        'Create'
        >>> get_workflow_label("validate-story-synthesis")
        'Val-Synth'
        >>> get_workflow_label("some-unknown-long-workflow")
        'Some-unknow…'

    """
    return _get_or_compute_config(workflow_name).label


def get_workflow_notification_config(workflow_name: str) -> WorkflowNotificationConfig:
    """Get full notification config for workflow.

    Uses caching for O(1) lookups after first access.

    Args:
        workflow_name: Workflow identifier.

    Returns:
        WorkflowNotificationConfig with icon and label.

    Examples:
        >>> config = get_workflow_notification_config("create-story")
        >>> config.icon
        '📝'
        >>> config.label
        'Create'

    """
    return _get_or_compute_config(workflow_name)


def clear_workflow_label_cache() -> None:
    """Clear the workflow label cache (for testing).

    Thread-safe operation that clears all cached configs.

    """
    with _cache_lock:
        _label_cache.clear()
