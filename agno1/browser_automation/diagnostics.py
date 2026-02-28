from __future__ import annotations

from typing import Any, Dict, Optional

from playwright.sync_api import Page

from .selectors import DIAGNOSTIC_SELECTORS


class DiagnosticsHelper:
    """Helper utilities for lightweight diagnostics and debug captures."""

    @staticmethod
    def collect(adapter: Any, page: Page, state: Optional[Any] = None) -> Dict[str, Any]:
        """Collect lightweight diagnostics without dumping full HTML."""
        diag: Dict[str, Any] = {}

        try:
            diag["url"] = page.url
        except Exception:
            diag["url"] = ""

        try:
            diag["title"] = page.title()
        except Exception:
            diag["title"] = ""

        try:
            diag["assistant_blocks"] = adapter._assistant_count(page)
        except Exception:
            diag["assistant_blocks"] = 0

        try:
            diag["user_blocks"] = adapter._user_count(page)
        except Exception:
            diag["user_blocks"] = 0

        try:
            diag["last_user_text_len"] = len((adapter._peek_last_user_text(page) or "").strip())
        except Exception:
            diag["last_user_text_len"] = 0

        try:
            diag["stop_visible"] = adapter._is_stop_visible(page)
        except Exception:
            diag["stop_visible"] = False

        try:
            diag["stop_active"] = adapter._is_stop_active(page)
        except Exception:
            diag["stop_active"] = False

        try:
            diag["send_ready"] = adapter._is_send_ready(page)
        except Exception:
            diag["send_ready"] = False

        try:
            diag["last_assistant_text_len"] = len((adapter._peek_last_assistant_text(page) or "").strip())
        except Exception:
            diag["last_assistant_text_len"] = 0

        # Quick presence checks
        try:
            diag["has_prompt_box"] = False
            for sel in (adapter.sel.prompt_box or [])[:6]:
                try:
                    if page.locator(sel).count() > 0:
                        diag["has_prompt_box"] = True
                        break
                except Exception:
                    continue
        except Exception:
            diag["has_prompt_box"] = False

        try:
            diag["has_send_button"] = False
            for sel in (adapter.sel.send_button or [])[:10]:  # 增加检查的选择器数量
                try:
                    if page.locator(sel).count() > 0:
                        diag["has_send_button"] = True
                        break
                except Exception:
                    continue
        except Exception:
            diag["has_send_button"] = False

        # Common blocker hints (best-effort, cheap presence checks)
        def _has_text(t: str) -> bool:
            try:
                return page.locator(f"text={t}").count() > 0
            except Exception:
                return False

        diag["hint_login"] = _has_text("Log in") or _has_text("Sign in") or _has_text("登录")
        diag["hint_verify"] = _has_text("Verify") or _has_text("验证")
        diag["hint_rate_limit"] = _has_text("Too many requests") or _has_text("请求过多")
        diag["hint_error"] = _has_text("Something went wrong") or _has_text("出错") or _has_text("错误")
        # 代码编译器/环境已过期：需重新发送主控 prompt 后再下载
        diag["hint_compiler_expired"] = (
            _has_text("代码编译器已过期")
            or _has_text("编译器已过期")
            or _has_text("Code interpreter has expired")
        )
        try:
            diag["hint_captcha"] = any(page.locator(sel).count() > 0 for sel in DIAGNOSTIC_SELECTORS["captcha_iframes"])
        except Exception:
            diag["hint_captcha"] = False

        if state is not None:
            diag["state_chat_url"] = getattr(state, "chat_url", None)
            diag["state_last_assistant_count"] = getattr(state, "last_assistant_count", None)
            diag["state_last_user_count"] = getattr(state, "last_user_count", None)

        return diag

    @staticmethod
    def capture_debug(adapter: Any, page: Page, out_dir: str, *, tag: str) -> Dict[str, Any]:
        diagnostics = DiagnosticsHelper.collect(adapter, page)
        return adapter._write_debug_artifacts(page, out_dir, tag=tag, diagnostics=diagnostics)


__all__ = ["DiagnosticsHelper"]
