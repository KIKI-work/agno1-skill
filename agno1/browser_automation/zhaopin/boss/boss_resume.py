"""BOSS 直聘简历筛适配器。

功能：
- 连接已登录的 Chrome（CDP attach 模式）
- 在「推荐牛人」列表页（/web/chat/recommend）批量获取候选人卡片
- 逐一点击卡片 → 等待打招呼按钮出现 → 提取卡片信息
- 根据 AI 筛选结果点击「立即沟通」(btn-greet) 或跳过
- 关闭弹窗，继续下一个候选人
- 滚动触发懒加载，直至无新候选人为止

DOM 操作策略（与智联版一致）：
- 优先通过 page.evaluate() 注入 JS 直接操作 DOM（与 goodHR content script 等价）
- Playwright 负责 CDP 连接、等待条件，不做复杂的多步 locator 链
- 关键选择器统一维护在 boss_selectors.py

参考来源：
- F:/KIKI/代码库/goodHR3.0.1/content_scripts/sites/boss.js
- F:/KIKI/代码库/goodHR3.0.1/content_scripts/index.js
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from playwright.sync_api import Frame, Page

from agno1.browser_automation.manager import BrowserManager
from agno1.browser_automation.zhaopin.boss import boss_selectors as S


# ---------------------------------------------------------------------------
# 候选人数据模型（与 zhilian_resume.py 结构完全一致）
# ---------------------------------------------------------------------------

@dataclass
class CandidateInfo:
    """从候选人卡片提取的简历信息。"""
    index: int
    card_id: str                    # 唯一标识卡片（data-boss-cid 或自动分配）
    name: str = "未知候选人"
    age: str = ""
    education: str = ""
    school: str = ""
    work_experience: str = ""
    expected_salary: str = ""
    skills: str = ""
    job_status: str = ""
    full_text: str = ""             # 卡片完整可见文本（最多 3000 字），供 AI 分析
    is_fallback: bool = False       # True 表示提取失败，使用兜底数据


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
    action: str                     # "greeted"|"rejected_keyword"|"rejected_ai"|"failed"|"skipped"
    reason: str = ""
    ai_result: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# BOSS 直聘简历筛适配器
# ---------------------------------------------------------------------------

class BossResumeAdapter:
    """
    BOSS 直聘推荐牛人简历筛适配器。

    BOSS 直聘与智联招聘的关键差异：
    1. 目标页面：/web/chat/recommend（推荐牛人），而非 /web/geek/recommend
    2. 卡片结构：多版本并存（candidate-card-wrap / geek-info-card / card-inner clear-fix）
    3. 简历信息：直接从卡片 DOM 提取（无需打开详情弹窗）
    4. 打招呼：鼠标 mouseover 触发按钮显示 → 点击 .btn-greet
    5. 无弹窗关闭：打完招呼后直接处理下一张卡片（无需关闭弹窗）
    6. 滚动策略：页面无限滚动，从上到下逐个处理新出现的卡片

    使用方式：
        adapter = BossResumeAdapter(browser=bm)
        page = adapter.get_page(session_id="boss_screener", url=url)
        cards = adapter.get_candidate_cards(page)
        for card in cards:
            info = adapter.open_detail_and_extract(page, card)
            # 调用 AI 筛选后：
            if passed:
                adapter.click_greet_button(page, card)
            # 无需 close_detail
    """

    PLATFORM = "boss"

    def __init__(self, *, browser: BrowserManager):
        self._browser = browser

    # ------------------------------------------------------------------
    # 页面准备
    # ------------------------------------------------------------------

    def get_page(self, *, session_id: str, url: Optional[str] = None) -> Page:
        """获取或创建一个与 BOSS 直聘关联的 Page。"""
        target_url = url or S.START_URL
        handle = self._browser.get_or_create_page(
            platform=self.PLATFORM,
            session_id=session_id,
            url=target_url,
        )
        page = handle.page
        if url:
            current = page.url or ""
            if S.URL_PATH_MARKER not in current:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        # 简单等待页面基本渲染，不做阻塞轮询
        # （卡片等待在 get_candidate_cards 的 _wait_for_cards 里负责）
        time.sleep(2.0)
        return page

    _SPA_READY_SELECTORS = [
        ".candidate-card-wrap",
        "ul.card-list",
        ".card-item",
        ".recommend-wrap",
        ".page-main",
        "#recommend-list",
        ".boss-login-wrap",      # 未登录状态也算页面已挂载
    ]

    def _wait_spa_mount(self, page: Page, timeout_s: int = 20) -> None:
        """等待 BOSS 直聘 Vue SPA 挂载完成（内容区节点出现）。
        同时检查所有 frame（主 frame + 所有 iframe）。
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            # 在所有 frame 中检查（包括 iframe）
            for frame in page.frames:
                try:
                    found = frame.evaluate(
                        "(sels) => sels.some(s => !!document.querySelector(s))",
                        self._SPA_READY_SELECTORS,
                    )
                    if found:
                        return
                except Exception:
                    continue
            time.sleep(0.8)
        # 超时后再补等 1 秒
        time.sleep(1.0)

    # ------------------------------------------------------------------
    # 关键：找到真正包含候选人卡片的 frame（可能在 iframe 里）
    # ------------------------------------------------------------------

    def _get_content_frame(self, page: Page) -> "Frame":
        """
        BOSS 直聘推荐牛人页面的真实 DOM 在 iframe 里。

        结构：
          外层 shell (page) → <iframe> → ul.card-list > li.card-item > div.candidate-card-wrap

        策略：遍历所有 frame，找到包含候选人卡片的那个。
        找不到时降级返回主 frame（page.main_frame）。
        """
        card_selectors = [
            ".candidate-card-wrap",
            "ul.card-list",
            "li.card-item",
            ".geek-info-card",
            ".card-container",
        ]
        for frame in page.frames:
            try:
                found = frame.evaluate(
                    "(sels) => sels.some(s => !!document.querySelector(s))",
                    card_selectors,
                )
                if found:
                    return frame
            except Exception:
                continue
        # 降级：返回主 frame
        return page.main_frame

    # ------------------------------------------------------------------
    # 候选人卡片列表获取
    # ------------------------------------------------------------------

    # ── 真实 DOM 结构（来自 2026-03 页面抓取）────────────────────────────
    # ul.card-list > li.card-item > div.candidate-card-wrap
    # 卡片唯一ID：data-geek 属性（平台自带，直接复用）
    _CARD_WAIT_SELECTORS = [
        ".candidate-card-wrap",
        "ul.card-list li",
        ".card-item",
        "li.card-item",
        ".geek-info-card",
        ".card-container",
    ]

    def _wait_for_cards(self, page: Page, timeout_s: int = 30) -> None:
        """在 Python 侧轮询等待候选人卡片出现（最多 timeout_s 秒）。
        在所有 frame 中检查（因为卡片可能在 iframe 里）。
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            for frame in page.frames:
                try:
                    found = frame.evaluate(
                        "(sels) => sels.some(s => !!document.querySelector(s))",
                        self._CARD_WAIT_SELECTORS,
                    )
                    if found:
                        return
                except Exception:
                    continue
            time.sleep(0.5)

    def get_candidate_cards(self, page: Page) -> List[CardSummary]:
        """
        获取当前页面上所有候选人卡片。

        真实 DOM 结构（2026-03 实测）：
          ul.card-list > li.card-item > div.candidate-card-wrap
            ├── div.card-inner[data-geek="xxx"]   ← 唯一 ID 直接取 data-geek
            ├── span.name                          ← 姓名
            ├── div.base-info.join-text-wrap       ← 年龄/年限/学历/状态
            ├── div.salary-wrap > span             ← 薪资
            ├── span.active-text / img.online-marker ← 在线状态
            ├── div.timeline-wrap.work-exps        ← 工作经历
            └── div.timeline-wrap.edu-exps         ← 教育经历

        使用 data-geek 作为唯一 card_id，无需手动打标签。
        """
        self._wait_for_cards(page)
        # 关键：在包含卡片的 frame 中执行（可能是 iframe）
        frame = self._get_content_frame(page)
        raw: List[Dict[str, Any]] = frame.evaluate("""
        () => {
            // ── Step1: 找卡片容器 ─────────────────────────────────────────
            // 真实结构：ul.card-list > li.card-item > div.candidate-card-wrap
            let cards = Array.from(document.querySelectorAll('.candidate-card-wrap'));

            // 降级1：li.card-item 下的第一个子 div
            if (cards.length === 0) {
                cards = Array.from(document.querySelectorAll('li.card-item'))
                    .map(li => li.querySelector('div') || li)
                    .filter(Boolean);
            }

            // 降级2：goodHR 旧版 class 名
            if (cards.length === 0) {
                for (const sel of ['.geek-info-card', '.card-container', '.card-inner.clear-fix']) {
                    cards = Array.from(document.querySelectorAll(sel));
                    if (cards.length > 0) break;
                }
            }

            if (cards.length === 0) {
                console.warn('[boss-py] 未找到候选人卡片，URL:', window.location.href);
                return [];
            }
            console.log('[boss-py] 找到', cards.length, '张卡片');

            // ── Step2: 提取每张卡片基础信息 ──────────────────────────────
            function queryText(root, sels) {
                for (const s of sels) {
                    const el = root.querySelector(s);
                    if (el && el.textContent.trim()) return el.textContent.trim();
                }
                return '';
            }

            const results = cards.map((card, idx) => {
                // 唯一 ID：优先使用 data-geek（平台自带），再降级到 data-geekid
                const inner = card.querySelector('[data-geek]') || card;
                const geekId = inner.getAttribute('data-geek')
                            || inner.getAttribute('data-geekid')
                            || card.getAttribute('data-geek')
                            || `boss_${idx}`;

                // 姓名
                const name = queryText(card, ['span.name', '.name']);

                // 薪资（salary-wrap > span）
                const salary = queryText(card, ['.salary-wrap span', '.salary-wrap']);

                // 年龄/学历/状态（base-info.join-text-wrap 里用 · 分隔的文本）
                const baseInfo = queryText(card, ['.base-info.join-text-wrap', '.base-info']);

                // 在线状态
                const activeEl = card.querySelector('span.active-text');
                const onlineEl = card.querySelector('img.online-marker');
                const status = activeEl ? activeEl.textContent.trim()
                             : (onlineEl ? '在线' : '');

                const rect = card.getBoundingClientRect();
                return {
                    index: idx,
                    card_id: geekId,
                    name: name || '未知候选人',
                    age: baseInfo,   // 整行传过去，extract_card_info 再细拆
                    salary,
                    status,
                    top: rect.top,
                };
            });

            results.sort((a, b) => a.top - b.top);
            results.forEach((c, i) => { c.index = i; });
            return results;
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
        向下滚动触发懒加载，返回尚未处理过的新卡片。
        若无新卡片，返回空列表（表示已到底）。

        策略（与 goodHR index.js 无限滚动逻辑一致）：
        - 优先滚动卡片列表容器
        - 无容器时滚动 window
        - 等待 3 秒后重新扫描
        """
        frame = self._get_content_frame(page)
        frame.evaluate("""
        () => {
            // 真实容器：div#recommend-list（id="recommend-list"）或 div.list-wrap.card-list-wrap
            const container = document.getElementById('recommend-list')
                           || document.querySelector('.list-wrap.card-list-wrap')
                           || document.querySelector('.card-list-wrap')
                           || document.querySelector('ul.card-list');
            if (container && container.scrollHeight > container.clientHeight) {
                container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
            } else {
                window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
            }
            console.log('[boss-py] 滚动触发懒加载...');
        }
        """)
        time.sleep(3.0)  # 等待懒加载网络请求完成

        all_cards = self.get_candidate_cards(page)
        new_cards = [c for c in all_cards if c.card_id not in processed_ids]
        return new_cards

    # ------------------------------------------------------------------
    # 候选人信息提取（直接从卡片 DOM 提取，无需打开弹窗）
    # ------------------------------------------------------------------

    def scroll_to_card(self, page: Page, card: CardSummary) -> None:
        """滚动到指定候选人卡片，确保其在视口内。用 data-geek 定位。"""
        frame = self._get_content_frame(page)
        frame.evaluate(
            """
            (geekId) => {
                // 通过 data-geek 找到 card-inner，再取其父级 candidate-card-wrap
                const inner = document.querySelector(`[data-geek="${geekId}"]`);
                const el = inner ? (inner.closest('.candidate-card-wrap') || inner) : null;
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
        time.sleep(0.5)

    def extract_card_info(self, page: Page, card: CardSummary) -> CandidateInfo:
        """
        从候选人卡片 DOM 直接提取简历信息。

        真实选择器（2026-03 实测）：
          - 姓名：span.name
          - 基本信息行（年龄/年限/学历/求职状态）：div.base-info.join-text-wrap
          - 薪资：div.salary-wrap > span
          - 在线状态：span.active-text 或 img.online-marker
          - 工作经历：div.timeline-wrap.work-exps .timeline-item
          - 教育经历：div.timeline-wrap.edu-exps .timeline-item（含学校/专业/学历）
          - 技能标签：div.tags-wrap .tag-item
          - 优势描述：div.geek-desc span.content
        """
        frame = self._get_content_frame(page)
        raw: Dict[str, Any] = frame.evaluate(
            """
            (geekId) => {
                const inner = document.querySelector(`[data-geek="${geekId}"]`);
                const card = inner ? (inner.closest('.candidate-card-wrap') || inner) : null;
                if (!card) {
                    return {
                        name: '未知候选人', age: '', education: '', school: '',
                        work_experience: '', expected_salary: '', skills: '',
                        job_status: '', full_text: '', is_fallback: true
                    };
                }

                // ── 姓名 ─────────────────────────────────────────────────
                const nameEl = card.querySelector('span.name');
                const name = nameEl ? nameEl.textContent.trim() : '未知候选人';

                // ── 基本信息行（年龄·年限·学历·求职状态）─────────────────
                // div.base-info.join-text-wrap 下所有 span 文本
                const baseInfoEl = card.querySelector('.base-info.join-text-wrap');
                const baseSpans = baseInfoEl
                    ? Array.from(baseInfoEl.querySelectorAll('span'))
                          .map(s => s.textContent.trim()).filter(Boolean)
                    : [];
                const baseInfoText = baseSpans.join(' · ');

                // 年龄：找含"岁"的 span
                const ageSpan = baseSpans.find(t => t.includes('岁')) || '';
                // 学历：找含本科/硕士/博士/专科/大专/MBA 等
                const eduKeywords = ['本科','硕士','博士','专科','大专','MBA','学士','研究生'];
                const education = baseSpans.find(t => eduKeywords.some(k => t.includes(k))) || '';
                // 求职状态（最后一项往往是"离职-随时到岗"等）
                const jobStatus = baseSpans.length > 0 ? baseSpans[baseSpans.length - 1] : '';

                // ── 在线标记覆盖 jobStatus ────────────────────────────────
                const activeEl = card.querySelector('span.active-text');
                const onlineEl = card.querySelector('img.online-marker');
                const status = activeEl ? activeEl.textContent.trim()
                             : (onlineEl ? '在线' : jobStatus);

                // ── 薪资 ──────────────────────────────────────────────────
                const salaryWrap = card.querySelector('.salary-wrap');
                const expected_salary = salaryWrap
                    ? salaryWrap.querySelector('span')?.textContent.trim() || salaryWrap.textContent.trim()
                    : '';

                // ── 工作经历（timeline-wrap.work-exps）────────────────────
                const workItems = Array.from(
                    card.querySelectorAll('.timeline-wrap.work-exps .timeline-item')
                ).map(item => {
                    const spans = Array.from(item.querySelectorAll('.join-text-wrap.content span'))
                        .map(s => s.textContent.trim()).filter(Boolean);
                    const time = Array.from(item.querySelectorAll('.join-text-wrap.time span'))
                        .map(s => s.textContent.trim()).filter(Boolean).join('-');
                    return (time ? time + ' ' : '') + spans.join(' · ');
                }).filter(Boolean);
                const work_experience = workItems.join(' | ');

                // ── 教育经历（timeline-wrap.edu-exps）─────────────────────
                const eduItems = Array.from(
                    card.querySelectorAll('.timeline-wrap.edu-exps .timeline-item')
                ).map(item => {
                    const spans = Array.from(item.querySelectorAll('.join-text-wrap.content span'))
                        .map(s => s.textContent.trim()).filter(Boolean);
                    const time = Array.from(item.querySelectorAll('.join-text-wrap.time span'))
                        .map(s => s.textContent.trim()).filter(Boolean).join('-');
                    return (time ? time + ' ' : '') + spans.join(' · ');
                }).filter(Boolean);
                // 第一条教育经历的第一个 span 即学校名
                const school = (() => {
                    const firstEdu = card.querySelector('.timeline-wrap.edu-exps .timeline-item .join-text-wrap.content span');
                    return firstEdu ? firstEdu.textContent.trim() : '';
                })();
                const eduText = eduItems.join(' | ');

                // ── 技能标签 ───────────────────────────────────────────────
                const tagItems = Array.from(card.querySelectorAll('.tags-wrap .tag-item'))
                    .map(t => t.textContent.trim()).filter(Boolean);
                const skills = tagItems.join(' / ');

                // ── 优势描述 ───────────────────────────────────────────────
                const descEl = card.querySelector('.geek-desc span.content');
                const geekDesc = descEl ? descEl.textContent.trim() : '';

                // ── 完整卡片文本（供 AI 分析）────────────────────────────
                const full_text = [
                    `姓名：${name}`,
                    baseInfoText ? `基本信息：${baseInfoText}` : '',
                    expected_salary ? `期望薪资：${expected_salary}` : '',
                    status ? `状态：${status}` : '',
                    work_experience ? `工作经历：${work_experience}` : '',
                    eduText ? `教育经历：${eduText}` : '',
                    skills ? `技能/标签：${skills}` : '',
                    geekDesc ? `优势：${geekDesc}` : '',
                ].filter(Boolean).join('\\n').substring(0, 3000);

                return {
                    name,
                    age: ageSpan,
                    education: education || eduText,
                    school,
                    work_experience,
                    expected_salary,
                    skills,
                    job_status: status,
                    full_text,
                    is_fallback: false,
                };
            }
            """,
            card.card_id,
        )

        return CandidateInfo(
            index=card.index,
            card_id=card.card_id,
            name=raw.get("name", "未知候选人"),
            age=raw.get("age", ""),
            education=raw.get("education", ""),
            school=raw.get("school", ""),
            work_experience=raw.get("work_experience", ""),
            expected_salary=raw.get("expected_salary", ""),
            skills=raw.get("skills", ""),
            job_status=raw.get("job_status", ""),
            full_text=raw.get("full_text", ""),
            is_fallback=bool(raw.get("is_fallback", False)),
        )

    # ------------------------------------------------------------------
    # 模拟真人：点击卡片打开简历详情页
    # ------------------------------------------------------------------

    def click_candidate_detail(self, page: Page, card: CardSummary) -> bool:
        """
        点击候选人卡片，打开简历详情弹窗/页面（模拟真人行为）。

        BOSS 直聘点击卡片可区域（与 goodHR detailSelectors.detailLink 一致）：
          优先点击 card-inner common-wrap / card-inner clear-fix / candidate-card-wrap
          等可点击容器，让平台弹出简历详情。

        注意：
          - 简历信息已从卡片 DOM 直接提取（extract_card_info），
            此动作纯为模拟真人浏览行为，不用于数据提取。
          - 返回 True 表示已找到并点击了可点击区域，False 表示降级直接点击卡片。
        """
        frame = self._get_content_frame(page)
        result: bool = frame.evaluate(
            """
            (geekId) => {
                const inner = document.querySelector(`[data-geek="${geekId}"]`);
                const card = inner ? (inner.closest('.candidate-card-wrap') || inner) : null;
                if (!card) return false;

                // goodHR detailSelectors.detailLink 顺序（完整 class 名称匹配）
                const DETAIL_CLASSES = [
                    'card-inner common-wrap',
                    'card-inner clear-fix',
                    'candidate-card-wrap',
                    'card-inner blue-collar-wrap',
                    'card-container',
                    'geek-info-card',
                    'card-inner new-geek-wrap',
                ];

                for (const cls of DETAIL_CLASSES) {
                    // 优先完整 class 名匹配（getElementsByClassName 语义）
                    let el = card.getElementsByClassName(cls)[0];
                    if (!el) {
                        // 降级：querySelector 属性包含匹配
                        el = card.querySelector(`[class*="${cls.split(' ')[0]}"]`);
                    }
                    if (el) {
                        el.click();
                        console.log('[boss-py] 点击详情区域:', cls, geekId);
                        return true;
                    }
                }

                // 最终降级：直接点击卡片本身
                card.click();
                console.log('[boss-py] 降级点击卡片本身:', geekId);
                return false;
            }
            """,
            card.card_id,
        )
        return result

    def _wait_for_detail_open(self, page: Page, timeout_s: float = 6.0) -> bool:
        """等待简历详情弹窗出现（最多 timeout_s 秒）。"""
        # 详情弹窗的典型选择器（boss-popup、dialog-wrap 等）
        DETAIL_SELECTORS = [
            ".boss-popup",
            ".dialog-wrap.active",
            ".resume-preview-wrap",
            ".boss-dialog",
            "[class*='resume-detail']",
            "[class*='geek-detail']",
        ]
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            # 在所有 frame（主 frame + iframe）中检查弹窗
            for frame in page.frames:
                try:
                    found = frame.evaluate(
                        "(sels) => sels.some(s => !!document.querySelector(s))",
                        DETAIL_SELECTORS,
                    )
                    if found:
                        return True
                except Exception:
                    continue
            time.sleep(0.4)
        return False

    # ------------------------------------------------------------------
    # 关闭简历详情弹窗
    # ------------------------------------------------------------------

    def close_detail(self, page: Page) -> None:
        """
        关闭简历详情弹窗及打招呼后可能出现的任何浮层。

        策略（参考 goodHR boss.js closeDetail() 重试机制）：
          ESC 键 → 关闭按钮（多选择器） → 遮罩层点击，最多重试 3 次。
        关闭按钮选择器来源：goodHR detailSelectors.closeButton。
        """
        CLOSE_SELECTORS = [
            # goodHR detailSelectors.closeButton
            '.boss-popup__close',
            '.resume-custom-close',
            '.dialog-wrap.active .boss-popup__close',
            # 通用关闭
            '[class*="iboss-close"]',
            '[class^="iboss iboss-close"]',
            '[class*="sati-times"]',
            '[class*="km-icon sati"]',
            '[aria-label*="关闭"]',
            'button[class*="close"]',
            '[class*="close-btn"]',
        ]
        for _attempt in range(3):
            # 在所有 frame 中尝试（弹窗可能在主 frame 外）
            closed = False
            for frame in page.frames:
                try:
                    closed = frame.evaluate(
                        """
                        (sels) => {
                            // ESC 键
                            document.dispatchEvent(new KeyboardEvent('keydown', {
                                key: 'Escape', keyCode: 27, code: 'Escape',
                                bubbles: true, cancelable: true
                            }));
                            for (const sel of sels) {
                                const btn = document.querySelector(sel);
                                if (btn) { btn.click(); return true; }
                            }
                            // 遮罩层
                            const backdrop = document.querySelector(
                                '.modal-backdrop, .modal-overlay, [class*="backdrop"], .boss-layer__wrapper'
                            );
                            if (backdrop) { backdrop.click(); return true; }
                            return false;
                        }
                        """,
                        CLOSE_SELECTORS,
                    )
                    if closed:
                        break
                except Exception:
                    continue
            if closed:
                break
            time.sleep(0.5)
        time.sleep(0.5)  # 等待关闭动画

    # ------------------------------------------------------------------
    # 打招呼（从卡片列表页点击 btn-greet）
    # ------------------------------------------------------------------

    def click_greet_button(self, page: Page, card: CardSummary) -> None:
        """
        对指定候选人卡片点击「打招呼」(btn btn-greet) 按钮。

        真实按钮 HTML（2026-03 实测）：
          <button data-v-6ec2d01a="" type="button" class="btn btn-greet">
            打招呼<i data-v-6ec2d01a="" class="overdue-tip-icon"></i>
          </button>

        流程（与 goodHR boss.js clickMatchedItem() 完全一致）：
          1. mouseover 触发按钮显示
          2. getElementsByClassName('btn btn-greet') 找按钮（完整双 class 匹配）
          3. 降级：querySelector('[class*="btn-greet"]')
          4. 降级：文字含「打招呼」的 button
        """
        frame = self._get_content_frame(page)
        result: bool = frame.evaluate(
            """
            (geekId) => {
                const inner = document.querySelector(`[data-geek="${geekId}"]`);
                const card = inner ? (inner.closest('.candidate-card-wrap') || inner) : null;
                if (!card) {
                    console.error('[boss-py] 打招呼：未找到卡片', geekId);
                    return false;
                }

                // Step 1：mouseover 触发按钮显示（与 goodHR clickMatchedItem 完全一致）
                card.dispatchEvent(new MouseEvent('mouseover', {
                    view: window, bubbles: true, cancelable: true
                }));

                // Step 2：getElementsByClassName 完整双 class 匹配（goodHR 主路）
                // class="btn btn-greet" → getElementsByClassName('btn btn-greet')
                let btns = card.getElementsByClassName('btn btn-greet');
                if (btns.length > 0) {
                    for (let i = 0; i < btns.length; i++) btns[i].click();
                    console.log('[boss-py] 打招呼成功(主路):', geekId);
                    return true;
                }

                // Step 3：降级 querySelector
                const greetBtn = card.querySelector('[class*="btn-greet"]');
                if (greetBtn) {
                    greetBtn.click();
                    console.log('[boss-py] 打招呼成功(降级):', geekId);
                    return true;
                }

                // Step 4：文字兜底
                for (const btn of card.querySelectorAll('button, a')) {
                    if (btn.textContent.includes('打招呼')) {
                        btn.click();
                        console.log('[boss-py] 打招呼成功(文字兜底):', geekId);
                        return true;
                    }
                }

                console.warn('[boss-py] 未找到打招呼按钮:', geekId);
                return false;
            }
            """,
            card.card_id,
        )
        if not result:
            raise RuntimeError(f"[boss] 未找到打招呼按钮: {card.name} (id={card.card_id})")
        time.sleep(2.0)  # 等待打招呼网络请求完成

    # ------------------------------------------------------------------
    # 完整单候选人处理流程（供 pipeline 调用）
    # ------------------------------------------------------------------

    def open_detail_and_extract(
        self,
        page: Page,
        card: CardSummary,
        last_captured_name: Optional[str] = None,
        keep_open: bool = False,
        **kwargs,
    ) -> CandidateInfo:
        """
        完整流程：滚动到卡片 → 提取信息 → 点击详情（模拟真人）→ 等待弹窗。

        流程说明：
          1. scroll_to_card：将卡片滚动到视口内
          2. extract_card_info：直接从卡片 DOM 提取简历信息（不依赖详情页）
          3. click_candidate_detail：点击卡片打开简历详情（纯模拟真人行为）
          4. 等待详情弹窗出现（最多 6 秒）
          5. keep_open=False（默认）：立即关闭弹窗并返回；
             keep_open=True：保持弹窗打开，由调用方在 AI 分析完成后自行调用 close_detail()

        打招呼动作在 pipeline 里由 AI 判断后单独调用 click_greet_button()。

        Args:
            page:               当前 Playwright Page
            card:               目标候选人卡片
            last_captured_name: 占位参数（兼容接口，BOSS 版不使用）
            keep_open:          True = 弹窗保持打开，由调用方关闭；False = 方法内部关闭（默认）
            **kwargs:           其他占位参数（兼容接口）

        Returns:
            CandidateInfo 候选人信息
        """
        # 1. 滚动到卡片
        self.scroll_to_card(page, card)
        time.sleep(0.3)

        # 2. 从卡片 DOM 直接提取信息（无需进入详情页）
        info = self.extract_card_info(page, card)
        if info.is_fallback:
            raise RuntimeError(
                f"[boss] 候选人卡片信息提取失败: {card.name} (id={card.card_id})"
            )

        # 3. 点击卡片打开简历详情（模拟真人浏览行为）
        self.click_candidate_detail(page, card)

        # 4. 等待详情弹窗出现（最多 6 秒）
        self._detail_is_open = self._wait_for_detail_open(page, timeout_s=6.0)

        if keep_open:
            # 调用方负责在 AI 分析完成后关闭弹窗
            return info

        # keep_open=False：方法内部关闭弹窗
        if self._detail_is_open:
            time.sleep(1.5)  # 模拟停留阅读
            self.close_detail(page)
            time.sleep(0.5)
        else:
            # 弹窗未出现（部分卡片点击跳新页面或无弹窗），ESC 保险
            self.close_detail(page)

        return info


__all__ = [
    "BossResumeAdapter",
    "CandidateInfo",
    "CardSummary",
    "ScreenResult",
]
