"""Resolve skill-layout variable placeholders in ``SKILL.md`` bodies.

This is the Phase 2 substitution layer that the new compiler invokes
*before* handing the markdown body off to the existing patch system.
The substitution surface is intentionally narrow:

* Path-style aliases (``{skill-root}``, ``{project-root}``,
  ``{skill-name}``, ``{installed_path}``) — required by every skill we
  bundle.
* Customization-driven prose — the four ``{workflow.*}`` references
  that BMAD's SKILL.md uses inline (``persistent_facts``,
  ``activation_steps_prepend``, ``activation_steps_append``,
  ``on_complete``).
* Caller-provided ``extra_vars`` — the compiler-injected variables
  (sprint-status path, project-context path, git intelligence, etc.).

Tokens we don't recognize are **left intact on purpose**: many
references in a SKILL.md body (e.g. ``{epic_num}``, ``{epics_content}``)
are resolved later in the pipeline by the existing variable engine
or the LLM itself. Raising on them here would defeat the parallel
architecture.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .types import SkillDocument

logger = logging.getLogger(__name__)

# A variable reference: matches ``{name}`` and ``{ns.path.to.value}``.
# Names use ASCII letters, digits, dot, underscore, hyphen — same shape
# as the parser's :data:`_VARIABLE_REF_RE` so what the parser captures
# the resolver can replace.
_VARIABLE_REF_RE = re.compile(r"\{([a-zA-Z][a-zA-Z0-9._-]*)\}")


def resolve_skill_variables(
    document: SkillDocument,
    customization: dict[str, Any],
    project_root: Path,
    extra_vars: dict[str, str] | None = None,
) -> str:
    """Substitute the resolver's known tokens into ``document.raw_markdown``.

    Args:
        document: Parsed :class:`SkillDocument` whose body to substitute.
        customization: Merged customization dict (typically the output of
            :func:`bmad_assist.skill_layout.resolve_customization`). We
            traverse it for ``{workflow.*}``-style tokens.
        project_root: Project root used to expand ``{project-root}``.
        extra_vars: Caller-provided overrides — last-write-wins. These
            shadow the built-in tokens, so the compiler can inject
            variables (e.g. ``sprint_status_path``) that aren't part of
            the standard token set.

    Returns:
        The substituted markdown body (the full file contents,
        frontmatter included).

    """
    extras = dict(extra_vars or {})
    project_root_resolved = project_root.resolve()
    skill_root_resolved = document.skill_root.resolve()

    builtins: dict[str, str] = {
        "skill-root": str(skill_root_resolved),
        "project-root": str(project_root_resolved),
        "skill-name": skill_root_resolved.name,
        # Legacy alias: older SKILL.md files used ``{installed_path}`` in
        # the same role as ``{skill-root}``. The patch validator even
        # enforces that ``{installed_path}`` is gone post-compile, so we
        # substitute it here to avoid leaving leftovers.
        "installed_path": str(skill_root_resolved),
    }

    def replace(match: re.Match[str]) -> str:
        token = match.group(1)

        # 1) Caller-provided overrides win.
        if token in extras:
            return extras[token]

        # 2) Built-in path/identifier tokens.
        if token in builtins:
            return builtins[token]

        # 3) Dotted tokens addressing the customization dict.
        if "." in token:
            value = _lookup_dotted(customization, token)
            if value is _MISSING:
                logger.debug(
                    "skill-layout: leaving unresolved customization token {%s} intact",
                    token,
                )
                return match.group(0)
            return _render_for_prose(value)

        # 4) Unknown bare token — leave intact for downstream layers.
        logger.debug("skill-layout: leaving unknown token {%s} intact", token)
        return match.group(0)

    return _VARIABLE_REF_RE.sub(replace, document.raw_markdown)


# --------------------------------------------------------------------------- #
# Internals                                                                   #
# --------------------------------------------------------------------------- #


# Sentinel returned by :func:`_lookup_dotted` when the path doesn't resolve.
# Module-level so callers compare with ``is`` (mirrors the resolver's
# ``_MISSING`` convention).
_MISSING: Any = object()


def _lookup_dotted(data: Any, dotted: str) -> Any:
    """Return ``data[parts[0]][parts[1]]…`` or :data:`_MISSING`.

    Each part is matched against keys in dicts only. We never index
    into lists by integer because the customization shape is dict-of-
    dicts at every level we substitute.
    """
    current: Any = data
    for part in dotted.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


def _render_for_prose(value: Any) -> str:
    """Render a customization value as the prose substitution it replaces.

    The rendering rules match what BMAD's SKILL.md *appears* to expect
    when it inlines a ``{workflow.*}`` token mid-sentence:

    * ``None`` → empty string.
    * Scalars (``str``/``int``/``float``/``bool``) → their string form.
    * Lists → bullet list, one entry per line, prefixed with ``- ``.
      Nested complex entries are JSON-encoded so structure survives.
    * Dicts → ``key: value`` lines, one per key. Nested complex
      values are JSON-encoded.

    Empty containers render as the empty string so the surrounding
    prose collapses cleanly.
    """
    if value is None:
        return ""

    if isinstance(value, bool):
        # ``bool`` *is* an ``int`` to Python; check first.
        return "true" if value else "false"

    if isinstance(value, (str, int, float)):
        return str(value)

    if isinstance(value, list):
        if not value:
            return ""
        lines: list[str] = []
        for item in value:
            lines.append(f"- {_render_inline(item)}")
        return "\n".join(lines)

    if isinstance(value, dict):
        if not value:
            return ""
        lines = []
        for key, item in value.items():
            lines.append(f"{key}: {_render_inline(item)}")
        return "\n".join(lines)

    # Fall back to JSON for anything exotic so we don't silently lose
    # information.
    return json.dumps(value, sort_keys=True)


def _render_inline(value: Any) -> str:
    """Render ``value`` as a single-line string for prose embedding."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    # Lists/dicts inside a bullet/list item collapse to JSON so the
    # outer container's structure is preserved on a single line.
    return json.dumps(value, sort_keys=True)
