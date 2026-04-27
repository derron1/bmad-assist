"""Pure-Python reimplementation of BMAD's TOML customization resolvers.

Mirrors the behavior of:

* ``_bmad/scripts/resolve_customization.py`` (three-layer skill merge)
* ``_bmad/scripts/resolve_config.py`` (four-layer central-config merge)

The merge algorithm is shape-aware:

* Two dicts deep-merge.
* Two lists merge by ``code`` or ``id`` when *every* item across base
  and override carries that same identifier; otherwise they concatenate.
* Anything else: the override wins.

We re-implement rather than subprocess so we don't pay a Python startup
penalty per workflow load. Parity tests in
``tests/skill_layout/test_resolver_parity.py`` keep this honest.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

from .errors import ResolverError

logger = logging.getLogger(__name__)

_KEYED_MERGE_FIELDS: tuple[str, ...] = ("code", "id")
_MISSING = object()


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


def resolve_customization(
    skill_root: Path,
    project_root: Path | None = None,
    keys: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve a skill's customization stack to a single merged dict.

    Layers are merged in priority order (highest priority last):

    1. ``{skill_root}/customize.toml`` — required defaults.
    2. ``{project_root}/_bmad/custom/{skill-name}.toml`` — team overrides.
    3. ``{project_root}/_bmad/custom/{skill-name}.user.toml`` — personal overrides.

    ``{skill-name}`` is the basename of ``skill_root`` (we keep the
    ``bmad-`` prefix; the upstream resolver does the same).

    Args:
        skill_root: Absolute path to the skill directory holding
            ``customize.toml``.
        project_root: Project root used to locate the override files. If
            ``None``, we walk up from ``skill_root`` (then from CWD) to
            find a directory containing ``_bmad/`` or ``.git/``.
        keys: Optional list of dotted key paths to extract from the
            merged result (e.g. ``["workflow", "agent.menu"]``). Missing
            keys are silently omitted, matching the BMAD script.

    Returns:
        The merged customization (full dict if ``keys`` is ``None``,
        else a dict containing only the keys that resolved).

    Raises:
        ResolverError: If ``customize.toml`` is missing or malformed.

    """
    skill_root = skill_root.resolve()
    skill_name = skill_root.name
    defaults_path = skill_root / "customize.toml"

    defaults = _load_toml(defaults_path, required=True)

    if project_root is None:
        project_root = _find_project_root(skill_root) or _find_project_root(Path.cwd())
    else:
        project_root = project_root.resolve()

    team: dict[str, Any] = {}
    user: dict[str, Any] = {}
    if project_root is not None:
        custom_dir = project_root / "_bmad" / "custom"
        team = _load_toml(custom_dir / f"{skill_name}.toml")
        user = _load_toml(custom_dir / f"{skill_name}.user.toml")

    merged = deep_merge(defaults, team)
    merged = deep_merge(merged, user)

    return _filter_keys(merged, keys)


