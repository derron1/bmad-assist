"""Read and index ``_bmad/_config/skill-manifest.csv``.

The manifest is the ground-truth mapping between skill IDs and their
canonical on-disk paths. The probe layer falls back to it when the
``.claude/skills`` and ``.agents/skills`` mirrors don't carry the
requested skill.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path

from .errors import ManifestError
from .types import ManifestSkill

# Manifest column candidates. The CSV header drives selection (we don't
# hard-code positions), but we accept either of these aliases for each
# logical column. The first match wins.
_NAME_COLUMNS: tuple[str, ...] = ("name", "skill_id", "canonicalId")
_MODULE_COLUMNS: tuple[str, ...] = ("module",)
_PATH_COLUMNS: tuple[str, ...] = ("path", "canonical_path")
_DESCRIPTION_COLUMNS: tuple[str, ...] = ("description",)


def read_skill_manifest(project_root: Path) -> dict[str, ManifestSkill]:
    """Load ``_bmad/_config/skill-manifest.csv`` from ``project_root``.

    Args:
        project_root: Project root containing ``_bmad/_config/skill-manifest.csv``.

    Returns:
        Dictionary mapping ``skill_id`` to :class:`ManifestSkill`.

    Raises:
        ManifestError: If the file is missing, unreadable, or lacks one
            of the four required logical columns.

    """
    manifest_path = project_root / "_bmad" / "_config" / "skill-manifest.csv"
    if not manifest_path.exists():
        raise ManifestError(f"skill manifest not found: {manifest_path}")

    try:
        with manifest_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames
            if not fieldnames:
                raise ManifestError(f"{manifest_path}: missing CSV header")

            name_col = _pick_column(fieldnames, _NAME_COLUMNS, manifest_path, "skill name")
            module_col = _pick_column(fieldnames, _MODULE_COLUMNS, manifest_path, "module")
            path_col = _pick_column(fieldnames, _PATH_COLUMNS, manifest_path, "canonical path")
            description_col = _pick_column(
                fieldnames, _DESCRIPTION_COLUMNS, manifest_path, "description"
            )

            skills: dict[str, ManifestSkill] = {}
            for row_no, row in enumerate(reader, start=2):
                skill_id = (row.get(name_col) or "").strip()
                if not skill_id:
                    # Empty/blank rows are tolerated silently — the upstream
                    # CSV occasionally accumulates trailing newlines.
                    continue
                skills[skill_id] = ManifestSkill(
                    skill_id=skill_id,
                    module=(row.get(module_col) or "").strip(),
                    canonical_path=(row.get(path_col) or "").strip(),
                    description=(row.get(description_col) or "").strip(),
                )
            return skills
    except OSError as exc:
        raise ManifestError(f"failed to read {manifest_path}: {exc}") from exc


def _pick_column(
    fieldnames: Sequence[str] | None,
    candidates: tuple[str, ...],
    manifest_path: Path,
    logical_name: str,
) -> str:
    """Return the first matching column name from ``candidates``.

    Raises :class:`ManifestError` if no candidate is present.
    """
    available = list(fieldnames or ())
    for candidate in candidates:
        if candidate in available:
            return candidate
    raise ManifestError(
        f"{manifest_path}: missing column for {logical_name} "
        f"(tried {list(candidates)}; header has {available})"
    )
