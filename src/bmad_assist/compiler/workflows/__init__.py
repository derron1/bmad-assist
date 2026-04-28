"""Legacy workflow-specific compiler modules.

After Phase 6 of the v6.4+ skill-layout refactor, the modules in this
package are kept as **private support** for the skill-layout compilers
under :mod:`bmad_assist.compiler.skills`. They are no longer the
primary routing target — :func:`bmad_assist.compiler.get_workflow_compiler`
always returns a skill-layout compiler, which in turn delegates to one
of these classes for the workflow-specific tail of compilation
(context-file building, mission, XML output).

Available workflows (private):
- create_story
- validate_story
- validate_story_synthesis
- code_review
- code_review_synthesis
- dev_story
- retrospective
- qa_plan_generate
- qa_plan_execute
- security_review

TEA Enterprise testarch workflows (tri-modal architecture):
- testarch_atdd
- testarch_automate
- testarch_ci
- testarch_framework
- testarch_nfr_assess
- testarch_test_design
- testarch_test_review
- testarch_trace

Helper:
- :func:`resolve_legacy_workflow_dir` — replacement for the deleted
  ``discover_workflow_dir``. Probes the project's ``_bmad/...`` install
  first, then falls back to the bundled skill source under
  :mod:`bmad_assist.skills` so the existence-check inside legacy
  ``get_workflow_dir`` keeps returning a real on-disk path.
"""

from __future__ import annotations

from pathlib import Path

from bmad_assist.compiler.workflow_discovery import (
    WORKFLOW_TO_SKILL_ID,
    _probe_legacy_user_install,
)


def resolve_legacy_workflow_dir(
    workflow_name: str,
    project_root: Path,
) -> Path | None:
    """Return a real on-disk directory for the legacy compiler's bookkeeping.

    Phase 6 removed the bundled ``src/bmad_assist/workflows/<name>/`` tree,
    so legacy compilers no longer have a guaranteed bundled fallback for
    ``workflow.yaml``/``instructions.xml``. The skill-layout compiler that
    wraps each legacy compiler pre-populates ``context.workflow_ir`` from
    the SKILL.md bundle, so the legacy ``compile()`` body never reads
    ``workflow.yaml`` from this path. The path is still used for
    existence checks (``workflow_dir.exists()``), debug logging, and the
    ``checklist.md`` lookup inside ``validate_story``.

    Probe order:

    1. Project's legacy ``_bmad/...`` install (still supported).
    2. Bundled v6.4+ skill source at
       ``src/bmad_assist/skills/<bmad-id>/`` — this directory exists on
       every install and lets ``Path.exists()`` checks succeed without
       referencing the deleted ``workflows/`` bundle.

    Returns ``None`` only when the workflow name has no skill-layout
    counterpart **and** no ``_bmad/...`` install is present.
    """
    legacy = _probe_legacy_user_install(workflow_name, project_root)
    if legacy is not None:
        return legacy

    skill_id = WORKFLOW_TO_SKILL_ID.get(workflow_name)
    if skill_id is None:
        return None

    try:
        from importlib.resources import files

        bundled = Path(str(files("bmad_assist.skills"))) / skill_id
    except Exception:
        bundled = Path(__file__).parent.parent.parent / "skills" / skill_id

    if bundled.is_dir():
        return bundled
    return None


__all__ = ["resolve_legacy_workflow_dir"]
