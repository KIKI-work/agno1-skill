from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple
from urllib.parse import urlparse

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from .errors import CDPConnectionError, PageClosedError
from .utils import ensure_dir


@dataclass
class BrowserConfig:
    mode: Literal["launch", "attach"] = "launch"

    # launch mode
    headless: bool = True
    channel: Optional[str] = None  # e.g. "chrome"
    executable_path: Optional[str] = None
    args: List[str] = field(default_factory=list)

    # attach mode (CDP)
    cdp_endpoint: Optional[str] = None  # e.g. "http://localhost:9222"

    # navigation
    # NOTE: ChatGPT 的 /g/... 页面首屏可能很慢；默认给更宽松的导航超时。
    navigation_timeout_ms: int = 600_000
    navigation_wait_until: str = "commit"  # "commit" is more tolerant than "domcontentloaded"

    # auth
    storage_state_path: Optional[str] = None  # cookies/localStorage snapshot

    # debug & downloads
    accept_downloads: bool = True
    base_artifacts_dir: str = "./artifacts"
    capture_debug_screenshot: bool = True
    capture_debug_html: bool = False


@dataclass
class SessionState:
    last_assistant_count: int = 0
    known_artifact_keys: set[str] = field(default_factory=set)

    # user-side markers (send ack / dedupe)
    last_user_count: int = 0
    last_user_text_snapshot: Optional[str] = None
    # ChatGPT 等 UI 中 message 文本可能重复（例如反复发送“继续”），用 message-id 才能稳定去重/判定发送成功
    last_user_id_snapshot: Optional[str] = None

    # Snapshot used to avoid mixing history & latest (and handle "count unchanged" streaming UIs)
    last_assistant_text_snapshot: Optional[str] = None

    # for "继续同一个对话" & crash-resume
    chat_url: Optional[str] = None
    selected_model: Optional[str] = None


@dataclass
class PageHandle:
    platform: str
    session_id: str
    page: Page
    state: SessionState
    created_at: float = field(default_factory=time.time)


