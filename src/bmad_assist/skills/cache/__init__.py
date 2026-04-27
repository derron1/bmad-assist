"""Compiled-template cache for the skill-layout compiler path.

Cache files live alongside the bundled skill sources at
``src/bmad_assist/skills/cache/<skill-id>.tpl.xml`` and
``...tpl.xml.meta.yaml``. The on-disk shape mirrors
``bmad_assist.workflows.cache`` so tools that walk both directories
keep working. The skill-layout cache is namespaced separately so
new-path and old-path artifacts cannot collide.
"""
