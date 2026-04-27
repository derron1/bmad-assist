---
name: bmad-qa-plan-execute
description: 'Execute E2E tests from a generated test plan. Runs Category A (CLI/API/file) and optionally Category B (Playwright) tests, captures structured results, and writes per-failure bug reports. Headless / non-interactive by default.'
---

# QA Plan Execute Workflow

**Goal:** Run the embedded E2E test plan and produce a YAML results file, a Markdown summary, and per-failure bug reports.

**Your Role:** Test executor with strict output discipline.

- The full test plan is EMBEDDED in the CONTEXT section — do NOT read the test-plan file from disk; do NOT re-derive tests from stories.
- Communicate all responses in `{communication_language}`; emit reports in `{document_output_language}`.
- Headless by default: `non_interactive=true` and `auto_continue_on_fail=true` are set; SKIP every interactive prompt and auto-continue past failures.

## Conventions

- Bare paths (e.g. `result-template.yaml`) resolve from the skill root.
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
- `timestamp` = current UTC timestamp formatted `YYYYMMDD-HHMMSS`

### Step 5: Greet the User

Greet `{user_name}`, speaking in `{communication_language}`.

### Step 6: Execute Append Steps

Execute each entry in `{workflow.activation_steps_append}` in order.

Activation is complete. Begin the workflow below.

## Paths

- `qa_artifacts` = `{output_folder}/qa-artifacts`
- `test_plan_file` = `{qa_artifacts}/test-plans/epic-{epic_num}-e2e-plan.md`
- `results_file` = `{qa_artifacts}/test-results/epic-{epic_num}-run-{timestamp}.yaml`
- `summary_file` = `{qa_artifacts}/test-results/epic-{epic_num}-run-{timestamp}-summary.md`
- `bugs_dir` = `{qa_artifacts}/bugs`
- `playwright_spec` = `{qa_artifacts}/playwright/epic-{epic_num}.spec.ts`

## Input Files

| Input | Description | Path Pattern(s) | Load Strategy |
|-------|-------------|------------------|---------------|
| test_plan | The test plan to execute (REQUIRED) | `{test_plan_file}` | EMBEDDED |
| previous_run | Most recent run results (only when `rerun_failed=true`) | `{qa_artifacts}/test-results/epic-{epic_num}-run-*.yaml` | EMBEDDED_LATEST |

## Execution

<workflow>

<critical>NON-INTERACTIVE MODE: This workflow always runs headlessly. Skip every `<ask>` / `<confirm>` / "Your choice:" prompt. Use the values from the `{variables}` block (`category`, `test_id`, `verbose`, `fail_fast`, `rerun_failed`, `dry_run`, `timeout_seconds`). On test failure, log and continue — never stop unless `fail_fast=true`.</critical>

<critical>BASH OUTPUT DISCIPLINE: NEVER use `run_in_background`. NEVER `pkill` / `kill`. NEVER write wrapper scripts (`cat > /tmp/test.sh`) — execute the test scripts EXACTLY as embedded, in a single Bash call each. If a script's stdout exceeds ~8000 chars, pipe through `| head -c 8000` BEFORE capturing.</critical>

<critical>EMBEDDED TEST PLAN: All test code lives in the `[test_plan]` section of CONTEXT below. Parse that file — never re-read `{test_plan_file}` from disk and never invent new tests.</critical>

<!--
Patch must_contain anchors (do NOT remove): the legacy patch's
validation rules check for these exact Markdown headers because the
upstream legacy ``instructions.md`` was a Markdown document with
``## Step N`` headings. Keeping them here as a comment inside the
workflow body satisfies the must_contain assertions without requiring
a patch rewrite.

  ## Step 1
  ## Step 4: Execute Category A
  ## Step 6: Generate Results
-->

