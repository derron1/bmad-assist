"""Batch verification orchestrator for Deep Verify.

Runs multi-turn LLM sessions per method across multiple files,
reducing N×M LLM calls to M sessions (one per method).

Architecture:
    verify_batch(files):
      1. Keyword domain detection per file (no LLM, fast)
      2. Build method matrix (method → applicable files by domain)
      3. Pattern match (#153): run locally per file
      4. LLM methods: parallel sessions with stagger
      5. Aggregate findings per file across all methods
      6. Score + verdict per file (Python, no LLM)
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import random
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bmad_assist.deep_verify.core.method_selector import MethodSelector
from bmad_assist.deep_verify.core.scoring import EvidenceScorer
from bmad_assist.deep_verify.core.types import (
    ArtifactDomain,
    DomainConfidence,
    Finding,
    MethodId,
    Severity,
    Verdict,
    VerdictDecision,
)
from bmad_assist.deep_verify.infrastructure.session import (
    MAX_FILES_PER_SESSION,
    MultiTurnSession,
)

if TYPE_CHECKING:
    from bmad_assist.core.config.models.providers import HelperProviderConfig
    from bmad_assist.deep_verify.config import DeepVerifyConfig
    from bmad_assist.deep_verify.methods.base import BaseVerificationMethod

logger = logging.getLogger(__name__)

# Keyword counts required per domain for keyword detection
_KEYWORD_THRESHOLD = 2

# Keywords per domain for fast keyword-based detection (subset of engine's keywords)
_DOMAIN_KEYWORDS: dict[ArtifactDomain, list[str]] = {
    ArtifactDomain.SECURITY: [
        "auth",
        "token",
        "encrypt",
        "password",
        "permission",
        "credential",
        "secret",
        "jwt",
        "oauth",
        "hash",
    ],
    ArtifactDomain.API: [
        "endpoint",
        "request",
        "response",
        "http",
        "api",
        "rest",
        "json",
        "graphql",
        "grpc",
        "webhook",
    ],
    ArtifactDomain.CONCURRENCY: [
        "async",
        "thread",
        "lock",
        "race",
        "concurrent",
        "parallel",
        "mutex",
        "semaphore",
        "goroutine",
        "worker",
    ],
    ArtifactDomain.STORAGE: [
        "database",
        "db",
        "cache",
        "persist",
        "storage",
        "sql",
        "query",
        "transaction",
        "repository",
        "orm",
    ],
    ArtifactDomain.MESSAGING: [
        "queue",
        "message",
        "event",
        "stream",
        "kafka",
        "rabbitmq",
        "pubsub",
        "consumer",
        "producer",
        "topic",
    ],
    ArtifactDomain.TRANSFORM: [
        "convert",
        "transform",
        "parse",
        "serialize",
        "format",
        "marshal",
        "unmarshal",
        "encode",
        "decode",
        "csv",
    ],
}

# File extension → language code mapping for pattern-match filtering.
# Keys MUST include the leading dot. Values are canonical language codes.
#
# The 7 languages currently supported by PatternLibrary's data tree
# (python, go, javascript, typescript, rust, java, ruby — see
# patterns/library.py LANGUAGE_CODE_MAP) have shipped pattern files
# under patterns/data/code/<lang>/. The remaining entries below map
# common production-language extensions whose patterns are not yet
# shipped: those files receive a language hint that the library will
# resolve to "spec-only patterns" (since no language-tagged patterns
# match), which is the correct safe default. When future pattern data
# is added for any of those languages, no map change is needed — the
# canonical code already routes correctly.
_EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    # Languages with shipped pattern data
    ".py": "python",
    ".pyi": "python",
    ".go": "go",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    # Languages without shipped pattern data (spec-only fallback applies)
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".c": "c",
    ".h": "c",  # Ambiguous (could be C++ header); defaults to C
    ".cs": "csharp",
    ".scala": "scala",
    ".sc": "scala",
    ".php": "php",
    ".dart": "dart",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".sql": "sql",
    ".groovy": "groovy",
    ".lua": "lua",
    ".pl": "perl",
    ".pm": "perl",
    ".r": "r",
    ".hs": "haskell",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".clj": "clojure",
    ".cljs": "clojure",
    ".jl": "julia",
}


def _detect_language_from_path(file_path: Path) -> str | None:
    """Detect a canonical language code from a file path's extension.

    Args:
        file_path: Path to the source file.

    Returns:
        Canonical language code (e.g. "python", "go", "typescript") if the
        extension is recognized, else None. None signals "no language hint"
        to the pattern matcher, which will then fall back to spec-only
        (language-agnostic) patterns rather than running every code-specific
        pattern across every file.

    """
    return _EXTENSION_LANGUAGE_MAP.get(file_path.suffix.lower())


# Methods that always run regardless of domain
_ALWAYS_RUN_METHODS = {MethodId("#153"), MethodId("#154"), MethodId("#203")}

# Domain → conditional method IDs (from MethodSelector matrix)
_DOMAIN_METHOD_MAP: dict[ArtifactDomain, set[MethodId]] = {
    ArtifactDomain.CONCURRENCY: {MethodId("#155"), MethodId("#205")},
    ArtifactDomain.API: {MethodId("#155"), MethodId("#201"), MethodId("#204")},
    ArtifactDomain.MESSAGING: {MethodId("#157"), MethodId("#204"), MethodId("#205")},
    ArtifactDomain.STORAGE: {MethodId("#157"), MethodId("#204"), MethodId("#205")},
    ArtifactDomain.SECURITY: {MethodId("#201")},
    ArtifactDomain.TRANSFORM: set(),
}


def _keyword_detect(content: str) -> set[ArtifactDomain]:
    """Fast keyword-based domain detection for a single file.

    Args:
        content: File content to analyze.

    Returns:
        Set of detected domains (requires >= _KEYWORD_THRESHOLD keyword hits).

    """
    text_lower = content.lower()
    detected: set[ArtifactDomain] = set()

    for domain, keywords in _DOMAIN_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in text_lower)
        if hits >= _KEYWORD_THRESHOLD:
            detected.add(domain)

    return detected


def _chunks(items: list[Any], size: int) -> list[list[Any]]:
    """Split list into chunks of given size."""
    return [items[i : i + size] for i in range(0, len(items), size)]


def _resolve_provider_config(
    config: DeepVerifyConfig,
    helper_config: HelperProviderConfig | None,
) -> tuple[str, Path | None]:
    """Resolve model and settings from config.

    Resolution order:
    1. deep_verify.provider
    2. global helper provider
    3. fallback to haiku

    Returns:
        Tuple of (model, settings_path).

    """
    dv_provider = config.provider
    if dv_provider is not None:
        return dv_provider.model, dv_provider.settings_path

    if helper_config is not None:
        return helper_config.model, helper_config.settings_path

    return "haiku", None


class BatchVerifyOrchestrator:
    """Orchestrates batch verification using multi-turn sessions.

    Each LLM method gets one session (or multiple for >10 files).
    Pattern match (#153) runs locally without sessions.

    """

    def __init__(
        self,
        config: DeepVerifyConfig,
        project_root: Path,
        helper_provider_config: HelperProviderConfig | None = None,
    ) -> None:
        """Initialize batch orchestrator with config and project root."""
        self._config = config
        self._project_root = project_root
        self._file_context_budget = config.context.file_context_budget
        self._model, self._settings = _resolve_provider_config(config, helper_provider_config)
        # MethodSelector with llm_client=None for batch mode
        # (sessions handle LLM calls, not individual methods)
        self._method_selector = MethodSelector(config, llm_client=None)
        self._scorer = EvidenceScorer(
            severity_weights=config.get_severity_weights(),
            clean_pass_bonus=config.clean_pass_bonus,
            reject_threshold=config.reject_threshold,
            accept_threshold=config.accept_threshold,
        )

    async def verify_batch(
        self,
        files: list[tuple[Path, str]],
        context: Any | None = None,
        base_timeout: int | None = None,
        file_hunk_ranges: dict[Path, list[tuple[int, int]]] | None = None,
    ) -> dict[Path, Verdict]:
        """Batch verify multiple files using multi-turn sessions.

        Args:
            files: List of (file_path, content) tuples.
            context: Optional verification context (unused currently).
            base_timeout: Base timeout per file in seconds.

        Returns:
            Dict mapping file_path → Verdict.

        """
        if not files:
            return {}

        effective_timeout = base_timeout or self._config.llm_config.default_timeout_seconds

        # 1. Keyword domain detection per file (fast, no LLM)
        file_domains: dict[Path, set[ArtifactDomain]] = {}
        for fp, content in files:
            file_domains[fp] = _keyword_detect(content)

        # 2. Method selection on union of all domains
        all_domains = list(set().union(*file_domains.values()))
        methods = self._method_selector.select(all_domains)

        if not methods:
            logger.info("BatchVerify: no methods selected, returning ACCEPT for all files")
            return {fp: self._empty_verdict() for fp, _ in files}

        logger.info(
            "BatchVerify: %d files, %d methods, domains=%s",
            len(files),
            len(methods),
            [d.value for d in all_domains],
        )

        # 3. Pattern match (#153) locally for all files
        pattern_findings: dict[Path, list[Finding]] = {}
        pattern_method = next((m for m in methods if m.method_id == MethodId("#153")), None)
        if pattern_method is not None:
            skipped = 0
            for fp, content in files:
                try:
                    domains_list: list[ArtifactDomain] = list(file_domains.get(fp, set()))
                    language = _detect_language_from_path(fp)
                    kwargs: dict[str, object] = {"domains": domains_list}
                    if language is not None:
                        kwargs["language"] = language
                    findings = await pattern_method.analyze(content, **kwargs)  # type: ignore[arg-type]
                    if findings:
                        pattern_findings[fp] = findings
                    else:
                        skipped += 1
                except (ValueError, RuntimeError) as e:
                    logger.warning("Pattern match failed for %s: %s", fp.name, e)
                    pattern_findings[fp] = []
            if skipped:
                logger.info(
                    "#153 pattern match: %d/%d files skipped (no patterns for detected domains)",
                    skipped,
                    len(files),
                )

        # 4. LLM methods: parallel tasks with stagger
        llm_methods = [m for m in methods if m.method_id != MethodId("#153")]
        stagger = self._config.llm_config.method_stagger_seconds
        jitter_factor = 0.2

        tasks: list[asyncio.Task[dict[Path, list[Finding]]]] = []
        for idx, method in enumerate(llm_methods):
            if idx > 0 and stagger > 0:
                jitter = random.uniform(1 - jitter_factor, 1 + jitter_factor)
                await asyncio.sleep(stagger * jitter)

            task = asyncio.create_task(
                self._run_method_sessions(
                    method,
                    files,
                    file_domains,
                    effective_timeout,
                    file_hunk_ranges=file_hunk_ranges,
                ),
                name=f"batch-{method.method_id}",
            )
            tasks.append(task)

        method_results = await asyncio.gather(*tasks, return_exceptions=True)

        # 5. Aggregate per file: pattern + all method findings → score → verdict
        all_method_ids = [m.method_id for m in methods]
        verdicts: dict[Path, Verdict] = {}

        for fp, _content in files:
            file_findings: list[Finding] = list(pattern_findings.get(fp, []))
            domains = file_domains.get(fp, set())

            # Collect findings from all LLM methods
            for result in method_results:
                if isinstance(result, Exception):
                    logger.warning("Method session failed: %s", result)
                    continue
                if isinstance(result, dict) and fp in result:
                    file_findings.extend(result[fp])

            verdicts[fp] = self._build_verdict(file_findings, list(domains), all_method_ids)

        logger.info(
            "BatchVerify complete: %d files processed, verdicts=%s",
            len(files),
            {fp.name: v.decision.value for fp, v in verdicts.items()},
        )

        return verdicts

    async def _run_method_sessions(
        self,
        method: BaseVerificationMethod,
        files: list[tuple[Path, str]],
        file_domains: dict[Path, set[ArtifactDomain]],
        base_timeout: int,
        file_hunk_ranges: dict[Path, list[tuple[int, int]]] | None = None,
    ) -> dict[Path, list[Finding]]:
        """Run one method across applicable files via multi-turn sessions.

        Filters files by domain applicability, chunks into batches of 10,
        and runs each batch in a separate session.

        Args:
            method: Verification method to run.
            files: All files to potentially analyze.
            file_domains: Per-file domain detection results.
            base_timeout: Base timeout per file.

        Returns:
            Dict mapping file_path → findings from this method.

        """
        # Filter files by domain applicability
        applicable = self._filter_by_domain(method, files, file_domains)
        if not applicable:
            return {}

        chunks = _chunks(applicable, MAX_FILES_PER_SESSION)
        if len(chunks) > 1:
            logger.info(
                "Method %s: starting %d LLM sessions for %d files (%d per session)",
                method.method_id,
                len(chunks),
                len(applicable),
                MAX_FILES_PER_SESSION,
            )
        else:
            logger.info(
                "Method %s: starting LLM session for %d files",
                method.method_id,
                len(applicable),
            )

        results: dict[Path, list[Finding]] = {}

        for chunk in chunks:
            try:
                async with MultiTurnSession(
                    method,
                    model=self._model,
                    base_timeout=base_timeout,
                    settings=self._settings,
                ) as session:
                    for fp, content in chunk:
                        # Per-file timeout scales with session length:
                        # later files have more context → LLM is slower
                        per_file_timeout = base_timeout * len(chunk)

                        # Intelligent context extraction
                        hunk_ranges = file_hunk_ranges.get(fp) if file_hunk_ranges else None
                        try:
                            from bmad_assist.context.extractor import extract_context
                            from bmad_assist.context.formatter import format_for_dv

                            ctx = extract_context(
                                content=content,
                                file_path=str(fp),
                                budget=self._file_context_budget,
                                hunk_ranges=hunk_ranges,
                            )
                            extracted = format_for_dv(ctx)
                        except (ImportError, ValueError) as e:
                            logger.debug("Context extraction failed for %s: %s", fp.name, e)
                            extracted = None

                        result = await session.analyze_file(
                            fp,
                            content,
                            timeout=per_file_timeout,
                            extracted_content=extracted,
                        )
                        results[fp] = result.findings if result.success else []

            except (OSError, RuntimeError, ConnectionError) as e:
                # Session connect/crash — log and skip remaining in this chunk
                logger.warning(
                    "Session failed for method %s: %s. Remaining files in chunk skipped.",
                    method.method_id,
                    e,
                )
                # Fill empty findings for remaining files in chunk
                for fp, _ in chunk:
                    if fp not in results:
                        results[fp] = []

        return results

    def _filter_by_domain(
        self,
        method: BaseVerificationMethod,
        files: list[tuple[Path, str]],
        file_domains: dict[Path, set[ArtifactDomain]],
    ) -> list[tuple[Path, str]]:
        """Filter files to those applicable for a given method.

        Always-run methods (#154, #203) apply to all files.
        Conditional methods apply only when file has matching domain.

        Args:
            method: The method to check applicability for.
            files: All files.
            file_domains: Per-file detected domains.

        Returns:
            Filtered list of applicable (file_path, content) tuples.

        """
        mid = method.method_id

        # Always-run methods apply to all files
        if mid in _ALWAYS_RUN_METHODS:
            return files

        # Build set of domains that trigger this method
        trigger_domains: set[ArtifactDomain] = set()
        for domain, method_ids in _DOMAIN_METHOD_MAP.items():
            if mid in method_ids:
                trigger_domains.add(domain)

        if not trigger_domains:
            return files  # Unknown method → run on all files

        # Filter files that have at least one triggering domain
        return [
            (fp, content) for fp, content in files if file_domains.get(fp, set()) & trigger_domains
        ]

    def _build_verdict(
        self,
        findings: list[Finding],
        domains: list[ArtifactDomain],
        methods_executed: list[MethodId],
    ) -> Verdict:
        """Build verdict from aggregated findings for a single file.

        Args:
            findings: All findings for this file.
            domains: Detected domains for this file.
            methods_executed: All method IDs that were part of the batch.

        Returns:
            Verdict with decision, score, and findings.

        """
        if not findings:
            return Verdict(
                decision=VerdictDecision.ACCEPT,
                score=0.0,
                findings=[],
                domains_detected=[DomainConfidence(domain=d, confidence=0.7) for d in domains],
                methods_executed=methods_executed,
                summary="ACCEPT verdict (score: 0.0). 0 findings. Batch mode.",
            )

        findings = self._deduplicate_findings(findings)
        findings = self._apply_finding_limits(findings)

        # Assign sequential IDs
        sorted_findings = self._sort_by_severity(findings)
        reassigned = [replace(f, id=f"F{i}") for i, f in enumerate(sorted_findings, 1)]

        # Calculate clean passes
        domain_findings: dict[ArtifactDomain, int] = dict.fromkeys(domains, 0)
        for f in reassigned:
            if f.domain and f.domain in domain_findings:
                domain_findings[f.domain] += 1
        clean_passes = sum(1 for c in domain_findings.values() if c == 0)

        score = self._scorer.calculate_score(reassigned, clean_passes)
        decision = self._scorer.determine_verdict(score, reassigned)

        domain_confs = [DomainConfidence(domain=d, confidence=0.7) for d in domains]

        summary = (
            f"{decision.value} verdict (score: {score:.1f}). "
            f"{len(reassigned)} findings. Batch mode."
        )

        return Verdict(
            decision=decision,
            score=score,
            findings=reassigned,
            domains_detected=domain_confs,
            methods_executed=methods_executed,
            summary=summary,
        )

    def _empty_verdict(self) -> Verdict:
        """Return ACCEPT verdict for files with no methods selected."""
        return Verdict(
            decision=VerdictDecision.ACCEPT,
            score=0.0,
            findings=[],
            domains_detected=[],
            methods_executed=[],
            summary="ACCEPT verdict (score: 0.0). No methods selected. Batch mode.",
        )

    def _deduplicate_findings(self, findings: list[Finding]) -> list[Finding]:
        """Deduplicate findings using the same pattern/evidence heuristics as engine mode."""
        if not findings:
            return []

        severity_priority = {
            Severity.CRITICAL: 4,
            Severity.ERROR: 3,
            Severity.WARNING: 2,
            Severity.INFO: 1,
        }
        unique: list[Finding] = []

        for finding in findings:
            is_duplicate = False
            for existing in unique:
                if finding.pattern_id and finding.pattern_id == existing.pattern_id:
                    is_duplicate = True
                    if severity_priority.get(finding.severity, 0) > severity_priority.get(
                        existing.severity, 0
                    ):
                        unique[unique.index(existing)] = finding
                    break

                if finding.evidence and existing.evidence:
                    similarity = difflib.SequenceMatcher(
                        None,
                        finding.evidence[0].quote,
                        existing.evidence[0].quote,
                    ).ratio()
                    if similarity > 0.8:
                        is_duplicate = True
                        if severity_priority.get(finding.severity, 0) > severity_priority.get(
                            existing.severity, 0
                        ):
                            unique[unique.index(existing)] = finding
                        break

            if not is_duplicate:
                unique.append(finding)

        return unique

    def _apply_finding_limits(self, findings: list[Finding]) -> list[Finding]:
        """Apply per-method and total finding limits before batch scoring."""
        if not findings:
            return []

        limits = self._config.resource_limits
        sorted_findings = self._sort_by_severity(findings)
        method_counts: dict[MethodId, int] = {}
        limited: list[Finding] = []

        for finding in sorted_findings:
            count = method_counts.get(finding.method_id, 0)
            if count >= limits.max_findings_per_method:
                continue
            if len(limited) >= limits.max_total_findings:
                break
            method_counts[finding.method_id] = count + 1
            limited.append(finding)

        return limited

    def _sort_by_severity(self, findings: list[Finding]) -> list[Finding]:
        """Sort findings by severity, highest first."""
        return sorted(
            findings,
            key=lambda f: {
                Severity.CRITICAL: 0,
                Severity.ERROR: 1,
                Severity.WARNING: 2,
                Severity.INFO: 3,
            }.get(f.severity, 99),
        )
