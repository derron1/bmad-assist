"""Run the project's quality-gate hook command (pre-commit by default).

Hard-fail mode: if any configured hook fails, the gate result is
``passed=False`` and the caller (typically a phase handler) treats the
phase as failed. Captured stdout/stderr is preserved verbatim for the
user to triage.

The module is dependency-light by design — it depends only on the
standard library and exposes a small frozen dataclass result plus a
single callable. No bmad-assist subsystem imports.
"""

from __future__ import annotations

import logging
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Match pre-commit hook result lines:
#   "ruff (lint)..............................................................Passed"
#   "detect-secrets...........................................................Failed"
#   "check json...........................................(no files to check)Skipped"
#
# The hook NAME may contain spaces and parentheses; the column of dots is
# variable-width; the suffix is exactly one of Passed / Failed / Skipped.
# We capture the name as everything from start-of-line up to the first run of
# >=3 dots, then look for the literal "Failed" terminator (optionally preceded
# by a parenthesised note such as "(no files to check)" — though that suffix
# is conventionally used with "Skipped" rather than "Failed").
_HOOK_LINE_RE = re.compile(
    r"^(?P<name>.+?)\.{3,}(?:\([^)]*\))?(?P<status>Passed|Failed|Skipped)\s*$"
)


@dataclass(frozen=True)
class QualityGateResult:
    """Result of running the project's quality-gate hooks on a set of files.

    Attributes:
        passed: True iff the hook command exited 0 and the gate actually ran.
            Always False when ``skipped`` is True.
        output: Combined stdout+stderr captured from the hook command (empty
            string when skipped before invocation).
        failed_hooks: Tuple of hook names parsed from the captured output as
            having status ``Failed``. Empty when ``passed`` or when output
            could not be parsed (e.g. timeout before any hook completed).
        skipped: True when the gate did not run at all (no files, no config,
            executable missing). Distinct from a fail.
        skip_reason: Human-readable reason populated whenever ``skipped`` is
            True; None otherwise.
        duration_ms: Wall-clock duration of the hook invocation in
            milliseconds. Zero for skip cases that returned before invoking
            the subprocess.

    """

    passed: bool
    output: str = ""
    failed_hooks: tuple[str, ...] = field(default_factory=tuple)
    skipped: bool = False
    skip_reason: str | None = None
    duration_ms: int = 0


def parse_failed_hooks(output: str) -> tuple[str, ...]:
    """Extract hook names with status ``Failed`` from pre-commit-style output.

    Each pre-commit line follows the pattern
    ``<name><dots><optional-paren-note><Status>``. We line-scan and keep only
    those whose terminating status is exactly ``Failed``.

    Args:
        output: Combined stdout/stderr from the hook command.

    Returns:
        A tuple of hook names in the order they appeared in the output.

    """
    failed: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        match = _HOOK_LINE_RE.match(line)
        if match is None:
            continue
        if match.group("status") != "Failed":
            continue
        name = match.group("name").strip()
        if name:
            failed.append(name)
    return tuple(failed)


def _format_files(changed_files: list[Path], project_root: Path) -> str:
    """Render ``changed_files`` as a shell-safe, space-separated string.

    Files inside ``project_root`` are rendered as paths relative to it so the
    hook output matches the conventional pre-commit display ("src/foo.py"
    rather than an absolute path). Each path is individually quoted with
    ``shlex.quote`` so spaces and other shell metacharacters are safe.
    """
    resolved_root = project_root.resolve()
    rendered: list[str] = []
    for path in changed_files:
        try:
            relative = path.resolve().relative_to(resolved_root)
            rendered.append(shlex.quote(str(relative)))
        except ValueError:
            # File is outside project_root — fall back to its given form.
            rendered.append(shlex.quote(str(path)))
    return " ".join(rendered)


def _substitute_files(hook_command: str, files_str: str) -> str:
    """Substitute the literal ``{files}`` placeholder in ``hook_command``."""
    return hook_command.replace("{files}", files_str)


def _resolve_executable(command_tokens: list[str]) -> str | None:
    """Return the absolute path of the command's executable, or None if missing.

    Honors ``PATH`` exactly as a subprocess invocation would.
    """
    if not command_tokens:
        return None
    return shutil.which(command_tokens[0])


