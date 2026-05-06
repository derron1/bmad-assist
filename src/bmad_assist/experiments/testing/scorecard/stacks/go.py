"""Go stack handler — build, tests, linting (go vet), complexity (gocyclo), security (gosec)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..base import BaseStackHandler
from ..helpers import soft_skip
from ..registry import register_stack
from ..security import empty_security_result, score_security_gradient

# Gosec rules known to produce frequent false positives in well-structured code
_GOSEC_FP_RULES = {"G104", "G301", "G304", "G307"}

# Correctness proxy patterns for Go files (advisory, not scored)
_GO_CORRECTNESS_PROXIES = [
    {
        "id": "goroutine_no_ctx",
        "pattern": r"go func\(",
        "guard": r"ctx|context\.",
        "description": "goroutine launched without context propagation",
    },
    {
        "id": "lock_no_defer",
        "pattern": r"\.Lock\(\)",
        "guard": r"defer.*Unlock\(\)",
        "description": "mutex locked without defer Unlock",
    },
    {
        "id": "http_no_timeout",
        "pattern": r"http\.Client\{",
        "guard": r"Timeout:",
        "description": "http.Client without Timeout",
    },
    {
        "id": "sql_no_pool_limit",
        "pattern": r"sql\.Open\(",
        "guard": r"SetMaxOpenConns",
        "description": "sql.Open without connection pool limit",
    },
]


@register_stack
class GoStackHandler(BaseStackHandler):
    """Handler for Go projects."""

    @property
    def name(self) -> str:
        """Return stack identifier."""
        return "go"

    @property
    def marker_files(self) -> list[str]:
        """Return marker files for Go projects."""
        return ["go.mod"]

    @property
    def comment_prefix(self) -> str:
        """Return Go comment prefix."""
        return "//"

    @property
    def source_globs(self) -> list[str]:
        """Return Go source file glob patterns."""
        return ["*.go"]

    @property
    def correctness_proxies(self) -> list[dict[str, Any]]:
        """Return Go correctness proxy patterns."""
        return _GO_CORRECTNESS_PROXIES

    @property
    def source_extensions(self) -> set[str]:
        """Return Go source file extensions."""
        return {".go"}

    def score_build(self, fixture_path: Path) -> dict[str, Any]:
        """Score Go build success."""
        result_dict: dict[str, Any] = {
            "max": 10,
            "score": 0,
            "success": False,
            "command": "go build ./...",
            "errors": [],
        }
        try:
            result = subprocess.run(
                ["go", "build", "./..."],
                cwd=fixture_path,
                capture_output=True,
                text=True,
                timeout=120,
            )
            result_dict["success"] = result.returncode == 0
            result_dict["score"] = 10 if result.returncode == 0 else 0
            if result.returncode != 0:
                result_dict["errors"] = result.stderr.split("\n")[:5]
        except Exception as e:
            result_dict["errors"] = [str(e)]
        return result_dict

    def score_unit_tests(self, fixture_path: Path) -> dict[str, Any]:
        """Score Go unit test results."""
        from ..helpers import score_test_results

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
            result = subprocess.run(
                ["go", "test", "-json", "./..."],
                cwd=fixture_path,
                capture_output=True,
                text=True,
                timeout=300,
            )
            passed = failed = skipped = 0
            for line in result.stdout.split("\n"):
                if '"Action":"pass"' in line and '"Test":' in line:
                    passed += 1
                elif '"Action":"fail"' in line and '"Test":' in line:
                    failed += 1
                elif '"Action":"skip"' in line and '"Test":' in line:
                    skipped += 1
            result_dict = score_test_results(passed, failed, skipped)
        except Exception as e:
            result_dict["errors"] = [str(e)]
        return result_dict

    def score_linting(self, fixture_path: Path) -> dict[str, Any]:
        """Score Go linting results."""
        result_dict: dict[str, Any] = {
            "max": 6,
            "score": 0,
            "tool": "go vet",
            "errors": 0,
            "warnings": 0,
            "top_issues": [],
        }

        if shutil.which("golangci-lint"):
            result_dict["tool"] = "golangci-lint"
            try:
                result = subprocess.run(
                    [
                        "golangci-lint",
                        "run",
                        "--no-config",
                        "--disable-all",
                        "--enable",
                        "govet,errcheck,staticcheck,unused",
                        "--out-format",
                        "json",
                        "./...",
                    ],
                    cwd=fixture_path,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode < 0 or result.returncode >= 2:
                    raise RuntimeError(f"golangci-lint exit code {result.returncode}")
                try:
                    lint_data = json.loads(result.stdout) if result.stdout.strip() else {}
                    issues = lint_data.get("Issues", []) or []
                    error_count = len(issues)
                    result_dict["errors"] = error_count
                    result_dict["score"] = round(max(0, 6 - error_count), 1)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("failed to parse golangci-lint JSON") from exc
            except subprocess.TimeoutExpired:
                result_dict = soft_skip(6, "golangci-lint", "golangci-lint timed out")
            except RuntimeError:
                # Fall back to go vet
                result_dict = self._run_go_vet(fixture_path)
        else:
            result_dict = self._run_go_vet(fixture_path)

        return result_dict

    def score_complexity(self, fixture_path: Path) -> dict[str, Any]:
        """Score Go cyclomatic complexity."""
        result_dict: dict[str, Any] = {
            "max": 4,
            "score": 0,
            "tool": "",
            "average": 0.0,
            "max_function": "",
            "max_value": 0,
        }
        if shutil.which("gocyclo"):
            result_dict["tool"] = "gocyclo"
            try:
                result = subprocess.run(
                    ["gocyclo", "-avg", "."],
                    cwd=fixture_path,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                found_avg = False
                for line in result.stdout.split("\n"):
                    if "Average" in line:
                        match = re.search(r"(\d+\.?\d*)", line)
                        if match:
                            avg = float(match.group(1))
                            result_dict["average"] = avg
                            result_dict["score"] = 4 if avg < 10 else (2 if avg < 15 else 0)
                            found_avg = True
                            break
                if not found_avg and result.returncode == 0:
                    result_dict["score"] = 4
                    result_dict["average"] = 0.0
            except subprocess.TimeoutExpired:
                result_dict["skipped"] = True
                result_dict["reason"] = "gocyclo timed out"
            except Exception as e:
                result_dict["skipped"] = True
                result_dict["reason"] = f"gocyclo failed: {e}"
        else:
            result_dict["skipped"] = True
            result_dict["reason"] = "gocyclo not installed"
        return result_dict

    def score_security(self, fixture_path: Path, kloc: float) -> dict[str, Any]:
        """Score Go security analysis."""
        result_dict: dict[str, Any] = {
            "max": 4,
            "score": 0,
            "tool": "",
            "high": 0,
            "medium": 0,
            "low": 0,
            "issues": [],
        }
        if shutil.which("gosec"):
            try:
                result = subprocess.run(
                    ["gosec", "-fmt", "json", f"{fixture_path}/..."],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                stdout = result.stdout.strip()
                if not stdout:
                    if result.returncode != 0:
                        result_dict["errors"] = [
                            f"gosec exited {result.returncode} with empty output"
                        ]
                    else:
                        result_dict = empty_security_result("gosec", kloc, has_fp=True)
                else:
                    try:
                        data = json.loads(stdout)
                        issues = data.get("Issues", [])
                        result_dict = score_security_gradient(
                            issues,
                            kloc,
                            tool_name="gosec",
                            fp_rules=_GOSEC_FP_RULES,
                        )
                    except json.JSONDecodeError:
                        result_dict["errors"] = ["failed to parse gosec output"]
            except subprocess.TimeoutExpired:
                result_dict["skipped"] = True
                result_dict["reason"] = "gosec timed out"
            except Exception as e:
                result_dict["skipped"] = True
                result_dict["reason"] = f"gosec failed: {e}"
        else:
            result_dict["skipped"] = True
            result_dict["reason"] = "gosec not installed"
        return result_dict

    def check_toolchain_available(self, fixture_path: Path) -> bool:
        """Check if Go toolchain is available."""
        return shutil.which("go") is not None

    @staticmethod
    def _run_go_vet(fixture_path: Path) -> dict[str, Any]:
        """Run go vet and return linting result dict."""
        try:
            result = subprocess.run(
                ["go", "vet", "./..."],
                cwd=fixture_path,
                capture_output=True,
                text=True,
                timeout=60,
            )
            errors = len([line for line in result.stderr.split("\n") if line.strip()])
            return {
                "max": 6,
                "score": round(max(0, 6 - errors), 1),
                "tool": "go vet",
                "errors": errors,
                "warnings": 0,
                "top_issues": [],
            }
        except Exception:
            return {
                "max": 6,
                "score": 0,
                "tool": "go vet",
                "errors": 0,
                "warnings": 0,
                "top_issues": [],
            }