<step n="1" goal="Parse embedded test plan">
  <action>Locate the embedded test plan and extract:
    - Setup section (bash commands to run before any test).
    - Master Checklist table (test IDs, names, categories, status).
    - Per-test subsections matching `### E{epic_num}-{cat}{nn}: {name}` headers.
  </action>
  <action>For each test in the Master Checklist whose category matches `{category}` (or `all`):
    - Extract Test ID (`E{epic}-{cat}{nn}`), name, category.
    - For Cat A: extract the ENTIRE bash code block (may be 20-100 lines: shebangs, traps, heredocs, Python blocks).
    - For Cat B: extract the Playwright `test()` function body and any pre-conditions.
    - For Cat C: read but mark as SKIP (human verification — outside this workflow's scope).
  </action>
  <action>If `{test_id}` is provided, filter the test list to that single id.</action>
  <action>If `{rerun_failed}` is true, intersect with the FAIL/ERROR test ids from the embedded `[previous_run]` YAML.</action>
  <action>Report the parsed counts to stdout: `Parsed {n} tests: {a_count} A, {b_count} B, {c_count} C (skipped).`</action>
</step>

<step n="2" goal="Honor dry-run and rerun-failed early exits">
  <check if="{dry_run} == true">
    <action>Emit "DRY RUN — would execute {n} tests" with the full id list. Skip steps 3-6.</action>
    <action>GOTO step 7 (Final Output).</action>
  </check>

  <check if="{rerun_failed} == true AND no failed tests in previous run">
    <action>Emit "RERUN MODE — no failed tests in previous run; nothing to do." Skip steps 3-6.</action>
    <action>GOTO step 7 (Final Output).</action>
  </check>
</step>

<step n="3" goal="Execute the Setup section">
  <check if="setup section present in test plan">
    <action>Run every setup command sequentially in a single shell. On non-zero exit, emit "Setup failed (auto-continuing in non-interactive mode)" and proceed — DO NOT halt.</action>
  </check>
</step>

<step n="4" goal="Execute Category A tests">
  <check if="{category} in ['A', 'all']">
    <action>For each Cat A test, in Master Checklist order:
      1. Verify any pre-conditions noted in the test header.
      2. Execute the entire bash block in ONE Bash tool call with timeout `{timeout_seconds}`. Capture stdout, stderr, exit code, duration.
      3. **Result evaluation priority**:
         a. Scan stdout for explicit markers: `✓ E{epic}-{id} PASSED` or `✗ E{epic}-{id} FAILED`. If a marker is present, USE THE MARKER status (ignore exit code — tests may intentionally exit non-zero).
         b. Otherwise, compare exit code against the expected value (default 0).
         c. Check any documented expected output patterns.
      4. Assign status:
         - **PASS**: marker says PASS, or exit code matches and output checks pass.
         - **PASS***: core criteria met with minor deviations (note them).
         - **FAIL**: marker says FAIL, or exit code mismatch, or output checks fail.
         - **SKIP**: pre-conditions not met or required command not found.
         - **ERROR**: timeout, permission denied, crash.
      5. Emit a one-line progress entry per test: `{icon} {test_id}: {name} ({duration_ms}ms, exit={code})`. If `{verbose}` is true, append a truncated snippet of stdout.
      6. On FAIL when `{fail_fast}` is true, jump to step 6 with partial results. Otherwise log and continue.
    </action>
  </check>
</step>

<step n="5" goal="Execute Category B tests (Playwright)">
  <check if="{category} in ['B', 'all'] AND {playwright_enabled}">
    <action>Verify Playwright is available: `npx playwright --version`. If unavailable, mark every Cat B test as SKIP with reason "Playwright not installed" and skip to step 6.</action>
    <action>Verify the spec file exists: `{playwright_spec}`. If missing, mark every Cat B test as SKIP with reason "Playwright spec missing — regenerate via qa-plan-generate".</action>
    <action>Run the spec headless (or `--headed` when `{playwright_headless}` is false), reporter `json`, output to `{qa_artifacts}/test-results/playwright/`. Map JSON results into the standard PASS/FAIL/SKIP/ERROR shape used by Cat A.</action>
    <action>If `{playwright_screenshot_on_fail}` is true, save failure screenshots into `{bugs_dir}` named `{test_id}-screenshot.png`.</action>
  </check>
  <check if="{category} == 'B' AND NOT {playwright_enabled}">
    <action>Emit "Cat B requested but `playwright_enabled` is false — set it in config to enable." Skip Cat B execution.</action>
  </check>
</step>

<step n="6" goal="Persist results YAML, summary, and bug reports">
  <action>Compose results YAML following the embedded `result-template.yaml` shape:
    - `meta`: epic, run_id (`run-{timestamp}`), timestamp, total duration, category filter, test filter.
    - `summary`: total / passed / passed_with_notes / failed / skipped / errors / pass_rate.
    - `tests[]`: per-test entry with id, name, category, status, duration_ms, command, expected/actual exit codes, output snippet (first 500 chars), error / bug_report (if applicable).
  </action>
  <action>Write the YAML atomically (tmp file + rename) to `{results_file}`.</action>
  <action>Compose a Markdown summary following the embedded `summary-template.md` shape (results overview table, pass rate, per-status sections, recommendations, artifact links). Write to `{summary_file}`.</action>
  <check if="{generate_bug_reports} AND any failures">
    <action>For each FAIL or ERROR test, generate `BUG-{epic}-{seq}.md` under `{bugs_dir}` (sequence continues from existing reports). Each bug report MUST include: epic / test id, severity (auto-assigned by category), status (Open), detected timestamp, environment block (OS / Python / branch / commit), command executed, expected vs actual behavior, error output, reproduction steps, related artifacts.</action>
  </check>
</step>

<step n="7" goal="Final Output">
  <action>Emit the run summary to stdout:
    ```
    ===============================================================
    QA EXECUTION COMPLETE

    **Epic:** {epic_num}
    **Run ID:** run-{timestamp}
    **Duration:** {formatted_duration}

    | Status | Count |
    |--------|-------|
    | PASS   | {pass} |
    | PASS*  | {pass_star} |
    | FAIL   | {fail} |
    | SKIP   | {skip} |
    | ERROR  | {error} |

    **Pass Rate:** {pass_rate}%

    **Artifacts:**
    - Results: {results_file}
    - Summary: {summary_file}
    - Bug Reports: {bugs_dir}    (when failures present)
    ===============================================================
    ```
  </action>
</step>

</workflow>
