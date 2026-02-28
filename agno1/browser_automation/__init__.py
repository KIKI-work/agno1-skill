"""High-level browser automation entrypoints."""

from .base import BaseChatAdapter, ExecutionConfig, ExecuteMode
from .errors import AutomationError, CDPConnectionError, PageClosedError, SelectorNotFoundError
from .manager import BrowserConfig, BrowserManager, PageHandle, SessionState
from .gpt import ChatGPTAdapter
from .gemini import GeminiAdapter
from .selectors import CHATGPT_SELECTORS, GEMINI_SELECTORS, PlatformSelectors

__all__ = [
    "BaseChatAdapter",
    "BrowserConfig",
    "BrowserManager",
    "ExecutionConfig",
    "ExecuteMode",
    "PageHandle",
    "SessionState",
    "ChatGPTAdapter",
    "GeminiAdapter",
    "PlatformSelectors",
    "CHATGPT_SELECTORS",
    "GEMINI_SELECTORS",
    "AutomationError",
    "CDPConnectionError",
    "PageClosedError",
    "SelectorNotFoundError",
]
