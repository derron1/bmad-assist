"""Skill-layout compiler for the ``bmad-create-story`` workflow.

This is the Phase-2 parallel of
:class:`bmad_assist.compiler.workflows.create_story.CreateStoryCompiler`.
Both classes coexist; the factory in :mod:`bmad_assist.compiler.core`
selects between them based on the resolved ``skill_layout`` mode.

The new path:

1. Locates the skill via :func:`bmad_assist.skill_layout.find_skill`
   (with the bundled fallback enabled).
2. Parses ``SKILL.md`` and merges ``customize.toml`` overrides.
3. Substitutes built-in path tokens and ``{workflow.*}`` customization
   prose via :func:`bmad_assist.skill_layout.resolve_skill_variables`.
4. Synthesises a :class:`WorkflowIR` whose ``raw_instructions`` field
   is the substituted SKILL.md body, so the existing patch system —
   LLM transforms + regex post-process — can operate on it without
   modification.
5. Caches the patched template under
   ``src/bmad_assist/skills/cache/<skill-id>.tpl.xml`` (bundled) or
   ``.bmad-assist/cache/skills/<skill-id>.tpl.xml`` (project-local
   runtime), namespaced apart from the legacy workflow cache.
6. Hands the synthesised IR to the existing
   :class:`CreateStoryCompiler` to build the final
   :class:`CompiledWorkflow` (context files, mission, XML output).

This minimises new code: the legacy compiler already handles project
context, git intelligence, sprint-status discovery, and XML output —
all of which we want to keep.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from bmad_assist.compiler.patching import (
    apply_llm_transforms,
    discover_patch,
    load_patch,
    post_process_compiled,
    validate_output,
)
from bmad_assist.compiler.patching.types import WorkflowPatch
from bmad_assist.compiler.shared_utils import find_project_context_file
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext, WorkflowIR
from bmad_assist.compiler.workflows.create_story import CreateStoryCompiler
from bmad_assist.core.exceptions import CompilerError
from bmad_assist.skill_layout import (
    find_skill,
    parse_skill,
    resolve_customization,
    resolve_skill_variables,
)

logger = logging.getLogger(__name__)

# Stable skill id used by all references (config, file paths, dispatch
# keys). Phase 2 keeps the ``bmad-`` prefix for parity with upstream
# BMAD's manifest.
SKILL_ID = "bmad-create-story"

# The bmad-assist patch file is still named after the legacy workflow
# (``create-story.patch.yaml``). The new compiler looks it up under
# the legacy name so Phase 2 doesn't have to fork the patch file.
# Phase 5 may rename the patch file to ``bmad-create-story.patch.yaml``
# once the legacy compiler is removed.
_LEGACY_PATCH_NAME = "create-story"

# Where the runtime project cache lives. Distinct from the legacy
# ``.bmad-assist/cache/`` patch cache so old/new caches cannot
# collide.
_PROJECT_CACHE_SUBDIR = ".bmad-assist/cache/skills"


class BmadCreateStoryCompiler:
    """:class:`WorkflowCompiler` for the v6.4+ ``bmad-create-story`` skill."""

    @property
    def workflow_name(self) -> str:
        """Stable workflow identifier — always the bmad-prefixed skill id."""
        return SKILL_ID

    def get_workflow_dir(self, context: CompilerContext) -> Path:
        """Return the *skill source directory* (parent of ``SKILL.md``).

        We satisfy the protocol by returning a directory rather than a
        ``workflow.yaml`` parent. The skill source dir doubles as the
        anchor for ``customize.toml``, ``template.md``, and
        ``checklist.md`` lookups.
        """
        skill_md = self._locate_skill_md(context)
        return skill_md.parent

    def get_required_files(self) -> list[str]:
        """Glob patterns the compiler expects to find under the project."""
        # Identical to the legacy compiler — both paths surface the
        # same project artefacts to the LLM. Mirroring the patterns
        # keeps Phase 2 a behavioural no-op for downstream consumers.
        return CreateStoryCompiler().get_required_files()

    def get_variables(self) -> dict[str, Any]:
        """Variables resolved by the compiler before invoking the LLM."""
        # Same surface as the legacy compiler. The skill-layout-only
        # variables (``skill-root``, ``skill-name``, etc.) live inside
        # the substituted SKILL.md body; they are not part of the
        # public ``get_variables`` contract.
        return CreateStoryCompiler().get_variables()

    def validate_context(self, context: CompilerContext) -> None:
        """Reuse the legacy compiler's validation — same project shape."""
        if context.project_root is None:
            raise CompilerError("project_root is required in context")
        if context.output_folder is None:
            raise CompilerError("output_folder is required in context")

        epic_num = context.resolved_variables.get("epic_num")
        story_num = context.resolved_variables.get("story_num")
        if epic_num is None:
            raise CompilerError(
                "epic_num is required for create-story compilation.\n"
                "  Suggestion: Provide epic_num via invocation params or ensure "
                "sprint-status.yaml has a backlog story"
            )
        if story_num is None:
            raise CompilerError(
                "story_num is required for create-story compilation.\n"
                "  Suggestion: Provide story_num via invocation params or ensure "
                "sprint-status.yaml has a backlog story"
            )

        skill_md = self._locate_skill_md(context)
        if not skill_md.is_file():  # pragma: no cover — _locate_skill_md raises first
            raise CompilerError(
                f"SKILL.md not found: {skill_md}\n"
                f"  Why it's needed: Source for the bmad-create-story skill\n"
                f"  How to fix: Install BMAD v6.4+ in the project, or rely on "
                f"the bmad-assist bundled fallback"
            )

        project_context_path = find_project_context_file(context)
        if project_context_path is None:
            raise CompilerError(
                f"project_context.md not found: {context.output_folder / 'project_context.md'}\n"
                f"  Why it's needed: Contains critical implementation rules for AI agents\n"
                f"  How to fix: Run 'generate-project-context' workflow"
            )

    def compile(self, context: CompilerContext) -> CompiledWorkflow:
        """Build the :class:`CompiledWorkflow` for the skill-layout path."""
        # 1. Locate + parse + customize the skill source.
        skill_md = self._locate_skill_md(context)
        document = parse_skill(skill_md)
        skill_dir = document.skill_root
        customization = self._safe_resolve_customization(skill_dir, context.project_root)

        # 2. Substitute path tokens + {workflow.*} prose. We deliberately
        #    leave workflow-internal tokens (``{epic_num}`` etc.) intact
        #    so the existing variable engine downstream can resolve them.
        body = resolve_skill_variables(
            document,
            customization,
            context.project_root,
            extra_vars=None,
        )

        # 3. Build (or load from cache) the patched body. The patch
        #    system is the LLM-driven transform layer that strips
        #    interactive elements and injects bmad-assist's critical
        #    instructions. We cache the result so repeat compiles
        #    don't re-pay the LLM cost.
        patched_body, cache_path, patch_path = self._ensure_patched_body(
            workflow_name=SKILL_ID,
            body=body,
            skill_md=skill_md,
            customize_toml=skill_dir / "customize.toml",
            context=context,
        )

        # 4. Synthesise a WorkflowIR around the patched body. The
        #    legacy compiler treats this exactly like a
        #    cached-template-from-workflow.yaml flow.
        synthetic_config: dict[str, Any] = {
            "name": SKILL_ID,
            "description": document.frontmatter.description,
            # Embed the bundled template so load_workflow_template() finds it
            # in the skill source dir without a path-traversal headache.
            "template": str(skill_dir / "template.md"),
            "validation": str(skill_dir / "checklist.md"),
            # The legacy compiler reads ``raw_config['description']`` for
            # the mission line; everything else is informational.
        }
        template_md = skill_dir / "template.md"
        embedded_template = (
            template_md.read_text(encoding="utf-8") if template_md.is_file() else None
        )
        synthetic_ir = WorkflowIR(
            name=SKILL_ID,
            config_path=skill_md,
            instructions_path=cache_path if cache_path is not None else skill_md,
            template_path=str(template_md) if template_md.is_file() else None,
            validation_path=str(skill_dir / "checklist.md"),
            raw_config=synthetic_config,
            raw_instructions=patched_body,
            output_template=embedded_template,
        )

        # 5. Stamp the synthesised IR onto the context and delegate.
        #    The legacy compiler reads context.workflow_ir; everything
        #    else (variable resolution, context-file building, XML
        #    output) is identical between the old and new paths.
        original_ir = context.workflow_ir
        original_patch = context.patch_path
        context.workflow_ir = synthetic_ir
        context.patch_path = patch_path  # post_process re-applies on the rendered XML
        try:
            legacy = CreateStoryCompiler()
            compiled = legacy.compile(context)
        finally:
            context.workflow_ir = original_ir
            context.patch_path = original_patch

        # Replace the workflow_name on the result so consumers see the
        # skill id, not the legacy ``create-story`` name.
        return CompiledWorkflow(
            workflow_name=SKILL_ID,
            mission=compiled.mission,
            context=compiled.context,
            variables=compiled.variables,
            instructions=compiled.instructions,
            output_template=compiled.output_template,
            token_estimate=compiled.token_estimate,
        )

    # --------------------------------------------------------------- #
    # Internal helpers                                                #
    # --------------------------------------------------------------- #

    def _locate_skill_md(self, context: CompilerContext) -> Path:
        """Resolve ``SKILL.md`` via the project probe + bundled fallback."""
        try:
            return find_skill(
                SKILL_ID,
                context.project_root,
                bundled_fallback=True,
            )
        except Exception as exc:
            raise CompilerError(
                f"Could not locate SKILL.md for '{SKILL_ID}': {exc}\n"
                f"  How to fix: install the skill under "
                f".claude/skills/{SKILL_ID}/SKILL.md, or rely on the "
                f"bmad-assist bundled fallback (ensure the package is "
                f"installed correctly)"
            ) from exc

    @staticmethod
    def _safe_resolve_customization(
        skill_dir: Path, project_root: Path
    ) -> dict[str, Any]:
        """Resolve customization, falling back to ``{}`` on missing files.

        ``resolve_customization`` raises when ``customize.toml`` is
        absent. The bundled skill always has one, but a future skill
        without defaults shouldn't fail compilation here — the rest of
        the pipeline handles missing keys gracefully.
        """
        customize_toml = skill_dir / "customize.toml"
        if not customize_toml.is_file():
            return {}
        return resolve_customization(skill_dir, project_root)

    def _ensure_patched_body(
        self,
        *,
        workflow_name: str,
        body: str,
        skill_md: Path,
        customize_toml: Path,
        context: CompilerContext,
    ) -> tuple[str, Path | None, Path | None]:
        """Return ``(patched_body, cache_path, patch_path)``.

        Caching strategy mirrors the legacy patch system but uses a
        distinct directory so old- and new-path artefacts never
        collide. The cache key includes ``customize.toml`` content,
        ``SKILL.md`` content, the patch hash, the skill-layout mode
        marker (``"new"``), and the **transform mode** (``"llm"`` or
        ``"regex_only"``). A change in any of those invalidates.

        When no patch exists, returns the body verbatim with a
        ``cache_path`` pointing at the project-local runtime cache so
        subsequent compiles can short-circuit.
        """
        # Patch files still live under the legacy workflow name. See
        # ``_LEGACY_PATCH_NAME`` for the rationale.
        patch_path = discover_patch(
            _LEGACY_PATCH_NAME, context.project_root, cwd=context.cwd
        )

        # Resolve transform mode ONCE per compile, before any cache
        # check. Mode determines cache key — switching providers must
        # rebuild the cache so we never serve regex-only output to a
        # caller that's now configured for full LLM transforms (or
        # vice versa).
        transform_mode = self._resolve_transform_mode(context)

        cache_path = self._get_cache_path(workflow_name, context.project_root)
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
        #    package and is shipped pre-compiled.
        bundled_cache = self._get_bundled_cache_path(workflow_name)
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
                # Mirror to project cache so subsequent runs can
                # short-circuit without re-touching the package.
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
                    "Could not load patch %s; skipping LLM transforms and "
                    "regex post-process: %s",
                    patch_path.name,
                    exc,
                )

        # 3a. LLM transforms (skill-layout-aware: only when a master
        #     provider is configured AND a patch was loaded).
        #     ``transforms_ran`` is True iff apply_llm_transforms was
        #     called and returned without raising — used below to gate
        #     the strict XML / patch.validation checks, which only
        #     apply to LLM-transformed output.
        body_after_llm, transforms_ran = self._apply_llm_transforms(
            body, patch, context, transform_mode
        )

        # 3b. Deterministic regex post-process (fires regardless of
        #     transform_mode — it's the bulk of what the patch does
        #     for create-story today).
        if patch_path is not None:
            patched = self._apply_patch_post_process(body_after_llm, patch_path)
        else:
            patched = body_after_llm

        # 3c. Validate <workflow> XML well-formedness — only when LLM
        #     transforms actually ran. The raw SKILL.md contains
        #     ``{{var}}`` placeholders, embedded code fences, and
        #     prose with bare ``<`` / ``>`` characters that are not
        #     valid XML on their own; the LLM step is responsible for
        #     producing the clean envelope this check enforces. (The
        #     legacy compiler does the equivalent: it only runs
        #     ``_validate_instructions_xml`` after LLM transforms.)
        if transforms_ran:
            self._validate_workflow_xml(patched)

        # 3d. Apply patch.validation rules (must_contain / must_not_contain).
        #     Same reasoning as 3c — these invariants assume a clean
        #     post-transform body.
        if patch is not None and transforms_ran:
            self._validate_patch_assertions(patched, patch)

        # 4. Persist the cache for next time. Cache writes are
        #    best-effort — a permission error here should not block
        #    compilation.
        try:
            meta = {
                "compiled_at": datetime.now(UTC).isoformat(),
                "skill_md_hash": skill_md_hash,
                "customize_toml_hash": customize_toml_hash,
                "patch_hash": patch_hash,
                "skill_layout_mode": "new",
                "transform_mode": transform_mode,
                "skill_id": workflow_name,
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
    def _read_cache(
        cache_path: Path, meta_path: Path
    ) -> dict[str, Any] | None:
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
        """Decide between full LLM transforms and regex-only fallback.

        The skill-layout compiler runs in two flavours:

        * ``"llm"`` — a master provider is configured. We run the
          patch's natural-language transforms through the existing
          ``apply_llm_transforms`` pipeline (with retries and
          per-attempt validation), then apply regex post-process.
        * ``"regex_only"`` — no master provider available. We skip
          the LLM step and rely on the deterministic post-process
          regexes alone. This preserves test- and offline-paths.

        The mode is recorded in cache meta; switching modes
        invalidates the cache.
        """
        try:
            from bmad_assist.core.config import get_config

            config = get_config()
        except Exception as exc:
            logger.info(
                "Could not load config for transform-mode resolution; "
                "using regex_only: %s",
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

        ``ran`` is ``True`` only when ``apply_llm_transforms`` executed
        and returned without raising. Callers use it to gate the
        strict XML / patch.validation checks, which assume the body
        has been through the LLM transforms layer.

        Falls back to ``(body, False)`` when:

        * ``transform_mode != "llm"`` (no master provider configured),
        * the patch couldn't be loaded or has no transforms, or
        * the LLM session itself raises :class:`PatchError` (e.g.
          provider unreachable, test conftest disabled the call). In
          this last case we degrade silently to regex-only so the
          compiler remains usable offline.
        """
        if transform_mode != "llm" or patch is None or not patch.transforms:
            logger.info(
                "Skipping LLM transforms for %s (mode=%s, patch_loaded=%s)",
                SKILL_ID,
                transform_mode,
                patch is not None,
            )
            return body, False

        from bmad_assist.core.config import get_config
        from bmad_assist.core.config.models.providers import (
            get_phase_provider_config,
        )
        from bmad_assist.core.exceptions import PatchError

        try:
            config = get_config()
        except Exception as exc:
            # Defensive — _resolve_transform_mode already exercised
            # this code path; if config disappears between then and
            # now, fall back rather than crash.
            logger.info(
                "Config disappeared after transform-mode resolution; "
                "falling back to regex_only: %s",
                exc,
            )
            return body, False

        # Phase name uses the bmad-assist convention (underscores). The
        # legacy compiler resolves create-story → create_story; we do
        # the same against the bmad-prefixed skill id so per-phase
        # config still routes correctly.
        phase_name = SKILL_ID.removeprefix("bmad-").replace("-", "_")
        phase_config = get_phase_provider_config(config, phase_name)
        if isinstance(phase_config, list):
            # Multi-LLM phase configured — fall back to global master.
            if config.providers is None or config.providers.master is None:
                logger.info(
                    "No master provider available for %s; skipping LLM transforms",
                    SKILL_ID,
                )
                return body, False
            provider_config = config.providers.master
        else:
            provider_config = phase_config

        logger.debug(
            "Running LLM transforms for %s via provider=%s, model=%s",
            SKILL_ID,
            provider_config.provider,
            provider_config.model,
        )

        try:
            transformed, _ = apply_llm_transforms(
                content=body,
                transforms=list(patch.transforms),
                provider_config=provider_config,
                config=config,
                phase_name=phase_name,
                workflow_label=SKILL_ID,
            )
        except PatchError as exc:
            # Don't fail the compile when the LLM step is unavailable
            # — that mirrors the legacy ensure_template_compiled
            # behaviour (which catches PatchError and uses the
            # original body). Mode stays "llm" in cache meta so the
            # next compile re-attempts when the provider returns.
            logger.info(
                "LLM transforms unavailable for %s (%s); falling back to regex-only",
                SKILL_ID,
                exc,
            )
            return body, False

        return transformed, True

    @staticmethod
    def _validate_workflow_xml(content: str) -> None:
        """Extract ``<workflow>...</workflow>`` and assert it parses.

        SKILL.md doesn't carry an ``<instructions-xml>`` envelope, so
        the legacy ``_validate_instructions_xml`` doesn't apply. We
        instead extract the first ``<workflow>`` block and parse it
        with stdlib ``xml.etree.ElementTree``. Compile fails when the
        block is missing or malformed.
        """
        import re
        import xml.etree.ElementTree as ET

        match = re.search(
            r"<workflow\b[^>]*>.*?</workflow>",
            content,
            re.DOTALL,
        )
        if not match:
            raise CompilerError(
                f"Compiled {SKILL_ID} body does not contain a <workflow>"
                "...</workflow> block.\n"
                "  Why this matters: the runtime compiler relies on the "
                "<workflow> envelope to drive step iteration.\n"
                "  How to fix: ensure SKILL.md contains a single "
                "<workflow>...</workflow> section and that patch "
                "transforms preserve it."
            )

        try:
            ET.fromstring(match.group(0))
        except ET.ParseError as exc:
            raise CompilerError(
                f"Compiled {SKILL_ID} body has malformed <workflow> XML: {exc}.\n"
                "  Why this matters: filter_instructions() and the loop "
                "runtime parse this XML; mismatched tags break the "
                "workflow at runtime.\n"
                "  How to fix: re-run with a master provider that "
                "honours XML well-formedness, or correct the source "
                "SKILL.md if regex post-process produced the breakage."
            ) from exc

    @staticmethod
    def _validate_patch_assertions(content: str, patch: WorkflowPatch) -> None:
        """Apply ``patch.validation`` ``must_contain`` / ``must_not_contain`` rules.

        Mirrors what :func:`compile_patch` does on the legacy path —
        running the rules against the post-processed output gives
        us the same content guarantees regardless of which compiler
        produced it.
        """
        if patch.validation is None:
            return
        errors = validate_output(content, patch.validation)
        if errors:
            raise CompilerError(
                f"Compiled {SKILL_ID} failed patch validation: {errors}.\n"
                "  Why this matters: the patch declares content "
                "invariants that the compiled body must satisfy.\n"
                "  How to fix: configure a master provider so LLM "
                "transforms can run, or update the patch's "
                "must_contain / must_not_contain rules to match the "
                "regex-only output."
            )

    @staticmethod
    def _apply_patch_post_process(body: str, patch_path: Path) -> str:
        """Apply only the regex ``post_process`` rules from the patch.

        We deliberately skip the LLM ``transforms`` step here. Phase 2
        keeps the compile-time LLM transforms optional (they require a
        configured master provider, which tests and offline runs may
        not have). The post-process regexes alone strip sprint-status
        and inject the scope-limitation critical block — which is the
        bulk of what the patch contributes for create-story.

        When an LLM is available, callers can pre-warm the bundled
        cache by running the existing patch CLI and committing the
        resulting cache file under
        ``src/bmad_assist/skills/cache/``.
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
        # Combine the patch's own post_process rules with the shared
        # defaults, matching what compile_patch() does on the legacy
        # path. Without the defaults the SCOPE LIMITATION injection
        # would fire but the broader sprint-status cleanup wouldn't.
        rules = list(patch.post_process or [])
        try:
            from bmad_assist.compiler.patching.discovery import load_defaults

            rules.extend(load_defaults(patch_path, _LEGACY_PATCH_NAME))
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
