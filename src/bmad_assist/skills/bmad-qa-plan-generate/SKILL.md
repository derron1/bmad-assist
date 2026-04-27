---
name: bmad-qa-plan-generate
description: 'Generate a comprehensive E2E test plan for a completed epic, classifying tests as Category A (CLI/API/file), B (Playwright UI), or C (human verification). Produces executable test code, not summaries.'
---

# QA Plan Generate Workflow

**Goal:** Produce an executable E2E test plan covering every functional requirement, acceptance criterion, and UI interaction in a completed epic. Output runnable test code (bash + Python heredoc + Playwright TypeScript) — not summaries.

**Your Role:** Test architect generating test-execution artifacts.

- All inputs (epic, stories, traceability, ux-elements, PRD, architecture) are EMBEDDED in the CONTEXT section — do not attempt to read files.
- Communicate all responses in `{communication_language}`; emit the test plan in `{document_output_language}`.
- Write the result to `{output_file}` using the embedded `template.md` as the structure guide.

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
- `output_folder`
- `date` as system-generated current datetime

### Step 5: Greet the User

Greet `{user_name}`, speaking in `{communication_language}`.

### Step 6: Execute Append Steps

Execute each entry in `{workflow.activation_steps_append}` in order.

Activation is complete. Begin the workflow below.

## Paths

- `qa_artifacts` = `{output_folder}/qa-artifacts`
- `output_file` = `{qa_artifacts}/test-plans/epic-{epic_num}-e2e-plan.md`
- `trace_file` = `{qa_artifacts}/traceability/epic-{epic_num}-trace.md`

## Input Files

| Input | Description | Path Pattern(s) | Load Strategy |
|-------|-------------|------------------|---------------|
| epic | Epic definition (objectives, FRs, story refs) | `{project-root}/docs/epics/epic-{epic_num}*.md` | EMBEDDED |
| stories | Story files for this epic (max 10) | `{output_folder}/implementation-artifacts/stories/{epic_num}-*.md` | EMBEDDED |
| trace | Traceability file (optional, for coverage gap analysis) | `{trace_file}` | EMBEDDED_IF_PRESENT |
| ux_elements | UI selectors documentation (REQUIRED for Category B) | `{project-root}/docs/modules/*/ux-elements.md`, `{project-root}/docs/ux-elements.md`, `{project-root}/docs/epics/epic-{epic_num}-ux.md` | EMBEDDED |
| prd | PRD for FR/NFR extraction | `{project-root}/docs/prd.md` | EMBEDDED |
| architecture | Architecture for NFR context | `{project-root}/docs/architecture.md` | EMBEDDED |

## Execution

<workflow>

<critical>OUTPUT FORMAT: This is NOT a summary. You MUST emit ACTUAL EXECUTABLE TEST CODE for every test. Each test gets its own Markdown subsection (`### E{epic_num}-{cat}{nn}: name`) and its own fenced code block. Tests must be immediately runnable without modification.</critical>

<critical>SELECTOR RULES (Category B): Use ONLY `data-testid` values present in the embedded `ux-elements.md`. NEVER invent selector names. NEVER use CSS classes, `aria-selected`, or `aria-checked`. If a selector is missing, downgrade the test to Category C with a note "requires ux-elements.md update".</critical>

<critical>WAIT STATE: Use `await page.waitForLoadState('domcontentloaded')` — NEVER `'networkidle'` (SSE breaks it).</critical>

<step n="1" goal="Analyze embedded context">
  <action>Read the embedded epic file. Capture: epic title, objectives, FRs, referenced stories, technical scope.</action>
  <action>Read every embedded story. For each story capture: title, ACs, technical implementation hints, UI components mentioned.</action>
  <action>If `ux-elements.md` is embedded, index every `data-testid` value — this is your ONLY allowed selector vocabulary for Category B.</action>
  <action>If `prd.md` is embedded, extract FR-* and NFR-* identifiers — every test must cite a requirement.</action>
  <action>If a trace file is embedded, note existing coverage and gaps to bias the new plan toward uncovered requirements.</action>
</step>

<step n="2" goal="Classify testable requirements">
  <action>For each requirement / AC / behavior, assign exactly one category:
    - **A (CLI/API/File)** — fully automatable: CLI commands, REST endpoints, file ops, DB state, config validation.
    - **B (Playwright/UI)** — UI interactions ONLY when `ux-elements.md` provides the needed selectors.
    - **C (Human Verification)** — external services, real credentials, subjective quality, third-party APIs, performance under real load.
  </action>
  <action>Default to Category C when in doubt — never invent code that won't run.</action>
