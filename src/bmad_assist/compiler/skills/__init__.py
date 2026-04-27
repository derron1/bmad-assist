"""Phase 2 skill-layout compilers.

Sibling of :mod:`bmad_assist.compiler.workflows`. Each module here
implements the :class:`bmad_assist.compiler.WorkflowCompiler` protocol
against the BMAD v6.4+ ``SKILL.md`` shape instead of the legacy
``workflow.yaml + instructions.xml`` shape. The factory in
:mod:`bmad_assist.compiler.core` routes to one or the other based on
the configured ``skill_layout``.
"""
