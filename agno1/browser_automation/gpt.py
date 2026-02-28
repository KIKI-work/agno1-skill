"""ChatGPT-specific automation adapter.

Default model policy (per your requirement):
- Default: GPT-5.2 Thinking
- Optional: GPT-5.2 Pro

We keep model matching flexible because the UI label may vary (language/casing).
"""

from __future__ import annotations

import os
import re
import time
from typing import Optional, Pattern, Tuple

from playwright.sync_api import Locator, Page

from .base import BaseChatAdapter, BrowserManager, ExecutionConfig, SessionState
from .selectors import CHATGPT_SELECTORS
from .utils import first_match_locator, first_match_role_locator, safe_inner_text, with_retry
from .base import SessionState
from playwright.sync_api import Page


class ChatGPTAdapter(BaseChatAdapter):
    # Canonical keys used by our code (NOT necessarily the UI label)
    DEFAULT_MODEL_KEY = "gpt-5.2-thinking"

    def _is_stop_active(self, page: Page) -> bool:
        """ChatGPT stop button can be sticky; use a stricter "active" definition."""

        stop = first_match_locator(page, self.sel.stop_button, must_be_visible=False)
        if not stop:
            return False

        try:
            if not stop.is_visible():
                return False
        except Exception:
            return False

        # stop 存在但不可用：视为已结束
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
            pass

        # send 按钮恢复：视为已结束（即便 stop 还在 DOM 里）
        if self._is_send_ready(page):
            return False

        return True

    # Backward-compat: old code used "stop_visible" as a proxy for "generating"
    def _is_stop_visible(self, page: Page) -> bool:
        return self._is_stop_active(page)

    def __init__(self, *, browser: BrowserManager, exec_cfg: ExecutionConfig | None = None):
        super().__init__(
            platform="chatgpt",
            selectors=CHATGPT_SELECTORS,
            browser=browser,
            exec_cfg=exec_cfg,
            default_model=self.DEFAULT_MODEL_KEY,
        )

    # --- Model selection ---

    @staticmethod
    def _canonical_model_key(model: str) -> str:
        m = (model or "").strip().lower()
        m = m.replace("_", " ").replace("-", " ")
        m = re.sub(r"\s+", " ", m)

        # Common shorthands like "5.2pro"
        if "5.2" in m or "5 2" in m:
            if "pro" in m:
                return "gpt-5.2-pro"
            if "think" in m or "thinking" in m or "思考" in m:
                return "gpt-5.2-thinking"

        return (model or "").strip()

    @staticmethod
    def _model_pattern(model_key_or_label: str) -> Pattern[str]:
        """Return a regex that matches the UI-visible option label."""
        key = (model_key_or_label or "").strip().lower()

        if key in {"gpt-5.2-thinking", "gpt 5.2 thinking", "gpt5.2 thinking"}:
            # English + some possible CN
            return re.compile(r"(gpt\s*[- ]?5\.?2.*think|5\.?2.*think|5\.?2.*思考)", re.I)

        if key in {"gpt-5.2-pro", "gpt 5.2 pro", "gpt5.2 pro"}:
            return re.compile(r"(gpt\s*[- ]?5\.?2.*pro|5\.?2.*pro|5\.?2.*专业)", re.I)

        # Fallback: treat input as literal text fragment
        return re.compile(re.escape(model_key_or_label), re.I)

    def _ensure_model(self, page: Page, state: SessionState, model: str) -> None:
        target_key = self._canonical_model_key(model)
        if state.selected_model and state.selected_model == target_key:
            return

        pattern = self._model_pattern(target_key)

        btn = first_match_locator(page, self.sel.model_switcher, must_be_visible=False)
        if not btn and self.sel.model_switcher_role_names:
            scopes = self.sel.model_switcher_role_scopes or []
            for scope_sel in scopes:
                try:
                    scope = page.locator(scope_sel).first
                    if scope.count() <= 0:
                        continue
                    btn = first_match_role_locator(
                        scope,
                        role="button",
                        name_patterns=self.sel.model_switcher_role_names,
                        must_be_visible=False,
                    )
                    if btn:
                        break
                except Exception:
                    continue
        if not btn and self.sel.model_switcher_role_names:
            btn = first_match_role_locator(
                page,
                role="button",
                name_patterns=self.sel.model_switcher_role_names,
                must_be_visible=False,
            )

        if not btn:
            raise RuntimeError("[chatgpt] model switcher not found. Update selectors.model_switcher")

        # If the button already shows the desired model, no need to click
        try:
            if pattern.search(safe_inner_text(btn) or ""):
                state.selected_model = target_key
                return
        except Exception:
            pass

        btn.click()
        time.sleep(0.3)

        option = None
        try:
            option = page.get_by_role("menuitem", name=pattern).first
            if option.count() == 0:
                option = None
        except Exception:
            option = None

        if option is None:
            try:
                option = page.get_by_role("option", name=pattern).first
                if option.count() == 0:
                    option = None
            except Exception:
                option = None

        if option is None:
            try:
                option = page.get_by_text(pattern).first
                if option.count() == 0:
                    option = None
            except Exception:
                option = None

        if option is None:
            raise RuntimeError(
                f"[chatgpt] model option not found for: {model}. "
                f"ChatGPT may lock model per conversation; try reset_chat=True before the first prompt."
            )

        option.click()
        state.selected_model = target_key

    # --- Upload (ChatGPT menu-aware) ---
    def _is_project_page(self, page: Page) -> bool:
        """Return True only when on the project management page (Sources/Files tab), NOT when inside a project chat.

        Project chat URL pattern: /g/<gid>/c/<cid>  → has /c/ → normal chat attachment
        Project management URL:   /g/<gid>/project  → has /project → Sources upload
        """
        try:
            url = (page.url or "").strip()
            # If already navigated into a conversation (/c/...), treat as normal chat page
            if "/c/" in url:
                return False
            return "/project" in url
        except Exception:
            return False

    def _upload_files_via_project_sources(self, page: Page, abs_paths: list[str]) -> bool:
        """Project page: Sources → 添加 → 弹窗内点击上传（选择文件）. Returns True if upload succeeded."""
        # 1) Click Sources tab/button（快速探测，不命中立即放弃）
        print("[chatgpt] project upload: looking for Sources tab...")
        sources = first_match_locator(
            page, self.sel.project_sources_tab, must_be_visible=True, timeout_ms_each=800
        )
        if not sources:
            print("[chatgpt] project upload: Sources tab not found, skipping project path")
            return False
        print("[chatgpt] project upload: Sources tab found, clicking...")
        sources.click()
        time.sleep(1.2)
        # 2) Click 添加 (Add) — 点击后出现弹窗
        print("[chatgpt] project upload: looking for Add sources button...")
        add_btn = first_match_locator(
            page, self.sel.project_add_sources_button, must_be_visible=True, timeout_ms_each=800
        )
        if not add_btn:
            print("[chatgpt] project upload: Add sources button not found")
            return False
        print("[chatgpt] project upload: Add sources button found, clicking...")
        add_btn.click()
        time.sleep(1.0)
        # 3) 弹窗出现后，在弹窗内找「上传」按钮并点击，用文件选择器选文件
        scope = page
        if self.sel.project_add_modal:
            print("[chatgpt] project upload: looking for modal...")
            modal = first_match_locator(
                page, self.sel.project_add_modal, must_be_visible=True, timeout_ms_each=800
            )
            if modal and modal.count() > 0:
                print("[chatgpt] project upload: modal found")
                scope = modal
            else:
                print("[chatgpt] project upload: modal not found, using page scope")
        # 先尝试弹窗内的 input[type=file]
        print("[chatgpt] project upload: looking for file input in modal...")
        file_input = first_match_locator(
            scope, self.sel.project_upload_file_input, must_be_visible=False, timeout_ms_each=800
        )
        if file_input and file_input.count() > 0:
            print("[chatgpt] project upload: file input found, setting files...")
            file_input.set_input_files(abs_paths)
            self._wait_upload_confirmation(page, abs_paths)
            return True
        print("[chatgpt] project upload: file input not found, looking for upload button...")
        upload_btn = first_match_locator(
            scope, self.sel.project_upload_button, must_be_visible=True, timeout_ms_each=800
        )
        if upload_btn:
            print("[chatgpt] project upload: upload button found, clicking...")
            with page.expect_file_chooser(timeout=12_000) as fc_info:
                upload_btn.click()
                time.sleep(0.5)
            fc_info.value.set_files(abs_paths)
            self._wait_upload_confirmation(page, abs_paths)
            return True
        print("[chatgpt] project upload: upload button not found either")
        return False

    def _upload_files(self, page: Page, paths: list[str]) -> None:
        abs_paths = [os.path.abspath(p) for p in paths]
        for p in abs_paths:
            if not os.path.exists(p):
                raise FileNotFoundError(p)

        # Project page: use Sources → Add sources → upload
        if self._is_project_page(page) and (
            self.sel.project_sources_tab and self.sel.project_add_sources_button
        ):
            try:
                if self._upload_files_via_project_sources(page, abs_paths):
                    return
            except Exception as _proj_e:
                print(f"[chatgpt] _upload_files_via_project_sources failed: {_proj_e}")

        print("[chatgpt] project upload path exhausted, trying attach_button (chat '+' icon)...")
        upload_btn = first_match_locator(page, self.sel.attach_button, must_be_visible=True, timeout_ms_each=800)
        if upload_btn:
            def do_chooser() -> None:
                with page.expect_file_chooser(timeout=15_000) as fc_info:
                    upload_btn.click()
                    time.sleep(0.8)

                    # If menu appears, click a menu item
                    for sel in self.sel.upload_menu_items:
                        try:
                            item = page.locator(sel).first
                            if item.count() > 0 and item.is_visible():
                                item.click()
                                time.sleep(0.3)
                                break
                        except Exception:
                            continue

                chooser = fc_info.value
                chooser.set_files(abs_paths)
                self._wait_upload_confirmation(page, abs_paths)

            try:
                with_retry(do_chooser, retries=2, backoff_ms=500)
                return
            except Exception:
                pass

        file_input = first_match_locator(page, self.sel.file_input, must_be_visible=False)
        if file_input and file_input.count() > 0:
            file_input.set_input_files(abs_paths)
            self._wait_upload_confirmation(page, abs_paths)
            return

        raise RuntimeError("[chatgpt] upload button and file input not found. Update selectors.attach_button")

    # --- Better extraction using copy button as fallback (preserves formatting) ---
    def _extract_latest_reply(self, page: Page, state: SessionState, *, since_last: bool) -> str:
        # First try super() extraction (with format preservation from HTML)
        txt = super()._extract_latest_reply(page, state, since_last=since_last)
        
        # If we got meaningful text, try to enhance with copy button if available
        if txt and len(txt.strip()) >= 10:
            # Try copy button to get better formatted version (preserves Markdown/formatting)
            try:
                blocks = self._assistant_blocks_locator(page)
                try:
                    total = blocks.count()
                    if total > 0:
                        last_block = blocks.nth(total - 1)
                        # Look for copy button within the last message block
                        copy_btn = first_match_locator(last_block, self.sel.copy_button, must_be_visible=False)
                        if not copy_btn:
                            # Also try in the page scope
                            copy_btn = first_match_locator(page, self.sel.copy_button, must_be_visible=False)
                        
                        if copy_btn and copy_btn.count() > 0:
                            try:
                                if copy_btn.is_visible():
                                    copy_btn.click()
                                    time.sleep(0.6)  # Give clipboard time to update
                                    try:
                                        copied = page.evaluate("() => navigator.clipboard.readText()")  # may throw
                                        if isinstance(copied, str) and len(copied.strip()) >= 10:
                                            # Use copied version if it's longer or different (better formatting)
                                            if len(copied.strip()) > len(txt.strip()) or copied.strip() != txt.strip():
                                                return copied.strip()
                                    except Exception:
                                        pass  # Clipboard read failed
                            except Exception:
                                pass  # Button click failed
                except Exception:
                    pass
            except Exception:
                pass
            return txt
        
        # Fallback: try copy button if super() extraction failed
        try:
            blocks = self._assistant_blocks_locator(page)
            try:
                total = blocks.count()
                if total > 0:
                    last_block = blocks.nth(total - 1)
                    copy_btn = first_match_locator(last_block, self.sel.copy_button, must_be_visible=False)
                    if not copy_btn:
                        copy_btn = first_match_locator(page, self.sel.copy_button, must_be_visible=False)
                    
                    if copy_btn and copy_btn.count() > 0:
                        try:
                            if copy_btn.is_visible():
                                copy_btn.click()
                                time.sleep(0.6)
                                try:
                                    copied = page.evaluate("() => navigator.clipboard.readText()")
                                    if isinstance(copied, str) and len(copied.strip()) >= 10:
                                        return copied.strip()
                                except Exception:
                                    pass
                        except Exception:
                            pass
            except Exception:
                pass
        except Exception:
            pass
        
        return txt

    # --- Branch in new chat (create branch by clicking UI) ---

    def branch_in_new_chat(
        self,
        page: Page,
        *,
        message_index: int = -1,
        wait_after_click_s: float = 2.0,
        navigation_timeout_ms: int = 60_000,
    ) -> Tuple[str, Optional[Page]]:
        """Open the message menu on the target assistant message and click 'Branch in new chat'.

        Target message: message_index is 0-based; -1 = last assistant message.

        Returns:
            (new_chat_url, new_page_if_new_tab).
            - If ChatGPT opens the branch in the same tab: (page.url, None).
            - If it opens a new tab: (new_page.url, new_page).
        """
        blocks = self._assistant_blocks_locator(page)
        try:
            total = blocks.count()
        except Exception:
            total = 0
        if total <= 0:
            raise RuntimeError("[chatgpt] branch_in_new_chat: no assistant message blocks found")
        idx = message_index if message_index >= 0 else max(0, total + message_index)
        block = blocks.nth(idx)

        # Find and click message menu button (⋯) within or near this block
        menu_btn = first_match_locator(
            block,
            getattr(self.sel, "message_menu_button", None) or [],
            must_be_visible=True,
            timeout_ms_each=3000,
        )
        if not menu_btn:
            menu_btn = first_match_locator(
                page,
                getattr(self.sel, "message_menu_button", None) or [],
                must_be_visible=True,
                timeout_ms_each=2000,
            )
        if not menu_btn:
            raise RuntimeError(
                "[chatgpt] branch_in_new_chat: message menu button not found. "
                "Update selectors.message_menu_button."
            )
        try:
            menu_btn.click()
        except Exception:
            # 底部输入区等可能遮挡「更多」按钮，强制点击穿透
            menu_btn.click(force=True)
        time.sleep(0.5)

        # Click "Branch in new chat" menu item
        menuitem = first_match_locator(
            page,
            getattr(self.sel, "branch_in_new_chat_menuitem", None) or [],
            must_be_visible=True,
            timeout_ms_each=5000,
        )
        if not menuitem:
            # Debug: list all visible menu items to identify correct selector
            try:
                all_items = page.locator("[role='menuitem']").all()
                labels = [
                    (el.inner_text() or "").strip()
                    for el in all_items
                    if el.is_visible()
                ]
                print(f"[branch_in_new_chat] visible menuitems: {labels}")
            except Exception as _de:
                print(f"[branch_in_new_chat] could not enumerate menuitems: {_de}")
            raise RuntimeError(
                "[chatgpt] branch_in_new_chat: 'Branch in new chat' menu item not found. "
                "Update selectors.branch_in_new_chat_menuitem."
            )

        before_url = (page.url or "").strip()
        before_pages = set()
        try:
            for ctx in page.context.browser.contexts:
                for p in ctx.pages:
                    if not p.is_closed():
                        before_pages.add(id(p))
        except Exception:
            for p in page.context.pages:
                before_pages.add(id(p))

        menuitem.click()
        time.sleep(wait_after_click_s)

        deadline = time.time() + (float(navigation_timeout_ms) / 1000)
        while time.time() < deadline:
            # Check for new tab across ALL contexts (CDP attach mode may use separate contexts)
            try:
                all_pages: list = []
                try:
                    for ctx in page.context.browser.contexts:
                        all_pages.extend(ctx.pages)
                except Exception:
                    all_pages = list(page.context.pages)

                for p in all_pages:
                    if id(p) not in before_pages and not p.is_closed():
                        try:
                            cur = (p.url or "").strip()
                            if cur and "chatgpt.com" in cur and "/c/" in cur:
                                p.wait_for_load_state(state="domcontentloaded", timeout=15_000)
                                cur = (p.url or "").strip()
                                print(f"[branch_in_new_chat] new tab detected: {cur}")
                                return (cur, p)
                        except Exception:
                            continue
            except Exception:
                pass

            # Same-tab navigation: URL change
            try:
                cur = (page.url or "").strip()
                if cur != before_url and cur and "/c/" in cur:
                    print(f"[branch_in_new_chat] same-tab navigation: {cur}")
                    return (cur, None)
            except Exception:
                pass

            time.sleep(0.5)

        # Last-resort: look for any new chatgpt.com/c/ page
        try:
            all_pages_final: list = []
            try:
                for ctx in page.context.browser.contexts:
                    all_pages_final.extend(ctx.pages)
            except Exception:
                all_pages_final = list(page.context.pages)
            for p in all_pages_final:
                if not p.is_closed():
                    try:
                        cur = (p.url or "").strip()
                        if cur and "chatgpt.com" in cur and "/c/" in cur and cur != before_url:
                            print(f"[branch_in_new_chat] late-found page: {cur}")
                            return (cur, p if id(p) not in before_pages else None)
                    except Exception:
                        continue
        except Exception:
            pass

        raise RuntimeError(
            "[chatgpt] branch_in_new_chat: no navigation or new tab observed after clicking. "
            "Check UI or increase navigation_timeout_ms."
        )


__all__ = ["ChatGPTAdapter"]
