"""Gemini web UI adapter.

Default model policy (per your requirement):
- Gemini 3 Pro (page default is usually Gemini 3 Pro)

We keep model matching flexible because the UI label may vary (language/casing).
"""

from __future__ import annotations

import os
import re
import time
from typing import List, Pattern

from playwright.sync_api import Page

from .base import BaseChatAdapter, BrowserManager, ExecutionConfig, SessionState
from .selectors import GEMINI_SELECTORS
from .utils import first_match_locator, first_match_role_locator, safe_inner_text


class GeminiAdapter(BaseChatAdapter):
    # Canonical keys used by our code (NOT necessarily the UI label)
    DEFAULT_MODEL_KEY = "gemini-3-pro"

    def __init__(self, *, browser: BrowserManager, exec_cfg: ExecutionConfig | None = None):
        super().__init__(
            platform="gemini",
            selectors=GEMINI_SELECTORS,
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

        if "gemini" in m or "g3" in m or "3 pro" in m or ("3" in m and "pro" in m):
            if "3" in m and "pro" in m:
                return "gemini-3-pro"

        return (model or "").strip()

    @staticmethod
    def _model_pattern(model_key_or_label: str) -> Pattern[str]:
        key = (model_key_or_label or "").strip().lower()

        if key in {"gemini-3-pro", "gemini 3 pro", "gemini3 pro"}:
            return re.compile(r"(gemini\s*3(\.0)?\s*pro|3\s*pro|3\s*专业)", re.I)

        return re.compile(re.escape(model_key_or_label), re.I)

    def _ensure_model(self, page: Page, state: SessionState, model: str) -> None:
        target_key = self._canonical_model_key(model)
        if state.selected_model and state.selected_model == target_key:
            return

        pattern = self._model_pattern(target_key)

        # Try open model switcher
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
            raise RuntimeError("[gemini] model switcher not found. Update selectors.model_switcher")

        # If the button already shows the desired model, no need to click
        try:
            if pattern.search(safe_inner_text(btn) or ""):
                state.selected_model = target_key
                return
        except Exception:
            pass

        btn.click()
        time.sleep(0.3)

        # Find option/menu item by text
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
            raise RuntimeError(f"[gemini] model option not found for: {model}. Maybe not available for this account/UI.")

        option.click()
        state.selected_model = target_key

    # --- Upload (Gemini menu-aware) ---
    def _upload_files(self, page: Page, paths: List[str]) -> None:
        abs_paths = [os.path.abspath(p) for p in paths]
        for p in abs_paths:
            if not os.path.exists(p):
                raise FileNotFoundError(p)

        # Gemini supports multiple, but safer to upload sequentially
        for p in abs_paths:
            self._upload_one(page, p)
            time.sleep(1.0)

    def _upload_one(self, page: Page, file_path: str) -> None:
        # Prefer input injection if exists (fast path)
        file_input = first_match_locator(page, self.sel.file_input, must_be_visible=False)
        if file_input and file_input.count() > 0:
            try:
                file_input.set_input_files(file_path)
                return
            except Exception:
                pass

        upload_btn = first_match_locator(page, self.sel.attach_button, must_be_visible=True, timeout_ms_each=5000)
        if not upload_btn:
            raise RuntimeError("[gemini] upload button not found. Update selectors.attach_button")

        # File chooser may be triggered by button OR by a menu item
        with page.expect_file_chooser(timeout=15_000) as fc_info:
            upload_btn.click()
            time.sleep(1.2)

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
        chooser.set_files(file_path)

    # --- Better extraction using copy button as fallback ---
    def _extract_latest_reply(self, page: Page, state: SessionState, *, since_last: bool) -> str:
        # First try super() extraction (with format preservation from HTML)
        txt = super()._extract_latest_reply(page, state, since_last=since_last)
        
        # Filter out user prompts: if extracted text matches the prompt box content, it's likely a user message
        if txt:
            try:
                prompt_text = self._peek_prompt_text(page)
                # If extracted text is very similar to prompt text, it's likely a user message
                if prompt_text and txt.strip() == prompt_text.strip():
                    # This is likely a user message, clear it and try copy button
                    txt = ""
            except Exception:
                pass
        
        # If we got meaningful text (not prompt), try to enhance with copy button if available
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
                                            # Filter out if it's the prompt
                                            try:
                                                prompt_text = self._peek_prompt_text(page)
                                                if prompt_text and copied.strip() == prompt_text.strip():
                                                    return txt  # Return original if copied is also prompt
                                            except Exception:
                                                pass
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
                                        try:
                                            prompt_text = self._peek_prompt_text(page)
                                            if prompt_text and copied.strip() == prompt_text.strip():
                                                return txt
                                        except Exception:
                                            pass
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


__all__ = ["GeminiAdapter"]
