"""智联招聘简历筛适配器。

功能：
- 连接已登录的 Chrome（CDP attach 模式）
- 在「推荐人才」列表页批量获取候选人卡片
- 逐一点击卡片打开简历详情弹窗
- 提取简历信息（姓名、年龄、学历、工作经历等）
- 根据 AI 筛选结果点击「打招呼」或跳过
- 关闭弹窗，继续下一个候选人

DOM 操作策略：
- 优先通过 page.evaluate() 注入 JS 直接操作页面 DOM（与插件 content script 逻辑等价）
- Playwright 负责 CDP 连接、页面导航、等待条件，不做复杂的多步 locator 链
- 关键选择器统一维护在 zhilian_screener_selectors.py ZHILIAN_SCREENER_SELECTORS

参考来源：
- F:/KIKI/代码库/chrome插件/HRchat/workspace/zhaopin-im-automation/zhaopin-resume-screener-skill.js
- F:/KIKI/代码库/chrome插件/HRchat/workspace/zhaopin-im-automation/content-jianlishai-selectors.js
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from playwright.sync_api import Page

from agno1.browser_automation.manager import BrowserManager
from agno1.browser_automation.zhaopin.zhilian.zhilian_screener_selectors import ZHILIAN_SCREENER_SELECTORS


# ---------------------------------------------------------------------------
# 候选人数据模型
# ---------------------------------------------------------------------------

@dataclass
class CandidateInfo:
    """从简历详情弹窗提取的候选人信息。"""
    index: int
    card_id: str                    # data-demo-cid 属性值，唯一标识卡片
    name: str = "未知候选人"
    age: str = ""
    education: str = ""
    work_experience: str = ""
    expected_salary: str = ""
    skills: str = ""
    job_status: str = ""
    full_text: str = ""             # 弹窗完整文本（最多 3000 字），供 AI 分析
    is_fallback: bool = False       # True 表示提取超时，使用了兜底数据


@dataclass
class CardSummary:
    """从卡片列表提取的候选人基础信息（打开弹窗前）。"""
    index: int
    card_id: str
    name: str = "未知候选人"
    age: str = ""
    salary: str = ""
    status: str = ""


@dataclass
class ScreenResult:
    """单个候选人的处理结果。"""
    card_id: str
    name: str
    action: str                         # "greeted" | "rejected_keyword" | "rejected_ai" | "failed" | "skipped"
    reason: str = ""
    ai_result: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# 智联招聘简历筛适配器
# ---------------------------------------------------------------------------

class ZhilianScreenerAdapter:
    """
    智联招聘推荐候选人简历筛适配器。

    使用方式：
        adapter = ZhilianScreenerAdapter(browser=bm)
        # 在已导航到智联招聘推荐人才页面的 Page 上执行
        page = adapter.get_page(session_id="zhilian_screener", url=url)
        cards = adapter.get_candidate_cards(page)
        for card in cards:
            detail = adapter.open_detail_and_extract(page, card)
            # 调用 AI 筛选后：
            if passed:
                adapter.click_greet(page)
            adapter.close_detail(page)
    """

    PLATFORM = "zhilian"

    def __init__(self, *, browser: BrowserManager):
        self._browser = browser
        self._sel = ZHILIAN_SCREENER_SELECTORS

    # ------------------------------------------------------------------
    # 页面准备
    # ------------------------------------------------------------------

    def get_page(self, *, session_id: str, url: Optional[str] = None) -> Page:
        """获取或创建一个与智联招聘关联的 Page。"""
        target_url = url or "https://rd6.zhaopin.com/app/recommend?tab=recommend"
        handle = self._browser.get_or_create_page(
            platform=self.PLATFORM,
            session_id=session_id,
            url=target_url,
        )
        page = handle.page
        if url:
            current = page.url or ""
            if not current.startswith("https://rd6.zhaopin.com"):
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                time.sleep(2.0)
        return page

    # ------------------------------------------------------------------
    # 候选人卡片列表获取
    # ------------------------------------------------------------------

    def inject_early_observer(self, page: Page) -> None:
        """
        注入 MutationObserver，为页面上的候选人卡片预打上 data-demo-cid 标记。
        等价于 zhaopin-resume-screener-skill.js _injectEarlyObserver()。
        """
        page.evaluate("""
        () => {
            if (window.__JIANLISHAI_OBSERVER_INJECTED__) return;
            window.__JIANLISHAI_OBSERVER_INJECTED__ = true;

            const TAG_SELECTOR = 'div.resume-item__content.resume-card-exp';
            let counter = 0;

            const tagCard = (card) => {
                if (!card.hasAttribute('data-demo-cid')) {
                    card.setAttribute('data-demo-cid', `init_candidate_${counter++}`);
                }
            };

            document.querySelectorAll(TAG_SELECTOR).forEach(tagCard);

            const observer = new MutationObserver(() => {
                document.querySelectorAll(TAG_SELECTOR).forEach(tagCard);
            });
            observer.observe(document.body, { childList: true, subtree: true });
            console.log('[zhilian-py] 早期观察器注入完成');
        }
        """)

    def get_candidate_cards(self, page: Page) -> List[CardSummary]:
        """
        获取当前页面上的候选人卡片列表。

        虚拟滚动适配策略：
        - 智联招聘使用虚拟滚动，DOM 中始终只有约 20 个卡片节点（复用），
          滚动时节点内容被替换，节点数量不变。
        - 用 data-index 属性作为业务唯一 id（平台自带连续序号，滚动后值随内容更新），
          不再依赖自定义的 data-demo-cid 去重。
        - data-demo-cid 只用于本次处理周期内通过 card_id 定位 DOM 元素（点击/滚动），
          每次调用时重新打标，不跨批次保留。

        Returns:
            按 data-index（虚拟列表行号）排序的候选人列表。
        """
        time.sleep(0.3)

        raw: List[Dict[str, Any]] = page.evaluate("""
        () => {
            // 优先：data-index 属性（虚拟滚动列表行号，平台自带）
            let cards = Array.from(document.querySelectorAll('[data-index].recommend-item'));

            // 降级1：原始 class 选择器（兼容旧版）
            if (cards.length === 0) {
                cards = Array.from(document.querySelectorAll('div.resume-item__content.resume-card-exp'));
            }

            // 降级2：结构兜底
            if (cards.length === 0) {
                cards = Array.from(document.querySelectorAll('[data-index]')).filter(el =>
                    el.querySelector('div[class*="talent-basic-info__name--inner"]')
                );
            }

            if (cards.length === 0) {
                console.warn('[zhilian-py] 未找到任何候选人卡片');
                return [];
            }
            console.log('[zhilian-py] 卡片数量:', cards.length);

            // 重新打 data-demo-cid（本次处理周期内用于 DOM 定位，旧值直接覆盖）
            const result = [];
            cards.forEach((card) => {
                const dataIndex = card.getAttribute('data-index');
                // card_id 使用 data-index 作为唯一业务标识（虚拟滚动下最稳定）
                const card_id = dataIndex !== null ? `vidx_${dataIndex}` : null;
                if (card_id === null) return;  // 没有 data-index 的节点跳过

                // 打 data-demo-cid（供 scroll_to_card / open_candidate_detail 定位用）
                card.setAttribute('data-demo-cid', card_id);

                const nameEl  = card.querySelector('div[class*="talent-basic-info__name--inner"]');
                const ageEl   = card.querySelector('span[class*="age-label"]');
                const salEl   = card.querySelector('span[class*="desired-salary"]');
                const statEl  = card.querySelector('span[class*="career-status-label"]');

                result.push({
                    card_id,
                    index:  parseInt(dataIndex, 10),
                    name:   nameEl?.textContent.trim()  || '未知候选人',
                    age:    ageEl?.textContent.trim()   || '',
                    salary: salEl?.textContent.trim()   || '',
                    status: statEl?.textContent.trim()  || '',
                });
            });

            // 按 data-index 排序（即虚拟列表中的真实顺序）
            result.sort((a, b) => a.index - b.index);
            console.log(`[zhilian-py] 当前窗口内候选人 index 范围: ${result[0]?.index} ~ ${result[result.length-1]?.index}`);
            return result;
        }
        """)

        return [
            CardSummary(
                index=r["index"],
                card_id=r["card_id"],
                name=r.get("name", "未知候选人"),
                age=r.get("age", ""),
                salary=r.get("salary", ""),
                status=r.get("status", ""),
            )
            for r in (raw or [])
        ]

    def scroll_and_get_new_cards(
        self,
        page: Page,
        processed_ids: set,
    ) -> List[CardSummary]:
        """
        虚拟滚动适配版：滚动列表，等待新 data-index 的卡片出现在窗口内。

        虚拟滚动下 DOM 卡片节点数量不变，判断新卡片的唯一依据是
        当前窗口内出现了 processed_ids 中没有的 card_id（vidx_N）。

        Args:
            page:          当前 Playwright Page
            processed_ids: 已处理的 card_id 集合（格式 "vidx_N"）

        Returns:
            新增的未处理卡片列表；若无新卡片则返回空列表（到底）。
        """
        def _get_visible_indices() -> set:
            """返回当前窗口内所有卡片的 card_id 集合（vidx_N 格式）。"""
            ids: List[str] = page.evaluate("""
            () => Array.from(document.querySelectorAll('[data-index].recommend-item'))
                       .map(el => 'vidx_' + el.getAttribute('data-index'))
                       .filter(Boolean)
            """)
            return set(ids)

        def _do_scroll() -> None:
            """模拟鼠标滚轮向下滚动，触发虚拟滚动加载下一批。"""
            ch: int = page.evaluate("() => document.documentElement.clientHeight")
            # 鼠标移到页面中央，分步滚动（虚拟滚动必须用真实 wheel 事件）
            page.mouse.move(ch // 2, ch // 2)
            for _ in range(12):
                page.mouse.wheel(0, 600)
                time.sleep(0.12)

        for attempt in range(3):
            _do_scroll()

            # 等待窗口内出现 processed_ids 之外的新 card_id（最多等 8 秒）
            for _ in range(8):
                time.sleep(1.0)
                cur_ids = _get_visible_indices()
                if cur_ids - processed_ids:
                    time.sleep(0.3)
                    break

            all_cards = self.get_candidate_cards(page)
            new_cards = [c for c in all_cards if c.card_id not in processed_ids]

            if new_cards:
                return new_cards

        return []

    # ------------------------------------------------------------------
    # 候选人详情操作
    # ------------------------------------------------------------------

    def scroll_to_card(self, page: Page, card: CardSummary) -> None:
        """滚动到指定候选人卡片，确保其在视口内。"""
        page.evaluate(
            """
            (cid) => {
                const el = document.querySelector(`[data-demo-cid="${cid}"]`);
                if (!el) return;
                const rect = el.getBoundingClientRect();
                const margin = 100;
                const inViewport = rect.top >= margin && rect.bottom <= window.innerHeight - margin;
                if (!inViewport) {
                    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
            }
            """,
            card.card_id,
        )
        time.sleep(0.8)

    def open_candidate_detail(self, page: Page, card: CardSummary) -> None:
        """
        点击候选人卡片，打开简历详情弹窗。
        优先点击姓名元素；降级到模拟完整鼠标事件序列。
        等价于 zhaopin-resume-screener-skill.js _openCandidateDetail()。
        """
        success: bool = page.evaluate(
            """
            ({cid, name}) => {
                const targets = document.querySelectorAll(`[data-demo-cid="${cid}"]`);
                if (targets.length === 0) {
                    console.error(`[zhilian-py] 未找到卡片: ${name}`);
                    return false;
                }

                let target = targets[0];
                if (targets.length > 1) {
                    const visible = Array.from(targets).filter(el => {
                        const r = el.getBoundingClientRect();
                        return r.top >= 0 && r.top <= window.innerHeight;
                    });
                    if (visible.length > 0) target = visible[0];
                }

                const nameEl = target.querySelector('div[class*="talent-basic-info__name--inner"]');
                const clickTarget = nameEl || target;

                try {
                    clickTarget.click();
                    console.log(`[zhilian-py] 点击候选人: ${name}`);
                    return true;
                } catch (e) {
                    const rect = clickTarget.getBoundingClientRect();
                    const cx = rect.left + rect.width / 2;
                    const cy = rect.top + rect.height / 2;
                    ['mousedown', 'mouseup', 'click'].forEach((type, i) => {
                        setTimeout(() => {
                            clickTarget.dispatchEvent(new MouseEvent(type, {
                                bubbles: true, cancelable: true,
                                view: window, clientX: cx, clientY: cy,
                                button: 0, buttons: 1
                            }));
                        }, i * 80);
                    });
                    console.log(`[zhilian-py] 增强点击: ${name}`);
                    return true;
                }
            }
            """,
            {"cid": card.card_id, "name": card.name},
        )
        if not success:
            raise RuntimeError(f"[zhilian] 未找到候选人卡片: {card.name} (id={card.card_id})")

    def wait_for_greet_button(self, page: Page, timeout_ms: int = 10_000) -> bool:
        """
        等待简历详情弹窗内的「打招呼」按钮出现。
        等价于 zhaopin-resume-screener-skill.js _waitForGreetButton()。
        """
        try:
            result: bool = page.evaluate(
                """
                async (ms) => {
                    const xpath = "//div[contains(@class,'resume-btn__text')][contains(.,'打招呼')]/ancestor::button | //button[contains(.,'打招呼')]";
                    const start = Date.now();

                    return new Promise((resolve) => {
                        const check = () => {
                            if (Date.now() - start >= ms) { resolve(false); return; }
                            try {
                                const btn = document.evaluate(
                                    xpath, document, null,
                                    XPathResult.FIRST_ORDERED_NODE_TYPE, null
                                ).singleNodeValue;
                                if (btn) { resolve(true); }
                                else { setTimeout(check, 500); }
                            } catch (e) { setTimeout(check, 500); }
                        };
                        check();
                    });
                }
                """,
                timeout_ms,
            )
            return bool(result)
        except Exception:
            return False

    def extract_resume_info(
        self, page: Page, last_captured_name: Optional[str] = None
    ) -> CandidateInfo:
        """
        从当前可见的简历详情弹窗提取候选人信息。
        含差异检测：确保提取到的是新弹窗而非旧弹窗。
        等价于 zhaopin-resume-screener-skill.js _extractResumeInfo()。
        """
        raw: Dict[str, Any] = page.evaluate(
            """
            async (lastName) => {
                return new Promise((resolve) => {
                    let attempts = 0;
                    const max = 15;  // 15 × 200ms = 3 秒超时

                    const tryExtract = () => {
                        attempts++;
                        const modals = Array.from(document.querySelectorAll('[class*="resume-detail"]'));
                        const modal = modals.find(el => el.offsetParent !== null);

                        if (!modal) {
                            if (attempts >= max) resolve(fallback());
                            else setTimeout(tryExtract, 200);
                            return;
                        }

                        const nameSelectors = [
                            'div[class*="talent-basic-info__name--inner"]',
                            '.name', '[class*="name"]', 'h1', 'h2'
                        ];
                        let currentName = '';
                        for (const sel of nameSelectors) {
                            const el = modal.querySelector(sel);
                            if (el?.textContent.trim()) {
                                currentName = el.textContent.trim();
                                break;
                            }
                        }

                        const isFirst = !lastName;
                        const changed = lastName && currentName && currentName !== lastName;

                        if ((currentName && (isFirst || changed)) || attempts >= max) {
                            if (attempts >= max) console.warn('[zhilian-py] 提取超时，返回兜底数据');
                            resolve(extract(modal, currentName));
                        } else {
                            setTimeout(tryExtract, 200);
                        }
                    };

                    tryExtract();

                    function extract(modal, name) {
                        const text = (sel) => modal.querySelector(sel)?.textContent.trim() || '';
                        return {
                            name,
                            age:            textFromPattern(modal, /(\\d+)岁/),
                            education:      text('[class*="education"]') || text('.edu-background'),
                            work_experience: text('[class*="experience"]') || text('.work-experience'),
                            expected_salary: text('[class*="salary"]'),
                            skills:         text('[class*="skill"]') || text('.tech-tags'),
                            job_status:     text('[class*="status"]'),
                            full_text:      (modal.innerText || '').substring(0, 3000),
                            is_fallback:    false,
                        };
                    }

                    function textFromPattern(modal, pattern) {
                        const items = modal.querySelectorAll(
                            '.resume-basic-new__meta-item, [class*="meta-item"], [class*="info-item"]'
                        );
                        for (const item of items) {
                            const m = item.textContent.match(pattern);
                            if (m) return m[0];
                        }
                        return '';
                    }

                    function fallback() {
                        return {
                            name: '未知候选人', age: '', education: '',
                            work_experience: '', expected_salary: '',
                            skills: '', job_status: '', full_text: '',
                            is_fallback: true
                        };
                    }
                });
            }
            """,
            last_captured_name,
        )

        return CandidateInfo(
            index=0,        # 调用方负责填充
            card_id="",     # 调用方负责填充
            name=raw.get("name", "未知候选人"),
            age=raw.get("age", ""),
            education=raw.get("education", ""),
            work_experience=raw.get("work_experience", ""),
            expected_salary=raw.get("expected_salary", ""),
            skills=raw.get("skills", ""),
            job_status=raw.get("job_status", ""),
            full_text=raw.get("full_text", ""),
            is_fallback=bool(raw.get("is_fallback", False)),
        )

    def click_greet_button(self, page: Page) -> None:
        """
        在当前简历详情弹窗内点击「打招呼」按钮。
        使用 XPath 精准定位，避免误触「打电话」。
        等价于 zhaopin-resume-screener-skill.js _clickGreetButton()。
        """
        page.evaluate("""
        () => {
            const modalSelectors = [
                '[class*="resume-detail"]', '.km-dialog__wrapper',
                '.km-overlay', '.modal-wrapper', '.candidate-detail', '.detail-modal'
            ];
            let modal = null;
            for (const sel of modalSelectors) {
                const candidates = document.querySelectorAll(sel);
                modal = Array.from(candidates).find(el => el.offsetParent !== null);
                if (modal) break;
            }

            if (!modal) throw new Error('未找到可见的简历弹窗');

            // XPath 精准定位「打招呼」（避免误触「打电话」）
            let btn = null;
            try {
                const node = document.evaluate(
                    ".//div[contains(text(),'打招呼')]",
                    modal, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null
                ).singleNodeValue;
                if (node) {
                    btn = node.closest('.km-ripple') ||
                          node.closest('button') ||
                          node.parentElement?.parentElement;
                }
            } catch (e) {}

            // 降级：遍历所有按钮
            if (!btn) {
                const allBtns = modal.querySelectorAll('button');
                btn = Array.from(allBtns).find(b =>
                    b.textContent.includes('打招呼') && !b.textContent.includes('打电话')
                );
            }

            if (!btn) throw new Error('未找到打招呼按钮');
            if (btn.textContent?.includes('打电话')) throw new Error('定位到的是「打电话」按钮，已中止');

            btn.scrollIntoView({ behavior: 'smooth', block: 'center' });
            setTimeout(() => {
                btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                btn.click();
                console.log('[zhilian-py] 打招呼按钮点击成功');
            }, 500);
        }
        """)
        time.sleep(2.0)     # 等待打招呼动作完成（网络请求）

    def close_detail(self, page: Page) -> None:
        """
        关闭简历详情弹窗。
        策略：ESC 键 → 关闭按钮 → 遮罩层点击。
        等价于 zhaopin-resume-screener-skill.js _closeDetail()。
        """
        page.evaluate("""
        () => {
            // 方法 1：ESC 键
            const esc = new KeyboardEvent('keydown', {
                key: 'Escape', keyCode: 27, code: 'Escape',
                bubbles: true, cancelable: true
            });
            document.dispatchEvent(esc);
            if (document.activeElement && document.activeElement !== document.body) {
                document.activeElement.dispatchEvent(esc);
            }

            // 方法 2（延迟兜底）：点击关闭按钮
            setTimeout(() => {
                const closeSelectors = [
                    'div.new-shortcut-resume__close',
                    '.close-btn', '.modal-close',
                    '[data-spm*="close"]', 'button[class*="close"]',
                    '.close-icon', '[class*="close"]'
                ];
                for (const sel of closeSelectors) {
                    const btn = document.querySelector(sel);
                    if (btn) { btn.click(); return; }
                }

                // 方法 3：点击遮罩层
                const backdrop = document.querySelector('.modal-backdrop, .modal-overlay, [class*="backdrop"]');
                if (backdrop) backdrop.click();
            }, 500);
        }
        """)
        time.sleep(0.8)     # 等待关闭动画

    # ------------------------------------------------------------------
    # 完整单候选人处理流程（供 pipeline 调用）
    # ------------------------------------------------------------------

    def open_detail_and_extract(
        self,
        page: Page,
        card: CardSummary,
        last_captured_name: Optional[str] = None,
        greet_wait_timeout_ms: int = 10_000,
    ) -> CandidateInfo:
        """
        点击卡片 → 等待弹窗 → 提取简历信息。
        不执行打招呼，由 pipeline 决定是否打招呼。

        Raises:
            RuntimeError: 卡片未找到或打招呼按钮等待超时。
        """
        self.scroll_to_card(page, card)
        self.open_candidate_detail(page, card)

        btn_found = self.wait_for_greet_button(page, timeout_ms=greet_wait_timeout_ms)
        if not btn_found:
            raise RuntimeError(f"[zhilian] 打招呼按钮未出现（{card.name}），弹窗可能未正常打开")

        info = self.extract_resume_info(page, last_captured_name=last_captured_name)
        info.index = card.index
        info.card_id = card.card_id
        return info


# 向后兼容别名
ZhilianResumeAdapter = ZhilianScreenerAdapter


__all__ = [
    "ZhilianScreenerAdapter",
    "ZhilianResumeAdapter",
    "CandidateInfo",
    "CardSummary",
    "ScreenResult",
]
