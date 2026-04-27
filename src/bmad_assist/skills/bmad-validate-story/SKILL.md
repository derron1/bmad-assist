---
name: bmad-validate-story
description: 'Adversarial story-quality validator. Use after `create-story` to surface issues, gaps, and disaster risks before development begins. Produces a validation report; does not modify the story.'
---

# Validate Story Workflow

**Goal:** Independently re-analyze a story file and surface every gap, ambiguity, or disaster risk the original `create-story` LLM may have missed. Output a structured validation report with an Evidence Score.

**Your Role:** Adversarial story-quality validator competing against the original create-story output.

- READ-ONLY: never edit files, never call Write/Edit/Bash. Output the report to stdout only — the orchestrator captures and persists it.
- Communicate all responses in `{communication_language}` and generate the report in `{document_output_language}`.
- Assume the story has problems. Be thorough. Cite specific lines.
- All inputs (story file, project context, architecture, epic) are EMBEDDED below — do not attempt to read files.

## Conventions

- Bare paths (e.g. `template.md`) resolve from the skill root.
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
- `planning_artifacts`, `implementation_artifacts`
- `date` as system-generated current datetime

### Step 5: Greet the User

Greet `{user_name}`, speaking in `{communication_language}`.

### Step 6: Execute Append Steps

Execute each entry in `{workflow.activation_steps_append}` in order.

Activation is complete. Begin the workflow below.

## Paths

- `story_dir` = `{implementation_artifacts}`
- `epics_file` = `{planning_artifacts}/epics.md`
- `architecture_file` = `{planning_artifacts}/architecture.md`
- `prd_file` = `{planning_artifacts}/PRD.md`

## Input Files

| Input | Description | Path Pattern(s) | Load Strategy |
|-------|-------------|------------------|---------------|
| story | Story file under validation (PRIMARY TARGET) | `{implementation_artifacts}/{{story_key}}.md` | EMBEDDED |
| project_context | Ground-truth conventions, standards, decisions | `{project-root}/**/project-context.md` | EMBEDDED |
| epics | Epic + acceptance criteria source | `{planning_artifacts}/*epic*.md` | EMBEDDED |
| architecture | Architecture for technical-alignment checks | `{planning_artifacts}/*architecture*.md` | EMBEDDED |
| prd | PRD fallback when epics omit detail | `{planning_artifacts}/*prd*.md` | EMBEDDED |

## Execution

<workflow>

<critical>SCOPE LIMITATION: You are a READ-ONLY VALIDATOR. Output your validation report to stdout ONLY. Do NOT create files, do NOT modify files, do NOT use Write/Edit/Bash tools. Your stdout output will be captured and saved by the orchestration system.</critical>

<critical>All configuration and context is available in the VARIABLES and CONTEXT sections below. Use these resolved values directly — do not attempt to load `{installed_path}` or other workflow files.</critical>

<critical>COMMON LLM MISTAKES TO HUNT FOR: reinventing wheels, wrong libraries, wrong file locations, breaking regressions, ignoring UX, vague implementations, missing acceptance criteria, omitted previous-story learnings.</critical>

<step n="1" goal="INVEST + acceptance-criteria + dependency analysis">
  <action>Read the embedded story file end-to-end. Note story id, title, current status.</action>

  <substep n="1a" title="INVEST scoring">
    <action>Score each INVEST criterion 1-10 (10 = critical violation). For each violation, capture a concrete quote and why it fails:</action>
    <action>**I — Independent**: hidden cross-story dependencies, ordering constraints.</action>
    <action>**N — Negotiable**: prescriptive HOW vs outcome-focused WHAT.</action>
    <action>**V — Valuable**: clear, meaningful business value tied to epic goals.</action>
    <action>**E — Estimable**: scope clear enough to estimate; unknown unknowns flagged.</action>
    <action>**S — Small**: deliverable in a single sprint; not too small to be meaningful.</action>
    <action>**T — Testable**: every AC objectively verifiable with edge/error cases.</action>
  </substep>

  <substep n="1b" title="Acceptance-criteria deep dive">
    <action>For each AC, hunt for: vague language ("should work well", "fast"), untestable claims, missing scenarios, contradictions, missing edge cases / error handling. Quote the exact text.</action>
  </substep>

  <substep n="1c" title="Hidden dependencies">
    <action>Identify undocumented dependencies: libraries/services/APIs, cross-team handoffs, infrastructure (DBs, queues, caches), data migrations/seeds, sequential story dependencies, external blockers. For each, state impact.</action>
  </substep>

  <substep n="1d" title="Estimation reality-check">
    <action>Compare stated/implied effort vs scope. Verdict: realistic / underestimated / overestimated / unestimable, with reasoning.</action>
  </substep>

  <substep n="1e" title="Technical alignment">
    <action>Verify story aligns with architecture: patterns, technologies, layering, naming conventions, integration points. Document any conflicts with architecture content embedded above.</action>
  </substep>
