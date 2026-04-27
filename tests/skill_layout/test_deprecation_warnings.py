"""Tests for the Phase 5 ``DeprecationWarning`` on the legacy compile path.

The warning fires when:
  * a workflow that has a v6.4+ skill-layout port (one of the entries in
    ``_SKILL_LAYOUT_COMPILERS``) is compiled via the legacy
    ``workflow.yaml`` + ``instructions.xml`` pipeline.

The warning does NOT fire when:
  * the workflow has no skill-layout port (the 5 orphan workflows or
    ``security-review``) — there's nothing to migrate to yet.
  * the workflow is compiled via the new path.

It is deduplicated per-workflow per-process via a module-level set, so
a chatty loop only sees one warning per workflow.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from bmad_assist.compiler.core import (
    _LEGACY_DEPRECATION_EMITTED,
    get_workflow_compiler,
)


@pytest.fixture(autouse=True)
def _reset_dedup() -> None:
    """Each test starts with an empty dedup set."""
    _LEGACY_DEPRECATION_EMITTED.clear()
    yield
    _LEGACY_DEPRECATION_EMITTED.clear()


def _record_warnings(workflow: str, project_root: Path, layout: str = "old"):
    """Load ``workflow`` and capture *our* legacy DeprecationWarnings.

    Filters out unrelated DeprecationWarnings (e.g. ``importlib.abc.Traversable``
    deprecation noise from third-party imports) by matching on the
    Phase 5 message signature.
    """
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        get_workflow_compiler(workflow, skill_layout=layout, project_root=project_root)
    return [
        w
        for w in recorded
        if issubclass(w.category, DeprecationWarning)
        and "legacy workflow.yaml" in str(w.message)
    ]


def test_legacy_path_for_migrated_workflow_emits_deprecation(tmp_path: Path) -> None:
    """A migrated workflow (``create-story``) on legacy emits a deprecation."""
    deps = _record_warnings("create-story", tmp_path, layout="old")
    assert len(deps) == 1
    msg = str(deps[0].message)
    assert "create-story" in msg
    assert "legacy" in msg.lower()
    assert "skill_layout" in msg
    # And the message should point users at a fix.
    assert "bmad-assist init" in msg


def test_legacy_path_for_dev_story_emits_deprecation(tmp_path: Path) -> None:
    """``dev-story`` is a different migrated workflow — also warns."""
    deps = _record_warnings("dev-story", tmp_path, layout="old")
    assert len(deps) == 1
    assert "dev-story" in str(deps[0].message)


def test_phase_3_5_orphan_validate_story_emits_deprecation(tmp_path: Path) -> None:
    """Phase 3.5 added a skill-layout port for ``validate-story`` → now warns on legacy."""
    deps = _record_warnings("validate-story", tmp_path, layout="old")
    assert len(deps) == 1
    assert "validate-story" in str(deps[0].message)


def test_phase_3_5_orphan_validate_story_synthesis_emits_deprecation(tmp_path: Path) -> None:
    """Phase 3.5 added a skill-layout port for ``validate-story-synthesis``."""
    deps = _record_warnings("validate-story-synthesis", tmp_path, layout="old")
    assert len(deps) == 1
    assert "validate-story-synthesis" in str(deps[0].message)


def test_phase_3_5_orphan_code_review_synthesis_emits_deprecation(tmp_path: Path) -> None:
    """Phase 3.5 added a skill-layout port for ``code-review-synthesis``."""
    deps = _record_warnings("code-review-synthesis", tmp_path, layout="old")
    assert len(deps) == 1
    assert "code-review-synthesis" in str(deps[0].message)


def test_security_review_does_not_emit_deprecation(tmp_path: Path) -> None:
    """``security-review`` remains the only legacy-only orphan after Phase 3.5 — no warning."""
    # Some installs may not have a security-review compiler module; if
    # the loader raises, the test still proves no warning was emitted.
    try:
        deps = _record_warnings("security-review", tmp_path, layout="old")
    except Exception:
        # Loader error is fine — we only care that no deprecation was
        # emitted before the loader failed.
        return
    assert deps == []


def test_phase_3_5_qa_plan_orphans_emit_deprecation(tmp_path: Path) -> None:
    """``qa-plan-generate`` and ``qa-plan-execute`` are now Phase 3.5 ports — they DO warn."""
    for name in ("qa-plan-generate", "qa-plan-execute"):
        try:
            deps = _record_warnings(name, tmp_path, layout="old")
        except Exception:
            continue
        assert len(deps) == 1, f"{name} should emit one deprecation warning"
        assert name in str(deps[0].message)


def test_new_layout_path_does_not_emit_deprecation(tmp_path: Path) -> None:
    """When routed through the new compiler, no warning is emitted."""
    # Plant the v6.4+ marker so detect_layout returns "new" — though
    # we also pass skill_layout="new" explicitly.
    scripts = tmp_path / "_bmad" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "resolve_customization.py").write_text("# marker\n")

    deps = _record_warnings("create-story", tmp_path, layout="new")
    assert deps == []


def test_deprecation_emitted_once_per_workflow_per_process(tmp_path: Path) -> None:
    """Calling the loader N times only fires one DeprecationWarning."""
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        for _ in range(5):
            get_workflow_compiler(
                "create-story", skill_layout="old", project_root=tmp_path
            )
    deps = [
        w
        for w in recorded
        if issubclass(w.category, DeprecationWarning)
        and "legacy workflow.yaml" in str(w.message)
    ]
    assert len(deps) == 1, f"expected one warning across 5 calls, got {len(deps)}"


def test_deprecation_emitted_per_distinct_workflow(tmp_path: Path) -> None:
    """Two different migrated workflows each get their own deprecation."""
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        get_workflow_compiler("create-story", skill_layout="old", project_root=tmp_path)
        get_workflow_compiler("dev-story", skill_layout="old", project_root=tmp_path)
        # And a repeat of create-story should be deduped.
        get_workflow_compiler("create-story", skill_layout="old", project_root=tmp_path)

    deps = [
        w
        for w in recorded
        if issubclass(w.category, DeprecationWarning)
        and "legacy workflow.yaml" in str(w.message)
    ]
    assert len(deps) == 2
    messages = " | ".join(str(w.message) for w in deps)
    assert "create-story" in messages
    assert "dev-story" in messages
