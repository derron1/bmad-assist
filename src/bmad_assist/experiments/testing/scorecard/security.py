"""Security scoring: gradient scorer, npm audit extraction, empty result helper."""

from __future__ import annotations

from typing import Any

from .constants import NPM_SEVERITY_MAP


def score_security_gradient(
    issues: list[dict[str, Any]],
    kloc: float,
    *,
    tool_name: str,
    severity_field: str = "severity",
    rule_id_field: str = "rule_id",
    fp_rules: set[str] | None = None,
) -> dict[str, Any]:
    """Score security issues using gradient with KLOC normalization and FP filtering.

    Unified scorer for gosec, bandit, and npm audit. Replaces the old step function
    with weighted severity: HIGH=1.0, MEDIUM=0.3, LOW=0.05.
    Gradient: 4.0 at 0 weighted/KLOC, linear decay to 0 at 1.0 weighted/KLOC.
    """
    severity_weights = {"HIGH": 1.0, "MEDIUM": 0.3, "LOW": 0.05}
    fp_downgraded = 0
    high = medium = low = 0
    weighted_total = 0.0

    for issue in issues:
        severity = issue.get(severity_field, "LOW").upper()
        rule_id = issue.get(rule_id_field, "")

        # Downgrade known FP rules to LOW
        if fp_rules and rule_id in fp_rules and severity in ("HIGH", "MEDIUM"):
            fp_downgraded += 1
            severity = "LOW"

        if severity == "HIGH":
            high += 1
        elif severity == "MEDIUM":
            medium += 1
        else:
            low += 1

        weighted_total += severity_weights.get(severity, 0.05)

    # Normalize by KLOC (minimum 0.5 to avoid over-penalizing small projects)
    effective_kloc = max(0.5, kloc)
    weighted_per_kloc = weighted_total / effective_kloc

    # Gradient: 4.0 at 0, linear decay to 0 at 1.0 weighted/KLOC
    score = round(max(0.0, 4.0 * (1.0 - weighted_per_kloc)), 1)

    result: dict[str, Any] = {
        "max": 4,
        "score": score,
        "tool": tool_name,
        "high": high,
        "medium": medium,
        "low": low,
        "issues": [],
        "kloc": round(kloc, 1),
        "weighted_per_kloc": round(weighted_per_kloc, 3),
    }
    if fp_rules:
        result["fp_downgraded"] = fp_downgraded
    return result


def extract_npm_audit_issues(audit_json: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Extract normalized issue list from npm audit JSON (v6 or v7+ format).

    Returns None if format is unknown/empty (means no vulns).
    """
    issues = []
    if "advisories" in audit_json:
        # npm v6 format
        for advisory in audit_json["advisories"].values():
            severity = NPM_SEVERITY_MAP.get(advisory.get("severity", "low"), "LOW")
            issues.append({"severity": severity})
    elif "vulnerabilities" in audit_json:
        # npm v7+ format — count each package once (dedup)
        for vuln in audit_json["vulnerabilities"].values():
            severity = NPM_SEVERITY_MAP.get(vuln.get("severity", "low"), "LOW")
            issues.append({"severity": severity})
    else:
        return None  # Unknown format or no vulns
    return issues


def empty_security_result(tool_name: str, kloc: float, *, has_fp: bool = False) -> dict[str, Any]:
    """Return a clean 'no issues' security result."""
    result: dict[str, Any] = {
        "max": 4,
        "score": 4,
        "tool": tool_name,
        "high": 0,
        "medium": 0,
        "low": 0,
        "issues": [],
        "kloc": round(kloc, 1),
        "weighted_per_kloc": 0.0,
    }
    if has_fp:
        result["fp_downgraded"] = 0
    return result
