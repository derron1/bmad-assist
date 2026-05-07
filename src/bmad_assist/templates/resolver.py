"""Resolver for bundled scaffolding templates.

Templates ship with the package so consumer projects (pip-installed or
development checkouts) can locate them without filesystem assumptions.

The idiom mirrors :mod:`bmad_assist.security.patterns` — try
``importlib.resources`` first, then fall back to a path relative to this
file for development checkouts.
"""

from __future__ import annotations

import importlib.resources
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def autoresearch_template_dir() -> Path:
    """Return the path to the bundled autoresearch template directory.

    The directory contains the five-file binary-classification harness
    (``program.md``, ``experiment.py``, ``benchmark.py``, ``scorecard.json``,
    ``README.md``). See :doc:`/docs/recipes/autoresearch` for the recipe
    that defines its contract.

    Returns:
        Path to the bundled ``autoresearch/template`` directory.

    Raises:
        FileNotFoundError: If neither resolution strategy can locate the
            directory (typically a broken install).

    """
    # Preferred path: installed package via importlib.resources.
    try:
        pkg = importlib.resources.files("bmad_assist.templates")
        template_dir = Path(str(pkg)) / "autoresearch" / "template"
        if template_dir.is_dir():
            return template_dir
    except (ModuleNotFoundError, TypeError):
        pass

    # Fallback: resolve relative to this file (development checkouts where
    # importlib.resources cannot resolve the package directory).
    fallback = Path(__file__).parent / "autoresearch" / "template"
    if fallback.is_dir():
        return fallback

    raise FileNotFoundError(
        "Autoresearch template directory not found. Reinstall: pip install -e ."
    )
