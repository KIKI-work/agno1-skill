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
    top: float = 0.0



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
        """获取当前可见候选人卡片（重构版）。"""
        time.sleep(0.2)

        raw: List[Dict[str, Any]] = page.evaluate("""
        () => {
            const selectors = [
                '[data-index].recommend-item',
                '.recommend-item',
                'div.resume-item__content.resume-card-exp',
                'div[class*="recommend-item__inner-content"]',
                'div[class*="resume-item__content"]',
            ];

            const isVisible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                return true;
            };

            const uniq = new Set();
            const cards = [];
            for (const sel of selectors) {
                for (const node of Array.from(document.querySelectorAll(sel))) {
                    const card = node.closest('.recommend-item') || node;
                    if (uniq.has(card)) continue;
                    uniq.add(card);
                    if (!isVisible(card)) continue;
                    if (!card.querySelector('div[class*="talent-basic-info__name--inner"]')) continue;
                    cards.push(card);
                }
            }

            if (cards.length === 0) return [];

            const result = cards.map((card, ordinal) => {
                const dataIndex = card.getAttribute('data-index') || card.closest('[data-index]')?.getAttribute('data-index') || '';

                const nameEl  = card.querySelector('div[class*="talent-basic-info__name--inner"]');
                const ageEl   = card.querySelector('span[class*="age-label"]');
                const salEl   = card.querySelector('span[class*="desired-salary"]');
                const statEl  = card.querySelector('span[class*="career-status-label"]');

                const name   = nameEl?.textContent?.trim() || '未知候选人';
                const age    = ageEl?.textContent?.trim() || '';
                const salary = salEl?.textContent?.trim() || '';
                const status = statEl?.textContent?.trim() || '';

                const rect = card.getBoundingClientRect();
                const top = rect.top + (window.pageYOffset || document.documentElement.scrollTop || 0);

                const sigRaw = `${name}|${age}|${salary}|${status}`;
                const sig = encodeURIComponent(sigRaw).replace(/%/g, '').slice(0, 40) || `ord_${ordinal}`;

                const parsedIndex = dataIndex !== '' ? parseInt(dataIndex, 10) : Number.NaN;
                const index = Number.isFinite(parsedIndex) ? parsedIndex : Math.round(top);
                const card_id = Number.isFinite(parsedIndex)
                    ? `vidx_${dataIndex}_${sig}`
                    : `vpos_${Math.round(top)}_${ordinal}_${sig}`;

                card.setAttribute('data-demo-cid', card_id);

                return { card_id, index, name, age, salary, status, top };
            });

            result.sort((a, b) => a.top - b.top);
            return result;
        }
        """)

        return [
            CardSummary(
                index=int(r.get("index", 0)),
                card_id=r.get("card_id", ""),
                name=r.get("name", "未知候选人"),
                age=r.get("age", ""),
                salary=r.get("salary", ""),
                status=r.get("status", ""),
                top=float(r.get("top", 0.0)),
            )
            for r in (raw or [])
            if r.get("card_id")
        ]


    def is_list_bottom(self, page: Page) -> bool:
        """
        检测智联招聘推荐列表是否已到物理尽头。

        唯一可靠信号：页面出现可见的
        <div class="recommend-indicator">已经到底啦～</div>

        Returns:
            True 表示确实到底，False 表示尚未到底。
        """
        return bool(page.evaluate("""
        () => {
            const els = document.querySelectorAll('.recommend-indicator');
            return Array.from(els).some(
                el => el.offsetParent !== null && el.textContent.includes('已经到底啦')
            );
        }
        """))

    def step_scroll(self, page: Page) -> None:
        """向下滚动一步：优先列表容器，降级 window。"""
        page.evaluate("""
        () => {
            const containers = [
                document.querySelector('div[class*="recommend-list"]'),
                document.querySelector('div[class*="candidate-list"]'),
                document.querySelector('.km-scrollbar__wrap'),
                document.querySelector('[role="list"]'),
            ].filter(Boolean);

            const scroller = containers.find(el => (el.scrollHeight - el.clientHeight) > 20);
            if (scroller) {
                const step = Math.max(220, Math.floor(scroller.clientHeight * 0.6));
                scroller.scrollBy({ top: step, left: 0, behavior: 'smooth' });
                return;
            }

            window.scrollBy({ top: 220, left: 0, behavior: 'smooth' });
        }
        """)

    def try_go_next_page(self, page: Page) -> bool:
        """尝试点击“下一页”按钮。"""
        return bool(page.evaluate("""
        () => {
            const candidates = [
                ...Array.from(document.querySelectorAll('button')),
                ...Array.from(document.querySelectorAll('[role="button"]')),
                ...Array.from(document.querySelectorAll('a')),
                ...Array.from(document.querySelectorAll('[aria-label*="下一页"]')),
            ];

            const btn = candidates.find(el => {
                const txt = (el.textContent || '').trim();
                const visible = el.offsetParent !== null;
                const cls = String(el.className || '');
                const disabled = el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true' || cls.includes('disabled');
                return visible && !disabled && (txt === '下一页' || txt.includes('下一页') || txt.includes('下页'));
            });

            if (!btn) return false;
            btn.scrollIntoView({ behavior: 'smooth', block: 'center' });
            btn.click();
            return true;
        }
        """))

    def scroll_and_get_new_cards(self, page: Page, processed_ids: set[str]) -> List[CardSummary]:
        """滚动获取新卡；无新卡时尝试翻页。"""
        for _ in range(6):
            self.step_scroll(page)
            time.sleep(0.9)
            all_cards = self.get_candidate_cards(page)
            new_cards = [c for c in all_cards if c.card_id not in processed_ids]
            if new_cards:
                return new_cards

        if self.try_go_next_page(page):
            time.sleep(2.0)
            all_cards = self.get_candidate_cards(page)
            return [c for c in all_cards if c.card_id not in processed_ids]

        return []


    # ------------------------------------------------------------------
    # 候选人详情操作
    # ------------------------------------------------------------------

    def scroll_to_card(self, page: Page, card: CardSummary) -> None:
        """
        滚动到指定候选人卡片，确保其在视口内，并重新打 data-demo-cid 标记。

        虚拟列表滚动后 DOM 节点被复用，旧的 data-demo-cid 会丢失。
        因此优先通过 data-index 定位，滚动到位后再刷新 data-demo-cid，
        保证后续 open_candidate_detail 能正确找到目标卡片。
        """
        page.evaluate(
            """
            ({dataIndex, cardId}) => {
                // 优先通过 data-index 精确定位（虚拟列表最稳定的属性）
                let el = document.querySelector(`[data-index="${dataIndex}"].recommend-item`);
                // 降级：通过 data-demo-cid（当前批次首次打标后仍有效时）
                if (!el) el = document.querySelector(`[data-demo-cid="${cardId}"]`);
                if (!el) return;

                const rect = el.getBoundingClientRect();
                const margin = 100;
                const inViewport = rect.top >= margin && rect.bottom <= window.innerHeight - margin;
                if (!inViewport) {
                    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
                // 重新打 data-demo-cid，确保 open_candidate_detail 能定位到
                el.setAttribute('data-demo-cid', cardId);
            }
            """,
            {"dataIndex": card.index, "cardId": card.card_id},
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
        """点击当前详情弹窗中的「打招呼」按钮。"""
        page.evaluate("""
        () => {
            const isVisible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
            };

            const isDisabled = (el) => {
                if (!el) return true;
                const cls = String(el.className || '');
                return el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true' || cls.includes('disabled');
            };

            const textOf = (el) => (el?.textContent || '').replace(/\\s+/g, ' ').trim();

            const modalSelectors = [
                '[class*="resume-detail"]',
                'div[class*="resume-detail-container"]',
                'div[class*="resume-detail-modal"]',
                '.candidate-detail',
                '.detail-modal',
                '.km-dialog__wrapper'
            ];
            const visibleModals = [];
            for (const sel of modalSelectors) {
                for (const m of Array.from(document.querySelectorAll(sel))) {
                    if (isVisible(m) && !visibleModals.includes(m)) visibleModals.push(m);
                }
            }

            const pickInRoot = (root) => {
                const roots = [root || document];
                for (const r of roots) {
                    const cssCandidates = Array.from(r.querySelectorAll(
                        'button, [role="button"], .km-ripple, div[class*="resume-btn__inner"], div[class*="resume-btn__text"]'
                    ));
                    const hit = cssCandidates.find((el) => {
                        const btn = el.closest('button, [role="button"], .km-ripple') || el;
                        const t = textOf(btn);
                        return t.includes('打招呼') && !t.includes('打电话') && isVisible(btn) && !isDisabled(btn);
                    });
                    if (hit) return hit.closest('button, [role="button"], .km-ripple') || hit;

                    try {
                        const xpath = ".//*[contains(normalize-space(.),'打招呼') and not(contains(normalize-space(.),'打电话'))]";
                        const node = document.evaluate(xpath, r, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                        if (node) {
                            const btn = node.closest('button, [role="button"], .km-ripple') || node;
                            if (isVisible(btn) && !isDisabled(btn)) return btn;
                        }
                    } catch (e) {}
                }
                return null;
            };

            let btn = null;
            for (const modal of visibleModals) {
                btn = pickInRoot(modal);
                if (btn) break;
            }
            if (!btn) btn = pickInRoot(document);

            if (!btn) {
                const nearby = Array.from(document.querySelectorAll('button, [role="button"], .km-ripple'))
                    .map(el => textOf(el))
                    .filter(Boolean)
                    .slice(0, 12)
                    .join(' | ');
                throw new Error(`未找到打招呼按钮; 当前可见按钮示例: ${nearby}`);
            }

            const finalText = textOf(btn);
            if (!finalText.includes('打招呼') || finalText.includes('打电话')) {
                throw new Error(`命中的按钮文本不合法: ${finalText}`);
            }

            btn.scrollIntoView({ behavior: 'smooth', block: 'center' });
            btn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
            btn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
            btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
            if (typeof btn.click === 'function') btn.click();
            console.log('[zhilian-py] 打招呼按钮点击成功');
        }
        """)
        time.sleep(2.0)


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
