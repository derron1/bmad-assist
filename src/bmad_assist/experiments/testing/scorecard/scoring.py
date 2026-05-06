"""Stack-agnostic scorers: completeness, documentation, ui_ux."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .helpers import (
    count_placeholders,
    count_todos,
    get_src_dirs,
    is_test_file,
    iter_source_files,
)


def score_completeness(fixture_path: Path, handler: Any = None) -> dict[str, Any]:
    """Score fixture completeness (25 points).

    - test_to_code_ratio (10 pts): SLOC test / SLOC production, linear cap at ratio 2.0
    - no_todos (5 pts): -0.5 per TODO/FIXME found
    - no_placeholders (5 pts): -1 per placeholder pattern
    - no_empty_files (5 pts): 5 if no empty files, 0 otherwise
    """
    extra = handler.extra_src_dirs if handler else None
    stack = handler.name if handler else None

    results: dict[str, dict[str, Any]] = {
        "test_to_code_ratio": {"max": 10, "score": 0, "ratio": 0.0, "test_sloc": 0, "prod_sloc": 0},
        "no_todos": {"max": 5, "score": 5, "metric": 0, "notes": ""},
        "no_placeholders": {"max": 5, "score": 5, "metric": 0, "patterns_found": []},
        "no_empty_files": {"max": 5, "score": 5, "metric": 0},
    }

    # Test-to-code ratio
    prod_sloc = 0
    test_sloc = 0
    for file in iter_source_files(fixture_path, stack=stack, extra_src_dirs=extra):
        try:
            lines = sum(1 for line in file.read_text(errors="ignore").splitlines() if line.strip())
        except Exception:
            continue
        if is_test_file(file):
            test_sloc += lines
        else:
            prod_sloc += lines
    ratio = round(test_sloc / prod_sloc, 2) if prod_sloc > 0 else 0.0
    results["test_to_code_ratio"]["ratio"] = ratio
    results["test_to_code_ratio"]["test_sloc"] = test_sloc
    results["test_to_code_ratio"]["prod_sloc"] = prod_sloc
    results["test_to_code_ratio"]["score"] = round(min(10, ratio * 5), 1)

    # TODO/FIXME count
    todo_count, todo_files = count_todos(fixture_path, stack=stack, extra_src_dirs=extra)
    results["no_todos"]["metric"] = todo_count
    results["no_todos"]["score"] = round(max(0, 5 - (todo_count * 0.5)), 1)
    if todo_files:
        results["no_todos"]["notes"] = "; ".join(todo_files[:5])

    # Placeholder patterns
    placeholder_count, found_patterns = count_placeholders(
        fixture_path, stack=stack, extra_src_dirs=extra
    )
    results["no_placeholders"]["metric"] = placeholder_count
    results["no_placeholders"]["score"] = round(max(0, 5 - placeholder_count), 1)
    results["no_placeholders"]["patterns_found"] = found_patterns

    # Empty files
    empty_count = 0
    for file in iter_source_files(fixture_path, stack=stack, extra_src_dirs=extra):
        if file.stat().st_size < 50:
            empty_count += 1

    results["no_empty_files"]["metric"] = empty_count
    results["no_empty_files"]["score"] = 5 if empty_count == 0 else 0

    return {
        "weight": 25,
        "score": round(sum(r["score"] for r in results.values()), 1),
        "details": results,
    }


def score_documentation(
    fixture_path: Path, handler: Any = None, stack: str | None = None
) -> dict[str, Any]:
    """Score documentation quality (15 points).

    - readme_exists (4 pts): README exists and has content
    - readme_sections (3 pts): has install/usage/config sections
    - api_docs (4 pts): API documentation exists
    - inline_comments (4 pts): comment ratio in code
    """
    results: dict[str, dict[str, Any]] = {
        "readme_exists": {"max": 4, "score": 0, "exists": False, "length": 0},
        "readme_sections": {
            "max": 3,
            "score": 0,
            "has_install": False,
            "has_usage": False,
            "has_config": False,
        },
        "api_docs": {"max": 4, "score": 0, "exists": False, "format": "", "location": ""},
        "inline_comments": {"max": 4, "score": 0, "ratio": 0.0, "sampled_files": []},
    }

    # README check
    readme_files = ["README.md", "README.rst", "README.txt", "README"]
    for readme_name in readme_files:
        readme = fixture_path / readme_name
        if readme.exists():
            content = readme.read_text(errors="ignore")
            results["readme_exists"]["exists"] = True
            results["readme_exists"]["length"] = len(content)
            results["readme_exists"]["score"] = 4 if len(content) > 100 else 2

            # Check sections
            content_lower = content.lower()
            if "install" in content_lower:
                results["readme_sections"]["has_install"] = True
                results["readme_sections"]["score"] += 1
            if "usage" in content_lower or "getting started" in content_lower:
                results["readme_sections"]["has_usage"] = True
                results["readme_sections"]["score"] += 1
            if "config" in content_lower or "environment" in content_lower:
                results["readme_sections"]["has_config"] = True
                results["readme_sections"]["score"] += 1
            break

    # API docs check
    api_doc_locations = [
        ("docs/api.md", "markdown"),
        ("docs/api.yaml", "openapi"),
        ("docs/openapi.yaml", "openapi"),
        ("api/openapi.yaml", "openapi"),
        ("swagger.yaml", "openapi"),
    ]
    for location, fmt in api_doc_locations:
        if (fixture_path / location).exists():
            results["api_docs"]["exists"] = True
            results["api_docs"]["format"] = fmt
            results["api_docs"]["location"] = location
            results["api_docs"]["score"] = 4
            break

    # Inline comment ratio — dynamic per stack
    total_lines = 0
    comment_lines = 0
    sampled_files: list[str] = []

    if handler:
        comment_prefix = handler.comment_prefix
        source_globs = handler.source_globs
        extra = handler.extra_src_dirs
    else:
        comment_prefix = "//"
        source_globs = ["*.py", "*.js", "*.ts", "*.go"]
        extra = None

    if stack is None and handler:
        stack = handler.name

    src_dirs = get_src_dirs(fixture_path, stack=stack, extra_src_dirs=extra)
    for src_dir in src_dirs:
        if src_dir.exists():
            for glob_pattern in source_globs:
                for file in list(src_dir.rglob(glob_pattern))[:10]:
                    try:
                        content = file.read_text(errors="ignore")
                        lines = content.split("\n")
                        total_lines += len(lines)
                        for line in lines:
                            stripped = line.strip()
                            if (
                                stripped.startswith(comment_prefix)
                                or stack == "python"
                                and ('"""' in stripped or "'''" in stripped)
                            ):
                                comment_lines += 1
                        sampled_files.append(str(file.relative_to(fixture_path)))
                    except Exception:
                        pass

    if total_lines > 0:
        ratio = (comment_lines / total_lines) * 100
        results["inline_comments"]["ratio"] = round(ratio, 1)
        results["inline_comments"]["score"] = min(4, int(ratio / 5))  # 5% = 1pt, 20% = 4pt
        results["inline_comments"]["sampled_files"] = sampled_files[:5]

    return {
        "weight": 15,
        "score": round(sum(r["score"] for r in results.values()), 1),
        "details": results,
    }


def score_ui_ux(fixture_path: Path) -> dict[str, Any]:
    """Check if fixture has UI (for manual scoring). Returns placeholder for manual review."""
    has_ui = any(
        [
            (fixture_path / "static").exists(),
            (fixture_path / "public").exists(),
            (fixture_path / "templates").exists(),
            (fixture_path / "frontend").exists(),
        ]
    )

    if not has_ui:
        return {
            "weight": 15,
            "score": None,
            "applicable": False,
            "details": "Fixture has no UI - points redistributed",
        }

    return {
        "weight": 15,
        "score": None,  # Requires manual review
        "applicable": True,
        "details": {
            "responsive": {"max": 3, "score": 0, "notes": ""},
            "navigation": {"max": 3, "score": 0, "notes": ""},
            "error_feedback": {"max": 3, "score": 0, "notes": ""},
            "loading_states": {"max": 3, "score": 0, "notes": ""},
            "visual_consistency": {"max": 3, "score": 0, "notes": ""},
        },
    }
