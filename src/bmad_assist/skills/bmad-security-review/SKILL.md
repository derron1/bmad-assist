---
name: bmad-security-review
description: 'CWE-based security vulnerability scanner. Reviews a code diff plus modified source files against bundled CWE pattern catalogues and emits a structured JSON findings report. Use in parallel with code-review reviewers for security-sensitive changes.'
---

# Security Review Workflow

**Goal:** Scan the embedded git diff and source files for security vulnerabilities matching known CWE patterns, then emit a structured JSON report with severities and confidence scores.

**Your Role:** Security vulnerability scanner performing CWE-based code analysis.

- READ-ONLY: never edit files. Output the JSON report between the markers below; the orchestrator captures it from stdout.
- All inputs (security patterns, modified source files, git diff) are EMBEDDED in the CONTEXT section — do NOT attempt to read upstream files.
- Communicate all responses in `{communication_language}`; emit the report in `{document_output_language}`.

## Conventions

- Bare paths (e.g. `patterns/core.yaml`) resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory (where `customize.toml` lives).
- `{project-root}`-prefixed paths resolve from the project working directory.
- `{skill-name}` resolves to the skill directory's basename.

## On Activation

### Step 1: Resolve the Workflow Block

Run: `python3 {project-root}/_bmad/scripts/resolve_customization.py --skill {skill-root} --key workflow`

**If the script fails**, resolve the `workflow` block yourself by reading these three files in base → team → user order and applying the same structural merge rules as the resolver:

1. `{skill-root}/customize.toml` — defaults
2. `{project-root}/_bmad/custom/{skill-name}.toml` — team overrides
3. `{project-root}/_bmad/custom/{skill-name}.user.toml` — personal overrides

Any missing file is skipped. Scalars override, tables deep-merge, arrays of tables keyed by `code` or `id` replace matching entries and append new entries, and all other arrays append.

### Step 2: Execute Prepend Steps

Execute each entry in `{workflow.activation_steps_prepend}` in order before proceeding.

### Step 3: Load Persistent Facts

Treat every entry in `{workflow.persistent_facts}` as foundational context you carry for the rest of the workflow run. Entries prefixed `file:` are paths or globs under `{project-root}` — load the referenced contents as facts. All other entries are facts verbatim.

### Step 4: Load Config

Load config from `{project-root}/_bmad/bmm/config.yaml` and resolve:

- `project_name`, `user_name`
- `communication_language`, `document_output_language`
- `date` as system-generated current datetime

### Step 5: Greet the User

Greet `{user_name}`, speaking in `{communication_language}`.

### Step 6: Execute Append Steps

Execute each entry in `{workflow.activation_steps_append}` in order.

Activation is complete. Begin the workflow below.

## Paths

- `pattern_dir` = `{skill-root}/patterns/`

## Input Files

| Input | Description | Path Pattern(s) | Load Strategy |
|-------|-------------|------------------|---------------|
| security_patterns | Tier-prioritized CWE pattern YAML (loaded by compiler) | `{skill-root}/patterns/*.yaml` | EMBEDDED |
| git_diff | Diff under analysis (PRIMARY TARGET) | injected as `[git-diff]` | EMBEDDED |
| source_files | Modified source files (full bodies for inter-procedural tracing) | injected via real file paths | EMBEDDED_IF_PRESENT |

## Execution

<workflow>

<critical>SCOPE LIMITATION: You are a READ-ONLY SCANNER. Do NOT create files, do NOT modify files, do NOT use Write/Edit/Bash tools. Output the JSON report to stdout between the SECURITY_REPORT_START / SECURITY_REPORT_END markers.</critical>

<critical>All security patterns and source context are EMBEDDED in the CONTEXT section — do NOT attempt to read upstream pattern files or modified sources.</critical>

<step n="1" goal="Scan diff against CWE patterns + score findings">
  <action>Review every changed line in `[git-diff]` against the embedded `security-patterns` catalogue. Trace data flow through callees that are present in the embedded source files; flag UNVERIFIED when a callee is absent.</action>
  <action>For each potential vulnerability capture: matched `cwe_id`, severity (HIGH/MEDIUM/LOW), confidence in [0.0, 1.0] (1.0 exact match • 0.8 strong • 0.6 ambiguous • 0.4 possible • 0.2 weak), and specific remediation with a safe alternative.</action>
  <action>Exclusions are narrow: skip test files (unless they contain production credentials) and generated/vendored code. Ambiguous findings ship with low confidence (0.2-0.4) — do NOT silently omit them.</action>
</step>

<step n="2" goal="Mandatory zero-finding re-examination">
  <check if="step 1 produced zero findings">
    <action>Re-examine the diff for: missing auth on HTTP handlers, SSRF (user-controlled URLs), missing rate limits on auth endpoints, unbounded resource consumption (goroutine spawning, unlimited reads), sensitive data in logs or error responses.</action>
    <action>If still zero, include a top-level `notes` string in the JSON explaining why.</action>
  </check>
</step>

<step n="3" goal="Emit structured JSON report (stdout)">
  <critical>OUTPUT FORMAT: your entire output MUST be ONLY the JSON between the markers. The first line MUST be `&lt;!-- SECURITY_REPORT_START --&gt;` and the last MUST be `&lt;!-- SECURITY_REPORT_END --&gt;`. Any text outside these markers FAILS the review.</critical>

  <action>Emit between the markers:

```
&lt;!-- SECURITY_REPORT_START --&gt;
{"findings": [{"id": "SEC-001", "file_path": "path/to/file.go", "line_number": 42, "cwe_id": "CWE-330", "severity": "HIGH", "title": "Weak Random Number Generator", "description": "math/rand used for security-sensitive operation instead of crypto/rand", "remediation": "Replace math/rand with crypto/rand for cryptographic operations", "confidence": 0.95}]}
&lt;!-- SECURITY_REPORT_END --&gt;
```
  </action>

  <action>Field rules: every finding has all fields shown • `severity` ∈ {HIGH, MEDIUM, LOW} • `confidence` is a float in [0.0, 1.0] • `id` format `SEC-NNN` sequential from SEC-001 • when no findings, emit `{"findings": [], "notes": "..."}`.</action>
</step>

</workflow>
