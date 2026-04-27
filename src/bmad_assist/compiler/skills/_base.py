"""Shared base class for BMAD v6.4+ skill-layout workflow compilers.

Phase 3.1 extracts the compile pipeline shared by every skill-layout
compiler into :class:`SkillLayoutCompilerBase`. Subclasses provide:

* ``skill_id`` — the bmad-prefixed canonical id (e.g.
  ``"bmad-create-story"``).
* ``legacy_workflow_name`` — the un-prefixed name still used by the
  patch file and the registry's legacy alias (e.g. ``"create-story"``).
* ``legacy_compiler_class`` — the existing
  :class:`bmad_assist.compiler.WorkflowCompiler` we delegate to for
  context-file building, mission, and XML output.
* ``build_extra_vars(context, customization)`` — workflow-specific
  variables to inject during SKILL.md substitution (or an empty dict
  when the workflow needs no extras).

Everything else — find/parse/resolve/substitute/transform/validate/
cache/delegate-to-legacy — lives here. See
:mod:`bmad_assist.compiler.skills.bmad_create_story` for the canonical
subclass.

Module-level lookup of :func:`apply_llm_transforms`
---------------------------------------------------
The original Phase 2 compiler imported ``apply_llm_transforms`` at
module top-level. Existing tests monkeypatch it via ``setattr(skill_mod,
"apply_llm_transforms", fake)``. To keep that contract working for
every subclass, the base class **does not** call
``apply_llm_transforms`` from this module. Instead it resolves the
attribute from the subclass's own module at call time. The subclass is
expected to ``from bmad_assist.compiler.patching import
apply_llm_transforms`` at module top-level so monkeypatches against
``<subclass module>.apply_llm_transforms`` are honoured.
"""

from __future__ import annotations

import hashlib
import importlib
import logging
import os
import sys
from abc import abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import yaml

from bmad_assist.compiler.core import WorkflowCompiler
from bmad_assist.compiler.patching import (
    discover_patch,
    load_patch,
    post_process_compiled,
)
from bmad_assist.compiler.patching.types import WorkflowPatch
from bmad_assist.compiler.skills._validation import (
    validate_patch_assertions,
    validate_workflow_xml,
)
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext, WorkflowIR
from bmad_assist.core.exceptions import CompilerError
from bmad_assist.skill_layout import (
    find_skill,
    parse_skill,
    resolve_customization,
    resolve_skill_variables,
)

logger = logging.getLogger(__name__)


# Where the runtime project cache lives. Distinct from the legacy
# ``.bmad-assist/cache/`` patch cache so old/new caches cannot
# collide.
_PROJECT_CACHE_SUBDIR = ".bmad-assist/cache/skills"


