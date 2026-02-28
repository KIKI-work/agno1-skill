from __future__ import annotations


class AutomationError(RuntimeError):
    """Base error for browser automation failures."""


class CDPConnectionError(AutomationError):
    """Raised when CDP attach/connect fails or disconnects."""


class PageClosedError(AutomationError):
    """Raised when an expected page is closed or unavailable."""


class SelectorNotFoundError(AutomationError):
    """Raised when required DOM elements cannot be located."""


__all__ = [
    "AutomationError",
    "CDPConnectionError",
    "PageClosedError",
    "SelectorNotFoundError",
]
