"""Legacy bundled-workflow package — emptied in Phase 6.

The bundled workflow source directories (``create-story/``, ``dev-story/``,
``cache/``, etc.) and the helper accessors (``get_bundled_workflow_dir``,
``get_bundled_cache``, ``list_bundled_workflows``, ``list_bundled_cache``)
were removed when the v6.4+ skill layout became the only routing path.
Workflow source bundles now live exclusively under
:mod:`bmad_assist.skills` (one directory per ``bmad-<name>`` skill id).

This module is preserved as a stub so installs that still import the
package keep importing cleanly. New code should depend on
:mod:`bmad_assist.skills` and :mod:`bmad_assist.compiler.skills`
directly.
"""
