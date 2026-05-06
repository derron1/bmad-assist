"""Orchestrator: generate_scorecard(), save, CLI arg parsing."""

from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .helpers import (
    count_placeholders,
    count_source_lines,
    count_todos,
    get_fixtures_dir,
    get_scorecards_dir,
)
from .registry import detect_stack, get_handler_for_fixture
from .scoring import score_completeness, score_documentation, score_ui_ux


def _score_functionality(fixture_path: Path, handler: Any) -> dict[str, Any]:
    """Score fixture functionality (25 points) using the detected stack handler."""
    results: dict[str, Any] = {
        "build": {"max": 10, "score": 0, "success": False, "command": "", "errors": []},
        "unit_tests": {
            "max": 10,
            "score": 0,
            "metric": "0/0",
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "errors": [],
        },
        "behavior_tests": {
            "max": 5,
            "score": 0,
            "metric": "0/0",
            "passed": 0,
            "failed": 0,
            "notes": "",
        },
    }

    if handler is not None:
        results["build"] = handler.score_build(fixture_path)
        results["unit_tests"] = handler.score_unit_tests(fixture_path)

    # Behavioral tests (check if they exist and count)
    fixture_tests_base = fixture_path.parent.parent / "fixture-tests"
    fixture_tests_dir = fixture_tests_base / fixture_path.name

    if not fixture_tests_dir.exists():
        base_name = re.sub(r"-\d+$", "", fixture_path.name)
        if base_name != fixture_path.name:
            fixture_tests_dir = fixture_tests_base / base_name

    if not fixture_tests_dir.exists():
        for test_dir_name in ("tests", "test"):
            candidate = fixture_path / test_dir_name
            if candidate.exists():
                fixture_tests_dir = candidate
                break

    if fixture_tests_dir.exists():
        test_files = list(fixture_tests_dir.glob("test_*.py"))
        if test_files:
            results["behavior_tests"]["notes"] = (
                f"{len(test_files)} test files found in {fixture_tests_dir.name}/"
            )
            results["behavior_tests"]["score"] = 2

    return {
        "weight": 25,
        "score": round(sum(r["score"] for r in results.values()), 1),
        "details": results,
    }


