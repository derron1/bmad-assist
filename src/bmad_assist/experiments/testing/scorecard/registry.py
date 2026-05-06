"""Auto-discovery registry for stack handlers."""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseStackHandler

_HANDLERS: dict[str, type[BaseStackHandler]] = {}
_discovered = False


def register_stack(handler_class: type[BaseStackHandler]) -> type[BaseStackHandler]:
    """Register a stack handler class. Can be used as a decorator or called directly."""
    _HANDLERS[handler_class.name.fget(handler_class)] = handler_class  # type: ignore[attr-defined]
    return handler_class


def _discover_stacks() -> None:
    """Auto-import all modules in the stacks/ sub-package."""
    global _discovered  # noqa: PLW0603
    if _discovered:
        return
    _discovered = True

    stacks_path = Path(__file__).parent / "stacks"
    # __package__ is "scorecard" (standalone) or "...common.scorecard" (installed).
    # The stacks sub-package is always <our_package>.stacks
    stacks_package = (__package__ or "scorecard") + ".stacks"

    for _importer, module_name, _ispkg in pkgutil.iter_modules([str(stacks_path)]):
        if module_name.startswith("_"):
            continue
        importlib.import_module(f"{stacks_package}.{module_name}")


def get_handler(stack_name: str) -> BaseStackHandler:
    """Get an instance of a registered stack handler by name."""
    _discover_stacks()
    if stack_name not in _HANDLERS:
        raise KeyError(f"Unknown stack: '{stack_name}'. Available: {', '.join(sorted(_HANDLERS))}")
    return _HANDLERS[stack_name]()


def detect_stack(fixture_path: Path) -> str:
    """Detect project stack from marker files. Returns stack name or 'unknown'."""
    _discover_stacks()
    import logging

    markers = []
    for name, handler_cls in _HANDLERS.items():
        handler = handler_cls()
        if handler.detect(fixture_path):
            markers.append(name)

    if not markers:
        return "unknown"

    if len(markers) > 1:
        logging.getLogger(__name__).warning(
            "Multiple stack markers found in %s: %s. Using %s.", fixture_path, markers, markers[0]
        )

    return markers[0]


def get_handler_for_fixture(fixture_path: Path) -> BaseStackHandler | None:
    """Detect stack and return handler instance, or None if unknown."""
    stack = detect_stack(fixture_path)
    if stack == "unknown":
        return None
    return _HANDLERS[stack]()