class BrowserManager:
    """Manages Playwright lifecycle + keeps Page instances for session persistence."""

    def __init__(self, config: BrowserConfig):
        self.config = config
        self._pw = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

        # page pool + state pool (state persists even if page is recreated)
        self._pages: Dict[Tuple[str, str], PageHandle] = {}
        self._states: Dict[Tuple[str, str], SessionState] = {}

        # one lock per (platform, session_id): prevent concurrent sends on same chat
        self._locks: Dict[Tuple[str, str], threading.RLock] = {}

        ensure_dir(self.config.base_artifacts_dir)

    def _ensure_connected(self) -> None:
        if self.config.mode != "attach":
            return

        if self._browser is None:
            self.start()
            return

        try:
            if hasattr(self._browser, "is_connected") and not self._browser.is_connected():
                self.close()
                self.start()
        except Exception as e:
            raise CDPConnectionError(f"CDP browser connection lost: {e}") from e

    def start(self) -> None:
        if self._browser is not None:
            return

        self._pw = sync_playwright().start()
        if self.config.mode == "attach":
            if not self.config.cdp_endpoint:
                raise CDPConnectionError("Attach mode requires BrowserConfig.cdp_endpoint")
            try:
                self._browser = self._pw.chromium.connect_over_cdp(self.config.cdp_endpoint)
            except Exception as e:
                raise CDPConnectionError(f"connect_over_cdp failed: {e}") from e
            if self._browser.contexts:
                # Reuse existing context (keeps login state when attaching to a user-data-dir Chrome)
                self._context = self._browser.contexts[0]
            else:
                self._context = self._browser.new_context(accept_downloads=self.config.accept_downloads)
        else:
            self._browser = self._pw.chromium.launch(
                headless=self.config.headless,
                channel=self.config.channel,
                executable_path=self.config.executable_path,
                args=self.config.args,
            )
            ctx_kwargs: Dict[str, Any] = {"accept_downloads": self.config.accept_downloads}
            if self.config.storage_state_path:
                ctx_kwargs["storage_state"] = self.config.storage_state_path
            self._context = self._browser.new_context(**ctx_kwargs)

        assert self._context is not None
        self._context.set_default_timeout(30_000)
        try:
            self._context.set_default_navigation_timeout(int(self.config.navigation_timeout_ms))
        except Exception:
            pass

        # Optional: allow clipboard read/write (useful for Gemini copy button extraction)
        try:
            self._context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://gemini.google.com")
        except Exception:
            pass
        try:
            self._context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://chatgpt.com")
        except Exception:
            pass

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            self.start()
        assert self._context is not None
        return self._context

    def _get_state(self, platform: str, session_id: str) -> SessionState:
        key = (platform, session_id)
        if key not in self._states:
            self._states[key] = SessionState()
        return self._states[key]

    def get_lock(self, platform: str, session_id: str) -> threading.RLock:
        key = (platform, session_id)
        if key not in self._locks:
            self._locks[key] = threading.RLock()
        return self._locks[key]

    def get_or_create_page(
        self,
        *,
        platform: str,
        session_id: str,
        url: str,
        page_instance: Optional[Page] = None,
        bring_to_front: bool = False,
    ) -> PageHandle:
        """Get or create a Page for a given (platform, session_id).

        IMPORTANT for stateful multi-turn:
        - Reuse the same Page for same (platform, session_id)
        - When reused, DO NOT goto()/refresh (keeps websocket + chat context)
        """
        self._ensure_connected()

        key = (platform, session_id)
        state = self._get_state(platform, session_id)

        # reuse page if alive
        if key in self._pages and not self._pages[key].page.is_closed():
            handle = self._pages[key]
            if bring_to_front:
                try:
                    handle.page.bring_to_front()
                except Exception:
                    pass
            return handle

        # choose a resume url if we have it
        target_url = state.chat_url or url

        def _origin(u: str) -> str:
            try:
                p = urlparse(u)
                if p.scheme and p.netloc:
                    return f"{p.scheme}://{p.netloc}"
            except Exception:
                pass
            return ""

        # create or reuse a page
        page = page_instance
        if page is None and self.config.mode == "attach":
            # When attaching over CDP, prefer reusing an existing tab on the same origin.
            # This makes the automation visible to the user and keeps existing login/session.
            try:
                tgt_origin = _origin(target_url)
                if tgt_origin and self._browser is not None:
                    for ctx in list(self._browser.contexts):
                        for p in list(ctx.pages):
                            try:
                                if p.is_closed():
                                    continue
                                cur = p.url or ""
                                if cur.startswith(tgt_origin):
                                    page = p
                                    break
                            except Exception:
                                continue
                        if page is not None:
                            break
            except Exception:
                page = None

        if page is None:
            page = self.context.new_page()

        try:
            if page.is_closed():
                raise PageClosedError("Page is closed after creation")
        except Exception as e:
            raise PageClosedError(f"Page is unavailable: {e}") from e

        # navigate when page is blank; also ensure first-use goes to requested url
        try:
            current = page.url or ""
        except Exception:
            current = ""

        if (not current) or current == "about:blank" or (state.chat_url is None and current != target_url):
            try:
                page.goto(
                    target_url,
                    wait_until=self.config.navigation_wait_until,
                    timeout=int(self.config.navigation_timeout_ms),
                )
            except Exception:
                # 导航失败时也不要直接打崩：后续 locate/send 会失败并落 debug。
                pass

        handle = PageHandle(platform=platform, session_id=session_id, page=page, state=state)
        self._pages[key] = handle

        if bring_to_front:
            try:
                page.bring_to_front()
            except Exception:
                pass

        return handle

    def close(self) -> None:
        """Close Playwright resources.

        IMPORTANT:
        - launch 模式：关闭由 Playwright 启动的 browser/context/page。
        - attach 模式（CDP 连接到你已打开的 Chrome）：默认**不关闭**真实浏览器/已有页面，
          只断开 Playwright 连接，避免把用户正在用的 Chrome 关掉。
        """

        try:
            if self.config.mode == "launch":
                for h in list(self._pages.values()):
                    try:
                        if not h.page.is_closed():
                            h.page.close()
                    except Exception:
                        pass

                if self._context is not None:
                    try:
                        self._context.close()
                    except Exception:
                        pass

                if self._browser is not None:
                    try:
                        self._browser.close()
                    except Exception:
                        pass
            else:
                # attach over CDP: DO NOT close user's Chrome / tabs.
                # We just drop our references; Playwright driver stop() will disconnect.
                pass

            self._pages.clear()
            self._context = None
            self._browser = None

        finally:
            self._locks.clear()
            if self._pw is not None:
                try:
                    self._pw.stop()
                except Exception:
                    pass
                self._pw = None


__all__ = [
    "BrowserConfig",
    "BrowserManager",
    "PageHandle",
    "SessionState",
]