class SkillLayoutCompilerBase(WorkflowCompiler):
    """Base class for BMAD v6.4+ skill-layout workflow compilers.

    Subclass contract (all class-level attributes are required):

    * :attr:`skill_id` — bmad-prefixed canonical id, e.g.
      ``"bmad-create-story"``.
    * :attr:`legacy_workflow_name` — un-prefixed name used by the patch
      file and the legacy compiler alias, e.g. ``"create-story"``.
    * :attr:`legacy_compiler_class` — :class:`WorkflowCompiler` subclass
      used for context-file building, mission, and XML output.
    * :meth:`build_extra_vars` — return a ``dict[str, str]`` of extra
      variables to inject during SKILL.md substitution. Return an empty
      dict when no extras are needed.

    The base class implements the WorkflowCompiler protocol (work-flow
    name, required files, variables, validate-context) by delegating to
    the legacy compiler for shape parity. Subclasses override these
    methods only when their behaviour diverges.
    """

    # --- Subclass contract ------------------------------------------------ #

    skill_id: ClassVar[str] = ""
    """Bmad-prefixed canonical skill id (e.g. ``"bmad-create-story"``).

    Subclasses MUST set this. Empty default exists only so missing
    overrides raise a clear error from :meth:`__init_subclass__`.
    """

    legacy_workflow_name: ClassVar[str] = ""
    """Un-prefixed legacy workflow name used to look up the patch file
    and to register an alias on the skill-layout routing table."""

    legacy_compiler_class: ClassVar[type[WorkflowCompiler] | None] = None
    """:class:`WorkflowCompiler` subclass we delegate to for the
    workflow-specific tail of compilation (context files, mission, XML
    output). Subclasses MUST set this."""

    @abstractmethod
    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """Return workflow-specific extras for SKILL.md substitution.

        Called from :meth:`compile` between
        :func:`resolve_customization` and
        :func:`resolve_skill_variables`. Return an empty dict when the
        workflow needs no extras.
        """

    # --- Subclass guard rails -------------------------------------------- #

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Allow further-derived abstract subclasses; only enforce the
        # contract on concrete leaves. We treat "concrete" as "directly
        # instantiable" — i.e. the build_extra_vars hook is implemented
        # on this class (or any non-base ancestor).
        if getattr(cls, "__abstractmethods__", None):
            return
        if not getattr(cls, "skill_id", ""):
            raise TypeError(f"{cls.__name__} must set ``skill_id`` (e.g. 'bmad-create-story')")
        if not getattr(cls, "legacy_workflow_name", ""):
            raise TypeError(
                f"{cls.__name__} must set ``legacy_workflow_name`` (e.g. 'create-story')"
            )
        if cls.legacy_compiler_class is None:
            raise TypeError(
                f"{cls.__name__} must set ``legacy_compiler_class`` to a WorkflowCompiler subclass"
            )

    # --- WorkflowCompiler protocol -------------------------------------- #

    @property
    def workflow_name(self) -> str:
        """Stable workflow identifier — always the bmad-prefixed skill id."""
        return self.skill_id

    def get_workflow_dir(self, context: CompilerContext) -> Path:
        """Return the *skill source directory* (parent of ``SKILL.md``)."""
        skill_md = self._locate_skill_md(context)
        return skill_md.parent

    def get_required_files(self) -> list[str]:
        """Glob patterns the compiler expects to find under the project."""
        # Match the legacy compiler so Phase 3+ stays a behavioural
        # no-op for downstream consumers that introspect the patterns.
        return self._instantiate_legacy().get_required_files()

    def get_variables(self) -> dict[str, Any]:
        """Variables resolved by the compiler before invoking the LLM."""
        return self._instantiate_legacy().get_variables()

    def validate_context(self, context: CompilerContext) -> None:
        """Default: delegate to the legacy compiler.

        Skill-specific validations (e.g. requiring ``project_context.md``
        for create-story, or a story file for dev-story) happen in the
        legacy compiler too; we route through it so both the old and
        new paths share the same error surface.

        Subclasses can override to add skill-specific checks (e.g.
        requiring SKILL.md to be locatable).
        """
        self._instantiate_legacy().validate_context(context)

    # --- The compile pipeline -------------------------------------------- #

    def compile(self, context: CompilerContext) -> CompiledWorkflow:
        """Build the :class:`CompiledWorkflow` for the skill-layout path.

        Pipeline:

        1. Locate, parse, and customize the SKILL.md source.
        2. Build subclass-supplied ``extra_vars`` and substitute
           SKILL.md variables.
        3. Build (or load from cache) the patched body — runs LLM
           transforms when configured, regex post-process otherwise.
        4. Validate XML well-formedness and patch assertions when
           transforms ran.
        5. Synthesise a :class:`WorkflowIR` and delegate to the legacy
           compiler for context-file building, mission, and XML output.
        6. Re-stamp the result's ``workflow_name`` with the skill id.
        """
        # 1. Locate + parse + customize the skill source.
        skill_md = self._locate_skill_md(context)
        document = parse_skill(skill_md)
        skill_dir = document.skill_root
        customization = self._safe_resolve_customization(skill_dir, context.project_root)

        # 2. Substitute path tokens + {workflow.*} prose, with
        # subclass-supplied extras layered on top. We deliberately
        # leave workflow-internal tokens (``{epic_num}`` etc.) intact
        # so the existing variable engine downstream can resolve them.
        extra_vars = self.build_extra_vars(context, customization) or {}
        body = resolve_skill_variables(
            document,
            customization,
            context.project_root,
            extra_vars=extra_vars or None,
        )

        # 3. Build (or load from cache) the patched body.
        patched_body, cache_path, patch_path = self._ensure_patched_body(
            body=body,
            skill_md=skill_md,
            customize_toml=skill_dir / "customize.toml",
            context=context,
        )

        # 4. Synthesise a WorkflowIR around the patched body. The
        # legacy compiler treats this exactly like a
        # cached-template-from-workflow.yaml flow.
        template_md = skill_dir / "template.md"
        embedded_template = (
            template_md.read_text(encoding="utf-8") if template_md.is_file() else None
        )
        synthetic_config: dict[str, Any] = {
            "name": self.skill_id,
            "description": document.frontmatter.description,
            "template": str(template_md),
            "validation": str(skill_dir / "checklist.md"),
        }
        synthetic_ir = WorkflowIR(
            name=self.skill_id,
            config_path=skill_md,
            instructions_path=cache_path if cache_path is not None else skill_md,
            template_path=str(template_md) if template_md.is_file() else None,
            validation_path=str(skill_dir / "checklist.md"),
            raw_config=synthetic_config,
            raw_instructions=patched_body,
            output_template=embedded_template,
        )

        # 5. Stamp the synthesised IR onto the context and delegate.
        original_ir = context.workflow_ir
        original_patch = context.patch_path
        context.workflow_ir = synthetic_ir
        context.patch_path = patch_path
        try:
            legacy = self._instantiate_legacy()
            compiled = legacy.compile(context)
        finally:
            context.workflow_ir = original_ir
            context.patch_path = original_patch

        # 6. Replace the workflow_name on the result so consumers see
        # the skill id, not the legacy ``create-story`` name.
        return CompiledWorkflow(
            workflow_name=self.skill_id,
            mission=compiled.mission,
            context=compiled.context,
            variables=compiled.variables,
            instructions=compiled.instructions,
            output_template=compiled.output_template,
            token_estimate=compiled.token_estimate,
        )

    # --- Internal helpers ------------------------------------------------ #

    def _instantiate_legacy(self) -> WorkflowCompiler:
        """Build a fresh instance of the legacy compiler we delegate to."""
        if self.legacy_compiler_class is None:  # pragma: no cover — guarded by __init_subclass__
            raise CompilerError(f"{type(self).__name__} did not set legacy_compiler_class")
        return self.legacy_compiler_class()

    def _locate_skill_md(self, context: CompilerContext) -> Path:
        """Resolve ``SKILL.md`` via the project probe + bundled fallback."""
        try:
            return find_skill(
                self.skill_id,
                context.project_root,
                bundled_fallback=True,
            )
        except Exception as exc:
            raise CompilerError(
                f"Could not locate SKILL.md for '{self.skill_id}': {exc}\n"
                f"  How to fix: install the skill under "
                f".claude/skills/{self.skill_id}/SKILL.md, or rely on the "
                f"bmad-assist bundled fallback (ensure the package is "
                f"installed correctly)"
            ) from exc

    @staticmethod
    def _safe_resolve_customization(skill_dir: Path, project_root: Path) -> dict[str, Any]:
        """Resolve customization, falling back to ``{}`` on missing files."""
        customize_toml = skill_dir / "customize.toml"
        if not customize_toml.is_file():
            return {}
        return resolve_customization(skill_dir, project_root)

    def _ensure_patched_body(
        self,
        *,
        body: str,
        skill_md: Path,
        customize_toml: Path,
        context: CompilerContext,
    ) -> tuple[str, Path | None, Path | None]:
        """Return ``(patched_body, cache_path, patch_path)``.

        Caching strategy: cache key includes ``customize.toml`` content,
        ``SKILL.md`` content, the patch hash, the skill-layout mode
        marker (``"new"``), and the **transform mode** (``"llm"`` or
        ``"regex_only"``). A change in any of those invalidates.
        """
        # Patch files still live under the legacy workflow name. Phase
        # 5 may rename to ``bmad-<workflow>.patch.yaml`` once the
        # legacy compiler is removed.
        patch_path = discover_patch(
            self.legacy_workflow_name, context.project_root, cwd=context.cwd
        )

        transform_mode = self._resolve_transform_mode(context)

        cache_path = self._get_cache_path(self.skill_id, context.project_root)
        cache_meta_path = cache_path.with_suffix(cache_path.suffix + ".meta.yaml")

        skill_md_hash = _hash_file(skill_md)
        customize_toml_hash = _hash_file(customize_toml) if customize_toml.is_file() else ""
        patch_hash = _hash_file(patch_path) if patch_path is not None else ""

        # 1. Fast path — re-use a valid project cache.
        cached = self._read_cache(cache_path, cache_meta_path)
        if cached is not None and self._cache_meta_matches(
            cached["meta"],
            skill_md_hash=skill_md_hash,
            customize_toml_hash=customize_toml_hash,
            patch_hash=patch_hash,
            transform_mode=transform_mode,
        ):
            logger.debug("Using project skill cache: %s", cache_path)
            return cached["body"], cache_path, patch_path

        # 2. Bundled cache fallback — same shape, lives inside the
        # package and is shipped pre-compiled.
        bundled_cache = self._get_bundled_cache_path(self.skill_id)
        if bundled_cache is not None:
            bundled_meta = bundled_cache.with_suffix(bundled_cache.suffix + ".meta.yaml")
            cached_b = self._read_cache(bundled_cache, bundled_meta)
            if cached_b is not None and self._cache_meta_matches(
                cached_b["meta"],
                skill_md_hash=skill_md_hash,
                customize_toml_hash=customize_toml_hash,
                patch_hash=patch_hash,
                transform_mode=transform_mode,
            ):
                logger.debug("Using bundled skill cache: %s", bundled_cache)
                self._write_cache(
                    cache_path,
                    cache_meta_path,
                    cached_b["body"],
                    cached_b["meta"],
                )
                return cached_b["body"], cache_path, patch_path

        # 3. Cache miss — compute the patched body.
        patch: WorkflowPatch | None = None
        if patch_path is not None:
            try:
                patch = load_patch(patch_path)
            except Exception as exc:
                logger.warning(
                    "Could not load patch %s; skipping LLM transforms and regex post-process: %s",
                    patch_path.name,
                    exc,
                )
        else:
            # No patch on disk — orphan-style skills (Phase 3.5) ship
            # outcome-based SKILL.md authored without a patch. The body
            # we substituted is the final body: no LLM transforms, no
            # regex post-process, no must_contain assertions to enforce.
            logger.debug(
                "No patch found for %s; using substituted SKILL.md body verbatim "
                "(no LLM transforms, no regex post-process)",
                self.skill_id,
            )

        body_after_llm, transforms_ran = self._apply_llm_transforms(
            body, patch, context, transform_mode
        )

        if patch_path is not None:
            patched = self._apply_patch_post_process(body_after_llm, patch_path)
        else:
            patched = body_after_llm

        if transforms_ran:
            validate_workflow_xml(patched, skill_id=self.skill_id)

        if patch is not None and transforms_ran:
            validate_patch_assertions(patched, patch, skill_id=self.skill_id)

        # 4. Persist the cache for next time.
        try:
            meta = {
                "compiled_at": datetime.now(UTC).isoformat(),
                "skill_md_hash": skill_md_hash,
                "customize_toml_hash": customize_toml_hash,
                "patch_hash": patch_hash,
                "skill_layout_mode": "new",
                "transform_mode": transform_mode,
                "skill_id": self.skill_id,
            }
            self._write_cache(cache_path, cache_meta_path, patched, meta)
        except OSError as exc:
            logger.warning("Could not write skill cache to %s: %s", cache_path, exc)

        return patched, cache_path, patch_path

    @staticmethod
    def _get_cache_path(workflow_name: str, project_root: Path) -> Path:
        """Project-local runtime cache path for the skill."""
        return project_root / _PROJECT_CACHE_SUBDIR / f"{workflow_name}.tpl.xml"

    @staticmethod
    def _get_bundled_cache_path(workflow_name: str) -> Path | None:
        """Return the bundled cache path if the package ships one."""
        try:
            from bmad_assist import skills as _skills_pkg

            cache_dir = Path(_skills_pkg.__file__).parent / "cache"
            candidate = cache_dir / f"{workflow_name}.tpl.xml"
            return candidate if candidate.is_file() else None
        except Exception:  # pragma: no cover — defensive
            return None

    @staticmethod
    def _read_cache(cache_path: Path, meta_path: Path) -> dict[str, Any] | None:
        """Read ``(body, meta)`` from disk, or ``None`` on any failure."""
        if not cache_path.is_file() or not meta_path.is_file():
            return None
        try:
            body = cache_path.read_text(encoding="utf-8")
            meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            logger.debug("Failed to read skill cache %s: %s", cache_path, exc)
            return None
        if not isinstance(meta, dict):
            return None
        return {"body": body, "meta": meta}

    @staticmethod
    def _write_cache(
        cache_path: Path,
        meta_path: Path,
        body: str,
        meta: dict[str, Any],
    ) -> None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_body = cache_path.with_suffix(".tmp")
        tmp_body.write_text(body, encoding="utf-8")
        os.replace(tmp_body, cache_path)
        tmp_meta = meta_path.with_suffix(".tmp")
        tmp_meta.write_text(
            yaml.safe_dump(meta, default_flow_style=False, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp_meta, meta_path)

    @staticmethod
    def _cache_meta_matches(
        meta: dict[str, Any],
        *,
        skill_md_hash: str,
        customize_toml_hash: str,
        patch_hash: str,
        transform_mode: str,
    ) -> bool:
        # Older cache entries may pre-date the transform_mode field.
        # Treat a missing entry as a cache miss so the next compile
        # writes the new shape rather than re-using ambiguous output.
        cached_mode = meta.get("transform_mode")
        return (
            meta.get("skill_layout_mode") == "new"
            and meta.get("skill_md_hash") == skill_md_hash
            and meta.get("customize_toml_hash") == customize_toml_hash
            and meta.get("patch_hash") == patch_hash
            and cached_mode == transform_mode
        )

    @staticmethod
    def _resolve_transform_mode(context: CompilerContext) -> str:
        """Decide between full LLM transforms and regex-only fallback."""
        try:
            from bmad_assist.core.config import get_config

            config = get_config()
        except Exception as exc:
            logger.info(
                "Could not load config for transform-mode resolution; using regex_only: %s",
                exc,
            )
            return "regex_only"

        providers = getattr(config, "providers", None)
        master = getattr(providers, "master", None) if providers else None
        if master is None:
            return "regex_only"
        return "llm"

    def _apply_llm_transforms(
        self,
        body: str,
        patch: WorkflowPatch | None,
        context: CompilerContext,
        transform_mode: str,
    ) -> tuple[str, bool]:
        """Run LLM transforms when configured; return ``(body, ran)``.

        Looks up :func:`apply_llm_transforms` from the **subclass's**
        own module so existing tests that monkeypatch
        ``<subclass module>.apply_llm_transforms`` continue to work
        verbatim (see module docstring for the rationale).
        """
        if transform_mode != "llm" or patch is None or not patch.transforms:
            self._subclass_logger().info(
                "Skipping LLM transforms for %s (mode=%s, patch_loaded=%s)",
                self.skill_id,
                transform_mode,
                patch is not None,
            )
            return body, False

        from bmad_assist.core.config import get_config
        from bmad_assist.core.config.models.providers import (
            get_phase_provider_config,
        )
        from bmad_assist.core.exceptions import PatchError

        sub_logger = self._subclass_logger()

        try:
            config = get_config()
        except Exception as exc:
            sub_logger.info(
                "Config disappeared after transform-mode resolution; "
                "falling back to regex_only: %s",
                exc,
            )
            return body, False

        # Phase name uses the bmad-assist convention (underscores). We
        # resolve against the bmad-prefixed skill id so per-phase
        # config still routes correctly.
        phase_name = self.skill_id.removeprefix("bmad-").replace("-", "_")
        phase_config = get_phase_provider_config(config, phase_name)
        if isinstance(phase_config, list):
            if config.providers is None or config.providers.master is None:
                sub_logger.info(
                    "No master provider available for %s; skipping LLM transforms",
                    self.skill_id,
                )
                return body, False
            provider_config = config.providers.master
        else:
            provider_config = phase_config

        sub_logger.debug(
            "Running LLM transforms for %s via provider=%s, model=%s",
            self.skill_id,
            provider_config.provider,
            provider_config.model,
        )

        apply_fn = self._resolve_apply_llm_transforms()

        try:
            transformed, _ = apply_fn(
                content=body,
                transforms=list(patch.transforms),
                provider_config=provider_config,
                config=config,
                phase_name=phase_name,
                workflow_label=self.skill_id,
            )
        except PatchError as exc:
            sub_logger.info(
                "LLM transforms unavailable for %s (%s); falling back to regex-only",
                self.skill_id,
                exc,
            )
            return body, False

        return transformed, True

    def _subclass_logger(self) -> logging.Logger:
        """Return a logger named after the subclass's module.

        Tests assert on log lines via ``rec.name == skill_mod.logger.name``;
        emitting under the subclass module's logger preserves that
        contract regardless of which base-class method writes the log.
        """
        return logging.getLogger(type(self).__module__)

    def _resolve_apply_llm_transforms(self):
        """Return ``apply_llm_transforms`` from the subclass's module.

        Tests monkeypatch the attribute on each subclass module so they
        can stub LLM calls without poisoning unrelated modules. We
        respect that contract by reading the attribute fresh on every
        call rather than capturing it at import time.
        """
        module_name = type(self).__module__
        try:
            module = sys.modules.get(module_name) or importlib.import_module(module_name)
        except ImportError as exc:  # pragma: no cover — defensive
            raise CompilerError(
                f"Could not import skill compiler module {module_name!r}: {exc}"
            ) from exc
        apply_fn = getattr(module, "apply_llm_transforms", None)
        if apply_fn is None:
            # Fall back to the canonical source. This shouldn't happen
            # for properly authored subclasses but keeps the base
            # robust if a subclass forgets the import.
            from bmad_assist.compiler.patching import (
                apply_llm_transforms as fallback,
            )

            return fallback
        return apply_fn

    def _apply_patch_post_process(self, body: str, patch_path: Path) -> str:
        """Apply the regex ``post_process`` rules from the patch.

        Combines the patch's own ``post_process`` rules with the shared
        defaults — matching what :func:`compile_patch` does on the
        legacy path. Without the defaults the SCOPE-style injection
        would fire but the broader sprint-status cleanup wouldn't.
        """
        try:
            patch = load_patch(patch_path)
        except Exception as exc:
            logger.warning(
                "Could not load patch %s for skill-layout post-process: %s",
                patch_path.name,
                exc,
            )
            return body
        rules = list(patch.post_process or [])
        try:
            from bmad_assist.compiler.patching.discovery import load_defaults

            rules.extend(load_defaults(patch_path, self.legacy_workflow_name))
        except Exception as exc:
            logger.debug("Could not load patch defaults: %s", exc)
        if not rules:
            return body
        return post_process_compiled(body, rules)


def _hash_file(path: Path) -> str:
    """SHA-256 hex digest of ``path`` (or empty string when missing)."""
    if not path.is_file():
        return ""
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            sha.update(chunk)
    return sha.hexdigest()


__all__ = ["SkillLayoutCompilerBase"]
