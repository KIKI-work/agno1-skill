# agno1/browser_automation/zhaopin/zhilian/zhilian_selectors.py
"""智联招聘（zhaopin.com）简历筛选器的 DOM 选择器定义。

选择器来源：
- content-jianlishai-selectors.js
- zhaopin-resume-screener-skill.js
平台：https://rd6.zhaopin.com
"""
from __future__ import annotations

import sys
from pathlib import Path

# 确保能找到上层 browser_automation 包
_BA_ROOT = Path(__file__).resolve().parents[3]
if str(_BA_ROOT) not in sys.path:
    sys.path.insert(0, str(_BA_ROOT))

from agno1.browser_automation.selectors import PlatformSelectors


# ===========================================================================
# 智联招聘简历筛：推荐候选人列表页
# 选择器来源：content-jianlishai-selectors.js + zhaopin-resume-screener-skill.js
# 平台：https://rd6.zhaopin.com
# ===========================================================================

ZHILIAN_RESUME_SELECTORS = PlatformSelectors(
    start_url="https://rd6.zhaopin.com/app/recommend?tab=recommend",

    # 候选人列表容器
    # 来源：JIANLISHAI_SELECTORS.CONTAINERS.CANDIDATE_LIST / PAGE_STRUCTURE.LIST_CONTAINER
    chat_root=[
        'div[class*="recommend-list"]:not(.appview):not(.page-view)',
        'div[class*="candidate-list"]:not(.appview):not(.page-view)',
        '[role="list"]',
        '[role="listitem"]',
    ],

    # 候选人卡片（每张卡片对应一个候选人，点击打开简历详情）
    # 来源：zhaopin-resume-screener-skill.js _getCandidates()
    # 精准主选择器：div.resume-item__content.resume-card-exp（直接命中真实卡片）
    assistant_message_blocks=[
        "div.resume-item__content.resume-card-exp",
        'div[class*="recommend-item__inner-content"]',
        'div[class*="resume-item__content resume-card-exp"]',
    ],
    assistant_message_blocks_fallback=[
        '[data-demo-cid]',                              # 早期 MutationObserver 预标记的节点
        'div[class*="resume-item__content"]',
        'div[class*="recommend-item__"]',
    ],

    # 候选人姓名元素（点击可跳转简历详情弹窗）
    # 来源：JIANLISHAI_SELECTORS.CANDIDATE_INFO.NAME
    prompt_box=[
        'div[class*="talent-basic-info__name--inner"][title]',
        'div[class*="talent-basic-info__name--inner"]',
    ],

    # 打招呼按钮（简历详情弹窗内）
    # 来源：zhaopin-resume-screener-skill.js _clickGreetButton() XPath 降级版
    # 注意：需要确认不是「打电话」按钮
    send_button=[
        'div[class*="resume-btn__inner"]:has-text("打招呼")',
        'div[class*="resume-btn__text"]:has-text("打招呼")',
        'button:has(div[class*="resume-btn__text"]):has-text("打招呼")',
        'button:has-text("打招呼")',
    ],

    # 简历详情弹窗容器（打开后等待其出现）
    # 来源：zhaopin-resume-screener-skill.js _extractResumeInfo() / _clickGreetButton()
    stop_button=[
        '[class*="resume-detail"]',
        'div[class*="resume-detail-container"]',
        'div[class*="resume-detail-modal"]',
        ".km-dialog__wrapper",
        ".km-overlay",
        ".modal-wrapper",
        ".candidate-detail",
    ],

    # 关闭简历详情弹窗按钮
    # 来源：zhaopin-resume-screener-skill.js _closeDetail()
    regenerate_button=[
        "div.new-shortcut-resume__close",       # 智联招聘真实关闭按钮
        ".close-btn",
        ".modal-close",
        '[data-spm*="close"]',
        'button[class*="close"]',
        ".close-icon",
        '[class*="close"]',
        '[aria-label*="关闭"]',
        '[aria-label*="Close"]',
    ],

    # 候选人基本信息字段（弹窗内文本提取用）
    # 来源：JIANLISHAI_SELECTORS.CANDIDATE_INFO 各字段
    assistant_text_blocks=[
        'div[class*="talent-basic-info"]',
        'div[class*="resume-detail"]',
        ":scope",
    ],

    # 工作经历（弹窗内）
    # 来源：JIANLISHAI_SELECTORS.EXPERIENCE.WORK_CONTAINER
    user_message_blocks=[
        'table[class*="talent-experience"] tr:not(.edu-exp-tr)',
        'div[class*="work-section"]',
        'div[class*="experience"]',
    ],

    # 教育经历（弹窗内）
    # 来源：JIANLISHAI_SELECTORS.EDUCATION_INFO / EXPERIENCE.EDU_CONTAINER
    copy_button=[
        'div[class*="new-education-experiences__item"]',
        'span[class*="new-education-experiences__item-name"][title]',
        'div[class*="education-section"]',
    ],

    # 加载更多/翻页按钮（候选人列表超出一屏时使用）
    retry_button=[
        'button[class*="km-button"]:has-text("下一页")',
        'button:has-text("下一页")',
        '[aria-label*="下一页"]',
    ],

    # 付费/VIP 拦截提示（遇到时跳过该候选人）
    artifact_candidates=[
        'div[class*="pay-tip"]',
        'div[class*="vip-tip"]',
        'span:has-text("金币")',
        'span:has-text("VIP")',
        'span:has-text("付费")',
    ],
)


__all__ = ["ZHILIAN_RESUME_SELECTORS"]
