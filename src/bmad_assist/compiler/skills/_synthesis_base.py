"""Shared base for multi-LLM aggregation (synthesis) skill compilers.

Phase 7.1 extracts the synthesis-specific contract surface from
:class:`bmad_assist.compiler.skills.bmad_validate_story_synthesis.BmadValidateStorySynthesisCompiler`
into a reusable base. Brief 7.2 will refactor
:mod:`bmad_assist.compiler.skills.bmad_code_review_synthesis` to also
inherit from this class once its inlining is mechanical.

What synthesis workflows share
------------------------------
* They read a list of anonymized LLM outputs from
  ``context.resolved_variables`` under a workflow-specific key
  (``"anonymized_validations"`` or ``"anonymized_reviews"``).
* They require at least :attr:`_min_inputs` outputs (default 2). Below
  that, synthesis is meaningless and the compile is rejected early
  with a workflow-tailored error message.
* They embed each output as a virtual context file keyed
  ``[<Label> X]`` (e.g. ``[Validator A]`` or ``[Reviewer A]``). The
  legacy ``_build_synthesis_context`` helpers do the actual embedding;
  the base class does NOT take that over yet (the legacy flow is what
  Brief 7.1 captures in snapshots and Brief 7.2 inlines).
* They have NO patch on disk (no LLM transforms, no regex
  post-process). The base class's existing no-patch path handles this.
* They have no SKILL.md-level extras to inject — ``build_extra_vars``
  always returns ``{}``.

What's deliberately NOT here
----------------------------
* The actual ``_build_synthesis_context`` implementation — that's
  workflow-specific (story file lookup, source files, git diff, DV /
  security findings, prompt-budget enforcement). Brief 7.2 inlines
  each of those into the corresponding skill compiler.
* Cross-workflow helpers like ``_build_synthesis_mission`` — different
  workflows emit different missions; they can grow into shared helpers
  later if patterns converge.
* TEA-specific shared logic — out of scope for Brief 7.1 (Brief 7.2
  introduces ``_testarch_base.py`` for that).

Subclass contract
-----------------
* :attr:`skill_id`, :attr:`legacy_workflow_name` — same as
  :class:`SkillLayoutCompilerBase`.
* :attr:`legacy_compiler_class` — optional. Inlined subclasses may
  leave it as ``None`` and override
  :meth:`SkillLayoutCompilerBase._run_workflow_compile`.
* :attr:`_input_variable_name` — e.g. ``"anonymized_validations"``.
* :attr:`_input_label` — e.g. ``"validations"`` (used in error
  messages); defaults to a sensible derivation from
  ``_input_variable_name``.
* :attr:`_min_inputs` — defaults to 2.
* :attr:`_input_kind` — short name surfaced in error suggestions
  (e.g. ``"validate-story"`` or ``"code-review"``).

The base class provides:
* :meth:`build_extra_vars` returning ``{}`` (synthesis workflows don't
  inject SKILL.md-level extras).
* :meth:`validate_context` enforcing ``epic_num`` / ``story_num`` /
  minimum-input contract + skill-source check. Subclasses can override
  for additional checks.
"""

from __future__ import annotations

from typing import Any, ClassVar

from bmad_assist.compiler.skills._base import SkillLayoutCompilerBase
from bmad_assist.compiler.types import CompilerContext
from bmad_assist.core.exceptions import CompilerError


