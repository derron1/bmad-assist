---
name: skill-with-workflow-xml
description: A SKILL.md that embeds an inline workflow XML block.
---

# Skill With Workflow XML

This skill exercises the inline workflow XML capture path.

## On Activation

### Step 1: Resolve

Run: `python3 {project-root}/_bmad/scripts/resolve_customization.py --skill {skill-root}`

## Execution

<workflow>
  <step n="1" goal="say hi">
    <action>Greet {user_name}</action>
  </step>
  <step n="2" goal="finish">
    <action>HALT</action>
  </step>
</workflow>