def _score_code_quality(
    fixture_path: Path, handler: Any, functionality_data: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Score code quality (20 points) using the detected stack handler."""
    results: dict[str, Any] = {
        "linting": {"max": 6, "score": 0, "tool": "", "errors": 0, "warnings": 0, "top_issues": []},
        "complexity": {
            "max": 4,
            "score": 0,
            "tool": "",
            "average": 0.0,
            "max_function": "",
            "max_value": 0,
        },
        "security": {
            "max": 4,
            "score": 0,
            "tool": "",
            "high": 0,
            "medium": 0,
            "low": 0,
            "issues": [],
        },
        "test_pass_rate": {"max": 3, "score": 0, "pass_rate": 0.0, "source": "functionality"},
        "code_maturity": {"max": 3, "score": 0, "todos": 0, "placeholders": 0},
    }

    # --- test_pass_rate (3 pts) - cross-ref from functionality ---
    if functionality_data and "details" in functionality_data:
        ut = functionality_data["details"].get("unit_tests", {})
        passed = ut.get("passed", 0)
        failed = ut.get("failed", 0)
        total = passed + failed
        if total > 0:
            pass_rate = passed / total
            results["test_pass_rate"]["pass_rate"] = round(pass_rate, 4)
            results["test_pass_rate"]["score"] = round(pass_rate * 3, 1)

    # --- code_maturity (3 pts) ---
    extra = handler.extra_src_dirs if handler else None
    stack = handler.name if handler else None
    todo_count, _ = count_todos(fixture_path, stack=stack, extra_src_dirs=extra)
    placeholder_count, _ = count_placeholders(fixture_path, stack=stack, extra_src_dirs=extra)
    results["code_maturity"]["todos"] = todo_count
    results["code_maturity"]["placeholders"] = placeholder_count
    results["code_maturity"]["score"] = round(
        max(0, 3 - (todo_count + placeholder_count) * 0.25), 1
    )

    if handler is None:
        # Unknown project type
        results["linting"]["skipped"] = True
        results["linting"]["reason"] = "unknown project type"
        results["complexity"]["skipped"] = True
        results["complexity"]["reason"] = "unknown project type"
        results["security"]["skipped"] = True
        results["security"]["reason"] = "unknown project type"
    elif not handler.check_toolchain_available(fixture_path):
        # Toolchain not installed
        results["linting"]["skipped"] = True
        results["linting"]["reason"] = f"{handler.name} not installed"
        results["complexity"]["skipped"] = True
        results["complexity"]["reason"] = f"{handler.name} not installed"
        results["security"]["skipped"] = True
        results["security"]["reason"] = f"{handler.name} not installed"
        return {
            "weight": 20,
            "score": round(sum(r["score"] for r in results.values()), 1),
            "skipped": True,
            "reason": f"{handler.name} toolchain not installed",
            "details": results,
        }
    else:
        results["linting"] = handler.score_linting(fixture_path)

        results["complexity"] = handler.score_complexity(fixture_path)

        kloc = count_source_lines(fixture_path, stack=stack, extra_src_dirs=extra) / 1000.0
        results["security"] = handler.score_security(fixture_path, kloc)

        # Correctness proxies (advisory, not scored)
        correctness_flags = handler.check_correctness_proxies(fixture_path)
        if correctness_flags:
            results["correctness_proxies"] = correctness_flags

    return {
        "weight": 20,
        "score": round(
            sum(r["score"] for r in results.values() if isinstance(r, dict) and "score" in r), 1
        ),
        "details": results,
    }


def generate_scorecard(fixture_name: str, *, fixture_path: Path | None = None) -> dict[str, Any]:
    """Generate a complete scorecard for a fixture."""
    if fixture_path is not None:
        if not fixture_path.exists():
            raise ValueError(f"Fixture path not found: {fixture_path}")
        resolved_path = fixture_path
    else:
        fixtures_dir = get_fixtures_dir()
        resolved_path = fixtures_dir / fixture_name
        if not resolved_path.exists():
            raise ValueError(f"Fixture not found: {resolved_path}")
    fixture_path = resolved_path

    # Detect stack once, get handler
    handler = get_handler_for_fixture(fixture_path)
    stack = handler.name if handler else detect_stack(fixture_path)

    # Score each category (functionality first, needed by code_quality)
    completeness = score_completeness(fixture_path, handler=handler)
    functionality = _score_functionality(fixture_path, handler)
    code_quality = _score_code_quality(fixture_path, handler, functionality_data=functionality)
    documentation = score_documentation(fixture_path, handler=handler, stack=stack)
    ui_ux = score_ui_ux(fixture_path)

    # Calculate totals
    scores = {
        "completeness": completeness,
        "functionality": functionality,
        "code_quality": code_quality,
        "documentation": documentation,
        "ui_ux": ui_ux,
    }

    raw_score = round(sum(s["score"] for s in scores.values() if s["score"] is not None), 1)

    # Adjust max possible if UI not applicable
    max_possible = 100 if ui_ux["applicable"] else 85

    weighted_score = round((raw_score / max_possible) * 100, 1)

    # Determine grade
    if weighted_score >= 90:
        grade = "A"
    elif weighted_score >= 80:
        grade = "B"
    elif weighted_score >= 70:
        grade = "C"
    elif weighted_score >= 60:
        grade = "D"
    else:
        grade = "F"

    return {
        "fixture": fixture_name,
        "generated_at": datetime.now(UTC).isoformat(),
        "generator_version": "2.0",
        "mode": "automated",
        "scores": scores,
        "totals": {
            "raw_score": raw_score,
            "max_possible": max_possible,
            "weighted_score": weighted_score,
            "grade": grade,
        },
        "notes": "",
        "recommendations": [],
        "comparison": {"baseline_fixture": None, "delta": {}},
    }


def save_scorecard(scorecard: dict[str, Any]) -> Path:
    """Save scorecard to the scorecards directory."""
    scorecards_dir = get_scorecards_dir()
    scorecards_dir.mkdir(parents=True, exist_ok=True)

    output_path = scorecards_dir / f"{scorecard['fixture']}.yaml"

    with open(output_path, "w") as f:
        yaml.dump(scorecard, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return output_path


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Generate quality scorecard for a fixture")
    parser.add_argument("fixture", help="Fixture name (e.g., webhook-relay-001)")
    parser.add_argument("--compare", help="Compare with another fixture")
    parser.add_argument("--output", "-o", help="Output file (default: scorecards/{fixture}.yaml)")
    parser.add_argument(
        "--fixture-path", help="Path to external fixture directory (overrides fixtures/ lookup)"
    )

    args = parser.parse_args()

    fp = Path(args.fixture_path) if args.fixture_path else None
    print(f"Generating scorecard for: {args.fixture}" + (f" (path: {fp})" if fp else ""))

    scorecard = generate_scorecard(args.fixture, fixture_path=fp)

    if args.compare:
        print(f"Comparing with: {args.compare}")
        baseline = generate_scorecard(args.compare)
        scorecard["comparison"]["baseline_fixture"] = args.compare
        scorecard["comparison"]["delta"] = {
            "completeness": round(
                scorecard["scores"]["completeness"]["score"]
                - baseline["scores"]["completeness"]["score"],
                1,
            ),
            "functionality": round(
                scorecard["scores"]["functionality"]["score"]
                - baseline["scores"]["functionality"]["score"],
                1,
            ),
            "code_quality": round(
                scorecard["scores"]["code_quality"]["score"]
                - baseline["scores"]["code_quality"]["score"],
                1,
            ),
            "documentation": round(
                scorecard["scores"]["documentation"]["score"]
                - baseline["scores"]["documentation"]["score"],
                1,
            ),
            "total": round(scorecard["totals"]["raw_score"] - baseline["totals"]["raw_score"], 1),
        }

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            yaml.dump(scorecard, f, default_flow_style=False, sort_keys=False)
    else:
        output_path = save_scorecard(scorecard)

    print(f"\nScorecard saved to: {output_path}")
    print(f"\nGrade: {scorecard['totals']['grade']} ({scorecard['totals']['weighted_score']}%)")
    print(f"  Completeness:  {scorecard['scores']['completeness']['score']}/25")
    print(f"  Functionality: {scorecard['scores']['functionality']['score']}/25")
    print(f"  Code Quality:  {scorecard['scores']['code_quality']['score']}/20")
    print(f"  Documentation: {scorecard['scores']['documentation']['score']}/15")
    ui_score = scorecard["scores"]["ui_ux"]["score"]
    print(f"  UI/UX:         {ui_score if ui_score is not None else 'N/A'}/15")