</step>

<step n="3" goal="Generate test code">
  <action>Use the test ID format: `E{epic_num}-A##` / `B##` / `C##` (zero-padded sequence).</action>

  <substep n="3a" title="Category A — bash + Python">
    <action>For each Cat A requirement, emit a Markdown subsection with header `### E{epic_num}-A##: {test_name}` and a single bash code block containing:
      - Comment header with test ID, requirement reference (FR/AC), expected outcome.
      - `cleanup()` function and `trap cleanup EXIT` for any temp state.
      - Setup: use `$PROJECT_ROOT` (NEVER hardcode absolute paths). Activate venv if invoking Python.
      - Test execution (bash commands or `python3 &lt;&lt;'PYEOF' ... PYEOF` heredoc).
      - Verification (asserts, output checks, exit-code echo).
      - `echo "Expected exit code: 0"` (or document the expected non-zero code).
    </action>
    <action>Each Cat A test must be idempotent and self-contained.</action>
  </substep>

  <substep n="3b" title="Category B — Playwright TypeScript">
    <critical>ONLY generate Cat B if ux-elements.md was embedded. Otherwise skip Cat B and add a note in the Master Checklist.</critical>
    <action>Each Cat B test is its OWN standalone `test()` function — NEVER nested inside `test.describe()`. The output parser extracts tests by `### E{epic_num}-B##:` header.</action>
    <action>Emit a Markdown subsection per test with header `### E{epic_num}-B##: {test_name}` and a single typescript code block containing:
      - Header comment: test ID, requirement reference, "Selectors from: ux-elements.md".
      - `test('{description}', async ({ page }) =&gt; { ... })` with `await page.goto('http://localhost:8765/')` and `await page.waitForLoadState('domcontentloaded')`.
      - Use `forceClick(page, '[data-testid="..."]')` for footer / off-viewport elements; the helper is included in the template's Cat B section header.
      - Wait conditions: `await expect(page.locator('[data-testid="..."]')).toBeVisible()` — check visibility, NOT classes (Alpine.js uses `x-show`).
      - All `data-testid` values copied EXACTLY from `ux-elements.md`.
    </action>
  </substep>

  <substep n="3c" title="Category C — human verification">
    <action>Each Cat C test is a Markdown subsection with `### E{epic_num}-C##: {test_name}` containing:
      - Requirement reference.
      - Prerequisites list.
      - Numbered steps.
      - Expected result.
      - Pass-criteria checklist using `- [ ]` checkbox syntax.
    </action>
  </substep>
</step>

<step n="4" goal="Assemble plan and write output">
  <action>Compose the full plan using the embedded `template.md` structure:
    1. Header (`# E2E Test Plan - Epic {epic_num}`, generated date, epic title).
    2. **Setup** section (PROJECT_ROOT export + venv activation).
    3. **Test Categories Summary** table with counts (A / B / C / total).
    4. **Master Checklist** table — every test by ID, name, category, status (initially "pending").
    5. **Category A Tests** section with prerequisites + every Cat A subsection from step 3a.
    6. **Category B Tests** section with prerequisites, the `forceClick` helper, and every Cat B subsection (or a note if ux-elements was missing).
    7. **Category C Tests** section with every Cat C subsection.
    8. **Traceability Matrix** table — every requirement (FR/AC/NFR) mapped to its covering test IDs and coverage status.
    9. **Notes** — flag any missing selectors, untestable requirements, or gaps.
    10. **Automation Recommendations** — actionable improvements for `ux-elements.md`, story specs, or test infrastructure.
    11. End with `&lt;!-- QA_PLAN_END --&gt;`.
  </action>
  <action>Write the assembled plan to `{output_file}` (creating `{qa_artifacts}/test-plans/` if it does not exist).</action>
</step>

<step n="5" goal="Report summary">
  <action>Emit a stdout summary:
    ```
    ## QA Plan Generated

    **Epic:** {epic_num}
    **Output:** {output_file}

    **Test Counts:**
    - Category A (CLI/API): {count_a} tests
    - Category B (Playwright): {count_b} tests
    - Category C (Human): {count_c} tests
    - **Total:** {total} tests

    **Notes:** {warnings_or_none}

    **Next Steps:**
    1. Review generated test plan
    2. Run Category A tests: `bmad-assist qa execute --epic {epic_num} --category A`
    3. Run Category B tests: `bmad-assist qa execute --epic {epic_num} --category B`
    4. Complete Category C manual tests
    ```
  </action>
</step>

</workflow>
