# browser_automation/base.py
from __future__ import annotations

import os
import re
import shutil
import time
import traceback
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from playwright.sync_api import Download, Locator, Page

from .diagnostics import DiagnosticsHelper
from .errors import SelectorNotFoundError
from .manager import BrowserConfig, BrowserManager, PageHandle, SessionState
from .selectors import PlatformSelectors
from .utils import ensure_dir, first_match_locator, now_ts, sanitize_filename, safe_inner_text, safe_inner_html, html_to_markdown, with_retry

ExecuteMode = Literal["send-prompt", "extract-latest", "download-artifact"]


@dataclass
class ExecutionConfig:
    """Execution knobs.

    Notes:
    - generation_timeout_s is the hard timeout for a single model generation.
    - stable_text_window_s is used as a platform-agnostic completion signal when
      there is no reliable stop button (e.g. Gemini streaming).
    - model_switch_strict controls whether model selection failures should raise.
      In UI automation, selectors can break after UI changes; default is best-effort.
    """

    generation_timeout_s: int = 300
    stable_text_window_s: float = 2.0
    poll_interval_s: float = 0.5
    artifact_wait_s: int = 30
    after_upload_wait_s: float = 0.8
    after_model_switch_wait_s: float = 0.8

    # prompt readiness (SPA pages can be slow; avoid giving up too early)
    prompt_ready_timeout_s: int = 60

    # Wait for the UI to be idle (no active generation) before sending a new prompt.
    # 0 disables this behavior.
    idle_before_send_timeout_s: float = 0.0

    # generation end fallbacks (stop button may stick around)
    force_end_if_stop_visible_s: float = 12.0
    force_end_min_text_chars: int = 40

    # post generation: extraction can lag behind UI completion signals
    post_generation_extract_retry_s: float = 6.0
    require_non_empty_reply: bool = False

    # Ensure the reply is actually rendered in DOM before proceeding.
    # This is important for virtualized chat UIs where generation may finish before the latest block is mounted.
    require_reply_visible: bool = True
    reply_visible_timeout_s: float = 90.0
    reply_visible_min_chars: int = 1

    # If reply is not visible within the timeout, try reloading the page once (best-effort) and re-check.
    refresh_on_reply_not_visible: bool = True
    refresh_max_attempts: int = 1
    refresh_cooldown_s: float = 2.0

    # Require specific completion marker in reply before proceeding to next step.
    require_completion_marker: Optional[str] = None

    # send ack (avoid duplicate sends on flaky UI)
    send_ack_timeout_s: float = 8.0
    send_max_attempts: int = 2
    require_send_ack: bool = False

    # upload confirmation (wait for UI to reflect selected files)
    upload_confirm_timeout_s: float = 12.0

    # If generation "starts" but there is no visible output for too long, fail fast.
    # (0 disables this behavior)
    no_output_timeout_s: float = 0.0

    # Optional hint to filter artifact downloads by name/text.
    artifact_name_hint: Optional[str] = None

    # bad reply retry policy
    min_reply_chars: int = 0  # 0 disables
    retry_on_short_reply: bool = False
    regenerate_max_attempts: int = 1

    model_switch_strict: bool = False


