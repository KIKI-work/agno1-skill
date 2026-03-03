"""Windows 右下角系统通知弹窗。

用于在简历筛选流水线中发送桌面通知：
- 每批次候选人处理完毕时推送统计摘要
- AI 调用失败时推送错误提示

弹窗特点：
- 固定在屏幕右下角（距离边缘 16px）
- 可点击右上角 ✕ 关闭
- 非阻塞：在独立线程中运行，不影响主流程
- 自动超时消失（默认 30 秒），防止堆积
- 多条通知自动向上堆叠

依赖：仅使用 Python 标准库 tkinter（Windows 自带）
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
from typing import Optional


# 活跃弹窗列表（线程安全：仅在 Tk 线程内访问）
_active_windows: list["_ToastWindow"] = []
_lock = threading.Lock()

# 弹窗尺寸与位置常量
_WIN_WIDTH  = 340
_WIN_HEIGHT = 120   # 动态调整，此为最小高度
_MARGIN     = 16    # 距屏幕边缘
_GAP        = 8     # 弹窗之间的间距


class _ToastWindow:
    """单个右下角弹窗（Toast）。"""

    def __init__(self, title: str, message: str, timeout_s: int = 30) -> None:
        self._timeout_s = timeout_s
        self._root: Optional[tk.Tk] = None
        self._title = title
        self._message = message

    def show(self) -> None:
        """在当前线程中显示弹窗（应在独立线程调用）。"""
        root = tk.Tk()
        self._root = root

        root.overrideredirect(True)          # 无系统标题栏
        root.attributes("-topmost", True)    # 置顶
        root.configure(bg="#2d2d2d")
        root.resizable(False, False)

        # ── 内容布局 ────────────────────────────────────────────────
        frame = tk.Frame(root, bg="#2d2d2d", padx=12, pady=8)
        frame.pack(fill="both", expand=True)

        # 标题行：左侧标题 + 右侧关闭按钮
        title_frame = tk.Frame(frame, bg="#2d2d2d")
        title_frame.pack(fill="x")

        tk.Label(
            title_frame,
            text=self._title,
            bg="#2d2d2d", fg="#ffffff",
            font=("微软雅黑", 10, "bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        tk.Button(
            title_frame,
            text="✕",
            bg="#2d2d2d", fg="#aaaaaa",
            activebackground="#444444", activeforeground="#ffffff",
            font=("Arial", 10),
            bd=0, padx=4, pady=0,
            cursor="hand2",
            command=self._close,
        ).pack(side="right")

        # 分隔线
        tk.Frame(frame, bg="#444444", height=1).pack(fill="x", pady=(4, 6))

        # 消息正文（自动换行）
        tk.Label(
            frame,
            text=self._message,
            bg="#2d2d2d", fg="#dddddd",
            font=("微软雅黑", 9),
            anchor="w", justify="left",
            wraplength=_WIN_WIDTH - 32,
        ).pack(fill="x")

        # ── 计算窗口尺寸与位置 ──────────────────────────────────────
        root.update_idletasks()
        w = _WIN_WIDTH
        h = root.winfo_reqheight()
        h = max(h, _WIN_HEIGHT)

        # 注册到活跃列表并计算 y 偏移（向上堆叠）
        with _lock:
            _active_windows.append(self)
            index = len(_active_windows) - 1

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        x = sw - w - _MARGIN
        y = sh - _MARGIN - sum(
            (_WIN_HEIGHT + _GAP) * i for i in range(1)
        ) - (index * (_WIN_HEIGHT + _GAP)) - h

        root.geometry(f"{w}x{h}+{x}+{y}")
        root.deiconify()

        # ── 自动超时关闭 ────────────────────────────────────────────
        root.after(self._timeout_s * 1000, self._close)
        root.mainloop()

    def _close(self) -> None:
        """关闭弹窗，从活跃列表中移除。"""
        with _lock:
            try:
                _active_windows.remove(self)
            except ValueError:
                pass
        try:
            if self._root:
                self._root.destroy()
        except Exception:
            pass


def notify(
    title: str,
    message: str,
    timeout_s: int = 30,
) -> None:
    """
    在右下角弹出一个可关闭的桌面通知（非阻塞）。

    Args:
        title:     弹窗标题（加粗显示）
        message:   弹窗正文（支持换行）
        timeout_s: 自动消失秒数，默认 30 秒
    """
    def _run() -> None:
        try:
            toast = _ToastWindow(title=title, message=message, timeout_s=timeout_s)
            toast.show()
        except Exception:
            pass  # 通知失败不应影响主流程

    t = threading.Thread(target=_run, daemon=True, name="toast-notify")
    t.start()


def notify_batch_complete(
    platform: str,
    stats: dict,
    batch: int,
) -> None:
    """
    每批次处理完成后推送统计通知。

    Args:
        platform: 平台名称（如"BOSS直聘"、"智联招聘"）
        stats:    统计字典，含 total/passed/rejected_ai/rejected_keyword/skipped/failed
        batch:    当前批次编号
    """
    total        = stats.get("total", 0)
    passed       = stats.get("passed", 0)
    rejected_ai  = stats.get("rejected_ai", 0)
    rejected_kw  = stats.get("rejected_keyword", 0)
    skipped      = stats.get("skipped", 0)
    failed       = stats.get("failed", 0)
    rejected     = rejected_ai + rejected_kw

    lines = [
        f"总数：{total}",
        f"通过：{passed}",
        f"不通过：{rejected}（AI拒绝 {rejected_ai} / 关键词 {rejected_kw}）",
        f"跳过：{skipped}",
        f"失败：{failed}",
    ]
    notify(
        title=f"【{platform}】第 {batch} 批处理完成",
        message="\n".join(lines),
        timeout_s=30,
    )


def notify_all_complete(
    platform: str,
    stats: dict,
) -> None:
    """
    全部候选人处理完成后推送最终统计通知。

    Args:
        platform: 平台名称
        stats:    统计字典
    """
    total        = stats.get("total", 0)
    passed       = stats.get("passed", 0)
    rejected_ai  = stats.get("rejected_ai", 0)
    rejected_kw  = stats.get("rejected_keyword", 0)
    skipped      = stats.get("skipped", 0)
    failed       = stats.get("failed", 0)
    rejected     = rejected_ai + rejected_kw

    lines = [
        f"筛选已全部完成",
        f"总数：{total}",
        f"通过：{passed}",
        f"不通过：{rejected}（AI拒绝 {rejected_ai} / 关键词 {rejected_kw}）",
        f"跳过：{skipped}",
        f"失败：{failed}",
    ]
    notify(
        title=f"【{platform}】筛选完成",
        message="\n".join(lines),
        timeout_s=60,
    )


def notify_ai_failure(
    platform: str,
    candidate_name: str,
    reason: str,
) -> None:
    """
    AI 调用失败时推送错误提示。

    Args:
        platform:       平台名称
        candidate_name: 当前候选人姓名
        reason:         失败原因
    """
    # 截断过长的 reason
    short_reason = reason[:80] + "..." if len(reason) > 80 else reason
    notify(
        title=f"【{platform}】AI 调用失败",
        message=f"候选人：{candidate_name}\n原因：{short_reason}",
        timeout_s=30,
    )


__all__ = [
    "notify",
    "notify_batch_complete",
    "notify_all_complete",
    "notify_ai_failure",
]
