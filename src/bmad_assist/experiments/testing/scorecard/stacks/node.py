"""Node/TypeScript stack handler — build (npm), tests (vitest/jest), linting (eslint), security (npm audit)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..base import BaseStackHandler
from ..helpers import score_test_results, soft_skip
from ..registry import register_stack
from ..security import empty_security_result, extract_npm_audit_issues, score_security_gradient

# Correctness proxy patterns for Node/TS files (advisory, not scored)
_NODE_CORRECTNESS_PROXIES: list[dict[str, Any]] = [
    {
        "id": "any_cast",
        "pattern": r"as\s+any\b|:\s*any\b",
        "guard": None,
        "description": "TypeScript 'any' type usage (bypasses type safety)",
    },
    {
        "id": "ts_ignore",
        "pattern": r"@ts-ignore|@ts-nocheck",
        "guard": None,
        "description": "TypeScript error suppression directive",
    },
    {
        "id": "unhandled_promise",
        "pattern": r"\.then\s*\(",
        "guard": r"\.catch\s*\(",
        "description": "promise chain without .catch() error handler",
    },
    {
        "id": "console_log",
        "pattern": r"console\.log\(",
        "guard": None,
        "description": "console.log left in production code",
    },
    {
        "id": "no_error_boundary",
        "pattern": r"export\s+(?:default\s+)?(?:function|class)\s+\w+.*Component",
        "guard": r"ErrorBoundary|componentDidCatch",
        "description": "React component without ErrorBoundary",
    },
]


@register_stack
class NodeStackHandler(BaseStackHandler):
    """Handler for Node/TypeScript projects."""

    @property
    def name(self) -> str:
        """Return stack identifier."""
        return "node"

    @property
    def marker_files(self) -> list[str]:
        """Return marker files for Node projects."""
        return ["package.json"]

    @property
    def comment_prefix(self) -> str:
        """Return JS/TS comment prefix."""
        return "//"

    @property
    def source_globs(self) -> list[str]:
        """Return Node/TS source file glob patterns."""
        return ["*.ts", "*.js", "*.tsx", "*.jsx", "*.svelte"]

    @property
    def extra_src_dirs(self) -> list[str]:
        """Return extra source directories for Node projects."""
        return ["pages", "components", "routes"]

    @property
    def correctness_proxies(self) -> list[dict[str, Any]]:
        """Return Node/TS correctness proxy patterns."""
        return _NODE_CORRECTNESS_PROXIES

    @property
    def source_extensions(self) -> set[str]:
        """Return Node/TS source file extensions."""
        return {".ts", ".js", ".tsx", ".jsx"}

    def score_build(self, fixture_path: Path) -> dict[str, Any]:
        """Score Node/TS build success."""
        result_dict: dict[str, Any] = {
            "max": 10,
            "score": 0,
            "success": False,
            "command": "",
            "errors": [],
        }
        try:
            pkg_json = json.loads((fixture_path / "package.json").read_text(encoding="utf-8"))
        except Exception:
            pkg_json = {}

        scripts = pkg_json.get("scripts", {})
        if "build" in scripts:
            result_dict["command"] = "npm run build"
            try:
                result = subprocess.run(
                    ["npm", "run", "build"],
                    cwd=fixture_path,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                result_dict["success"] = result.returncode == 0
                result_dict["score"] = 10 if result.returncode == 0 else 0
                if result.returncode != 0:
                    result_dict["errors"] = result.stderr.split("\n")[:5]
            except subprocess.TimeoutExpired:
                result_dict["errors"] = ["npm run build timed out"]
            except Exception as e:
                result_dict["errors"] = [str(e)]
        elif (fixture_path / "tsconfig.json").exists():
            result_dict["command"] = "npx tsc --noEmit"
            tsc_bin = fixture_path / "node_modules" / ".bin" / "tsc"
            if tsc_bin.exists():
                try:
                    result = subprocess.run(
                        [str(tsc_bin), "--noEmit"],
                        cwd=fixture_path,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    result_dict["success"] = result.returncode == 0
                    result_dict["score"] = 10 if result.returncode == 0 else 0
                    if result.returncode != 0:
                        result_dict["errors"] = result.stdout.split("\n")[:5]
                except subprocess.TimeoutExpired:
                    result_dict["errors"] = ["tsc timed out"]
                except Exception as e:
                    result_dict["errors"] = [str(e)]
            else:
                result_dict = soft_skip(10, "tsc", "tsc not available in node_modules")
        else:
            result_dict = soft_skip(10, "npm", "no build script or tsconfig.json found")

        return result_dict

    def score_unit_tests(self, fixture_path: Path) -> dict[str, Any]:
        """Score Node/TS unit test results."""
        result_dict: dict[str, Any] = {
            "max": 10,
            "score": 0,
            "metric": "0/0",
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "errors": [],
        }
        try:
            pkg_json = json.loads((fixture_path / "package.json").read_text(encoding="utf-8"))
        except Exception:
            pkg_json = {}

        dev_deps = pkg_json.get("devDependencies", {})
        deps = pkg_json.get("dependencies", {})
        all_deps = {**deps, **dev_deps}

        vitest_bin = fixture_path / "node_modules" / ".bin" / "vitest"
        jest_bin = fixture_path / "node_modules" / ".bin" / "jest"

        if "vitest" in all_deps and vitest_bin.exists():
            try:
                result = subprocess.run(
                    [str(vitest_bin), "run", "--reporter=json"],
                    cwd=fixture_path,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                json_text = result.stdout or result.stderr
                try:
                    test_data = json.loads(json_text)
                    if "numPassedTests" not in test_data and "numFailedTests" not in test_data:
                        result_dict["errors"] = ["vitest output missing test counts"]
                    else:
                        passed = test_data.get("numPassedTests", 0)
                        failed = test_data.get("numFailedTests", 0)
                        result_dict = score_test_results(passed, failed)
                except json.JSONDecodeError:
                    stdout = result.stdout + result.stderr
                    match = re.search(r"\b(\d+) passed\b", stdout)
                    fail_match = re.search(r"\b(\d+) failed\b", stdout)
                    passed = int(match.group(1)) if match else 0
                    failed = int(fail_match.group(1)) if fail_match else 0
                    result_dict = score_test_results(passed, failed)
            except subprocess.TimeoutExpired:
                result_dict["errors"] = ["vitest timed out"]
            except Exception as e:
                result_dict["errors"] = [str(e)]
        elif "jest" in all_deps and jest_bin.exists():
            try:
                result = subprocess.run(
                    [str(jest_bin), "--json"],
                    cwd=fixture_path,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                try:
                    test_data = json.loads(result.stdout)
                    if "numPassedTests" not in test_data and "numFailedTests" not in test_data:
                        result_dict["errors"] = ["jest output missing test counts"]
                    else:
                        passed = test_data.get("numPassedTests", 0)
                        failed = test_data.get("numFailedTests", 0)
                        result_dict = score_test_results(passed, failed)
                except json.JSONDecodeError:
                    result_dict["errors"] = ["failed to parse jest JSON output"]
            except subprocess.TimeoutExpired:
                result_dict["errors"] = ["jest timed out"]
            except Exception as e:
                result_dict["errors"] = [str(e)]
        else:
            result_dict = soft_skip(
                10, "vitest/jest", "no test framework detected or not installed in node_modules"
            )

        return result_dict

    def score_linting(self, fixture_path: Path) -> dict[str, Any]:
        """Score ESLint linting results."""
        result_dict: dict[str, Any] = {
            "max": 6,
            "score": 0,
            "tool": "",
            "errors": 0,
            "warnings": 0,
            "top_issues": [],
        }
        eslint_bin = fixture_path / "node_modules" / ".bin" / "eslint"
        if eslint_bin.exists() or shutil.which("eslint"):
            result_dict["tool"] = "eslint"
            eslint_cmd = [str(eslint_bin)] if eslint_bin.exists() else ["eslint"]
            try:
                result = subprocess.run(
                    eslint_cmd + [".", "--format", "json", "--no-error-on-unmatched-pattern"],
                    cwd=fixture_path,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode < 0:
                    result_dict = soft_skip(6, "eslint", "eslint killed by signal")
                else:
                    stderr_lower = (result.stderr or "").lower()
                    if (
                        any(
                            msg in stderr_lower
                            for msg in (
                                "couldn't find",
                                "no eslint configuration",
                                "eslintrc",
                                "config file",
                                "plugin",
                                "failed to load",
                            )
                        )
                        and not result.stdout.strip()
                    ):
                        result_dict = soft_skip(6, "eslint", "ESLint configuration error")
                    else:
                        try:
                            eslint_data = json.loads(result.stdout) if result.stdout.strip() else []
                            error_count = sum(
                                1
                                for entry in eslint_data
                                for msg in entry.get("messages", [])
                                if msg.get("severity") == 2
                            )
                            result_dict["errors"] = error_count
                            result_dict["score"] = round(max(0, 6 - error_count), 1)
                        except json.JSONDecodeError:
                            result_dict = soft_skip(6, "eslint", "failed to parse eslint output")
            except subprocess.TimeoutExpired:
                result_dict = soft_skip(6, "eslint", "eslint timed out")
            except Exception as e:
                result_dict["skipped"] = True
                result_dict["reason"] = f"eslint failed: {e}"
        else:
            result_dict = soft_skip(6, "eslint", "eslint not available")

        return result_dict

    def score_complexity(self, fixture_path: Path) -> dict[str, Any]:
        """Score TypeScript complexity (not measured)."""
        return {
            "max": 4,
            "score": 0,
            "tool": "none",
            "not_measured": True,
            "reason": "no reliable TypeScript complexity tool available",
        }

    def score_security(self, fixture_path: Path, kloc: float) -> dict[str, Any]:
        """Score npm audit security results."""
        result_dict: dict[str, Any] = {
            "max": 4,
            "score": 0,
            "tool": "",
            "high": 0,
            "medium": 0,
            "low": 0,
            "issues": [],
        }
        if shutil.which("npm"):
            try:
                result = subprocess.run(
                    ["npm", "audit", "--json"],
                    cwd=fixture_path,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode < 0:
                    result_dict = soft_skip(
                        4, "npm_audit", f"npm audit killed by signal {result.returncode}"
                    )
                else:
                    stdout = result.stdout.strip()
                    if not stdout:
                        if result.returncode != 0:
                            result_dict["errors"] = [
                                f"npm audit exited {result.returncode} with empty output"
                            ]
                        else:
                            result_dict = empty_security_result("npm_audit", kloc)
                    else:
                        try:
                            audit_data = json.loads(stdout)
                            if "error" in audit_data:
                                result_dict = empty_security_result("npm_audit", kloc)
                                result_dict["warning"] = (
                                    "npm audit returned error (misconfigured project?)"
                                )
                            else:
                                npm_issues = extract_npm_audit_issues(audit_data)
                                if npm_issues is None:
                                    result_dict = empty_security_result("npm_audit", kloc)
                                else:
                                    result_dict = score_security_gradient(
                                        npm_issues,
                                        kloc,
                                        tool_name="npm_audit",
                                    )
                        except json.JSONDecodeError:
                            result_dict["errors"] = ["failed to parse npm audit output"]
            except subprocess.TimeoutExpired:
                result_dict = soft_skip(4, "npm_audit", "npm audit timed out")
            except Exception as e:
                result_dict["skipped"] = True
                result_dict["reason"] = f"npm audit failed: {e}"
        else:
            result_dict = soft_skip(4, "npm", "npm not installed")

        return result_dict