def resolve_central_config(
    project_root: Path,
    keys: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve BMAD's central config to a single merged dict.

    Layers (highest priority last):

    1. ``{project_root}/_bmad/config.toml`` — required, installer-owned.
    2. ``{project_root}/_bmad/config.user.toml`` — installer-owned, per-user.
    3. ``{project_root}/_bmad/custom/config.toml`` — committed team overrides.
    4. ``{project_root}/_bmad/custom/config.user.toml`` — gitignored personal overrides.

    Args:
        project_root: Project root containing the ``_bmad/`` directory.
        keys: Optional list of dotted key paths; same semantics as
            :func:`resolve_customization`.

    Returns:
        The merged central config.

    Raises:
        ResolverError: If the required ``_bmad/config.toml`` is missing
            or malformed.

    """
    project_root = project_root.resolve()
    bmad_dir = project_root / "_bmad"

    base_team = _load_toml(
        bmad_dir / "config.toml",
        required=True,
        missing_label="config file",
    )
    base_user = _load_toml(bmad_dir / "config.user.toml")
    custom_team = _load_toml(bmad_dir / "custom" / "config.toml")
    custom_user = _load_toml(bmad_dir / "custom" / "config.user.toml")

    merged = deep_merge(base_team, base_user)
    merged = deep_merge(merged, custom_team)
    merged = deep_merge(merged, custom_user)

    return _filter_keys(merged, keys)


# --------------------------------------------------------------------------- #
# Merge primitives — kept module-level so tests can exercise them directly.   #
# --------------------------------------------------------------------------- #


def deep_merge(base: Any, override: Any) -> Any:
    """Recursively merge ``override`` into ``base`` using BMAD rules.

    * Two dicts → recursive deep merge.
    * Two lists → :func:`merge_arrays` (keyed merge if every item carries
      ``code`` or ``id``, else concat).
    * Anything else → override wins.
    """
    if isinstance(base, dict) and isinstance(override, dict):
        result: dict[str, Any] = dict(base)
        for key, over_val in override.items():
            if key in result:
                result[key] = deep_merge(result[key], over_val)
            else:
                result[key] = over_val
        return result
    if isinstance(base, list) and isinstance(override, list):
        return merge_arrays(base, override)
    return override


def merge_arrays(base: list[Any], override: list[Any]) -> list[Any]:
    """Merge two lists using BMAD's shape-aware rules.

    If every item across ``base + override`` is a dict and they all
    share the *same* identifier field (``code`` first, then ``id``),
    items with matching identifiers replace base entries while new
    identifiers are appended. Otherwise the lists are concatenated.
    """
    base_arr = base if isinstance(base, list) else []
    override_arr = override if isinstance(override, list) else []
    keyed_field = _detect_keyed_merge_field(base_arr + override_arr)
    if keyed_field is not None:
        return _merge_by_key(base_arr, override_arr, keyed_field)
    return base_arr + override_arr


def extract_key(data: Any, dotted_key: str) -> Any:
    """Return the value at ``dotted_key`` in ``data``, or ``_MISSING``.

    Mirrors :func:`resolve_customization.extract_key` from the BMAD
    script. Returns the module-level ``_MISSING`` sentinel when the
    path cannot be traversed; callers should compare with ``is``.
    """
    parts = dotted_key.split(".")
    current: Any = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


# --------------------------------------------------------------------------- #
# Internal helpers                                                            #
# --------------------------------------------------------------------------- #


def _detect_keyed_merge_field(items: list[Any]) -> str | None:
    """Return ``'code'`` or ``'id'`` if every item shares that identifier.

    Mixed arrays (some items use ``code``, others use ``id``) and
    arrays containing non-dict items return ``None`` and fall through
    to append semantics — matching the upstream resolver exactly.
    """
    if not items or not all(isinstance(item, dict) for item in items):
        return None
    for candidate in _KEYED_MERGE_FIELDS:
        if all(item.get(candidate) is not None for item in items):
            return candidate
    return None


def _merge_by_key(base: list[Any], override: list[Any], key_name: str) -> list[Any]:
    """Replicate the BMAD script's ``_merge_by_key`` exactly."""
    result: list[Any] = []
    index_by_key: dict[Any, int] = {}

    for item in base:
        if not isinstance(item, dict):
            continue
        if item.get(key_name) is not None:
            index_by_key[item[key_name]] = len(result)
        result.append(dict(item))

    for item in override:
        if not isinstance(item, dict):
            result.append(item)
            continue
        key = item.get(key_name)
        if key is not None and key in index_by_key:
            result[index_by_key[key]] = dict(item)
        else:
            if key is not None:
                index_by_key[key] = len(result)
            result.append(dict(item))

    return result


def _load_toml(
    file_path: Path,
    required: bool = False,
    missing_label: str = "customization file",
) -> dict[str, Any]:
    """Load a TOML file with the BMAD script's error semantics.

    Required-but-missing files raise :class:`ResolverError`. Optional
    files that are missing return an empty dict silently. Optional
    files that fail to parse log a warning and return an empty dict
    (matching the script's stderr-based "warning"). Required files that
    fail to parse raise :class:`ResolverError`.

    ``missing_label`` controls the wording of the not-found error so
    that central-config and skill-customization callers each get a
    message that matches the BMAD script they replace.
    """
    if not file_path.exists():
        if required:
            raise ResolverError(f"required {missing_label} not found: {file_path}")
        return {}

    try:
        with file_path.open("rb") as handle:
            parsed = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        if required:
            raise ResolverError(f"failed to parse {file_path}: {exc}") from exc
        logger.warning("failed to parse %s: %s", file_path, exc)
        return {}
    except OSError as exc:
        if required:
            raise ResolverError(f"failed to read {file_path}: {exc}") from exc
        logger.warning("failed to read %s: %s", file_path, exc)
        return {}

    if not isinstance(parsed, dict):
        if required:
            raise ResolverError(f"{file_path} did not parse to a table")
        return {}
    return parsed


def _find_project_root(start: Path) -> Path | None:
    """Walk up from ``start`` looking for ``_bmad/`` or ``.git/``.

    Mirrors the BMAD script's ``find_project_root`` (lines 56-64). Stops
    at the filesystem root and returns ``None`` if nothing matches.
    """
    current = start.resolve()
    while True:
        if (current / "_bmad").exists() or (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _filter_keys(merged: dict[str, Any], keys: list[str] | None) -> dict[str, Any]:
    """Apply the ``--key`` filter the same way the BMAD script does."""
    if not keys:
        return merged
    output: dict[str, Any] = {}
    for key in keys:
        value = extract_key(merged, key)
        if value is not _MISSING:
            output[key] = value
    return output
