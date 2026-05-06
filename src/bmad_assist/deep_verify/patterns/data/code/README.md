# Deep Verify Code Patterns

Language-specific code patterns for Deep Verify's pattern matching engine. These patterns detect concrete implementation issues in source code (as opposed to spec patterns which detect conceptual issues in requirements).

## Directory Structure

```
code/
├── README.md              # This file
├── go/                    # Go code patterns
│   ├── concurrency.yaml   # Goroutine, channel, mutex patterns
│   ├── quality.yaml       # Code quality and style patterns
│   └── security.yaml      # Security vulnerability patterns
└── python/                # Python code patterns
    ├── concurrency.yaml   # Threading, asyncio patterns
    └── quality.yaml       # Code quality and style patterns
```

## Pattern File Format

Code patterns use the same YAML schema as spec patterns:

```yaml
patterns:
  - id: "CC-001-CODE-GO"          # Pattern ID with -CODE and -{LANG} suffix
    domain: "concurrency"         # ArtifactDomain value
    severity: "critical"          # Severity: critical, error, warning, info
    signals:                      # List of signals to match
      - "go func("               # Exact match signal
      - 'regex:\bgo\s+\w+\('      # Regex match signal (prefix with regex:)
    description: "Description of the issue"
    remediation: "How to fix the issue"
```

## Pattern ID Convention

- **Spec patterns**: `CC-001`, `SEC-004`, `DB-005` (2-3 letter prefix)
- **Code patterns**: Add `-CODE` suffix and optional `-{LANG}` suffix
  - `CC-001-CODE` - Generic code pattern
  - `CC-001-CODE-GO` - Go-specific code pattern
  - `SEC-004-CODE-PY` - Python-specific code pattern

## Signal Writing Best Practices

### Exact Match Signals

Use for literal syntax that won't vary:

```yaml
signals:
  - "go func("           # Anonymous goroutine
  - "except:"            # Bare except clause
  - "sync.Mutex"         # Type name
```

**Pros**: Fast, deterministic  
**Cons**: Won't catch variations (spacing, formatting)

### Regex Match Signals

Use with `regex:` prefix for flexible matching:

```yaml
signals:
  - 'regex:\bgo\s+\w+\('           # Goroutine call with variable spacing
  - 'regex:\bexcept\s*:\s*$'       # Bare except at end of line
  - 'regex:\bdef\s+\w+\s*\([^)]*=\s*\[\s*\]'  # Mutable default list
```

**Guidelines**:
- Use `\b` (word boundaries) to avoid partial matches
- Use non-capturing groups `(?:...)` for alternation when needed
- Use `\s*` for optional whitespace, `\s+` for required whitespace
- Use `(?!...)` negative lookahead when appropriate

## Adding New Language Patterns

1. Create a new subdirectory: `code/{language}/`
2. Create YAML files for each domain: `{domain}.yaml`
3. Use pattern IDs with `-CODE-{LANG}` suffix
4. Add the language to the language map in `library.py`:
   - `_extract_language_from_path()` method
   - `get_patterns()` method

### Language Code Mapping

Accepted language codes (case-insensitive):

| Code(s) | Canonical | Notes |
|---------|-----------|-------|
| `go`, `golang` | `go` | Go |
| `py`, `python` | `python` | Python |
| `js`, `javascript` | `javascript` | JavaScript |
| `ts`, `typescript` | `typescript` | TypeScript |
| `rs`, `rust` | `rust` | Rust |
| `java` | `java` | Java |
| `rb`, `ruby` | `ruby` | Ruby |

## Testing New Patterns

Create a test file to verify pattern effectiveness:

```python
from pathlib import Path
from bmad_assist.deep_verify.patterns import PatternLibrary, PatternMatcher
from bmad_assist.deep_verify.core.types import ArtifactDomain

# Load patterns for your language
library = PatternLibrary.load([Path("patterns/data/code/go")])
patterns = library.get_patterns([ArtifactDomain.CONCURRENCY], language="go")

# Test against sample code
code = '''
func main() {
    go func() {  // Should match CC-001-CODE-GO
        doWork()
    }()
}
'''

matcher = PatternMatcher(patterns)
results = matcher.match(code)

# Verify pattern matched
assert any(r.pattern.id == "CC-001-CODE-GO" for r in results)
```

## Pattern Categories

### Concurrency Patterns

Detect race conditions, deadlocks, and improper synchronization:

- Goroutine lifecycle issues
- Mutex misuse (no defer Unlock)
- Channel blocking
- Context cancellation

### Quality Patterns

Detect code quality issues and anti-patterns:

- Error handling
- Resource leaks
- Hardcoded values
- Interface pollution

### Security Patterns

Detect security vulnerabilities:

- SQL injection
- Weak crypto
- Path traversal
- Command injection

## Confidence Calculation

The `PatternMatcher` calculates confidence as:

```
confidence = sum(matched_signal_weights) / sum(all_signal_weights)
```

Default threshold is 0.6 (60%). A pattern matches when:
- At least one signal matches
- Confidence >= threshold

To adjust sensitivity:
- Add more signals for higher precision
- Adjust threshold in `PatternMatchMethod` constructor

## Integration with Code Review

Code patterns are automatically loaded by `get_default_pattern_library()` and used during code review phase when language detection identifies the target language:

```python
from bmad_assist.deep_verify.patterns import get_default_pattern_library

library = get_default_pattern_library()

# Get patterns for specific language
patterns = library.get_patterns(
    domains=[ArtifactDomain.CONCURRENCY, ArtifactDomain.SECURITY],
    language="go",  # Filters to Go code patterns + all spec patterns
)
```

## API Contract: `get_patterns()` vs `get_all_patterns()`

Two distinct accessors with deliberately different defaults:

```python
library = get_default_pattern_library()

# RECOMMENDED — language-aware: returns spec patterns + code patterns for the
# target language only. Cross-language patterns (e.g. Go's defer-in-loop) will
# NOT fire on Python files.
py_patterns = library.get_patterns(domains=[...], language="python")

# Default with no language hint: returns SPEC-ONLY patterns (language-agnostic).
# This is the safe default for callers that don't know the target language —
# they get the universally-applicable spec patterns and nothing more.
spec_only = library.get_patterns(domains=[...])

# Escape hatch: returns the full unfiltered library (every language).
# Use only for inspection, library audits, or migration tooling.
everything = library.get_all_patterns()
```

The default-to-spec-only behavior of `get_patterns()` (when `language` is
omitted) was introduced to prevent cross-language false positives — the
historical default of "return everything" caused Go-tagged patterns to match
on Python files, JavaScript-tagged patterns to match on Ruby files, etc.
Callers that legitimately need the full library should call
`get_all_patterns()` explicitly.

## Notes

- Spec patterns (in `spec/` directory) apply to all languages (no `language` field)
- Code patterns are filtered by the `language` parameter in `get_patterns()`
- `get_patterns()` with no `language` argument returns spec-only patterns; use `get_all_patterns()` for the unfiltered library
- Unknown languages return spec-only patterns (no error raised — the library treats them as "no language-specific patterns match")
- Code patterns override spec patterns on ID collision (last loaded wins)
