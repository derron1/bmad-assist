"""Python stack handler — build (pip), tests (pytest), linting (ruff+mypy), complexity (radon), security (bandit)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..base import BaseStackHandler
from ..helpers import score_test_results, soft_skip
from ..registry import register_stack
from ..security import empty_security_result, score_security_gradient

# Bandit rules known to produce frequent false positives
_BANDIT_FP_RULES = {"B101"}  # assert usage

# Correctness proxy patterns for Python files (advisory, not scored)
_PYTHON_CORRECTNESS_PROXIES: list[dict[str, Any]] = [
    {
        "id": "bare_except",
        "pattern": r"except\s*:",
        "guard": None,
        "description": "bare except clause (catches BaseException including SystemExit/KeyboardInterrupt)",
    },
    {
        "id": "mutable_default",
        "pattern": r"def\s+\w+\([^)]*(?:=\s*\[|=\s*\{|=\s*set\()",
        "guard": None,
        "description": "mutable default argument in function definition",
    },
    {
        "id": "global_state",
        "pattern": r"^[a-zA-Z_]\w*\s*=\s*[\[\{]",
        "guard": r"^[A-Z_]+\s*=",
        "description": "module-level mutable state (not a constant)",
    },
    {
        "id": "no_type_hints",
        "pattern": r"def\s+[a-z]\w+\([^)]*\)\s*:",
        "guard": r"def\s+[a-z]\w+\([^)]*\)\s*->",
        "description": "function without return type annotation",
    },
    {
        "id": "broad_except",
        "pattern": r"except\s+Exception\s*:",
        "guard": None,
        "description": "broad Exception catch (may swallow unexpected errors)",
    },
    {
        "id": "hardcoded_secret",
        "pattern": r"(?:password|secret|token|api_key)\s*=\s*[\"'][^\"']{8,}",
        "guard": None,
        "description": "potential hardcoded secret or credential",
    },
]


@register_stack
class PythonStackHandler(BaseStackHandler):
    """Handler for Python projects."""

    @property
    def name(self) -> str:
        """Return stack identifier."""
        return "python"

    @property
    def marker_files(self) -> list[str]:
        """Return marker files for Python projects."""
        return ["pyproject.toml"]

    @property
    def comment_prefix(self) -> str:
        """Return Python comment prefix."""
        return "#"

    @property
    def source_globs(self) -> list[str]:
        """Return Python source file glob patterns."""
        return ["*.py"]

    @property
    def correctness_proxies(self) -> list[dict[str, Any]]:
        """Return Python correctness proxy patterns."""
        return _PYTHON_CORRECTNESS_PROXIES

    @property
    def source_extensions(self) -> set[str]:
        """Return Python source file extensions."""
        return {".py"}

    def score_build(self, fixture_path: Path) -> dict[str, Any]:
        """Score Python build success."""
        result_dict: dict[str, Any] = {
            "max": 10,
            "score": 0,
            "success": False,
            "command": "pip install . (temp venv)",
            "errors": [],
        }
        build_success = False
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                venv_dir = Path(tmpdir) / "venv"
                python_bin = sys.executable or "python"
                venv_result = subprocess.run(
                    [python_bin, "-m", "venv", str(venv_dir)],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if venv_result.returncode == 0:
                    pip_bin = venv_dir / "bin" / "pip"
                    install_result = subprocess.run(
                        [str(pip_bin), "install", str(fixture_path)],
                        capture_output=True,
                        text=True,
                        timeout=300,
                    )
                    if install_result.returncode == 0:
                        build_success = True
                    else:
                        result_dict["errors"] = install_result.stderr.split("\n")[:5]
                else:
                    result_dict["errors"] = [
                        f"venv creation failed (python={python_bin})",
                        *(venv_result.stderr.split("\n")[:3]),
                    ]
        except subprocess.TimeoutExpired:
            result_dict["errors"] = ["pip install timed out"]
        except Exception as e:
            result_dict["errors"] = [str(e)]

        # Dry-check fallback: try importing the package
        if not build_success:
            try:
                import tomllib

                toml_content = (fixture_path / "pyproject.toml").read_bytes()
                toml_data = tomllib.loads(toml_content.decode())
                pkg_name = toml_data.get("project", {}).get("name")
                if pkg_name:
                    normalized = pkg_name.replace("-", "_")
                    dry_result = subprocess.run(
                        ["python", "-c", f"import {normalized}"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if dry_result.returncode == 0:
                        build_success = True
                        result_dict["command"] = f"python -c 'import {normalized}' (dry-check)"
            except Exception:
                pass

        result_dict["success"] = build_success
        if build_success:
            result_dict["score"] = 10
        else:
            result_dict = {
                **result_dict,
                **soft_skip(10, "pip", "pip install failed and dry-check unavailable"),
            }

        return result_dict

    def score_unit_tests(self, fixture_path: Path) -> dict[str, Any]:
        """Score Python unit test results."""
        result_dict: dict[str, Any] = {
            "max": 10,
            "score": 0,
            "metric": "0/0",
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "errors": [],
        }
        if shutil.which("pytest"):
            try:
                result = subprocess.run(
                    ["pytest", "--tb=short", "-q", str(fixture_path)],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                stdout = result.stdout
                match = re.search(
                    r"(\d+) passed(?:,\s*(\d+) failed)?(?:,\s*(\d+) skipped)?", stdout
                )
                if match:
                    passed = int(match.group(1))
                    failed = int(match.group(2)) if match.group(2) else 0
                    skipped_count = int(match.group(3)) if match.group(3) else 0
                    result_dict = score_test_results(passed, failed, skipped_count)
                elif re.search(r"no tests ran|collected 0 items", stdout):
                    result_dict = score_test_results(0, 0)
                elif result.returncode != 0:
                    result_dict["errors"] = result.stderr.split("\n")[:5]
            except subprocess.TimeoutExpired:
                result_dict["errors"] = ["pytest timed out"]
            except Exception as e:
                result_dict["errors"] = [str(e)]
        else:
            result_dict = soft_skip(10, "pytest", "pytest not installed")
        return result_dict

    def score_linting(self, fixture_path: Path) -> dict[str, Any]:
        """Score Python linting results via ruff and mypy."""
        result_dict: dict[str, Any] = {
            "max": 6,
            "score": 0,
            "tool": "ruff+mypy",
            "errors": 0,
            "warnings": 0,
            "top_issues": [],
        }
        ruff_score = 0.0
        mypy_score = 0.0
        ruff_errors = 0
        mypy_errors = 0

        if shutil.which("ruff"):
            try:
                result = subprocess.run(
                    ["ruff", "check", "--output-format=json", "."],
                    cwd=fixture_path,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode < 0:
                    ruff_score = 1.6
                else:
                    try:
                        diagnostics = json.loads(result.stdout) if result.stdout.strip() else []
                        ruff_errors = len(diagnostics)
                    except json.JSONDecodeError:
                        ruff_score = round(4 * 0.4, 1)
                        result_dict["ruff_warning"] = "failed to parse ruff JSON output"
                    else:
                        ruff_score = round(max(0, 4 - ruff_errors * 0.5), 1)
            except subprocess.TimeoutExpired:
                ruff_score = 1.6
            except Exception:
                pass
        else:
            ruff_score = round(4 * 0.4, 1)
            result_dict["ruff_warning"] = "ruff not installed"

        if shutil.which("mypy"):
            try:
                result = subprocess.run(
                    ["mypy", str(fixture_path), "--no-error-summary", "--ignore-missing-imports"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode < 0:
                    mypy_score = 0.8
                else:
                    mypy_errors = sum(1 for line in result.stdout.split("\n") if ": error:" in line)
                    mypy_score = round(max(0, 2 - mypy_errors * 0.2), 1)
            except subprocess.TimeoutExpired:
                mypy_score = 0.8
            except Exception:
                pass
        else:
            mypy_score = round(2 * 0.4, 1)
            result_dict["mypy_warning"] = "mypy not installed"

        result_dict["ruff_errors"] = ruff_errors
        result_dict["mypy_errors"] = mypy_errors
        result_dict["score"] = round(ruff_score + mypy_score, 1)

        return result_dict

    def score_complexity(self, fixture_path: Path) -> dict[str, Any]:
        """Score Python cyclomatic complexity via radon."""
        result_dict: dict[str, Any] = {
            "max": 4,
            "score": 0,
            "tool": "",
            "average": 0.0,
            "max_function": "",
            "max_value": 0,
        }
        if shutil.which("radon"):
            result_dict["tool"] = "radon"
            try:
                result = subprocess.run(
                    ["radon", "cc", str(fixture_path), "-a", "-j"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode < 0:
                    result_dict = soft_skip(4, "radon", "radon killed by signal")
                else:
                    avg_match = re.search(
                        r"Average complexity:\s*[\w\s]*\(([\d.]+)\)", result.stderr
                    )
                    if not avg_match:
                        avg_match = re.search(
                            r"Average complexity:\s*[\w\s]*([\d.]+)", result.stderr
                        )
                    if avg_match:
                        avg = float(avg_match.group(1))
                        result_dict["average"] = avg
                        result_dict["score"] = 4 if avg < 10 else (2 if avg < 15 else 0)
                    elif not result.stdout.strip() or result.stdout.strip() == "{}":
                        result_dict["score"] = 4
                        result_dict["average"] = 0.0
                    else:
                        result_dict["score"] = 4
                        result_dict["average"] = 0.0
            except subprocess.TimeoutExpired:
                result_dict = soft_skip(4, "radon", "radon timed out")
            except Exception as e:
                result_dict["skipped"] = True
                result_dict["reason"] = f"radon failed: {e}"
        else:
            result_dict = soft_skip(4, "radon", "radon not installed")
        return result_dict

    def score_security(self, fixture_path: Path, kloc: float) -> dict[str, Any]:
        """Score Python security analysis via bandit."""
        result_dict: dict[str, Any] = {
            "max": 4,
            "score": 0,
            "tool": "",
            "high": 0,
            "medium": 0,
            "low": 0,
            "issues": [],
        }
        if shutil.which("bandit"):
            try:
                result = subprocess.run(
                    ["bandit", "-r", str(fixture_path), "-f", "json"],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if result.returncode < 0:
                    result_dict = soft_skip(4, "bandit", "bandit killed by signal")
                else:
                    stdout = result.stdout.strip()
                    if not stdout or stdout == "{}":
                        if result.returncode != 0:
                            result_dict["errors"] = [
                                f"bandit exited {result.returncode} with empty output"
                            ]
                        else:
                            result_dict = empty_security_result("bandit", kloc, has_fp=True)
                    else:
                        try:
                            data = json.loads(stdout)
                            issues = data.get("results", [])
                            result_dict = score_security_gradient(
                                issues,
                                kloc,
                                tool_name="bandit",
                                severity_field="issue_severity",
                                rule_id_field="test_id",
                                fp_rules=_BANDIT_FP_RULES,
                            )
                        except json.JSONDecodeError:
                            result_dict["errors"] = ["failed to parse bandit output"]
            except subprocess.TimeoutExpired:
                result_dict = soft_skip(4, "bandit", "bandit timed out")
            except Exception as e:
                result_dict["skipped"] = True
                result_dict["reason"] = f"bandit failed: {e}"
        else:
            result_dict = soft_skip(4, "bandit", "bandit not installed")
        return result_dict