class SynthesisCompilerBase(SkillLayoutCompilerBase):
    """Base class for multi-LLM aggregation skill compilers.

    Concrete subclasses set the synthesis-specific class variables and
    inherit a working ``validate_context`` and ``build_extra_vars``.
    The compile pipeline is otherwise the same as any other
    :class:`SkillLayoutCompilerBase` subclass.
    """

    # Mark this class as an intermediate base so SkillLayoutCompilerBase's
    # ``__init_subclass__`` skips the concrete-leaf contract check on it
    # (it doesn't set ``skill_id`` itself; concrete workflow subclasses do).
    _is_intermediate_base: ClassVar[bool] = True

    # --- Synthesis subclass contract ----------------------------------- #

    _input_variable_name: ClassVar[str] = ""
    """Key under ``context.resolved_variables`` holding the list of
    anonymized LLM outputs (e.g. ``"anonymized_validations"`` or
    ``"anonymized_reviews"``). Subclasses MUST set this."""

    _input_label: ClassVar[str] = ""
    """Human-readable label for an individual input, used in error
    messages — e.g. ``"validations"`` or ``"reviews"``. Defaults to the
    second segment of :attr:`_input_variable_name` (``"validations"``
    from ``"anonymized_validations"``) when left empty."""

    _input_kind: ClassVar[str] = ""
    """Workflow name surfaced in error suggestions, e.g.
    ``"validate-story"`` or ``"code-review"``. Subclasses SHOULD set
    this so the error message tells the user which upstream workflow to
    run."""

    _min_inputs: ClassVar[int] = 2
    """Minimum number of inputs required for meaningful synthesis."""

    # --- Subclass guard rail -------------------------------------------- #

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if getattr(cls, "__abstractmethods__", None):
            return
        if not getattr(cls, "_input_variable_name", ""):
            raise TypeError(
                f"{cls.__name__} must set ``_input_variable_name`` (e.g. 'anonymized_validations')"
            )

    # --- Helpers --------------------------------------------------------- #

    @classmethod
    def _resolved_input_label(cls) -> str:
        """Return the configured label, with a sensible fallback."""
        if cls._input_label:
            return cls._input_label
        # ``anonymized_validations`` -> ``validations``
        parts = cls._input_variable_name.split("_", 1)
        return parts[1] if len(parts) == 2 else cls._input_variable_name

    @classmethod
    def _resolved_input_kind(cls) -> str:
        """Return the workflow kind for the upstream-suggestion message."""
        return cls._input_kind or cls.legacy_workflow_name.replace("-synthesis", "")

    # --- WorkflowCompiler protocol -------------------------------------- #

    def build_extra_vars(
        self,
        context: CompilerContext,
        customization: dict[str, Any],
    ) -> dict[str, str]:
        """Synthesis workflows don't inject SKILL.md-level extras.

        Anonymized inputs, session id, story metadata, and findings
        flow through ``context.resolved_variables`` and are read by the
        compile body when it builds the per-input ``[Label X]`` virtual
        files. SKILL.md substitution does not need them.
        """
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Enforce the shared synthesis contract.

        Checks (in order):

        1. ``project_root`` and ``output_folder`` are set on the
           context.
        2. ``epic_num`` and ``story_num`` are present in
           ``resolved_variables``.
        3. The configured ``_input_variable_name`` resolves to a
           non-empty list with at least :attr:`_min_inputs` entries.
        4. SKILL.md is locatable for the bundled fallback path.

        Subclasses can override to add workflow-specific checks (e.g.
        requiring TEA findings). They typically still call
        ``super().validate_context(context)`` first.
        """
        if context.project_root is None:
            raise CompilerError("project_root is required in context")
        if context.output_folder is None:
            raise CompilerError("output_folder is required in context")

        epic_num = context.resolved_variables.get("epic_num")
        story_num = context.resolved_variables.get("story_num")
        if epic_num is None:
            raise CompilerError(
                f"epic_num is required for {self.legacy_workflow_name} compilation.\n"
                "  Suggestion: Provide epic_num via invocation params"
            )
        if story_num is None:
            raise CompilerError(
                f"story_num is required for {self.legacy_workflow_name} compilation.\n"
                "  Suggestion: Provide story_num via invocation params"
            )

        label = self._resolved_input_label()
        upstream = self._resolved_input_kind()
        inputs = context.resolved_variables.get(self._input_variable_name, [])
        if not inputs:
            raise CompilerError(
                f"No anonymized {label} provided for synthesis.\n"
                f"  Why it's needed: Synthesis requires {label[:-1]} outputs "
                f"to synthesize.\n"
                f"  Suggestion: Run {upstream} workflow first with multiple LLMs"
            )
        if len(inputs) < self._min_inputs:
            raise CompilerError(
                f"Synthesis requires at least {self._min_inputs} {label}, "
                f"but only {len(inputs)} provided.\n"
                f"  Why: Single {label[:-1]} doesn't need synthesis - "
                f"use it directly.\n"
                f"  Suggestion: Run {upstream} workflow with additional LLM providers"
            )

        skill_md = self._locate_skill_md(context)
        if not skill_md.is_file():  # pragma: no cover — _locate_skill_md raises first
            raise CompilerError(
                f"SKILL.md not found: {skill_md}\n"
                f"  Why it's needed: Source for the {self.skill_id} skill\n"
                f"  How to fix: Install bmad-assist v0.5.1+ or rely on the bundled "
                f"fallback under src/bmad_assist/skills/{self.skill_id}/"
            )


__all__ = ["SynthesisCompilerBase"]
