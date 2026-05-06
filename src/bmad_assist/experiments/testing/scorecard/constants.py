"""Stack-agnostic constants, weights, and patterns."""

from __future__ import annotations

import re

# Source file extensions considered across all stacks
SOURCE_EXTENSIONS = (".go", ".py", ".js", ".ts", ".rs", ".svelte", ".jsx", ".tsx")

# Regex for TODO/FIXME markers
TODO_PATTERN = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b", re.IGNORECASE)

# Placeholder code patterns (regex, display name)
PLACEHOLDER_PATTERNS = [
    (r"\bpass\s*$", "pass"),
    (r"raise NotImplementedError", "NotImplementedError"),
    (r'panic\("not implemented"\)', "panic"),
    (r"unimplemented!\(\)", "unimplemented!"),
    (r"// TODO:", "TODO comment"),
    (r"^\s*\.\.\.\s*$", "Ellipsis"),
]

# Test file naming conventions per extension
TEST_FILE_PATTERNS = {
    ".go": lambda name: name.endswith("_test.go"),
    ".py": lambda name: name.startswith("test_") or name.endswith("_test.py"),
    ".js": lambda name: name.endswith(".test.js") or name.endswith(".spec.js"),
    ".ts": lambda name: name.endswith(".test.ts") or name.endswith(".spec.ts"),
}

# Directories to exclude from source file iteration
EXCLUDED_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", ".next"}

# npm audit severity to normalized severity mapping
NPM_SEVERITY_MAP: dict[str, str] = {
    "critical": "HIGH",
    "high": "HIGH",
    "moderate": "MEDIUM",
    "low": "LOW",
    "info": "LOW",
}