def run_quality_gate(
    *,
    changed_files: list[Path],
    project_root: Path,
    hook_command: str = "pre-commit run --files {files}",
    skip_if_no_config: bool = True,
    timeout_seconds: int = 300,
) -> QualityGateResult:
    """Run the project's pre-commit hooks against the changed files.

    Args:
        changed_files: Files modified by the phase under inspection. An empty
            list short-circuits to a ``skipped`` result.
        project_root: Project working directory. Used as the subprocess
            ``cwd`` and as the anchor for converting changed-file paths to
            relative form.
        hook_command: Shell-style command template. Must contain a literal
            ``{files}`` placeholder, which is substituted with the
            shell-quoted file list before invocation. Default invokes
            pre-commit.
        skip_if_no_config: When True (default), skip the gate entirely if
            ``project_root/.pre-commit-config.yaml`` does not exist. When
            False, attempt the command regardless — the command itself
            decides what to do.
        timeout_seconds: Hard cap on the hook command's wall-clock duration.
            On timeout the process is killed and ``passed=False`` is returned
            with a timeout marker in ``output``.

    Returns:
        A :class:`QualityGateResult` describing the outcome. See the
        dataclass docstring for the meaning of each field.

    """
    # Skip: nothing to check.
    if not changed_files:
        logger.info("quality_gate: skipped (no files changed)")
        return QualityGateResult(
            passed=False,
            skipped=True,
            skip_reason="no files changed",
        )

    # Skip: missing pre-commit config.
    if skip_if_no_config:
        config_path = project_root / ".pre-commit-config.yaml"
        if not config_path.exists():
            logger.info(
                "quality_gate: skipped (no .pre-commit-config.yaml at %s)",
                config_path,
            )
            return QualityGateResult(
                passed=False,
                skipped=True,
                skip_reason="no .pre-commit-config.yaml found",
            )

    files_str = _format_files(changed_files, project_root)
    rendered_command = _substitute_files(hook_command, files_str)

    try:
        command_tokens = shlex.split(rendered_command)
    except ValueError as exc:
        # Malformed command string — treat as fail with a clear message
        # rather than as a skip; the user asked us to run something specific.
        msg = f"quality_gate: failed to parse hook_command: {exc}"
        logger.error(msg)
        return QualityGateResult(
            passed=False,
            output=msg,
            failed_hooks=(),
            skipped=False,
            skip_reason=None,
            duration_ms=0,
        )

    # Skip: executable not on PATH.
    executable = _resolve_executable(command_tokens)
    if executable is None:
        program = command_tokens[0] if command_tokens else hook_command
        reason = f"pre-commit not available: {program} not found on PATH"
        logger.info("quality_gate: skipped (%s)", reason)
        return QualityGateResult(
            passed=False,
            skipped=True,
            skip_reason=reason,
        )

    logger.info("quality_gate: running hooks on %d file(s)", len(changed_files))
    logger.debug("quality_gate: command=%r files=%r", rendered_command, changed_files)

    start = time.monotonic()
    try:
        completed = subprocess.run(
            command_tokens,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        # Partial output may be available on the exception object.
        partial_stdout = _decode_stream(exc.stdout)
        partial_stderr = _decode_stream(exc.stderr)
        combined = _combine(partial_stdout, partial_stderr)
        timeout_marker = (
            f"\n[quality_gate] TIMEOUT after {timeout_seconds}s — "
            f"process killed. Command: {rendered_command}\n"
        )
        output = (combined + timeout_marker) if combined else timeout_marker.lstrip("\n")
        logger.warning(
            "quality_gate: timeout after %ss (duration=%dms)",
            timeout_seconds,
            duration_ms,
        )
        return QualityGateResult(
            passed=False,
            output=output,
            failed_hooks=parse_failed_hooks(output),
            skipped=False,
            skip_reason=None,
            duration_ms=duration_ms,
        )

    duration_ms = int((time.monotonic() - start) * 1000)
    output = _combine(completed.stdout or "", completed.stderr or "")

    if completed.returncode == 0:
        logger.info("quality_gate: passed (duration=%dms)", duration_ms)
        return QualityGateResult(
            passed=True,
            output=output,
            failed_hooks=(),
            skipped=False,
            skip_reason=None,
            duration_ms=duration_ms,
        )

    failed = parse_failed_hooks(output)
    logger.warning(
        "quality_gate: failed (exit=%d, duration=%dms, failed_hooks=%s)",
        completed.returncode,
        duration_ms,
        list(failed),
    )
    return QualityGateResult(
        passed=False,
        output=output,
        failed_hooks=failed,
        skipped=False,
        skip_reason=None,
        duration_ms=duration_ms,
    )


def _decode_stream(stream: bytes | str | None) -> str:
    """Best-effort decode of a subprocess stdout/stderr payload."""
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream


def _combine(stdout: str, stderr: str) -> str:
    """Combine stdout and stderr into a single human-readable blob.

    stderr is appended after stdout with a separator only when both are
    non-empty, to keep the common all-stdout case clean.
    """
    if stdout and stderr:
        return f"{stdout}\n{stderr}" if not stdout.endswith("\n") else f"{stdout}{stderr}"
    return stdout or stderr
