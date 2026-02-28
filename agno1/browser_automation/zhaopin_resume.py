"""智联招聘简历筛适配器。

功能：
- 在推荐候选人列表页（rd6.zhaopin.com）逐张点击简历卡片
- 打开候选人详情、提取简历信息（学历/工作经历/基本信息）
- 执行"打招呼"动作
- 关闭详情，移至下一张卡片

参考来源：
- F:/KIKI/代码库/chrome插件/HRchat/workspace/zhaopin-im-automation/content-jianlishai.js
  ZhaopinAdapter 类（getCandidateCards / extractCandidateInfo / openCandidateDetail / extractResumeInfo）
- content-jianlishai-selectors.js（JIANLISHAI_SELECTORS）

设计原则：
- 不依赖 Chrome 扩展；通过 CDP attach 到已登录的 Chrome 直接操作页面 DOM
- 选择器统一维护在 selectors.py 的 ZHAOPIN_RESUME_SELECTORS
- 仅封装页面级操作，业务逻辑（AI 筛选/结果记录）由 pipeline 层负责
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from playwright.sync_api import Page

from .manager import BrowserManager
from .selectors import ZHAOPIN_RESUME_SELECTORS
from .utils import first_match_locator

# 默认等待时长（秒）
_DEFAULT_DETAIL_TIMEOUT_S = 8.0
_DEFAULT_AFTER_CLICK_S = 1.5
_DEFAULT_AFTER_CLOSE_S = 0.8

# 敏感词：遇到这些词跳过该候选人（付费/VIP 限制）
_SENSITIVE_WORDS = ["金币", "付费", "道具", "¥", "元", "收费", "VIP", "会员"]


@dataclass
class CandidateInfo:
    """单个候选人的基本标识信息（从列表页卡片提取）。"""
    id: str                           # 唯一标识（优先 title 属性，回退随机串）
    name: str                         # 候选人姓名
    card_index: int                   # 在当前列表中的序号（0-based）
    platform: str = "zhaopin"
    timestamp: float = field(default_factory=time.time)


@dataclass
class ResumeInfo:
    """从详情页提取的简历信息。"""
    name: str = ""
    age: str = ""
    education: str = ""               # 最高学历
    work_years: str = ""              # 工作年限
    current_status: str = ""          # 在职/离职状态
    city: str = ""                    # 所在城市
    target_position: str = ""         # 期望职位

    # 详细经历（原始文本，供 AI 分析）
    work_experience_text: str = ""    # 工作经历
    education_detail_text: str = ""   # 教育经历详情

    # 是否命中付费限制
    has_sensitive_content: bool = False
    sensitive_reason: str = ""

    # 原始全文（降级用）
    raw_text: str = ""


class ZhaopinResumeAdapter:
    """智联招聘推荐候选人列表页的 Playwright 操作适配器。

    典型用法（由 pipeline 层调用）：
        adapter = ZhaopinResumeAdapter(browser=manager)
        cards = adapter.get_candidate_cards(page)
        for i, card_info in enumerate(cards):
            success = adapter.open_candidate_detail(page, card_info)
            if not success:
                continue
            resume = adapter.extract_resume_info(page)
            if resume.has_sensitive_content:
                adapter.close_detail(page)
                continue
            # ... AI 筛选 ...
            adapter.click_greet_button(page)
            adapter.close_detail(page)
    """

    def __init__(self, *, browser: BrowserManager):
        self.browser = browser
        self.sel = ZHAOPIN_RESUME_SELECTORS

    # ------------------------------------------------------------------
    # 列表页操作
    # ------------------------------------------------------------------

    def get_candidate_cards(self, page: Page) -> List[CandidateInfo]:
        """获取当前页面的所有候选人卡片，返回 CandidateInfo 列表。

        对应插件 ZhaopinAdapter.getCandidateCards() + extractCandidateInfo()
        """
        results: List[CandidateInfo] = []

        # 依次尝试各候选选择器
        card_selectors = self.sel.assistant_message_blocks + self.sel.assistant_message_blocks_fallback
        cards_locator = None
        for sel in card_selectors:
            try:
                loc = page.locator(sel)
                count = loc.count()
                if count > 0:
                    cards_locator = loc
                    print(f"[智联简历筛] 找到 {count} 张候选人卡片 (selector: {sel})")
                    break
            except Exception:
                continue

        if cards_locator is None:
            print("[智联简历筛] 未找到任何候选人卡片，请确认当前页面是推荐候选人列表页")
            return results

        total = cards_locator.count()
        for i in range(total):
            try:
                card = cards_locator.nth(i)

                # 提取姓名（点击姓名元素跳转详情）
                name_el = None
                for name_sel in self.sel.prompt_box:
                    try:
                        el = card.locator(name_sel).first
                        if el.count() > 0:
                            name_el = el
                            break
                    except Exception:
                        continue

                name = ""
                card_id = ""
                if name_el:
                    try:
                        name = (name_el.inner_text() or "").strip()
                        card_id = name_el.get_attribute("title") or ""
                    except Exception:
                        pass

                if not name:
                    name = f"候选人_{i}"
                if not card_id:
                    card_id = f"card_{i}_{int(time.time() * 1000)}"

                results.append(CandidateInfo(
                    id=card_id,
                    name=name,
                    card_index=i,
                ))
            except Exception as e:
                print(f"[智联简历筛] 提取第 {i} 张卡片信息失败: {e}")
                continue

        return results

    def open_candidate_detail(
        self,
        page: Page,
        candidate: CandidateInfo,
        *,
        timeout_s: float = _DEFAULT_DETAIL_TIMEOUT_S,
    ) -> bool:
        """点击候选人姓名，等待详情弹窗/页面出现。

        对应插件 ZhaopinAdapter.openCandidateDetail()
        返回 True 表示详情已加载，False 表示超时/失败。
        """
        # 重新定位卡片（避免 DOM 刷新导致引用失效）
        card_selectors = self.sel.assistant_message_blocks + self.sel.assistant_message_blocks_fallback
        card_el = None
        for sel in card_selectors:
            try:
                cards = page.locator(sel)
                if cards.count() > candidate.card_index:
                    card_el = cards.nth(candidate.card_index)
                    break
            except Exception:
                continue

        if card_el is None:
            print(f"[智联简历筛] 无法重新定位卡片 index={candidate.card_index}")
            return False

        # 找到并点击姓名元素
        name_el = None
        for name_sel in self.sel.prompt_box:
            try:
                el = card_el.locator(name_sel).first
                if el.count() > 0:
                    name_el = el
                    break
            except Exception:
                continue

        if name_el is None:
            print(f"[智联简历筛] 未找到候选人 {candidate.name} 的姓名元素，跳过")
            return False

        try:
            name_el.click()
            print(f"[智联简历筛] 已点击候选人: {candidate.name}")
        except Exception as e:
            print(f"[智联简历筛] 点击姓名元素失败: {e}")
            return False

        # 等待详情弹窗出现
        return self._wait_for_detail(page, timeout_s=timeout_s)

    def _wait_for_detail(self, page: Page, *, timeout_s: float) -> bool:
        """轮询等待详情弹窗出现。对应插件 waitForDetailModal()"""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            for sel in self.sel.stop_button:  # stop_button 复用为详情弹窗选择器
                try:
                    el = page.locator(sel).first
                    if el.count() > 0 and el.is_visible():
                        print("[智联简历筛] 详情弹窗已加载")
                        time.sleep(_DEFAULT_AFTER_CLICK_S)
                        return True
                except Exception:
                    continue
            time.sleep(0.4)
        print("[智联简历筛] 等待详情弹窗超时")
        return False

    # ------------------------------------------------------------------
    # 详情页操作
    # ------------------------------------------------------------------

    def extract_resume_info(self, page: Page) -> ResumeInfo:
        """从已打开的详情弹窗/页中提取简历信息。

        对应插件 ZhaopinAdapter.extractResumeInfo()
        """
        info = ResumeInfo()

        # 检查是否有付费/敏感内容
        for sel in self.sel.artifact_candidates:
            try:
                el = page.locator(sel).first
                if el.count() > 0 and el.is_visible():
                    text = (el.inner_text() or "").strip()
                    for word in _SENSITIVE_WORDS:
                        if word in text:
                            info.has_sensitive_content = True
                            info.sensitive_reason = f"检测到敏感内容: {word}"
                            return info
            except Exception:
                continue

        # 基本信息字段
        _basic_selectors: Dict[str, List[str]] = {
            "name":            ['div[class*="talent-basic-info__name--inner"]', 'div[class*="resume-name"]'],
            "age":             ['span[class*="age-label"]', 'span[class^="age-"]'],
            "education":       ['span[class*="education-label"]', 'span[class^="education-"]'],
            "work_years":      ['span[class*="work-exp-label"]', 'span[class^="work-exp-"]'],
            "current_status":  ['span[class*="work-status-label"]', 'span[class^="work-status-"]'],
            "city":            ['span[class*="city-label"]', 'span[class^="city-"]'],
            "target_position": ['span[class*="expect-job-label"]', 'span[class^="expect-job-"]'],
        }

        for field_name, selectors in _basic_selectors.items():
            for sel in selectors:
                try:
                    el = page.locator(sel).first
                    if el.count() > 0:
                        text = (el.inner_text() or "").strip()
                        if text:
                            setattr(info, field_name, text)
                            break
                except Exception:
                    continue

        # 工作经历（取全文）
        work_texts: List[str] = []
        for sel in self.sel.user_message_blocks:
            try:
                els = page.locator(sel).all()
                for el in els:
                    t = (el.inner_text() or "").strip()
                    if t:
                        work_texts.append(t)
                if work_texts:
                    break
            except Exception:
                continue
        info.work_experience_text = "\n".join(work_texts)

        # 教育经历（取全文）
        edu_texts: List[str] = []
        for sel in self.sel.copy_button:  # copy_button 复用为教育经历选择器
            try:
                els = page.locator(sel).all()
                for el in els:
                    t = (el.inner_text() or "").strip()
                    if t:
                        edu_texts.append(t)
                if edu_texts:
                    break
            except Exception:
                continue
        info.education_detail_text = "\n".join(edu_texts)

        # 降级：提取页面全文
        if not info.work_experience_text and not info.education_detail_text:
            for sel in self.sel.assistant_text_blocks:
                try:
                    el = page.locator(sel).first
                    if el.count() > 0:
                        info.raw_text = (el.inner_text() or "").strip()
                        break
                except Exception:
                    continue

        return info

    def click_greet_button(
        self,
        page: Page,
        *,
        timeout_s: float = 5.0,
    ) -> bool:
        """点击详情页中的"打招呼"按钮。

        对应插件 ZhaopinAdapter 中 greet_button_xpath 的点击逻辑。
        返回 True 表示成功点击，False 表示未找到按钮。
        """
        greet_btn = first_match_locator(
            page,
            self.sel.send_button,
            must_be_visible=True,
            timeout_ms_each=int(timeout_s * 1000 / max(len(self.sel.send_button), 1)),
        )
        if greet_btn is None:
            print("[智联简历筛] 未找到"打招呼"按钮（可能已打过招呼或按钮选择器失效）")
            return False

        try:
            greet_btn.click()
            print("[智联简历筛] 已点击"打招呼"按钮")
            time.sleep(1.0)
            return True
        except Exception as e:
            print(f"[智联简历筛] 点击"打招呼"按钮失败: {e}")
            return False

    def close_detail(self, page: Page) -> None:
        """关闭候选人详情弹窗。

        对应插件 closeDetail()
        """
        close_btn = first_match_locator(
            page,
            self.sel.regenerate_button,
            must_be_visible=True,
            timeout_ms_each=800,
        )
        if close_btn:
            try:
                close_btn.click()
                print("[智联简历筛] 已关闭详情弹窗")
                time.sleep(_DEFAULT_AFTER_CLOSE_S)
                return
            except Exception as e:
                print(f"[智联简历筛] 点击关闭按钮失败: {e}，尝试按 Escape")

        # 备用：按 Escape 键关闭
        try:
            page.keyboard.press("Escape")
            time.sleep(_DEFAULT_AFTER_CLOSE_S)
        except Exception as e:
            print(f"[智联简历筛] Escape 关闭失败: {e}")

    def go_to_next_page(self, page: Page) -> bool:
        """点击"下一页"按钮翻页。返回 True 表示成功触发翻页。"""
        next_btn = first_match_locator(
            page,
            self.sel.retry_button,
            must_be_visible=True,
            timeout_ms_each=1000,
        )
        if next_btn is None:
            print("[智联简历筛] 未找到下一页按钮，可能已是最后一页")
            return False

        try:
            next_btn.click()
            print("[智联简历筛] 已点击下一页")
            time.sleep(2.0)  # 等待列表刷新
            return True
        except Exception as e:
            print(f"[智联简历筛] 点击下一页失败: {e}")
            return False


def build_resume_text(resume: ResumeInfo) -> str:
    """将 ResumeInfo 格式化为供 AI 分析的纯文本。"""
    parts: List[str] = []

    if resume.name:
        parts.append(f"姓名：{resume.name}")
    if resume.age:
        parts.append(f"年龄：{resume.age}")
    if resume.education:
        parts.append(f"学历：{resume.education}")
    if resume.work_years:
        parts.append(f"工作年限：{resume.work_years}")
    if resume.current_status:
        parts.append(f"当前状态：{resume.current_status}")
    if resume.city:
        parts.append(f"城市：{resume.city}")
    if resume.target_position:
        parts.append(f"期望职位：{resume.target_position}")

    if resume.education_detail_text:
        parts.append(f"\n【教育经历】\n{resume.education_detail_text}")
    if resume.work_experience_text:
        parts.append(f"\n【工作经历】\n{resume.work_experience_text}")
    if resume.raw_text and not resume.education_detail_text and not resume.work_experience_text:
        parts.append(f"\n【简历全文】\n{resume.raw_text}")

    return "\n".join(parts)


__all__ = [
    "ZhaopinResumeAdapter",
    "CandidateInfo",
    "ResumeInfo",
    "build_resume_text",
]