</step>

<step n="2" goal="Disaster prevention gap analysis">
  <action>Hunt for risks the original LLM missed across these categories. Document each finding with: what's missing, where it would bite, suggested fix.</action>

  <substep n="2a" title="Reinvention risks">
    <action>Areas where developer might duplicate existing code; reuse opportunities not surfaced; patterns from previous stories not referenced.</action>
  </substep>

  <substep n="2b" title="Technical specification gaps">
    <action>Missing version pins, missing API contract details, schema risks, security/perf gaps that could cause failures.</action>
  </substep>

  <substep n="2c" title="File structure / convention gaps">
    <action>Wrong file locations, naming convention drift, integration pattern breaks, deployment/env requirements missing.</action>
  </substep>

  <substep n="2d" title="Regression risks">
    <action>Breaking changes to existing behavior, missing test requirements, UX violations, learnings from prior stories not applied.</action>
  </substep>

  <substep n="2e" title="Implementation risks">
    <action>Vague instructions inviting wrong interpretation, completion-lie loopholes (ACs that allow fake "done"), scope-creep openings, missing quality requirements.</action>
  </substep>
</step>

<step n="3" goal="LLM-dev-agent optimization analysis">
  <action>Identify story-content issues that hurt downstream LLM consumption: verbosity wasting tokens, ambiguous instructions, context overload, critical signals buried in prose, poor scannable structure. For each, capture before/after suggestions and rationale.</action>
</step>

<step n="4" goal="Categorize, prioritize, and score evidence">
  <action>Categorize every finding into one of:
    - **critical_issues** — must fix (security, blocking, missing essentials)
    - **enhancements** — should add (helpful guidance, better specifications)
    - **optimizations** — nice to have (performance hints, dev tips)
    - **llm_optimizations** — token efficiency / clarity improvements
  </action>
  <action>Number each finding for the report.</action>

  <substep n="4a" title="Calculate Evidence Score">
    <critical>You MUST calculate and emit the Evidence Score. The score gates downstream synthesis decisions.</critical>

    <action>Map each finding to severity points:
      - **CRITICAL** (+3): security vulns, data corruption risks, blocking gaps, missing essential requirements
      - **IMPORTANT** (+1): missing guidance, unclear specs, integration risks
      - **MINOR** (+0.3): typos, style, minor clarifications
    </action>
    <action>Count CLEAN PASS categories (areas with NO issues): each is **-0.5 points**. Categories: 6 INVEST criteria + Acceptance Criteria + Dependencies + Technical Alignment + Implementation.</action>
    <action>Compute: `evidence_score = sum(finding_points) + clean_pass_count * -0.5`</action>
    <action>Determine verdict:
      - **EXCELLENT** when score ≤ -3
      - **PASS** when -3 &lt; score &lt; 3
      - **MAJOR REWORK** when 3 ≤ score &lt; 7
      - **REJECT** when score ≥ 7
    </action>
  </substep>
</step>

<step n="5" goal="Generate validation report (stdout)">
  <critical>OUTPUT MARKERS REQUIRED: Your validation report MUST start with the marker `&lt;!-- VALIDATION_REPORT_START --&gt;` on its own line BEFORE the report header, and MUST end with `&lt;!-- VALIDATION_REPORT_END --&gt;` on its own line AFTER the final line. The orchestrator extracts ONLY content between these markers; anything outside is discarded.</critical>

  <critical>TEMPLATE USAGE: Use the embedded `template.md` as a STRUCTURE GUIDE — replace every `{{placeholder}}` with the actual value derived from your analysis. Expand `{{#each X}}` sections by listing real items. NEVER emit literal `{{...}}` syntax in the final report.</critical>

  <action>Emit the report between the markers, following the template's section order:
    1. Report header (story id, key, title, file, date, validator).
    2. Executive summary table (counts per category) and overall assessment.
    3. Evidence Score Summary table — every finding as a row with severity icon, severity, description, source, score; followed by clean-pass count and final score → verdict.
    4. Story Quality Gate — INVEST table with per-criterion status / severity / details, INVEST violations list, AC issues list, hidden risks/dependencies list, estimation reality-check, technical alignment status.
    5. Critical Issues section (numbered, each with impact, source, problem, recommended fix).
    6. Enhancements section (numbered, each with benefit, source, gap, suggested addition).
    7. Optimizations section (numbered, each with value and suggestion).
    8. LLM Optimizations section (numbered, each with issue type, token impact, current vs optimized snippet, rationale).
    9. Competition Results — quality metrics table and disaster-prevention assessment.
  </action>
  <action>Do NOT include a "Changes Applied" section — this validator does not modify the story.</action>
  <action>End with `&lt;!-- VALIDATION_REPORT_END --&gt;` on its own line.</action>
</step>

</workflow>
