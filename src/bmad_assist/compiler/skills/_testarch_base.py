"""Shared base for TEA (testarch) tri-modal skill compilers.

Phase 7.2 extracts the tri-modal compile pipeline shared by every TEA
(testarch) skill compiler into a reusable
:class:`SkillLayoutCompilerBase` subclass — mirroring what
:class:`bmad_assist.compiler.skills._synthesis_base.SynthesisCompilerBase`
does for synthesis workflows.

Intermediate-base contract
--------------------------
``_is_intermediate_base = True`` opts this class out of the
concrete-leaf guard rails on
:class:`SkillLayoutCompilerBase.__init_subclass__` (it does not set
``skill_id`` itself; concrete TEA workflow subclasses do).

What TEA workflows share
------------------------
* They use BMAD v6.4+ step-file architecture (workflow.md +
  ``steps-c/``, ``steps-v/``, ``steps-e/`` — tri-modal).
* They compute ``story_id`` / ``story_file`` / ``workflow_mode``
  uniformly.
* They build context files with strategic + TEA + source + story
  ordering by default; per-workflow overrides extend this.
* They emit a two-line ``Mode: <name>`` / ``Target: ...`` mission
  framing by default.
* They share the same ``get_required_files`` / ``get_variables`` /
  base-level ``validate_context`` defaults.

What's deliberately NOT here
----------------------------
* Per-workflow specifics — each concrete skill compiler overrides
  ``_build_context_files`` / ``_get_workflow_specific_variables`` /
  ``_build_mission`` / ``validate_context`` exactly the way the legacy
  tri-modal subclasses did.

Subclass contract (all class-level attributes)
----------------------------------------------
* :attr:`skill_id` — bmad-prefixed canonical id, e.g.
  ``"bmad-testarch-atdd"``.
* :attr:`legacy_workflow_name` — un-prefixed legacy name still used by
  the patch file, e.g. ``"testarch-atdd"``.
* :attr:`legacy_compiler_class` — set to ``None`` (Phase 7.2+ inlined).

Subclasses inherit working defaults for ``build_extra_vars``,
``get_required_files``, ``get_variables``, ``validate_context``, and
the inlined :meth:`_run_workflow_compile`. Override only when behaviour
diverges from the tri-modal default — exactly the way the legacy
subclasses extended :class:`TestarchTriModalCompiler`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, ClassVar

from bmad_assist.compiler.filtering import filter_instructions
from bmad_assist.compiler.output import generate_output
from bmad_assist.compiler.shared_utils import (
    apply_post_process,
    context_snapshot,
    get_stories_dir,
    load_workflow_template,
    safe_read_file,
)
from bmad_assist.compiler.skills._base import SkillLayoutCompilerBase
from bmad_assist.compiler.step_chain import compile_step_chain
from bmad_assist.compiler.tri_modal import get_workflow_mode, validate_workflow_mode
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext, WorkflowIR
from bmad_assist.compiler.variable_utils import substitute_variables
from bmad_assist.core.exceptions import CompilerError

logger = logging.getLogger(__name__)


class TestarchSkillCompilerBase(SkillLayoutCompilerBase):
    """Base class for tri-modal TEA (testarch) skill compilers.

    Concrete subclasses set ``skill_id`` / ``legacy_workflow_name`` and
    inherit the full tri-modal compile pipeline. Per-workflow
    customisations slot into ``_build_context_files`` /
    ``_get_workflow_specific_variables`` / ``_build_mission``, exactly
    as the legacy :class:`TestarchTriModalCompiler` exposed them.
    """

    # Mark this class as an intermediate base so SkillLayoutCompilerBase's
    # ``__init_subclass__`` skips the concrete-leaf contract checks on it.
    _is_intermediate_base: ClassVar[bool] = True

    # --- WorkflowCompiler protocol -------------------------------------- #

    def get_required_files(self) -> list[str]:
        """Return list of required file glob patterns.

        Default tri-modal globs. Subclasses may override (e.g. trace
        adds ``epic*.md``).
        """
        return [
            "**/project_context.md",
            "**/project-context.md",
        ]

    def get_variables(self) -> dict[str, Any]:
        """Return workflow-specific variables to resolve.

        Default tri-modal variable surface; subclasses may extend with
        e.g. ``test_dir`` / ``project_path`` / ``gate_type``.
        """
        return {
            "epic_num": None,
            "story_num": None,
            "story_id": None,
            "story_file": None,
            "workflow_mode": None,
            "date": None,
        }

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """No SKILL.md-level extras for TEA workflows.

        Workflow tokens (``{epic_num}`` / ``{story_num}`` /
        ``{story_id}`` / ``{story_file}`` / ``{date}`` /
        ``{test_dir}`` / etc.) are resolved by the inlined compile
        pipeline downstream; SKILL.md substitution doesn't need them.
        """
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Apply the shared TEA contract + skill-source check.

        Checks (in order):

        1. ``project_root`` and ``output_folder`` are set on the
           context.
        2. ``epic_num`` is present in ``resolved_variables``.
        3. ``story_num`` (when present) maps to a story file (warn
           only — non-blocking).
        4. SKILL.md is locatable for the bundled fallback path.

        Subclasses override to add story-level requirements (atdd /
        test-review require ``story_num``).
        """
        if context.project_root is None:
            raise CompilerError("project_root is required in context")
        if context.output_folder is None:
            raise CompilerError("output_folder is required in context")

        epic_num = context.resolved_variables.get("epic_num")
        if epic_num is None:
            raise CompilerError(
                f"epic_num is required for {self.legacy_workflow_name} compilation.\n"
                "  Suggestion: Provide epic_num via invocation params"
            )

        story_num = context.resolved_variables.get("story_num")
        if story_num is not None:
            story_path = self._find_story_file(context, epic_num, story_num)
            if story_path is None:
                logger.warning(
                    "Story file not found for %s-%s-*.md (non-blocking)",
                    epic_num,
                    story_num,
                )

        skill_md = self._locate_skill_md(context)
        if not skill_md.is_file():  # pragma: no cover — _locate_skill_md raises first
            raise CompilerError(
                f"SKILL.md not found: {skill_md}\n"
                f"  Why it's needed: Source for the {self.skill_id} skill\n"
                f"  How to fix: Install BMAD v6.4+ in the project, or rely on "
                f"the bmad-assist bundled fallback"
            )

    # --- Inlined workhorse compile ------------------------------------- #

    def _run_workflow_compile(self, context: CompilerContext) -> CompiledWorkflow:
        """Build the tri-modal :class:`CompiledWorkflow` for this skill.

        Mirrors the pre-Phase-7
        ``TestarchTriModalCompiler.compile`` flow byte-for-byte. The
        base class has already set ``context.workflow_ir`` to a
        synthetic IR and wired ``context.patch_path``.
        """
        workflow_ir = context.workflow_ir
        if workflow_ir is None:  # pragma: no cover — guaranteed by base
            raise CompilerError(
                "workflow_ir not set in context. This is a bug - core.py should have loaded it."
            )

        # Skill-layout: workflow_dir is the SKILL.md parent directory.
        workflow_dir = self.get_workflow_dir(context)

        with context_snapshot(context):
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "Using %s workflow from %s", self.legacy_workflow_name, workflow_dir
                )

            # Build resolved variables.
            resolved = dict(context.resolved_variables)

            epic_num = resolved.get("epic_num")
            story_num = resolved.get("story_num")

            # Compute story_id if story_num is provided.
            if story_num is not None:
                resolved["story_id"] = f"{epic_num}.{story_num}"

                # Find and add story file path.
                story_path = self._find_story_file(context, epic_num, story_num)
                if story_path:
                    resolved["story_file"] = str(story_path)

            # Add workflow-specific variables.
            self._get_workflow_specific_variables(resolved, context, workflow_ir)

            # Determine workflow mode (None for macro workflows).
            mode = get_workflow_mode(context)
            resolved["workflow_mode"] = mode

            # Build step chain or use legacy instructions.
            step_context_files: list[str] = []

            if workflow_ir.has_tri_modal and mode is not None:
                # Tri-modal: validate mode and build step chain.
                validate_workflow_mode(workflow_ir, mode)

                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("Using mode: %s", mode)

                first_step = self._get_first_step_for_mode(workflow_ir, mode)
                if first_step is None:
                    raise CompilerError(
                        f"No first step found for mode '{mode}' in "
                        f"{self.legacy_workflow_name}\n"
                        f"  Suggestion: Check that steps-{mode}/ directory exists "
                        f"with step files"
                    )

                step_content, step_context_files = compile_step_chain(
                    first_step, resolved, context.project_root
                )

                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("Compiled step chain: %d bytes", len(step_content))

                filtered_instructions = substitute_variables(step_content, resolved)
            else:
                # Legacy macro workflow: use filter_instructions.
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("Using legacy macro workflow compilation")

                filtered_instructions = filter_instructions(workflow_ir)
                filtered_instructions = substitute_variables(filtered_instructions, resolved)

            # Build context files.
            context_files = self._build_context_files(context, resolved)

            # Add step-provided context files (like knowledge index).
            for cf in step_context_files:
                if cf not in context_files:
                    content = safe_read_file(Path(cf), context.project_root)
                    if content:
                        context_files[cf] = content

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Built context with %d files", len(context_files))

            # Load template if defined.
            template_content = load_workflow_template(workflow_ir, context)

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Instructions: %d bytes", len(filtered_instructions))

            # Build mission.
            mission = self._build_mission(workflow_ir, resolved)

            # Generate output.
            compiled = CompiledWorkflow(
                workflow_name=self.legacy_workflow_name,
                mission=mission,
                context="",
                variables=resolved,
                instructions=filtered_instructions,
                output_template=template_content,
                token_estimate=0,
            )

            result = generate_output(
                compiled,
                project_root=context.project_root,
                context_files=context_files,
                links_only=context.links_only,
            )

            # Apply post_process rules if patch exists.
            final_xml = apply_post_process(result.xml, context)

            return CompiledWorkflow(
                workflow_name=self.legacy_workflow_name,
                mission=mission,
                context=final_xml,
                variables=resolved,
                instructions=filtered_instructions,
                output_template=template_content,
                token_estimate=result.token_estimate,
            )

    # --- Hooks subclasses may override --------------------------------- #

    def _get_first_step_for_mode(self, workflow_ir: WorkflowIR, mode: str) -> Path | None:
        """Get the first step file for the given mode."""
        if mode == "c":
            return workflow_ir.first_step_c
        elif mode == "v":
            return workflow_ir.first_step_v
        elif mode == "e":
            return workflow_ir.first_step_e
        return None

    def _find_story_file(
        self,
        context: CompilerContext,
        epic_num: Any,
        story_num: Any,
    ) -> Path | None:
        """Find story file by epic and story number."""
        stories_dir = get_stories_dir(context)
        if not stories_dir.exists():
            return None

        pattern = f"{epic_num}-{story_num}-*.md"
        matches = sorted(stories_dir.glob(pattern))

        if not matches:
            logger.debug("No story file found matching %s in %s", pattern, stories_dir)
            return None

        return matches[0]

    def _build_context_files(
        self,
        context: CompilerContext,
        resolved: dict[str, Any],
    ) -> dict[str, str]:
        """Build context files with strategic, TEA, and source context.

        Default tri-modal ordering (recency bias): general first, story
        last. Subclasses extend by calling ``super()._build_context_files``
        and appending workflow-specific entries.
        """
        files: dict[str, str] = {}
        project_root = context.project_root

        # Cache story content once for reuse (ADR-6).
        story_file_path = resolved.get("story_file")
        story_content: str | None = None
        if story_file_path:
            story_content = safe_read_file(Path(story_file_path), project_root)

        # 1. Strategic context (lazy import to avoid circular deps).
        from bmad_assist.compiler.strategic_context import StrategicContextService

        strategic_service = StrategicContextService(context, self.legacy_workflow_name)
        strategic_files = strategic_service.collect()
        files.update(strategic_files)

        # 2. TEA context (artifacts from previous TEA runs).
        from bmad_assist.testarch.context import collect_tea_context, is_tea_context_enabled

        if is_tea_context_enabled(context):
            tea_files = collect_tea_context(context, self.legacy_workflow_name, resolved)
            files.update(tea_files)

        # 3. Source context for workflows that need it.
        from bmad_assist.compiler.source_context import SourceContextService

        source_service = SourceContextService(context, self.legacy_workflow_name)
        if source_service.is_enabled() and story_content:
            from bmad_assist.compiler.source_context import extract_file_paths_from_story

            file_list_paths = extract_file_paths_from_story(story_content)
            source_files = source_service.collect_files(file_list_paths, None)
            files.update(source_files)

        # 4. Story file (LAST - recency bias per ADR-6).
        if story_file_path and story_content:
            files[str(story_file_path)] = story_content

        # ADR-7: Warn if combined context is large.
        from bmad_assist.compiler.shared_utils import estimate_tokens

        total_tokens = sum(estimate_tokens(c) for c in files.values())
        if total_tokens > 15000:
            logger.info(
                "TEA context large: %d tokens across %d files (workflow: %s)",
                total_tokens,
                len(files),
                self.legacy_workflow_name,
            )

        return files

    def _get_workflow_specific_variables(
        self,
        resolved: dict[str, Any],
        context: CompilerContext,
        workflow_ir: WorkflowIR,
    ) -> None:
        """Add workflow-specific variables to resolved dict.

        Default implementation does nothing. Subclasses may override
        to add workflow-specific tokens (e.g. ``test_dir``,
        ``source_dir``, ``gate_type``).
        """
        pass

    def _build_mission(
        self,
        workflow_ir: WorkflowIR,
        resolved: dict[str, Any],
    ) -> str:
        """Build mission description for the compiled workflow."""
        base_description = workflow_ir.raw_config.get(
            "description", f"Execute {self.legacy_workflow_name} workflow"
        )

        mode = resolved.get("workflow_mode", "c")
        mode_name = {"c": "Create", "v": "Validate", "e": "Edit"}.get(mode, mode)

        story_id = resolved.get("story_id")
        if story_id:
            mission = f"{base_description}\n\nMode: {mode_name}\nTarget: Story {story_id}"
        else:
            epic_num = resolved.get("epic_num", "?")
            mission = f"{base_description}\n\nMode: {mode_name}\nTarget: Epic {epic_num}"

        return mission


__all__ = ["TestarchSkillCompilerBase"]