class BaseChatAdapter:
    def __init__(
        self,
        *,
        platform: str,
        selectors: PlatformSelectors,
        browser: BrowserManager,
        exec_cfg: Optional[ExecutionConfig] = None,
        default_model: Optional[str] = None,
    ):
        self.platform = platform
        self.sel = selectors
        self.browser = browser
        self.cfg = exec_cfg or ExecutionConfig()
        self.default_model = default_model

    # ---------- Public API ----------

    def execute(
        self,
        *,
        mode: ExecuteMode,
        session_id: str,
        instruction: Optional[str] = None,
        files: Optional[List[str]] = None,
        url: Optional[str] = None,
        page_instance: Optional[Page] = None,
        output_dir: Optional[str] = None,
        download_after: bool = False,
        bring_to_front: bool = False,
        # model control
        model: Optional[str] = None,
        reset_chat: bool = False,
        ensure_model: bool = True,
        # navigation control
        force_goto: bool = False,
        wait_for: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Unified execution entry.

        Modes:
        - send-prompt: (optional upload) + send prompt + wait + extract text (+ optional download artifacts)
        - extract-latest: do not send; just extract last assistant message
        - download-artifact: do not send; wait for current gen (best-effort) and download artifacts
        """
        out_dir = ensure_dir(output_dir or os.path.join(self.browser.config.base_artifacts_dir, self.platform, session_id))
        url_to_use = url or self.sel.start_url

        lock = self.browser.get_lock(self.platform, session_id)
        with lock:
            try:
                handle = self.browser.get_or_create_page(
                    platform=self.platform,
                    session_id=session_id,
                    url=url_to_use,
                    page_instance=page_instance,
                    bring_to_front=bring_to_front,
                )
            except Exception as e:
                # get_or_create_page 可能因 goto 超时/attach 异常直接抛出，这里统一兜底返回
                return {"status": "error", "text": "", "files": [], "error": f"{e}"}

            page = handle.page
            state = handle.state

            # 强制跳转：用于“复用同一个 session 但要去新的 /c/ 会话”等场景。
            # 默认情况下，为了保持 websocket + chat 上下文，我们不会对已存在 page 做 goto。
            if force_goto and url:
                try:
                    page.goto(
                        url,
                        wait_until=self.browser.config.navigation_wait_until,
                        timeout=int(self.browser.config.navigation_timeout_ms),
                    )
                    self._update_chat_url(page, state)
                except Exception:
                    # 失败时后续逻辑会在找不到输入框等位置报错，并落 debug。
                    pass

            try:
                if mode == "extract-latest":
                    if wait_for and isinstance(wait_for, dict):
                        sel = wait_for.get("selector")
                        timeout_ms = int(wait_for.get("timeout_ms") or 30000)
                        visible = bool(wait_for.get("visible", True))
                        if sel:
                            try:
                                page.wait_for_selector(
                                    sel,
                                    timeout=timeout_ms,
                                    state="visible" if visible else "attached",
                                )
                            except Exception:
                                pass
                    self._update_chat_url(page, state)
                    text = self._extract_latest_reply(page, state, since_last=False)
                    return {"status": "complete", "text": text, "files": [], "error": None}

                if mode == "send-prompt":
                    if not instruction:
                        raise ValueError("send-prompt requires instruction")

                    # If user wants reset chat (useful when model is locked per conversation)
                    if reset_chat:
                        self._start_new_chat(page, state)
                        time.sleep(0.8)

                    # Decide model to use
                    model_to_use: Optional[str]
                    if model is not None and not model.strip():
                        # explicit blank => skip
                        model_to_use = None
                    else:
                        model_to_use = model or self.default_model

                    # Ensure model (best-effort by default)
                    if ensure_model and model_to_use:
                        try:
                            self._ensure_model(page, state, model_to_use)
                            time.sleep(self.cfg.after_model_switch_wait_s)
                        except Exception:
                            if self.cfg.model_switch_strict:
                                raise

                    # Make sure we're at the bottom so the virtualized list mounts latest messages.
                    self._scroll_to_bottom(page)

                    if self.cfg.idle_before_send_timeout_s and self.cfg.idle_before_send_timeout_s > 0:
                        self._wait_idle_before_send(page, timeout_s=self.cfg.idle_before_send_timeout_s)

                    # snapshot before send
                    prev_count = self._assistant_count(page)
                    prev_last_text = self._peek_last_assistant_text(page)
                    prev_user_count = self._user_count(page)
                    prev_last_user_text = self._peek_last_user_text(page)
                    prev_last_user_id = self._peek_last_user_id(page)

                    state.last_assistant_count = prev_count
                    state.last_assistant_text_snapshot = prev_last_text
                    state.last_user_count = prev_user_count
                    state.last_user_text_snapshot = prev_last_user_text
                    state.last_user_id_snapshot = prev_last_user_id

                    if files:
                        print(f"🔼 准备上传 {len(files)} 个文件: {[os.path.basename(f) for f in files]}")
                        self._upload_files(page, files)
                        print(f"✅ 文件上传完成，等待 {self.cfg.after_upload_wait_s}s")
                        time.sleep(self.cfg.after_upload_wait_s)

                    send_info = self._send_prompt_with_ack(
                        page,
                        instruction,
                        prev_user_count=prev_user_count,
                        prev_last_user_text=prev_last_user_text,
                        prev_last_user_id=prev_last_user_id,
                    )

                    if self.cfg.require_send_ack and send_info.get("acked") is False:
                        raise RuntimeError(f"[{self.platform}] send-ack not observed; refusing to proceed")

                    # update state markers for dedupe
                    state.last_user_count = send_info.get("user_count") or prev_user_count
                    state.last_user_text_snapshot = send_info.get("last_user") or prev_last_user_text
                    state.last_user_id_snapshot = send_info.get("last_user_id") or prev_last_user_id

                    # wait generation
                    self._wait_generation(page, prev_assistant_count=prev_count, prev_last_text=prev_last_text)

                    # update chat url for crash-resume
                    self._update_chat_url(page, state)

                    # Ensure latest messages are mounted (ChatGPT uses virtualization).
                    self._scroll_to_bottom(page)

                    text = self._extract_latest_reply(page, state, since_last=True)

                    # Sometimes completion signals flip before final text is attached to DOM.
                    if not (text or "").strip() and self.cfg.post_generation_extract_retry_s > 0:
                        end_by = time.time() + self.cfg.post_generation_extract_retry_s
                        while time.time() < end_by:
                            time.sleep(min(0.5, self.cfg.poll_interval_s))
                            text = self._extract_latest_reply(page, state, since_last=True)
                            if (text or "").strip():
                                break

                    # Hard gate: do not proceed until the assistant reply is actually visible in DOM.
                    if self.cfg.require_reply_visible:
                        text = self._wait_reply_visible_or_refresh(page, state, current_text=text)

                    # Optional bad-reply retry: prefer regenerate (no duplicate user message)
                    regen_attempts = 0
                    while True:
                        if self.cfg.require_non_empty_reply and not (text or "").strip():
                            raise RuntimeError(f"[{self.platform}] empty assistant reply after generation")

                        if (
                            self.cfg.retry_on_short_reply
                            and self.cfg.min_reply_chars
                            and len((text or "").strip()) < self.cfg.min_reply_chars
                            and regen_attempts < self.cfg.regenerate_max_attempts
                        ):
                            if self._regenerate_last_reply(page):
                                regen_attempts += 1
                                # regeneration may stream into the same last block; use prev_last_text to detect change
                                self._wait_generation(
                                    page,
                                    prev_assistant_count=prev_count,
                                    prev_last_text=self._peek_last_assistant_text(page),
                                    allow_no_new=True,
                                )
                                self._update_chat_url(page, state)
                                text = self._extract_latest_reply(page, state, since_last=True)
                                continue
                        break

                    downloaded: List[str] = []
                    if download_after:
                        # 若页面提示代码编译器已过期，先重新发送当前 prompt 再下载
                        diag = self._collect_diagnostics(page, state)
                        if diag.get("hint_compiler_expired"):
                            print(f"[{self.platform}] 检测到代码编译器已过期，重新发送 prompt 后重试下载")
                            prev_count = self._assistant_count(page)
                            prev_last_text = self._peek_last_assistant_text(page)
                            prev_user_count = self._user_count(page)
                            prev_last_user_text = self._peek_last_user_text(page)
                            prev_last_user_id = self._peek_last_user_id(page)
                            send_info = self._send_prompt_with_ack(
                                page,
                                instruction,
                                prev_user_count=prev_user_count,
                                prev_last_user_text=prev_last_user_text,
                                prev_last_user_id=prev_last_user_id,
                            )
                            state.last_user_count = send_info.get("user_count") or prev_user_count
                            state.last_user_text_snapshot = send_info.get("last_user") or prev_last_user_text
                            state.last_user_id_snapshot = send_info.get("last_user_id") or prev_last_user_id
                            self._wait_generation(page, prev_assistant_count=prev_count, prev_last_text=prev_last_text)
                            self._update_chat_url(page, state)
                            self._scroll_to_bottom(page)
                            text = self._extract_latest_reply(page, state, since_last=True)
                        downloaded = self._download_artifacts(page, state, out_dir=out_dir)

                    # Check for required completion marker before proceeding
                    if self.cfg.require_completion_marker and self.cfg.require_completion_marker.strip():
                        marker = self.cfg.require_completion_marker.strip()
                        if marker not in text:
                            # Wait a bit more and re-extract to ensure we have the complete reply
                            time.sleep(2.0)
                            text = self._extract_latest_reply(page, state, since_last=True)
                            if marker not in text:
                                raise RuntimeError(
                                    f"[{self.platform}] required completion marker '{marker}' not found in reply. "
                                    f"Reply length: {len(text)}, preview: {text[-200:]}"
                                )

                    return {"status": "complete", "text": text, "files": downloaded, "error": None, "send": send_info}

                if mode == "download-artifact":
                    # 必须打开指定 URL，不发送任何 prompt；若未传 url 或 attach 复用了其它页签，这里强制导航
                    if not (url or "").strip():
                        return {"status": "error", "text": "", "files": [], "error": "download-artifact 需要传入 url（会话 URL）"}
                    try:
                        print(f"[download-artifact] navigate: {url.strip()}")
                        page.goto(
                            url.strip(),
                            wait_until=self.browser.config.navigation_wait_until,
                            timeout=int(self.browser.config.navigation_timeout_ms),
                        )
                        self._update_chat_url(page, state)
                    except Exception as e:
                        return {"status": "error", "text": "", "files": [], "error": f"打开 URL 失败: {e}"}
                    if wait_for and isinstance(wait_for, dict):
                        sel = wait_for.get("selector")
                        timeout_ms = int(wait_for.get("timeout_ms") or 30000)
                        visible = bool(wait_for.get("visible", True))
                        if sel:
                            try:
                                page.wait_for_selector(
                                    sel,
                                    timeout=timeout_ms,
                                    state="visible" if visible else "attached",
                                )
                            except Exception:
                                pass
                    print("[download-artifact] prepare page for artifact extraction")
                    self._scroll_to_bottom(page)
                    time.sleep(2.0)  # 等待文件卡片/下载链接渲染
                    prev_count = self._assistant_count(page)
                    prev_last_text = self._peek_last_assistant_text(page)
                    # In download-only mode we should never block for long generation wait.
                    # Best-effort short wait, then continue downloading.
                    old_timeout = self.cfg.generation_timeout_s
                    try:
                        self.cfg.generation_timeout_s = min(int(self.cfg.generation_timeout_s or 60), 20)
                        self._wait_generation(
                            page,
                            prev_assistant_count=max(0, prev_count - 1),
                            prev_last_text=prev_last_text,
                            allow_no_new=True,
                        )
                    except Exception:
                        pass
                    finally:
                        self.cfg.generation_timeout_s = old_timeout
                    self._update_chat_url(page, state)
                    self._scroll_to_bottom(page)
                    print("[download-artifact] start download extraction")
                    downloaded = self._download_artifacts(page, state, out_dir=out_dir)
                    print(f"[download-artifact] finished, files={len(downloaded)}")
                    return {"status": "complete", "text": "", "files": downloaded, "error": None}

                raise ValueError(f"Unknown mode: {mode}")

            except TimeoutError as e:
                dbg = self._capture_debug(page, out_dir, tag="timeout")
                advice = self._classify_timeout(str(e), dbg.get("diagnostics") or {})
                return {
                    "status": "timeout",
                    "text": "",
                    "files": [],
                    "error": f"{e}",
                    "retryable": advice.get("retryable"),
                    "retry_reason": advice.get("reason"),
                    "retry_action": advice.get("action"),
                    **dbg,
                }
            except Exception as e:
                dbg = self._capture_debug(page, out_dir, tag="error")
                return {"status": "error", "text": "", "files": [], "error": f"{e}\n{traceback.format_exc()}", **dbg}

    # ---------- Hooks to override in subclasses ----------
    def _ensure_model(self, page: Page, state: SessionState, model: str) -> None:
        # default no-op
        state.selected_model = model

    def _start_new_chat(self, page: Page, state: SessionState) -> None:
        """Start a new chat.

        Default: click "new chat" button; fallback to navigating start_url.
        Subclasses can override for platform-specific reliability.
        """
        clicked = False
        btn = first_match_locator(page, self.sel.new_chat_button, must_be_visible=False)
        if btn and btn.count() > 0:
            try:
                btn.click()
                clicked = True
            except Exception:
                clicked = False
        if not clicked:
            try:
                page.goto(self.sel.start_url, wait_until="domcontentloaded")
            except Exception:
                pass

        # reset state markers
        state.last_assistant_count = 0
        state.last_assistant_text_snapshot = None
        state.last_user_count = 0
        state.last_user_text_snapshot = None
        state.last_user_id_snapshot = None
        state.known_artifact_keys.clear()
        # keep selected_model (often global) but reset chat url
        state.chat_url = None

    # ---------- Helpers ----------
    def _update_chat_url(self, page: Page, state: SessionState) -> None:
        try:
            u = page.url
            if u and u != "about:blank":
                state.chat_url = u
        except Exception:
            pass

    def _assistant_blocks_locator(self, page: Page) -> Locator:
        # 尽量把 message block 的查找限制在 chat root 内，避免误匹配到侧栏/隐藏区域
        scope = self._chat_root_scope(page)

        primary = ",".join(self.sel.assistant_message_blocks) if self.sel.assistant_message_blocks else ""
        fallback = ",".join(self.sel.assistant_message_blocks_fallback) if self.sel.assistant_message_blocks_fallback else ""

        if primary:
            loc = scope.locator(primary) if scope is not page else page.locator(primary)
            try:
                if loc.count() > 0:
                    return loc
            except Exception:
                pass
            # 当 chat_root 内无匹配时，尝试全页查找（ChatGPT Project / custom GPT 页面结构可能不同）
            if scope is not page:
                try:
                    loc_full = page.locator(primary)
                    if loc_full.count() > 0:
                        return loc_full
                except Exception:
                    pass

        if fallback:
            loc = scope.locator(fallback) if scope is not page else page.locator(fallback)
            try:
                if loc.count() > 0:
                    return loc
            except Exception:
                pass
            if scope is not page:
                try:
                    loc_full = page.locator(fallback)
                    if loc_full.count() > 0:
                        return loc_full
                except Exception:
                    pass

        # last resort: empty locator
        return page.locator("non-existent-tag")

    def _user_blocks_locator(self, page: Page) -> Locator:
        scope = self._chat_root_scope(page)
        sel = ",".join(self.sel.user_message_blocks) if getattr(self.sel, "user_message_blocks", None) else ""
        if sel:
            return scope.locator(sel) if scope is not page else page.locator(sel)
        return page.locator("non-existent-tag")

    def _chat_root_scope(self, page: Page) -> Any:
        """Best-effort root scope for artifact scanning (and potential future scoping)."""
        for sel in self.sel.chat_root:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    return loc
            except Exception:
                continue
        return page

    # ---------- Core interactions ----------
    def _locate_prompt_box(self, page: Page) -> Locator:
        deadline = time.time() + self.cfg.prompt_ready_timeout_s
        last_err: Optional[Exception] = None

        while time.time() < deadline:
            try:
                box = first_match_locator(page, self.sel.prompt_box, must_be_visible=True, timeout_ms_each=1500)
                if box:
                    # Check that a send button exists on the page (but allow it to be disabled)
                    send = first_match_locator(page, self.sel.send_button, must_be_visible=False)
                    if send is not None:
                        try:
                            if send.count() <= 0:
                                # No send button found, but continue anyway (some platforms hide it when empty)
                                pass
                        except Exception:
                            # Send button check failed, but continue anyway
                            pass
                    # Note: We don't fail if send button is not found, since some platforms hide/disable it when input is empty

                    # Ensure the prompt box is actually editable (ChatGPT may render a disabled/skeleton composer early)
                    try:
                        if (box.get_attribute("disabled") is not None) or (
                            (box.get_attribute("aria-disabled") or "").strip().lower() == "true"
                        ):
                            time.sleep(0.3)
                            continue
                        ce = (box.get_attribute("contenteditable") or "").strip().lower()
                        if ce and ce == "false":
                            time.sleep(0.3)
                            continue
                    except Exception:
                        pass

                    return box
            except Exception as e:
                last_err = e
            time.sleep(0.4)

        extra = f" last_err={last_err}" if last_err else ""
        raise SelectorNotFoundError(
            f"[{self.platform}] prompt box not found after {self.cfg.prompt_ready_timeout_s}s. "
            f"Update selectors.prompt_box or increase prompt_ready_timeout_s.{extra}"
        )

    def _send_prompt(self, page: Page, text: str) -> None:
        """Perform a single best-effort send attempt.

        IMPORTANT: this method must be side-effect aware (no blind retries).
        Higher-level logic should confirm send-ack before re-attempting.
        """
        box = self._locate_prompt_box(page)

        box.click()

        # Clear existing content (best-effort across textarea/contenteditable)
        try:
            box.press("Control+A")
            box.press("Backspace")
        except Exception:
            pass

        tag = ""
        try:
            tag = (box.evaluate("el => el.tagName") or "").strip().lower()
        except Exception:
            tag = ""

        if tag in {"textarea", "input"}:
            try:
                box.fill(text)
            except Exception:
                page.keyboard.insert_text(text)
        else:
            # ProseMirror/contenteditable: keyboard input tends to trigger the correct event chain
            page.keyboard.insert_text(text)

        # Wait for send button to become enabled (ChatGPT often renders it disabled until JS finishes binding events)
        end_by = time.time() + 20.0
        while time.time() < end_by:
            if self._is_send_ready(page):
                send_btn = first_match_locator(page, self.sel.send_button, must_be_visible=False)
                if send_btn and send_btn.count() > 0:
                    try:
                        send_btn.scroll_into_view_if_needed()
                    except Exception:
                        pass
                    try:
                        send_btn.click()
                        return
                    except Exception:
                        try:
                            send_btn.click(force=True)
                            return
                        except Exception:
                            pass
            time.sleep(0.2)

        # fallback Enter (may send or insert newline depending on settings)
        try:
            box.press("Enter")
        except Exception:
            page.keyboard.press("Enter")

    def _user_count(self, page: Page) -> int:
        loc = self._user_blocks_locator(page)
        try:
            return loc.count()
        except Exception:
            return 0

    def _peek_last_user_text(self, page: Page) -> str:
        """Return the last non-empty user message text (best-effort).

        ChatGPT may insert placeholder/empty user blocks while streaming.
        If we always pick the last block, send-ack can be tricked by placeholders.
        """

        loc = self._user_blocks_locator(page)
        try:
            n = loc.count()
        except Exception:
            n = 0
        if n <= 0:
            return ""

        # scan backwards and pick the last non-empty text
        for idx in range(n - 1, max(-1, n - 8) - 1, -1):
            block = loc.nth(idx)
            try:
                t = safe_inner_text(block)
            except Exception:
                t = ""
            if (t or "").strip():
                return t

        return ""

    def _peek_last_user_id(self, page: Page) -> str:
        """Return the message id of the last user block if present (best-effort).

        ChatGPT 的对话流里通常会有 data-message-id（或其祖先节点持有）。相比文本更稳定：
        - 用户可能反复发送同一句（例如“继续”），仅凭 last_user_text 无法判定是否发送成功
        - DOM 虚拟列表渲染会导致 count 抖动，message-id 能有效避免假阳性
        """

        loc = self._user_blocks_locator(page)
        try:
            n = loc.count()
        except Exception:
            n = 0
        if n <= 0:
            return ""

        def _is_placeholder(mid: str) -> bool:
            m = (mid or "").strip().lower()
            return m.startswith("placeholder")

        # scan backwards and pick the last non-placeholder id
        for idx in range(n - 1, max(-1, n - 8) - 1, -1):
            block = loc.nth(idx)

            # 1) Prefer explicit message id attributes (self or ancestor)
            for js in (
                "el => el.getAttribute('data-message-id') || ''",
                "el => el.closest('[data-message-id]')?.getAttribute('data-message-id') || ''",
            ):
                try:
                    mid = (block.evaluate(js) or "").strip()
                    if mid and (not _is_placeholder(mid)):
                        return mid
                except Exception:
                    pass

            # 2) Fallback to data-testid (ChatGPT often sets conversation-turn ids there)
            for js in (
                "el => el.getAttribute('data-testid') || ''",
                "el => el.closest('[data-testid]')?.getAttribute('data-testid') || ''",
            ):
                try:
                    mid = (block.evaluate(js) or "").strip()
                    if mid and (not _is_placeholder(mid)):
                        return mid
                except Exception:
                    pass

            # 3) Fallback to DOM id (rare, but cheap)
            for js in (
                "el => el.id || ''",
                "el => el.closest('[id]')?.id || ''",
            ):
                try:
                    mid = (block.evaluate(js) or "").strip()
                    if mid and (not _is_placeholder(mid)):
                        return mid
                except Exception:
                    pass

        return ""

    def _peek_prompt_text(self, page: Page) -> str:
        """Best-effort read of current composer text (textarea/input/contenteditable)."""

        box = first_match_locator(page, self.sel.prompt_box, must_be_visible=False)
        if box is None:
            return ""
        try:
            if box.count() <= 0:
                return ""
        except Exception:
            return ""

        # Prefer input_value() when applicable
        try:
            tag = (box.evaluate("el => (el.tagName || '').toLowerCase()") or "").strip()
        except Exception:
            tag = ""
        if tag in {"textarea", "input"}:
            try:
                return box.input_value() or ""
            except Exception:
                pass

        # contenteditable / ProseMirror
        try:
            return box.evaluate("el => el.textContent") or ""
        except Exception:
            return ""

    def _scroll_to_bottom(self, page: Page) -> None:
        """Best-effort scroll to the bottom to trigger virtualized list to mount latest messages."""
        try:
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        try:
            page.keyboard.press("End")
        except Exception:
            pass

    def _is_meaningful_reply_text(self, text: str) -> bool:
        s = " ".join((text or "").strip().split())
        if not s:
            return False
        if len(s) < int(self.cfg.reply_visible_min_chars or 1):
            return False

        # Filter common "thinking" placeholders that can appear as a standalone block.
        low = s.lower()
        if re.fullmatch(r"thought for\s+.*", s, flags=re.I):
            return False
        if re.fullmatch(r"思考.*", s) and ("秒" in s or "分钟" in s or "分" in s):
            return False

        return True

    def _is_thinking_placeholder_only(self, text: str) -> bool:
        """True if the text is only a 'thinking/generating' placeholder; generation should not be considered complete."""
        s = (text or "").strip()
        if not s:
            return False
        s_norm = " ".join(s.split()).lower()
        # "Thought for X seconds/minutes"
        if re.fullmatch(r"thought for\s+.*", s_norm, flags=re.I):
            return True
        # "思考 X 秒/分钟/分"
        if re.fullmatch(r"思考.*", s_norm) and ("秒" in s or "分钟" in s or "分" in s):
            return True
        # "Thinking...", "思考中", "Generating...", "生成中"
        if re.fullmatch(r"(thinking|思考中|generating|生成中)(\s*\.{0,3})?", s_norm, flags=re.I):
            return True
        if s_norm in ("thinking", "思考中", "generating", "生成中", "thinking...", "思考中..."):
            return True
        return False

    def _wait_reply_visible_or_refresh(self, page: Page, state: SessionState, *, current_text: str) -> str:
        """Wait until latest assistant reply becomes visible (rendered in DOM).

        If it does not become visible within `reply_visible_timeout_s`, try page reload and re-check.
        """

        def _wait_once(timeout_s: float, text0: str) -> str:
            end_by = time.time() + max(0.2, float(timeout_s))
            last = text0 or ""
            while time.time() < end_by:
                self._scroll_to_bottom(page)

                if self._is_meaningful_reply_text(last):
                    return last

                # poll for newly mounted content
                time.sleep(min(0.5, self.cfg.poll_interval_s))
                t = self._extract_latest_reply(page, state, since_last=True)
                if (t or "").strip():
                    last = t
            return ""

        # First try without reloading.
        out = _wait_once(self.cfg.reply_visible_timeout_s, current_text)
        if out:
            return out

        # Optional refresh + re-check.
        if self.cfg.refresh_on_reply_not_visible and int(self.cfg.refresh_max_attempts) > 0:
            for _ in range(int(self.cfg.refresh_max_attempts)):
                try:
                    page.reload(
                        wait_until=self.browser.config.navigation_wait_until,
                        timeout=int(self.browser.config.navigation_timeout_ms),
                    )
                except Exception:
                    pass

                time.sleep(max(0.2, float(self.cfg.refresh_cooldown_s)))
                self._update_chat_url(page, state)
                self._scroll_to_bottom(page)

                # After reload, the DOM may be re-hydrated; allow another window.
                out2 = _wait_once(min(self.cfg.reply_visible_timeout_s, 60.0), "")
                if out2:
                    return out2

        raise TimeoutError(
            f"[{self.platform}] reply not visible in DOM after generation (timeout={self.cfg.reply_visible_timeout_s}s)"
        )

    def _wait_send_ack(
        self,
        page: Page,
        *,
        expected_user_text: str,
        prev_user_count: int,
        prev_last_user_text: str,
        prev_last_user_id: str,
        timeout_s: float,
    ) -> Dict[str, Any]:
        """Wait until the prompt is observed in the UI.

        ⚠️ 重要：不要仅凭 user block count 增加就判定发送成功。
        ChatGPT 新 UI（尤其长对话/项目页）会做虚拟列表/惰性渲染：
        - 页面加载/滚动时 user_blocks 可能变化，即使没有发送新 prompt
        - 用户可能重复发送同一句（例如“继续”），last_user_text 无法区分是否新增

        因此 send-ack 需要组合更强信号：
        - last user 的 message-id 变化（最稳）
        - 或 输入框清空 + stop 进入 active / send 变为不可用
        """

        def _norm(s: str) -> str:
            return " ".join((s or "").strip().split())

        expected = _norm(expected_user_text)
        prev_last = _norm(prev_last_user_text)
        prev_id = (prev_last_user_id or "").strip()

        end_by = time.time() + max(0.2, timeout_s)
        soft_busy_seen = False

        while time.time() < end_by:
            cur_count = self._user_count(page)
            cur_last = _norm(self._peek_last_user_text(page))
            cur_id = (self._peek_last_user_id(page) or "").strip()

            # composer state is a much more reliable "did we actually send" signal than count alone
            prompt_txt = ""
            try:
                prompt_txt = self._peek_prompt_text(page)
            except Exception:
                prompt_txt = ""
            prompt_empty = _norm(prompt_txt) == ""

            stop_active = False
            send_ready = False
            try:
                stop_active = self._is_stop_active(page)
            except Exception:
                stop_active = False
            try:
                send_ready = self._is_send_ready(page)
            except Exception:
                send_ready = False

            # Strongest: message id changes (works even when user text is identical like "继续")
            # BUT: on some ChatGPT pages, history messages can load AFTER the composer is ready.
            # If prev_id was empty, a later history render can look like a "new" id and cause false acks.
            # Therefore we also require a text match to the prompt (best-effort) when possible.
            if cur_id and cur_id != prev_id:
                # Guard against placeholder ids / late history hydration.
                if (cur_id or "").strip().lower().startswith("placeholder"):
                    time.sleep(min(0.25, self.cfg.poll_interval_s))
                    continue

                if expected:
                    # In strict mode, require a visible user message text that matches this prompt.
                    if not cur_last:
                        time.sleep(min(0.25, self.cfg.poll_interval_s))
                        continue
                    if expected[: min(20, len(expected))] not in cur_last:
                        time.sleep(min(0.25, self.cfg.poll_interval_s))
                        continue

                return {
                    "acked": True,
                    "ack_reason": "user_id_changed",
                    "user_count": cur_count,
                    "last_user": cur_last,
                    "last_user_id": cur_id,
                    "prompt_empty": prompt_empty,
                    "stop_active": stop_active,
                    "send_ready": send_ready,
                }

            # NOTE: composer cleared + UI busy is only a *soft* signal.
            # It can be a false positive on ChatGPT:
            # - we may have cleared the composer ourselves
            # - stop may be active because of a previous generation
            # - virtualized message list may re-render and change counts without a real send
            # Therefore we DO NOT treat it as a final ack; we keep waiting for a real user-message change.
            if prompt_empty and (stop_active or (not send_ready)):
                soft_busy_seen = True

            # fallback: in some UIs count may be stable but last user text updates
            if expected and cur_last and cur_last != prev_last and expected[:20] in cur_last and prompt_empty:
                return {
                    "acked": True,
                    "ack_reason": "user_text_changed_and_cleared",
                    "user_count": cur_count,
                    "last_user": cur_last,
                    "last_user_id": cur_id,
                    "prompt_empty": prompt_empty,
                    "stop_active": stop_active,
                    "send_ready": send_ready,
                }

            # DO NOT use user_count increase as an ack signal (too flaky on ChatGPT virtualized lists).

            time.sleep(min(0.25, self.cfg.poll_interval_s))

        # timeout: return last observed state for debugging
        try:
            cur_count = self._user_count(page)
        except Exception:
            cur_count = 0
        try:
            cur_last = _norm(self._peek_last_user_text(page))
        except Exception:
            cur_last = ""
        try:
            cur_id = (self._peek_last_user_id(page) or "").strip()
        except Exception:
            cur_id = ""
        try:
            prompt_txt = self._peek_prompt_text(page)
            prompt_empty = _norm(prompt_txt) == ""
        except Exception:
            prompt_empty = False
        try:
            stop_active = self._is_stop_active(page)
        except Exception:
            stop_active = False
        try:
            send_ready = self._is_send_ready(page)
        except Exception:
            send_ready = False

        return {
            "acked": False,
            "ack_reason": "soft_busy_no_user_message" if soft_busy_seen else "timeout",
            "user_count": cur_count,
            "last_user": cur_last,
            "last_user_id": cur_id,
            "prompt_empty": prompt_empty,
            "stop_active": stop_active,
            "send_ready": send_ready,
        }

    def _send_prompt_with_ack(
        self,
        page: Page,
        text: str,
        *,
        prev_user_count: int,
        prev_last_user_text: str,
        prev_last_user_id: str,
    ) -> Dict[str, Any]:
        """Send prompt with idempotency.

        - If we can observe user messages, we will NOT re-send once acked.
        - If we cannot observe user messages (no selectors), we do a single attempt.
        """

        has_user_selectors = bool(getattr(self.sel, "user_message_blocks", None))
        if not has_user_selectors:
            self._send_prompt(page, text)
            return {"attempts": 1, "acked": None}

        last_ack: Dict[str, Any] = {"acked": False}
        attempts = max(1, int(self.cfg.send_max_attempts))

        for i in range(1, attempts + 1):
            # If already acked (from previous loop), never send again.
            if last_ack.get("acked") is True:
                break

            try:
                self._send_prompt(page, text)
            except Exception:
                # still attempt to observe ack; click may have succeeded despite an exception
                pass

            last_ack = self._wait_send_ack(
                page,
                expected_user_text=text,
                prev_user_count=prev_user_count,
                prev_last_user_text=prev_last_user_text,
                prev_last_user_id=prev_last_user_id,
                timeout_s=self.cfg.send_ack_timeout_s,
            )
            if last_ack.get("acked") is True:
                return {"attempts": i, **last_ack}

        return {"attempts": attempts, **last_ack}

    def _regenerate_last_reply(self, page: Page) -> bool:
        # Prefer explicit regenerate button; fall back to generic retry.
        for key in ("regenerate_button", "retry_button"):
            sels = getattr(self.sel, key, None) or []
            btn = first_match_locator(page, sels, must_be_visible=False)
            if not btn:
                continue
            try:
                if btn.count() <= 0:
                    continue
            except Exception:
                continue
            try:
                btn.scroll_into_view_if_needed()
            except Exception:
                pass
            try:
                btn.click()
                return True
            except Exception:
                continue
        return False

    def _wait_idle_before_send(self, page: Page, *, timeout_s: float) -> None:
        """Wait until the UI is idle (no active generation) before sending."""
        end_by = time.time() + max(0.1, float(timeout_s))
        while time.time() < end_by:
            try:
                if not self._is_stop_active(page):
                    return
            except Exception:
                # If we cannot detect, proceed cautiously.
                return
            time.sleep(min(0.5, self.cfg.poll_interval_s))
        raise TimeoutError(f"[{self.platform}] UI still generating; refuse to send new prompt after {timeout_s}s")

    def _upload_files(self, page: Page, paths: List[str]) -> None:
        abs_paths = [os.path.abspath(p) for p in paths]
        for p in abs_paths:
            if not os.path.exists(p):
                raise FileNotFoundError(p)

        # Strategy 1: input injection
        file_input = first_match_locator(page, self.sel.file_input, must_be_visible=False)
        if file_input and file_input.count() > 0:
            def do_inject() -> None:
                file_input.set_input_files(abs_paths)
                self._wait_upload_confirmation(page, abs_paths)
            with_retry(do_inject, retries=2, backoff_ms=300)
            return

        # Strategy 2: file chooser
        attach = first_match_locator(page, self.sel.attach_button, must_be_visible=False)
        if not attach:
            raise SelectorNotFoundError(f"[{self.platform}] No file input and attach button not found. Update selectors.")
        def do_chooser() -> None:
            with page.expect_file_chooser(timeout=15_000) as fc:
                attach.click()
            chooser = fc.value
            chooser.set_files(abs_paths)
            self._wait_upload_confirmation(page, abs_paths)
        with_retry(do_chooser, retries=2, backoff_ms=500)

    def _wait_upload_confirmation(self, page: Page, paths: List[str]) -> None:
        timeout_s = float(self.cfg.upload_confirm_timeout_s or 0)
        if timeout_s <= 0:
            return

        names = [os.path.basename(p) for p in paths if p]
        expected = len(names)
        if expected <= 0:
            return

        end_by = time.time() + max(0.2, timeout_s)

        # 记录上传前的文件输入元素状态，用于检测变化
        def _get_file_input_state() -> dict:
            try:
                return page.evaluate(
                    """() => {
                        const inputs = Array.from(document.querySelectorAll("input[type='file']"));
                        return {
                            total_files: inputs.reduce((sum, input) => sum + (input.files ? input.files.length : 0), 0),
                            inputs_count: inputs.length,
                            file_names: inputs.flatMap(input => 
                                input.files ? Array.from(input.files).map(f => f.name) : []
                            )
                        };
                    }"""
                )
            except Exception:
                return {"total_files": 0, "inputs_count": 0, "file_names": []}

        initial_state = _get_file_input_state()

        def _files_added() -> bool:
            """检查是否有新文件被添加到输入框"""
            try:
                current = _get_file_input_state()
                # 检查1: 文件总数是否增加
                if current["total_files"] < expected:
                    return False
                # 检查2: 是否包含所有预期的文件名
                current_names = set(current["file_names"])
                for expected_name in names:
                    if expected_name not in current_names:
                        return False
                return True
            except Exception:
                return False

        def _names_visible_near_input() -> bool:
            """检查文件名是否在输入区域附近可见（避免命中输入框正文）"""
            for name in names:
                if not name:
                    continue
                try:
                    # 尝试在多个可能的位置查找文件名
                    # 1. 输入区域内
                    loc = page.locator(f"form:has(input[type='file']):has-text('{name}')").first
                    if loc.count() > 0:
                        continue
                    # 2. 文件附件预览区域
                    loc = page.locator(
                        f"[class*='attach']:has-text('{name}'), "
                        f"[class*='attachment']:has-text('{name}'), "
                        f"[class*='file']:has-text('{name}'), "
                        f"[class*='upload']:has-text('{name}'), "
                        f"[data-testid*='attach']:has-text('{name}'), "
                        f"[data-testid*='file']:has-text('{name}')"
                    ).first
                    if loc.count() > 0:
                        continue
                    return False
                except Exception:
                    return False
            return True if names else False

        def _upload_in_progress() -> bool:
            """检查上传是否仍在进行（基于可见进度/状态提示）"""
            try:
                scope = page.locator("form:has(input[type='file'])").first
                if scope.count() <= 0:
                    scope = page.locator("main").first
                if scope.count() <= 0:
                    scope = page.locator("body").first
            except Exception:
                scope = page.locator("body").first

            try:
                if scope.locator("[aria-busy='true']").count() > 0:
                    return True
            except Exception:
                pass
            try:
                if scope.locator("[role='progressbar']").count() > 0:
                    return True
            except Exception:
                pass
            try:
                if scope.locator("[data-testid*='upload']").count() > 0:
                    return True
            except Exception:
                pass
            try:
                if scope.locator("[class*='progress']").count() > 0:
                    return True
            except Exception:
                pass

            keywords = [
                "uploading",
                "processing",
                "上传中",
                "正在上传",
                "处理中",
                "上传",
            ]
            for kw in keywords:
                try:
                    loc = scope.get_by_text(kw, exact=False).first
                    if loc.count() > 0 and loc.is_visible():
                        return True
                except Exception:
                    continue
            return False

        while time.time() < end_by:
            # 严格检查：必须同时满足三个条件
            files_added = _files_added()
            names_visible = _names_visible_near_input()

            if files_added and names_visible and not _upload_in_progress():
                # 上传成功，打印确认信息
                current_state = _get_file_input_state()
                print(f"✓ 文件上传确认成功: {current_state['file_names']}")
                return
            
            time.sleep(min(0.5, self.cfg.poll_interval_s))

        # 提供详细的失败信息
        final_state = _get_file_input_state()
        files_added = _files_added()
        names_visible = _names_visible_near_input()
        
        raise RuntimeError(
            f"[{self.platform}] 文件上传确认失败 (超时 {timeout_s}s)\n"
            f"  预期文件: {names}\n"
            f"  初始状态: {initial_state}\n"
            f"  当前状态: {final_state}\n"
            f"  文件已添加: {files_added}\n"
            f"  文件名可见: {names_visible}"
        )

    def _is_stop_visible(self, page: Page) -> bool:
        stop = first_match_locator(page, self.sel.stop_button, must_be_visible=False)
        if not stop:
            return False
        try:
            return stop.is_visible()
        except Exception:
            return False

    def _is_stop_active(self, page: Page) -> bool:
        """Return True only when the UI indicates generation is actively running.

        Some UIs keep a stop button in DOM even after completion (disabled / aria-disabled).
        """
        stop = first_match_locator(page, self.sel.stop_button, must_be_visible=False)
        if not stop:
            return False

        try:
            if not stop.is_visible():
                return False
        except Exception:
            return False

        try:
            if stop.get_attribute("disabled") is not None:
                return False
        except Exception:
            pass

        try:
            if (stop.get_attribute("aria-disabled") or "").strip().lower() == "true":
                return False
        except Exception:
            pass

        try:
            if not stop.is_enabled():
                return False
        except Exception:
            # some locators don't support is_enabled reliably; ignore
            pass

        return True

    def _is_send_ready(self, page: Page) -> bool:
        send = first_match_locator(page, self.sel.send_button, must_be_visible=False)
        if not send:
            return False
        try:
            if send.count() <= 0:
                return False
        except Exception:
            return False

        try:
            if not send.is_visible():
                return False
        except Exception:
            return False

        try:
            if send.get_attribute("disabled") is not None:
                return False
        except Exception:
            pass

        try:
            if (send.get_attribute("aria-disabled") or "").strip().lower() == "true":
                return False
        except Exception:
            pass

        try:
            if not send.is_enabled():
                return False
        except Exception:
            pass

        return True

    def _assistant_count(self, page: Page) -> int:
        loc = self._assistant_blocks_locator(page)
        try:
            return loc.count()
        except Exception:
            return 0

    def _peek_last_assistant_text(self, page: Page) -> str:
        loc = self._assistant_blocks_locator(page)
        try:
            n = loc.count()
        except Exception:
            n = 0
        if n <= 0:
            return ""
        last = loc.nth(n - 1)
        # Preserve format when peeking
        return self._extract_text_from_block(last, preserve_format=True)

    def _wait_generation(
        self,
        page: Page,
        *,
        prev_assistant_count: int,
        prev_last_text: Optional[str] = None,
        allow_no_new: bool = False,
    ) -> None:
        """Wait until model generation completes.

        Three-phase strategy — event-driven where possible, short poll only at the end:

        Phase 1 – Wait for START:
          page.wait_for_function() blocks Python with zero polling until the stop
          button becomes active (= generation has started). Fallback to manual check
          for text/count signals not visible in the stop-button JS.

        Phase 2 – Wait for generation BODY:
          page.wait_for_function() blocks Python until the stop button disappears.
          No Python-side polling at all while the model generates (seconds to minutes).

        Phase 3 – STABILITY window + edge-cases:
          Short polling loop covering only `stable_text_window_s` (default 2 s) plus
          the force-end guard. Handles sticky stop buttons, thinking placeholders, and
          the no-output timeout.
        """
        import json as _json

        start_time = time.time()
        timeout_s = self.cfg.generation_timeout_s
        poll = self.cfg.poll_interval_s
        prev_last_text = prev_last_text if prev_last_text is not None else ""

        # Build safe JS selector arrays from platform selectors
        _stop_js = _json.dumps(self.sel.stop_button)

        # JS: true when stop button is actively blocking (generation running)
        _js_stop_active = f"""() => {{
            for (const s of {_stop_js}) {{
                try {{
                    const el = document.querySelector(s);
                    if (el && el.offsetParent !== null
                            && !el.disabled
                            && el.getAttribute('aria-disabled') !== 'true') return true;
                }} catch(e) {{}}
            }}
            return false;
        }}"""

        # JS: true when generation has finished (stop gone / inactive)
        _js_gen_ended = f"""() => {{
            for (const s of {_stop_js}) {{
                try {{
                    const el = document.querySelector(s);
                    if (el && el.offsetParent !== null
                            && !el.disabled
                            && el.getAttribute('aria-disabled') !== 'true') return false;
                }} catch(e) {{}}
            }}
            return true;
        }}"""

        # ── Phase 1: wait for START ───────────────────────────────────────────
        stop_active_before = self._is_stop_active(page)
        start_wait_cap_s = min(60, max(20, timeout_s // 3))
        started = False

        try:
            page.wait_for_function(_js_stop_active, timeout=int(start_wait_cap_s * 1000))
            started = True
        except Exception:
            pass

        if not started:
            # Manual fallback: text/count signals not covered by stop-button JS
            elapsed = time.time() - start_time
            cur_count = self._assistant_count(page)
            cur_text = ""
            try:
                cur_text = self._peek_last_assistant_text(page)
            except Exception:
                pass
            if cur_count > prev_assistant_count:
                started = True
            elif cur_text.strip() and cur_text != prev_last_text:
                started = True
            elif self._is_stop_active(page):
                started = True
            elif not allow_no_new and elapsed >= start_wait_cap_s and cur_count >= prev_assistant_count:
                started = True  # cap: treat as started

        if not started and not allow_no_new:
            raise TimeoutError(
                f"[{self.platform}] generation did not start within {timeout_s}s "
                f"(prev_count={prev_assistant_count}, cur_count={self._assistant_count(page)}, "
                f"url={getattr(page, 'url', '')})."
            )

        gen_started_at = time.time()
        saw_new = self._assistant_count(page) > prev_assistant_count
        saw_stop_active = self._is_stop_active(page)

        # ── Phase 2: wait for generation BODY ────────────────────────────────
        # Python blocks here with zero polling while the model generates.
        # We wake exactly once when the browser signals the stop button is gone.
        remaining_s = max(2.0, timeout_s - (time.time() - start_time))
        try:
            page.wait_for_function(_js_gen_ended, timeout=int(remaining_s * 1000))
        except Exception:
            # Timed out (hard limit reached) or sticky stop — fall through to Phase 3
            pass

        # ── Phase 3: stability window + edge-case handling ────────────────────
        # Covers only `stable_text_window_s` (2 s default) + force-end guard.
        # Much cheaper than running the entire wait as a poll loop.
        started_at = gen_started_at
        last_text = ""
        stable_since = time.time()
        force_stable_since: Optional[float] = None

        while time.time() - start_time < timeout_s:
            cur_count = self._assistant_count(page)
            if cur_count > prev_assistant_count:
                saw_new = True

            cur_text = self._peek_last_assistant_text(page) if (saw_new or allow_no_new or prev_last_text is not None) else ""
            if prev_last_text is not None and cur_text and cur_text != (prev_last_text or ""):
                saw_new = True

            if cur_text != last_text:
                last_text = cur_text
                stable_since = time.time()
                force_stable_since = None

            stop_active = self._is_stop_active(page)
            if stop_active:
                saw_stop_active = True
            send_ready = self._is_send_ready(page)
            stable_enough = (time.time() - stable_since) >= self.cfg.stable_text_window_s

            # Fail fast: started but no visible output for too long
            if (
                self.cfg.no_output_timeout_s
                and self.cfg.no_output_timeout_s > 0
                and (time.time() - started_at) >= self.cfg.no_output_timeout_s
                and (not saw_new)
                and (not (last_text or "").strip())
                and stop_active
            ):
                raise TimeoutError(f"[{self.platform}] generation stuck: no visible output after {self.cfg.no_output_timeout_s}s")

            # Thinking placeholder: re-enter event-driven wait rather than polling fast
            if self._is_thinking_placeholder_only(last_text or ""):
                stable_since = time.time()
                force_stable_since = None
                if stop_active:
                    _rem = max(2.0, timeout_s - (time.time() - start_time))
                    try:
                        page.wait_for_function(_js_gen_ended, timeout=int(min(_rem, 60.0) * 1000))
                    except Exception:
                        pass
                else:
                    time.sleep(poll)
                continue

            if stable_enough and (not stop_active) and (saw_new or allow_no_new):
                return

            if (
                stable_enough
                and send_ready
                and (saw_new or allow_no_new)
                and (saw_stop_active or len((last_text or "").strip()) >= self.cfg.force_end_min_text_chars)
            ):
                return

            if (
                stable_enough
                and stop_active
                and (saw_new or allow_no_new)
                and len((last_text or "").strip()) >= self.cfg.force_end_min_text_chars
            ):
                if force_stable_since is None:
                    force_stable_since = time.time()
                if (time.time() - force_stable_since) >= self.cfg.force_end_if_stop_visible_s:
                    return

            time.sleep(poll)

        raise TimeoutError(
            f"[{self.platform}] generation did not finish within {timeout_s}s "
            f"(prev_count={prev_assistant_count}, cur_count={self._assistant_count(page)}, "
            f"stop_active={self._is_stop_active(page)}, send_ready={self._is_send_ready(page)}, "
            f"last_text_len={len((last_text or '').strip())}, url={getattr(page, 'url', '')})."
        )

    # ---------- Extraction ----------
    def _extract_latest_reply(self, page: Page, state: SessionState, *, since_last: bool) -> str:
        """Extract assistant reply.

        Key requirements:
        - since_last=True must *not* include history; only return new content after the last snapshot.
        - if the UI streams into an existing node and count does not increase, detect by text diff.
        """
        blocks = self._assistant_blocks_locator(page)
        try:
            total = blocks.count()
        except Exception:
            total = 0

        if total <= 0:
            return ""

        # "extract-latest" mode: always return the last assistant message
        if not since_last:
            last = blocks.nth(total - 1)
            # Try to preserve format for better readability
            txt = self._extract_text_from_block(last, preserve_format=True).strip()
            state.last_assistant_count = total
            state.last_assistant_text_snapshot = txt
            return txt

        # since_last=True: incremental extraction
        if total < state.last_assistant_count:
            # Conversation likely reset / DOM rebuilt (manual reload, new chat, etc.)
            # Best-effort: treat current last message as the latest snapshot.
            # Use preserve_format to get better formatted content
            if total > 0:
                last = blocks.nth(total - 1)
                last_txt = self._extract_text_from_block(last, preserve_format=True).strip()
            else:
                last_txt = self._peek_last_assistant_text(page).strip()
            state.last_assistant_count = total
            state.last_assistant_text_snapshot = last_txt
            return last_txt

        if total == state.last_assistant_count:
            # No new blocks. Possibly streaming updated existing last block.
            # Use preserve_format to get better formatted content
            last = blocks.nth(total - 1)
            cur_last = self._extract_text_from_block(last, preserve_format=True).strip()
            prev = (state.last_assistant_text_snapshot or "").strip()
            if cur_last and cur_last != prev:
                state.last_assistant_text_snapshot = cur_last
                return cur_last
            return ""

        start_idx = max(0, min(state.last_assistant_count, total))
        parts: List[str] = []
        for i in range(start_idx, total):
            block = blocks.nth(i)
            # Preserve format for new blocks
            t = self._extract_text_from_block(block, preserve_format=True).strip()
            if t:
                parts.append(t)

        state.last_assistant_count = total
        if parts:
            state.last_assistant_text_snapshot = parts[-1]
        else:
            state.last_assistant_text_snapshot = self._peek_last_assistant_text(page).strip()

        return "\n\n".join(parts).strip()

    def _extract_text_from_block(self, block: Any, preserve_format: bool = False) -> str:
        """Extract text from a message block.
        
        Args:
            block: The message block locator
            preserve_format: If True, try to preserve Markdown formatting by extracting HTML first
        """
        # Try to extract formatted content if requested
        if preserve_format:
            for sel in self.sel.assistant_text_blocks:
                try:
                    if sel == ":scope":
                        html = safe_inner_html(block)
                        if html:
                            markdown = html_to_markdown(html)
                            if markdown and len(markdown.strip()) > 10:  # Only use if meaningful
                                return markdown
                    else:
                        inner = block.locator(sel).first
                        if inner.count() > 0:
                            html = safe_inner_html(inner)
                            if html:
                                markdown = html_to_markdown(html)
                                if markdown and len(markdown.strip()) > 10:
                                    return markdown
                except Exception:
                    continue
        
        # Fallback to plain text extraction
        for sel in self.sel.assistant_text_blocks:
            try:
                if sel == ":scope":
                    return safe_inner_text(block)
                inner = block.locator(sel).first
                if inner.count() > 0:
                    txt = safe_inner_text(inner)
                    if txt:
                        return txt
            except Exception:
                continue
        return safe_inner_text(block)

    # ---------- Artifact download ----------
    def _extract_filename_from_dom(self, el: Any) -> Optional[str]:
        """从元素所在容器（如文件卡片）的 DOM 文本中提取真实文件名（如 xxx.md、report.zip），
        不依赖下载流的 suggested_filename（可能是 UUID）。"""
        try:
            scope_text = el.evaluate(
                """
                (node) => {
                    let cur = node;
                    for (let i = 0; i < 10 && cur; i++) {
                        const text = (cur.innerText || '').trim();
                        if (text.length > 2 && text.length < 2000) return text;
                        cur = cur.parentElement;
                    }
                    return (node.innerText || '').trim();
                }
                """
            )
            if not scope_text:
                return None
            # 匹配常见扩展名的文件名（名称中可含 . - _）
            m = re.search(r"([a-zA-Z0-9_\-\.]+\.(?:md|zip|csv|json|txt|yaml|yml|pdf))", scope_text)
            if m:
                return m.group(1).strip()
            return None
        except Exception:
            return None

    def _artifact_key(self, el: Any) -> str:
        dom_path = ""
        rect_sig = ""
        try:
            dom_path = (
                el.evaluate(
                    """
                    (node) => {
                        const parts = [];
                        let cur = node;
                        for (let i = 0; i < 8 && cur && cur.nodeType === 1; i++) {
                            let sameTagIdx = 1;
                            let p = cur;
                            while ((p = p.previousElementSibling)) {
                                if (p.tagName === cur.tagName) sameTagIdx++;
                            }
                            const tag = (cur.tagName || '').toLowerCase();
                            const id = cur.id ? `#${cur.id}` : '';
                            const dt = (cur.getAttribute('data-testid') || '').slice(0, 40);
                            const role = (cur.getAttribute('role') || '').slice(0, 20);
                            parts.unshift(`${tag}${id}:nth(${sameTagIdx})${dt ? `[dt=${dt}]` : ''}${role ? `[r=${role}]` : ''}`);
                            cur = cur.parentElement;
                        }
                        return parts.join('>');
                    }
                    """
                )
                or ""
            )
        except Exception:
            dom_path = ""
        try:
            rect_sig = (
                el.evaluate(
                    """
                    (node) => {
                        const r = node.getBoundingClientRect();
                        return `${Math.round(r.x)}:${Math.round(r.y)}:${Math.round(r.width)}:${Math.round(r.height)}`;
                    }
                    """
                )
                or ""
            )
        except Exception:
            rect_sig = ""

        href = ""
        try:
            href = el.get_attribute("href") or ""
        except Exception:
            pass
        if href:
            return f"href:{href}|path:{dom_path}|rect:{rect_sig}"
        try:
            aria = el.get_attribute("aria-label") or ""
        except Exception:
            aria = ""
        if aria:
            return f"aria:{aria}|path:{dom_path}|rect:{rect_sig}"
        try:
            testid = el.get_attribute("data-testid") or ""
        except Exception:
            testid = ""
        if testid:
            return f"testid:{testid}|path:{dom_path}|rect:{rect_sig}"
        return f"text:{safe_inner_text(el)[:80]}|path:{dom_path}|rect:{rect_sig}"

    @staticmethod
    def _is_uuid_like(s: str) -> bool:
        """Check if a string looks like a UUID, with or without extension/suffix."""
        v = (s or "").strip().lower()
        if not v:
            return False
        base = os.path.basename(v)
        stem, _ext = os.path.splitext(base)
        # pure uuid
        if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", v):
            return True
        if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", stem):
            return True
        # uuid.ext
        if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.[a-z0-9]{1,10}", v):
            return True
        # 32-hex token (common object key style)
        if re.fullmatch(r"[0-9a-f]{32}", stem):
            return True
        # ULID-like token
        if re.fullmatch(r"[0-9a-hjkmnp-tv-z]{26}", stem):
            return True
        # file-<id> / attachment-<id> style random IDs
        if re.fullmatch(r"(file|files|attachment|artifact)[\-_]?[a-z0-9\-]{20,}", stem):
            return True
        # filename contains uuid token only (e.g. "file-<uuid>.bin")
        return bool(re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", v))

    def _save_download_once(self, dl: Download, save_path: str, *, wait_for_nonzero_s: float = 5.0) -> bool:
        """Save a Playwright Download to save_path. Waits for completion, checks failure and 0-byte.
        Returns True if file was saved with non-zero size, False otherwise (and removes 0-byte file)."""
        try:
            err = dl.failure()
            if err:
                return False
            # Wait for download to complete (path() blocks until ready)
            dl.path()
            dl.save_as(save_path)
            # Some UIs (e.g. blob:) write asynchronously; give a short window for non-zero size
            deadline = time.time() + wait_for_nonzero_s
            while time.time() < deadline:
                if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
                    return True
                time.sleep(0.5)
            if os.path.exists(save_path) and os.path.getsize(save_path) == 0:
                try:
                    os.remove(save_path)
                except Exception:
                    pass
            return False
        except Exception:
            return False

    @staticmethod
    def _ensure_unique_save_path(save_path: str) -> str:
        if not os.path.exists(save_path):
            return save_path
        base_dir = os.path.dirname(save_path)
        name = os.path.basename(save_path)
        stem, ext = os.path.splitext(name)
        for i in range(1, 1000):
            cand = os.path.join(base_dir, f"{stem} ({i}){ext}")
            if not os.path.exists(cand):
                return cand
        return os.path.join(base_dir, f"{stem}_{now_ts()}{ext}")

    @staticmethod
    def _snapshot_dir(directory: str) -> set:
        """Return set of filenames currently in directory (ignoring hidden/in-progress files)."""
        try:
            return {
                f for f in os.listdir(directory)
                if not f.startswith(".") and not f.endswith(".crdownload") and not f.endswith(".tmp")
            }
        except Exception:
            return set()

    @staticmethod
    def _get_system_downloads_dir() -> str:
        """Return the OS default downloads directory (where Chrome saves files natively)."""
        import subprocess
        try:
            r = subprocess.run(["xdg-user-dir", "DOWNLOAD"], capture_output=True, text=True, timeout=3)
            p = r.stdout.strip()
            if p and os.path.isdir(p):
                return p
        except Exception:
            pass
        return os.path.expanduser("~/Downloads")

    @staticmethod
    def _wait_for_new_files_in_dir(
        directory: str,
        before: set,
        *,
        timeout_s: float = 35.0,
        stable_s: float = 2.0,
    ) -> List[str]:
        """Wait until new complete (non-.crdownload) files appear in directory that are not
        in the 'before' snapshot. Returns list of absolute paths of new stable files."""
        deadline = time.time() + timeout_s
        last_new: set = set()
        stable_since: float = 0.0

        while time.time() < deadline:
            try:
                current = {
                    f for f in os.listdir(directory)
                    if not f.startswith(".") and not f.endswith(".crdownload") and not f.endswith(".tmp")
                }
            except Exception:
                time.sleep(0.5)
                continue

            new = current - before
            try:
                in_progress = any(
                    f.endswith(".crdownload") or f.endswith(".tmp") for f in os.listdir(directory)
                )
            except Exception:
                in_progress = False

            if new:
                if new == last_new and not in_progress:
                    if stable_since == 0.0:
                        stable_since = time.time()
                    elif time.time() - stable_since >= stable_s:
                        return [os.path.join(directory, f) for f in new]
                else:
                    last_new = new
                    stable_since = 0.0
            time.sleep(0.5)

        # Timeout: return whatever new files exist
        try:
            current = {
                f for f in os.listdir(directory)
                if not f.startswith(".") and not f.endswith(".crdownload") and not f.endswith(".tmp")
            }
            return [os.path.join(directory, f) for f in (current - before)]
        except Exception:
            return []

    def _extract_candidate_file_urls(self, page: Page, el: Any) -> List[str]:
        """Extract potential backend file URLs from element/ancestors/descendants.
        This avoids relying on UI menu entries that may not exist."""
        origin = ""
        try:
            m = re.match(r"^(https?://[^/]+)", page.url or "")
            if m:
                origin = m.group(1)
        except Exception:
            origin = ""

        raw: List[str] = []
        try:
            vals = el.evaluate(
                """
                (node) => {
                    const attrs = ['href', 'src', 'data-url', 'data-href', 'data-download-url', 'onclick'];
                    const bag = new Set();
                    const addAttr = (n) => {
                        if (!n || !n.getAttribute) return;
                        for (const a of attrs) {
                            const v = n.getAttribute(a);
                            if (v && typeof v === 'string') bag.add(v);
                        }
                    };
                    let cur = node;
                    for (let i = 0; i < 8 && cur; i++) {
                        addAttr(cur);
                        cur = cur.parentElement;
                    }
                    const root = node.closest('main') || document.body;
                    if (root) {
                        const links = root.querySelectorAll("a[href*='/backend-api/files/'],a[href*='/files/']");
                        for (const a of links) {
                            const h = a.getAttribute('href');
                            if (h) bag.add(h);
                        }
                    }
                    return Array.from(bag);
                }
                """
            ) or []
            if isinstance(vals, list):
                raw.extend([str(v) for v in vals if v])
        except Exception:
            pass

        # Pull out url/path/id patterns from raw strings
        ids: List[str] = []
        out: List[str] = []
        for s in raw:
            s1 = (s or "").strip()
            if not s1:
                continue

            # Full URLs
            for m in re.findall(r"https?://[^\"'\\s]+", s1):
                out.append(m)

            # Relative backend paths
            for m in re.findall(r"(?:/)?backend-api/files/[0-9a-f\\-]+(?:/download)?", s1, flags=re.I):
                out.append(m)

            # File IDs
            for fid in re.findall(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", s1, flags=re.I):
                ids.append(fid)

        for fid in ids:
            out.append(f"/backend-api/files/{fid}/download")
            out.append(f"/backend-api/files/{fid}")

        norm: List[str] = []
        for u in out:
            uu = (u or "").strip()
            if not uu:
                continue
            if uu.startswith("http://") or uu.startswith("https://"):
                norm.append(uu)
            elif uu.startswith("/"):
                if origin:
                    norm.append(f"{origin}{uu}")
            else:
                if origin:
                    norm.append(f"{origin}/{uu.lstrip('/')}")

        # dedupe while keeping order
        seen = set()
        deduped: List[str] = []
        for u in norm:
            if u in seen:
                continue
            seen.add(u)
            deduped.append(u)
        return deduped

    def _extract_direct_file_links_from_last_assistant(self, page: Page) -> List[Dict[str, str]]:
        """Extract downloadable links from the latest assistant message.
        This is robust when UI has no explicit 'download' menu/button."""
        blocks = self._assistant_blocks_locator(page)
        try:
            n = blocks.count()
        except Exception:
            n = 0
        if n <= 0:
            return []

        block = blocks.nth(n - 1)
        origin = ""
        try:
            m = re.match(r"^(https?://[^/]+)", page.url or "")
            if m:
                origin = m.group(1)
        except Exception:
            origin = ""

        items: List[Dict[str, str]] = []
        try:
            raw_items = block.evaluate(
                """
                (node) => {
                    const out = [];
                    const push = (href, label) => {
                        if (!href) return;
                        out.push({ href: String(href), label: String(label || '') });
                    };

                    const links = node.querySelectorAll("a[href]");
                    for (const a of links) {
                        push(a.getAttribute("href"), (a.innerText || a.textContent || "").trim());
                    }

                    const txt = (node.innerText || node.textContent || "");
                    const re = /(https?:\\/\\/[^\\s\\]\\)\"'>]+|\\/?backend-api\\/files\\/[0-9a-f\\-]+(?:\\/download)?)/ig;
                    let m;
                    while ((m = re.exec(txt)) !== null) {
                        push(m[1], "");
                    }
                    return out;
                }
                """
            ) or []
            if isinstance(raw_items, list):
                items.extend([{"href": str((x or {}).get("href") or ""), "label": str((x or {}).get("label") or "")} for x in raw_items])
        except Exception:
            return []

        norm: List[Dict[str, str]] = []
        seen = set()
        for it in items:
            href = (it.get("href") or "").strip()
            label = (it.get("label") or "").strip()
            if not href:
                continue
            if href.startswith("blob:"):
                # blob: cannot be fetched via context.request
                continue
            if href.startswith("http://") or href.startswith("https://"):
                u = href
            elif href.startswith("/"):
                if not origin:
                    continue
                u = f"{origin}{href}"
            else:
                if not origin:
                    continue
                u = f"{origin}/{href.lstrip('/')}"
            if u in seen:
                continue
            seen.add(u)
            norm.append({"url": u, "label": label})
        return norm

    @staticmethod
    def _extract_filename_like(text: str) -> str:
        t = (text or "").strip()
        if not t:
            return ""
        m = re.search(
            r"([a-zA-Z0-9\u4e00-\u9fff_\-\.()（）\[\]【】\s]{1,220}\.(?:md|json|zip|txt|py|yaml|yml|csv|pdf|xlsx|xls|docx|doc|pptx|ppt|tsv|xml|html|7z|tar|gz|rar|sql|log|parquet))",
            t,
            flags=re.I,
        )
        if not m:
            return ""
        return sanitize_filename(m.group(1).strip(" \t\r\n`'\"“”‘’，,。:：;；"))

    def _extract_expected_filenames_from_last_assistant(self, page: Page) -> List[str]:
        blocks = self._assistant_blocks_locator(page)
        try:
            n = blocks.count()
        except Exception:
            n = 0
        if n <= 0:
            return []
        txt = ""
        try:
            txt = safe_inner_text(blocks.nth(n - 1)) or ""
        except Exception:
            txt = ""
        if not txt:
            return []
        pats = re.findall(
            r"([a-zA-Z0-9\u4e00-\u9fff_\-\.()（）\[\]【】\s]{1,220}\.(?:md|json|zip|txt|py|yaml|yml|csv|pdf|xlsx|xls|docx|doc|pptx|ppt|tsv|xml|html|7z|tar|gz|rar|sql|log|parquet))",
            txt,
            flags=re.I,
        )
        out: List[str] = []
        seen = set()
        for p in pats:
            s = sanitize_filename(p.strip(" \t\r\n`'\"“”‘’，,。:：;；"))
            if not s or self._is_uuid_like(s) or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out

    @staticmethod
    def _extract_filename_from_url(url: str) -> str:
        try:
            from urllib.parse import parse_qs, unquote, urlparse

            p = urlparse(url or "")
            q = parse_qs(p.query or "", keep_blank_values=False)
            for k in ("filename", "file_name", "name", "download_name"):
                vals = q.get(k) or []
                for v in vals:
                    vv = sanitize_filename(unquote(v or "").strip())
                    if vv:
                        return vv
            # Some services encode filename in response-content-disposition
            vals = q.get("response-content-disposition") or q.get("content-disposition") or []
            for v in vals:
                cd = unquote(v or "")
                m = re.search(r"filename\\*?\\s*=\\s*(?:UTF-8''|\"?)([^\";]+)", cd, flags=re.I)
                if m:
                    vv = sanitize_filename(m.group(1).strip())
                    if vv:
                        return vv
        except Exception:
            pass
        return ""

    @staticmethod
    def _extract_file_id(text: str) -> str:
        m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", (text or ""), flags=re.I)
        return (m.group(1) if m else "").strip()

    def _resolve_filename_by_file_id(self, page: Page, file_id: str) -> str:
        fid = (file_id or "").strip()
        if not fid:
            return ""
        origin_m = re.match(r"^(https?://[^/]+)", page.url or "")
        origin = origin_m.group(1) if origin_m else ""
        if not origin:
            return ""

        candidates = [
            f"{origin}/backend-api/files/{fid}",
            f"{origin}/backend-api/files/{fid}/download",
        ]
        for u in candidates:
            try:
                resp = page.context.request.get(u, timeout=15_000)
            except Exception:
                continue
            if not resp.ok:
                continue
            # Prefer content-disposition if present
            hname = self._filename_from_content_disposition(resp.headers.get("content-disposition") or "")
            if hname and (not self._is_uuid_like(hname)):
                return hname
            ctype = (resp.headers.get("content-type") or "").lower()
            if "application/json" not in ctype:
                continue
            try:
                js = resp.json() or {}
            except Exception:
                js = {}
            flat: List[str] = []

            def _walk(o: Any) -> None:
                if o is None:
                    return
                if isinstance(o, dict):
                    for vv in o.values():
                        _walk(vv)
                elif isinstance(o, list):
                    for vv in o:
                        _walk(vv)
                elif isinstance(o, (str, int, float)):
                    flat.append(str(o))

            _walk(js)
            for s in flat:
                n = self._extract_filename_like(s)
                if n and (not self._is_uuid_like(n)):
                    return n
        return ""

    def _download_via_direct_links(
        self,
        page: Page,
        state: SessionState,
        *,
        out_dir: str,
        trace_events: Optional[List[Dict[str, Any]]] = None,
    ) -> List[str]:
        """Download files by parsing direct links from latest assistant reply."""
        downloaded: List[str] = []
        links = self._extract_direct_file_links_from_last_assistant(page)
        if trace_events is not None:
            trace_events.append(
                {
                    "stage": "direct_links_extracted",
                    "count": len(links),
                    "links_preview": [str((x or {}).get("url") or "") for x in links[:10]],
                }
            )
        for item in links:
            u = (item.get("url") or "").strip()
            label = (item.get("label") or "").strip()
            if not u:
                continue
            key = f"url:{u}"
            if key in state.known_artifact_keys:
                if trace_events is not None:
                    trace_events.append({"stage": "direct_link_skip_known", "url": u})
                continue
            try:
                resp = page.context.request.get(u, timeout=20_000)
            except Exception:
                if trace_events is not None:
                    trace_events.append({"stage": "direct_link_request_error", "url": u})
                continue
            if not resp.ok:
                if trace_events is not None:
                    trace_events.append(
                        {"stage": "direct_link_not_ok", "url": u, "status": getattr(resp, "status", None)}
                    )
                continue

            ctype = (resp.headers.get("content-type") or "").lower()
            final_resp = resp
            json_name = ""

            # Some endpoints return metadata JSON that includes real filename/download URL.
            if "application/json" in ctype:
                try:
                    js = resp.json() or {}
                except Exception:
                    js = {}
                if isinstance(js, dict):
                    # Best-effort recursive extraction from metadata json
                    def _walk(obj: Any, out: List[str]) -> None:
                        if obj is None:
                            return
                        if isinstance(obj, dict):
                            for vv in obj.values():
                                _walk(vv, out)
                        elif isinstance(obj, list):
                            for vv in obj:
                                _walk(vv, out)
                        elif isinstance(obj, (str, int, float)):
                            out.append(str(obj))

                    flat_vals: List[str] = []
                    _walk(js, flat_vals)

                    for cand in flat_vals:
                        n1 = self._extract_filename_like(cand)
                        if n1 and (not self._is_uuid_like(n1)):
                            json_name = n1
                            break

                    direct_urls: List[str] = []
                    for k in ("download_url", "url", "signed_url", "file_url"):
                        du = str(js.get(k) or "").strip()
                        if du:
                            direct_urls.append(du)
                    for s in flat_vals:
                        s1 = (s or "").strip()
                        if re.match(r"^https?://", s1):
                            direct_urls.append(s1)
                        if "/backend-api/files/" in s1:
                            direct_urls.append(s1)

                    # If metadata contains file id, try canonical download endpoints
                    fid = str(js.get("id") or js.get("file_id") or "").strip()
                    if fid and re.fullmatch(r"[0-9a-f\\-]{36}", fid, flags=re.I):
                        origin_m = re.match(r"^(https?://[^/]+)", page.url or "")
                        origin = origin_m.group(1) if origin_m else ""
                        if origin:
                            direct_urls.append(f"{origin}/backend-api/files/{fid}/download")
                            direct_urls.append(f"{origin}/backend-api/files/{fid}")

                    # dedupe direct urls
                    seen_du = set()
                    for du in direct_urls:
                        duu = (du or "").strip()
                        if not duu or duu in seen_du:
                            continue
                        seen_du.add(duu)
                        try:
                            rr = page.context.request.get(duu, timeout=20_000)
                            if rr.ok and "application/json" not in (rr.headers.get("content-type") or "").lower():
                                final_resp = rr
                                break
                        except Exception:
                            continue

            body = final_resp.body() or b""
            if not body:
                continue

            header_name = self._filename_from_content_disposition(
                final_resp.headers.get("content-disposition") or ""
            )
            label_name = self._extract_filename_like(label)
            query_name = self._extract_filename_from_url(u) or self._extract_filename_from_url(final_resp.url or "")
            url_name = sanitize_filename(os.path.basename(u.split("?")[0]) or "")
            if self._is_uuid_like(url_name):
                url_name = ""
            file_id = self._extract_file_id(u) or self._extract_file_id(final_resp.url or "")
            resolved_name = self._resolve_filename_by_file_id(page, file_id) if file_id else ""
            # Never trust UUID-like "names" as final filename.
            if self._is_uuid_like(header_name):
                header_name = ""
            if self._is_uuid_like(json_name):
                json_name = ""
            if self._is_uuid_like(resolved_name):
                resolved_name = ""
            if self._is_uuid_like(query_name):
                query_name = ""
            if self._is_uuid_like(label_name):
                label_name = ""

            filename = header_name or json_name or resolved_name or query_name or label_name or url_name or f"{self.platform}_artifact.bin"
            if self._is_uuid_like(filename):
                low_label = (label or "").lower()
                low_url = u.lower()
                if "markdown" in low_label or "markdown" in low_url or "text/markdown" in ctype:
                    filename = "context_pack.md"
                elif "zip" in low_label or low_url.endswith(".zip") or "application/zip" in ctype:
                    filename = "context_pack.zip"
                elif "csv" in low_label or "text/csv" in ctype:
                    filename = "artifact.csv"
                elif "json" in low_label or "application/json" in ctype:
                    filename = "artifact.json"
                else:
                    ext = ".bin"
                    if "pdf" in ctype:
                        ext = ".pdf"
                    elif "text/plain" in ctype:
                        ext = ".txt"
                    filename = f"{self.platform}_artifact{ext}"
            filename = sanitize_filename(filename)
            save_path = os.path.join(out_dir, filename)
            save_path = self._ensure_unique_save_path(save_path)
            try:
                with open(save_path, "wb") as f:
                    f.write(body)
                downloaded.append(save_path)
                if trace_events is not None:
                    trace_events.append(
                        {
                            "stage": "direct_link_saved",
                            "url": u,
                            "saved_path": save_path,
                            "filename": os.path.basename(save_path),
                            "content_type": ctype,
                            "status": getattr(final_resp, "status", None),
                        }
                    )
            except Exception:
                state.known_artifact_keys.add(key)
                if trace_events is not None:
                    trace_events.append({"stage": "direct_link_save_error", "url": u, "target_name": filename})
                continue
            state.known_artifact_keys.add(key)
        return downloaded

    @staticmethod
    def _filename_from_content_disposition(cd: str) -> str:
        v = (cd or "").strip()
        if not v:
            return ""
        # RFC5987: filename*=UTF-8''...
        m = re.search(r"filename\\*\\s*=\\s*([^;]+)", v, flags=re.I)
        if m:
            raw = m.group(1).strip().strip("\"'")
            raw = re.sub(r"^utf-8''", "", raw, flags=re.I)
            try:
                from urllib.parse import unquote

                raw = unquote(raw)
            except Exception:
                pass
            return sanitize_filename(raw)
        # filename="..."
        m = re.search(r"filename\\s*=\\s*\"([^\"]+)\"", v, flags=re.I)
        if m:
            return sanitize_filename(m.group(1).strip())
        m = re.search(r"filename\\s*=\\s*([^;]+)", v, flags=re.I)
        if m:
            return sanitize_filename(m.group(1).strip().strip("\"'"))
        return ""

    def _artifact_search_scopes(self, page: Page) -> List[Any]:
        scopes: List[Any] = []

        # Scope 1: last assistant message (fast path)
        blocks = self._assistant_blocks_locator(page)
        try:
            n = blocks.count()
        except Exception:
            n = 0
        if n > 0:
            scopes.append(blocks.nth(n - 1))

        # Scope 2: chat root container (covers artifacts rendered outside the last message)
        root = self._chat_root_scope(page)
        if root is not page:
            scopes.append(root)

        # Scope 3: full page (last resort)
        scopes.append(page)
        return scopes

    def _find_artifact_elements(self, page: Page) -> List[Any]:
        found: List[Any] = []
        scopes = self._artifact_search_scopes(page)

        for scope in scopes:
            for sel in self.sel.artifact_candidates:
                try:
                    loc = scope.locator(sel)
                    c = loc.count()
                    for i in range(min(c, 30)):
                        found.append(loc.nth(i))
                except Exception:
                    continue

        # 兜底：按可见文本匹配（ChatGPT 等可能用 div/span 包裹下载入口）
        if not found:
            for scope in scopes:
                for text_pat in ["下载 CP (Markdown)", "下载 CP", "Download file", "下载文件", "下载", "Download"]:
                    try:
                        loc = scope.get_by_text(text_pat)
                        c = loc.count()
                        for i in range(min(c, 15)):
                            found.append(loc.nth(i))
                    except Exception:
                        continue
                # 再兜底：按可访问角色名称匹配
                for role_name in ["Download", "Download file", "下载", "下载文件", "导出", "Markdown"]:
                    try:
                        loc_btn = scope.get_by_role("button", name=role_name)
                        c_btn = loc_btn.count()
                        for i in range(min(c_btn, 15)):
                            found.append(loc_btn.nth(i))
                    except Exception:
                        pass
                    try:
                        loc_link = scope.get_by_role("link", name=role_name)
                        c_link = loc_link.count()
                        for i in range(min(c_link, 15)):
                            found.append(loc_link.nth(i))
                    except Exception:
                        pass

        uniq: Dict[str, Any] = {}
        for i, el in enumerate(found):
            k = self._artifact_key(el)
            if k in uniq:
                k = f"{k}#idx{i}"
            uniq[k] = el
        return list(uniq.values())

    def _download_artifacts(self, page: Page, state: SessionState, *, out_dir: str) -> List[str]:
        """Method-2 first downloader: use DOM-displayed filename as source of truth."""
        trace_events: List[Dict[str, Any]] = []
        downloaded: List[str] = []
        used_urls = set()
        used_names = set()

        expected_names = self._extract_expected_filenames_from_last_assistant(page)
        assistant_links = self._extract_direct_file_links_from_last_assistant(page)
        if not expected_names:
            seen_expected = set()
            for it in assistant_links:
                lb = str((it or {}).get("label") or "").strip()
                n = self._extract_filename_like(lb)
                if n and (not self._is_uuid_like(n)) and n not in seen_expected:
                    expected_names.append(n)
                    seen_expected.add(n)
        trace_events.append(
            {
                "stage": "init",
                "page_url": page.url or "",
                "out_dir": out_dir,
                "expected_names": expected_names,
                "assistant_link_count": len(assistant_links),
            }
        )

        def _collect_all_assistant_urls() -> List[str]:
            urls: List[str] = []
            for it in assistant_links:
                u = str((it or {}).get("url") or "").strip()
                if u:
                    urls.append(u)
            seen = set()
            out: List[str] = []
            for u in urls:
                if u in seen:
                    continue
                seen.add(u)
                out.append(u)
            return out

        all_assistant_urls = _collect_all_assistant_urls()

        def _fetch_with_preferred_name(preferred_name: str, seed_urls: List[str]) -> str:
            queue = [u for u in seed_urls if u]
            seen = set()
            while queue:
                u = (queue.pop(0) or "").strip()
                if not u or u in seen or u in used_urls:
                    continue
                seen.add(u)
                trace_events.append({"stage": "request_try", "preferred_name": preferred_name, "url": u})
                try:
                    resp = page.context.request.get(u, timeout=20_000)
                except Exception:
                    trace_events.append({"stage": "request_error", "preferred_name": preferred_name, "url": u})
                    continue
                if not resp.ok:
                    trace_events.append(
                        {
                            "stage": "request_not_ok",
                            "preferred_name": preferred_name,
                            "url": u,
                            "status": getattr(resp, "status", None),
                        }
                    )
                    continue

                ctype = (resp.headers.get("content-type") or "").lower()
                if "application/json" in ctype:
                    try:
                        js = resp.json() or {}
                    except Exception:
                        js = {}
                    flat: List[str] = []

                    def _walk(o: Any) -> None:
                        if o is None:
                            return
                        if isinstance(o, dict):
                            for vv in o.values():
                                _walk(vv)
                        elif isinstance(o, list):
                            for vv in o:
                                _walk(vv)
                        elif isinstance(o, (str, int, float)):
                            flat.append(str(o))

                    _walk(js)
                    for s in flat:
                        s1 = (s or "").strip()
                        if not s1:
                            continue
                        if s1.startswith("http://") or s1.startswith("https://"):
                            queue.append(s1)
                            continue
                        m = re.search(r"(/backend-api/files/[0-9a-f\\-]+(?:/download)?)", s1, flags=re.I)
                        if m:
                            origin_m = re.match(r"^(https?://[^/]+)", page.url or "")
                            origin = origin_m.group(1) if origin_m else ""
                            if origin:
                                queue.append(f"{origin}{m.group(1)}")
                    trace_events.append({"stage": "request_json_hop", "preferred_name": preferred_name, "url": u})
                    continue

                body = resp.body() or b""
                if not body:
                    trace_events.append({"stage": "request_empty_body", "preferred_name": preferred_name, "url": u})
                    continue
                target_name = sanitize_filename(preferred_name or "")
                if not target_name:
                    # Last-resort fallback when preferred name missing.
                    header_name = self._filename_from_content_disposition(resp.headers.get("content-disposition") or "")
                    query_name = self._extract_filename_from_url(u)
                    target_name = sanitize_filename(header_name or query_name or f"{self.platform}_artifact.bin")
                save_path = self._ensure_unique_save_path(os.path.join(out_dir, target_name))
                try:
                    with open(save_path, "wb") as f:
                        f.write(body)
                    used_urls.add(u)
                    used_names.add(os.path.basename(save_path))
                    trace_events.append(
                        {
                            "stage": "saved",
                            "preferred_name": preferred_name,
                            "url": u,
                            "saved_path": save_path,
                            "status": getattr(resp, "status", None),
                            "content_type": ctype,
                        }
                    )
                    return save_path
                except Exception:
                    trace_events.append(
                        {
                            "stage": "save_error",
                            "preferred_name": preferred_name,
                            "url": u,
                            "target_name": target_name,
                        }
                    )
                    continue
            return ""

        # Step 1: DOM-name targeted downloads (Method 2)
        for fname in expected_names:
            if fname in used_names:
                continue
            try:
                name_loc = page.get_by_text(fname, exact=False).first
                if name_loc.count() <= 0:
                    trace_events.append({"stage": "name_not_found_in_dom", "filename": fname})
                    continue
            except Exception:
                trace_events.append({"stage": "name_locator_error", "filename": fname})
                continue

            seed_urls: List[str] = []
            try:
                anc = name_loc.locator("xpath=ancestor-or-self::*[self::a or self::button][1]").first
                if anc.count() > 0:
                    dom_name = self._extract_filename_from_dom(anc) or ""
                    if dom_name and (not self._is_uuid_like(dom_name)):
                        fname = dom_name
                    seed_urls.extend(self._extract_candidate_file_urls(page, anc))
            except Exception:
                pass
            try:
                box = name_loc.locator(
                    "xpath=ancestor::*[contains(@class,'file') or contains(@class,'attachment') or contains(@class,'artifact')][1]"
                ).first
                if box.count() > 0:
                    dom_name2 = self._extract_filename_from_dom(box) or ""
                    if dom_name2 and (not self._is_uuid_like(dom_name2)):
                        fname = dom_name2
                    seed_urls.extend(self._extract_candidate_file_urls(page, box))
            except Exception:
                pass
            # add links whose label contains filename, then all links as fallback
            for it in assistant_links:
                lb = str((it or {}).get("label") or "").strip()
                uu = str((it or {}).get("url") or "").strip()
                if lb and uu and fname.lower() in lb.lower():
                    seed_urls.append(uu)
            seed_urls.extend(all_assistant_urls)

            # dedupe
            _seen = set()
            dedup_seed = []
            for u in seed_urls:
                u1 = (u or "").strip()
                if not u1 or u1 in _seen:
                    continue
                _seen.add(u1)
                dedup_seed.append(u1)
            trace_events.append({"stage": "name_seed_urls", "filename": fname, "count": len(dedup_seed), "urls_preview": dedup_seed[:12]})

            saved = _fetch_with_preferred_name(fname, dedup_seed)
            if saved:
                downloaded.append(saved)
            else:
                # Method-2 fallback: no URLs in DOM (e.g. ChatGPT uses click-to-download).
                # Only attempt click when a nearby explicit download control is found.
                trace_events.append({"stage": "name_click_fallback_try", "filename": fname})
                click_saved = ""
                try:
                    try:
                        name_loc.hover(timeout=2000)
                        time.sleep(0.15)
                    except Exception:
                        pass

                    click_target = None
                    # Prefer clicking the filename link itself (ChatGPT often uses <a class="cursor-pointer">).
                    try:
                        link_target = name_loc.locator(
                            "xpath=ancestor-or-self::a[contains(@class,'cursor-pointer')][1]"
                        ).first
                        if link_target.count() > 0:
                            click_target = link_target
                            trace_events.append({"stage": "name_link_target_found", "filename": fname})
                    except Exception:
                        pass

                    scopes: List[Any] = []
                    try:
                        li = name_loc.locator("xpath=ancestor::li[1]").first
                        if li.count() > 0:
                            try:
                                li.hover(timeout=2000)
                                time.sleep(0.15)
                            except Exception:
                                pass
                            scopes.append(li)
                    except Exception:
                        pass
                    try:
                        cand = name_loc.locator(
                            "xpath=ancestor::*[contains(@class,'file') or contains(@class,'attachment') or contains(@class,'artifact')][1]"
                        ).first
                        if cand.count() > 0:
                            try:
                                cand.hover(timeout=2000)
                                time.sleep(0.15)
                            except Exception:
                                pass
                            scopes.append(cand)
                    except Exception:
                        pass
                    scopes.append(name_loc)

                    if click_target is None:
                        selectors = [
                            "a.cursor-pointer",
                            "button:has-text('下载')",
                            "button:has-text('Download')",
                            "a:has-text('下载')",
                            "a:has-text('Download')",
                            "button[aria-label*='Download']",
                            "button[aria-label*='download']",
                            "button[aria-label*='下载']",
                            "a[aria-label*='Download']",
                            "a[aria-label*='download']",
                            "a[aria-label*='下载']",
                            "button[title*='Download']",
                            "button[title*='download']",
                            "button[title*='下载']",
                            "[data-testid*='download']",
                            "[data-testid*='Download']",
                        ]
                        for scope in scopes:
                            if click_target is not None:
                                break
                            for sel in selectors:
                                try:
                                    btn = scope.locator(sel).first
                                    if btn.count() > 0:
                                        click_target = btn
                                        break
                                except Exception:
                                    continue
                    if click_target is None:
                        trace_events.append({"stage": "name_click_fallback_no_control", "filename": fname})
                    else:
                        trace_events.append(
                            {
                                "stage": "name_click_target_resolved",
                                "filename": fname,
                                "target_tag": click_target.evaluate("n => (n.tagName || '').toLowerCase()"),
                                "target_class": click_target.get_attribute("class") or "",
                                "target_aria": click_target.get_attribute("aria-label") or "",
                            }
                        )
                        # --- CDP intercept: behavior="default" + eventsEnabled ---
                        # Key insight: behavior="allow" + downloadPath causes Chrome to route downloads
                        # through Playwright; in attach mode Playwright can't complete them → "出了点问题".
                        # Solution: behavior="default" lets Chrome download normally (correct filename,
                        # to its own configured dir). We only use events to capture the mapping
                        # guid → suggestedFilename, then find and copy the file to out_dir.
                        _cdp_sess = None
                        _cdp_guid_map: Dict[str, str] = {}   # guid -> suggestedFilename
                        _cdp_done: set = set()
                        _abs_out = os.path.abspath(out_dir)
                        _is_attach = (
                            getattr(getattr(self, "browser", None), "config", None) is not None
                            and getattr(self.browser.config, "mode", None) == "attach"
                        )
                        try:
                            _cdp_sess = page.context.new_cdp_session(page)
                            _cdp_sess.send("Browser.setDownloadBehavior", {
                                "behavior": "default",
                                "eventsEnabled": True,
                            })

                            def _on_dl_begin(e: Any) -> None:
                                g = str(e.get("guid") or "")
                                s = str(e.get("suggestedFilename") or "")
                                if g:
                                    _cdp_guid_map[g] = s

                            def _on_dl_progress(e: Any) -> None:
                                if str(e.get("state") or "") == "completed":
                                    g = str(e.get("guid") or "")
                                    if g:
                                        _cdp_done.add(g)

                            _cdp_sess.on("Browser.downloadWillBegin", _on_dl_begin)
                            _cdp_sess.on("Browser.downloadProgress", _on_dl_progress)
                            trace_events.append({"stage": "cdp_intercept_ok", "filename": fname, "is_attach": _is_attach})
                        except Exception as _cdp_e:
                            trace_events.append({"stage": "cdp_intercept_failed", "filename": fname, "error": str(_cdp_e)})

                        sys_dl_dir = self._get_system_downloads_dir()
                        before_click = self._snapshot_dir(sys_dl_dir)

                        if _is_attach and _cdp_sess is not None:
                            # In attach mode: click directly, Chrome handles download natively.
                            # expect_download() is NOT used here — it triggers behavior="allow"
                            # interception that fails in attach mode.
                            try:
                                click_target.click()
                            except Exception:
                                try:
                                    click_target.click(force=True)
                                except Exception as _ce:
                                    trace_events.append({"stage": "cdp_click_failed", "filename": fname, "error": str(_ce)})
                            trace_events.append({"stage": "cdp_click_done", "filename": fname})
                        else:
                            # In launch mode: use Playwright's expect_download (reliable)
                            try:
                                with page.expect_download(timeout=20_000) as dl_info:
                                    try:
                                        click_target.click()
                                    except Exception:
                                        click_target.click(force=True)
                                dl = dl_info.value
                                target_name = sanitize_filename(fname or "")
                                try:
                                    suggested = sanitize_filename(dl.suggested_filename or "")
                                except Exception:
                                    suggested = ""
                                if (not target_name or self._is_uuid_like(target_name)) and suggested and (not self._is_uuid_like(suggested)):
                                    target_name = suggested
                                if not target_name:
                                    target_name = f"{self.platform}_artifact.bin"
                                save_path = self._ensure_unique_save_path(os.path.join(out_dir, target_name))
                                if self._save_download_once(dl, save_path):
                                    click_saved = save_path
                                    trace_events.append(
                                        {
                                            "stage": "name_click_saved",
                                            "filename": fname,
                                            "suggested": suggested,
                                            "saved_path": save_path,
                                        }
                                    )
                            except Exception as dl_err:
                                trace_events.append({"stage": "name_click_expect_download_failed", "filename": fname, "error": str(dl_err)})

                        # --- CDP fallback: Chrome downloaded natively; find by suggestedFilename ---
                        # With behavior="default", Chrome saves the file to sys_dl_dir using the
                        # server-suggested filename (real name, not UUID). We wait for completion,
                        # find the file, and copy it to out_dir with our preferred name.
                        if not click_saved and _cdp_guid_map:
                            _cdp_deadline = time.time() + 15.0
                            while time.time() < _cdp_deadline:
                                time.sleep(0.4)
                                if _cdp_done.issuperset(_cdp_guid_map.keys()):
                                    break
                            _search_dirs = list(dict.fromkeys([_abs_out, sys_dl_dir, os.path.expanduser("~/Downloads")]))
                            for _guid, _suggested in list(_cdp_guid_map.items()):
                                _clean_sug = sanitize_filename(_suggested or "")
                                _tname = sanitize_filename(fname or "")
                                if not _tname or self._is_uuid_like(_tname):
                                    if _clean_sug and not self._is_uuid_like(_clean_sug):
                                        _tname = _clean_sug
                                if not _tname or self._is_uuid_like(_tname):
                                    _ext = os.path.splitext(_clean_sug)[1] if _clean_sug else ""
                                    _tname = f"{self.platform}_artifact{_ext or '.bin'}"
                                # Search by suggestedFilename first (Chrome native), then by GUID
                                _search_names = [n for n in [_clean_sug, _guid] if n]
                                _found_path = None
                                _wt = time.time() + 10.0
                                while _found_path is None and time.time() < _wt:
                                    for _sdir in _search_dirs:
                                        for _sname in _search_names:
                                            _cand = os.path.join(_sdir, _sname)
                                            if os.path.exists(_cand) and os.path.getsize(_cand) > 0:
                                                _found_path = _cand
                                                break
                                        if _found_path:
                                            break
                                    if _found_path is None:
                                        time.sleep(0.3)
                                if _found_path is None:
                                    trace_events.append({"stage": "cdp_file_missing", "guid": _guid, "suggested": _suggested, "searched": _search_dirs, "search_names": _search_names})
                                    continue
                                trace_events.append({"stage": "cdp_file_found", "guid": _guid, "suggested": _suggested, "found_at": _found_path})
                                _dst = self._ensure_unique_save_path(os.path.join(_abs_out, _tname))
                                try:
                                    _found_abs = os.path.abspath(_found_path)
                                    _dst_abs = os.path.abspath(_dst)
                                    if _found_abs == _dst_abs:
                                        click_saved = _dst
                                    elif os.path.dirname(_found_abs) == _abs_out:
                                        shutil.move(_found_path, _dst)
                                        click_saved = _dst
                                    else:
                                        shutil.copy2(_found_path, _dst)
                                        click_saved = _dst
                                    trace_events.append({
                                        "stage": "cdp_download_saved",
                                        "guid": _guid,
                                        "suggested": _suggested,
                                        "target_name": _tname,
                                        "saved_path": _dst,
                                    })
                                    break
                                except Exception as _me:
                                    trace_events.append({"stage": "cdp_rename_error", "guid": _guid, "suggested": _suggested, "error": str(_me)})

                        # Detach CDP session (best-effort)
                        if _cdp_sess is not None:
                            try:
                                _cdp_sess.detach()
                            except Exception:
                                pass

                        if not click_saved:
                            trace_events.append({"stage": "name_click_fallback_sys_dir_try", "filename": fname, "sys_dl_dir": sys_dl_dir})
                            new_files = self._wait_for_new_files_in_dir(sys_dl_dir, before_click, timeout_s=15.0, stable_s=1.5)
                            if not new_files:
                                trace_events.append({"stage": "name_click_fallback_sys_dir_no_new_files", "filename": fname})
                            elif new_files:
                                try:
                                    src = max(new_files, key=lambda p: os.path.getmtime(p))
                                except Exception:
                                    src = new_files[0]
                                src_name = sanitize_filename(os.path.basename(src))
                                target_name = sanitize_filename(fname or "")
                                if (not target_name or self._is_uuid_like(target_name)) and src_name and (not self._is_uuid_like(src_name)):
                                    target_name = src_name
                                if not target_name or self._is_uuid_like(target_name):
                                    ext = os.path.splitext(src_name)[1] if src_name else ""
                                    target_name = f"{self.platform}_artifact{ext or '.bin'}"
                                dst = self._ensure_unique_save_path(os.path.join(out_dir, target_name))
                                try:
                                    shutil.copy2(src, dst)
                                    click_saved = dst
                                    trace_events.append({"stage": "name_click_copied_from_sys", "filename": fname, "saved_path": dst})
                                except Exception as copy_err:
                                    trace_events.append({"stage": "name_click_copy_error", "filename": fname, "error": str(copy_err)})
                except Exception as e:
                    trace_events.append({"stage": "name_click_fallback_error", "filename": fname, "error": str(e)})
                if click_saved:
                    downloaded.append(click_saved)
                    used_names.add(os.path.basename(click_saved))
                else:
                    trace_events.append({"stage": "name_download_failed", "filename": fname})

        # Step 2: fallback for extras/unmatched links (still keep filename hygiene)
        if not downloaded:
            trace_events.append({"stage": "fallback_direct_links_start"})
            direct_downloaded = self._download_via_direct_links(page, state, out_dir=out_dir, trace_events=trace_events)
            for p in direct_downloaded:
                b = os.path.basename(p)
                if b not in used_names:
                    downloaded.append(p)
                    used_names.add(b)

        trace_events.append(
            {
                "stage": "end",
                "downloaded_count": len(downloaded),
                "downloaded_names": [os.path.basename(p) for p in downloaded],
                "missing_expected": [n for n in expected_names if n not in {os.path.basename(p) for p in downloaded}],
            }
        )
        try:
            import json

            dbg_dir = ensure_dir(os.path.join(out_dir, "_debug"))
            trace_path = os.path.join(dbg_dir, f"{now_ts()}_download_trace.json")
            with open(trace_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "schema": "download_trace.v1",
                        "summary": trace_events[-1] if trace_events else {},
                        "events": trace_events,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            print(f"[download-trace] {trace_path}")
        except Exception:
            pass

        if not downloaded:
            try:
                self._capture_debug(page, out_dir, tag="download_empty")
            except Exception:
                pass

        return downloaded

    # ---------- Debug capture ----------
    def _collect_diagnostics(self, page: Page, state: Optional[SessionState] = None) -> Dict[str, Any]:
        return DiagnosticsHelper.collect(self, page, state)

    def _classify_timeout(self, error_msg: str, diagnostics: Dict[str, Any]) -> Dict[str, Any]:
        msg = (error_msg or "").lower()
        if diagnostics.get("hint_login") or diagnostics.get("hint_verify") or diagnostics.get("hint_captcha"):
            return {"retryable": False, "reason": "needs_auth_or_captcha", "action": "manual_login"}
        if diagnostics.get("hint_rate_limit"):
            return {"retryable": True, "reason": "rate_limited", "action": "backoff_then_retry"}
        if diagnostics.get("hint_compiler_expired"):
            return {"retryable": True, "reason": "compiler_expired", "action": "resend_prompt_then_retry"}
        if "no visible output" in msg or "generation stuck" in msg:
            return {"retryable": True, "reason": "no_output", "action": "refresh_and_retry"}
        if diagnostics.get("stop_active") and not diagnostics.get("last_assistant_text_len"):
            return {"retryable": True, "reason": "stuck_generation", "action": "refresh_and_retry"}
        if not diagnostics.get("has_prompt_box") or not diagnostics.get("has_send_button"):
            return {"retryable": False, "reason": "ui_not_ready", "action": "check_selectors_or_login"}
        return {"retryable": True, "reason": "unknown_timeout", "action": "retry_or_increase_timeout"}

    def _capture_debug(self, page: Page, out_dir: str, *, tag: str) -> Dict[str, Any]:
        return DiagnosticsHelper.capture_debug(self, page, out_dir, tag=tag)

    def _write_debug_artifacts(
        self,
        page: Page,
        out_dir: str,
        *,
        tag: str,
        diagnostics: Dict[str, Any],
    ) -> Dict[str, Any]:
        dbg_dir = ensure_dir(os.path.join(out_dir, "_debug"))
        ts = now_ts()

        diag_path = os.path.join(dbg_dir, f"{ts}_{tag}.json")
        png = os.path.join(dbg_dir, f"{ts}_{tag}.png")
        html = os.path.join(dbg_dir, f"{ts}_{tag}.html")

        try:
            import json

            with open(diag_path, "w", encoding="utf-8") as f:
                json.dump(diagnostics, f, ensure_ascii=False, indent=2)
        except Exception:
            diag_path = ""

        if not self.browser.config.capture_debug_screenshot:
            png = ""
        else:
            try:
                page.screenshot(path=png, full_page=True)
            except Exception:
                png = ""

        if not self.browser.config.capture_debug_html:
            html = ""
        else:
            try:
                content = page.content()
                with open(html, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception:
                html = ""

        return {"debug_screenshot": png, "debug_html": html, "debug_diagnostics": diag_path, "diagnostics": diagnostics}


__all__ = [
    "ExecuteMode",
    "BrowserConfig",
    "ExecutionConfig",
    "BrowserManager",
    "BaseChatAdapter",
    "SessionState",
    "PageHandle",
]
