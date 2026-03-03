# agno1/browser_automation/zhaopin/boss/boss_screener_selectors.py
"""BOSS 直聘简历筛选器的 DOM 选择器定义。

平台：https://www.zhipin.com/web/chat/recommend（推荐牛人页面）

选择器来源：F:/KIKI/代码库/goodHR3.0.1/content_scripts/sites/boss.js
采用与 goodHR 相同的多级降级策略（精确匹配 → className包含匹配）。
"""
from __future__ import annotations

# ===========================================================================
# BOSS 直聘推荐牛人页面 DOM 选择器
# 参考：goodHR3.0.1/content_scripts/sites/boss.js
# ===========================================================================

# ── 目标页面 ──────────────────────────────────────────────────────────────
START_URL = "https://www.zhipin.com/web/chat/recommend"
URL_PATH_MARKER = "web/chat/recommend"  # 用于判断是否在正确页面

# ── 候选人卡片（多级降级策略，与 goodHR fullClasses.items 一致）───────────
# 优先精确匹配，依次降级到 className 包含匹配
CARD_SELECTORS = [
    # 精确 className（new 版推荐牛人卡片）
    ".candidate-card-wrap",
    ".geek-info-card",
    ".card-container",
    # 旧版卡片
    ".card-inner.clear-fix",
    # 降级：className 包含匹配
    "[class*='candidate-card-wrap']",
    "[class*='geek-info-card']",
    "[class*='card-container']",
    "[class*='card-inner']",
]

# ── 候选人卡片可点击区域（detail link，与 goodHR detailSelectors.detailLink 一致）
CARD_CLICK_SELECTORS = [
    ".card-inner.common-wrap",
    ".card-inner.clear-fix",
    ".candidate-card-wrap",
    ".card-inner.blue-collar-wrap",
    ".card-container",
    ".geek-info-card",
    ".card-inner.new-geek-wrap",
]

# ── 卡片内信息提取选择器 ──────────────────────────────────────────────────
# 姓名（与 goodHR fullClasses.name 一致）
CARD_NAME_SELECTORS = [
    ".name",
    "[class*='name']",
]

# 年龄/标签行（与 goodHR fullClasses.age 一致）
# BOSS 直聘年龄在 .job-card-left 行的多个标签中，需要从文本中提取
CARD_AGE_SELECTORS = [
    "[class*='job-card-left']",
    "[class*='candidate-info']",
    ".geek-info-detail",
]

# 学历
CARD_EDUCATION_SELECTORS = [
    ".base-info.join-text-wrap",
    ".geek-info-detail",
    "[class*='education']",
    "[class*='degree']",
]

# 学校/简历内容（goodHR fullClasses.school）
CARD_SCHOOL_SELECTORS = [
    ".content.join-text-wrap",
    "[class*='content']",
    "[class*='school']",
]

# 在线状态（goodHR fullClasses.active）
CARD_STATUS_SELECTORS = [
    ".active-text",
    "[class*='active-text']",
    "[class*='online-marker']",
]

# 薪资期望（extraSelectors salary-text）
CARD_SALARY_SELECTORS = [
    "[class*='salary-text']",
    "[class*='salary']",
]

# ── 打招呼按钮（与 goodHR selectors.clickTarget 一致）────────────────────
GREET_BUTTON_SELECTORS = [
    ".btn.btn-greet",
    "[class*='btn-greet']",
    # 获取联系方式按钮（部分候选人页面显示此按钮替代打招呼）
    ".btn.btn-getcontact",
    "[class*='btn-getcontact']",
]

# ── 弹窗/浮层关闭按钮 ─────────────────────────────────────────────────────
CLOSE_BUTTON_SELECTORS = [
    ".boss-popup__close",
    ".resume-custom-close",
    "[class*='iboss-close']",
    "[class^='iboss iboss-close']",
    "[class*='close']",
    "[aria-label*='关闭']",
    # 消息/确认弹窗关闭
    "[class*='sati-times']",
    "[class*='km-icon sati']",
]

# ── 列表容器（用于滚动加载更多）─────────────────────────────────────────
LIST_CONTAINER_SELECTORS = [
    ".card-list",
    "[class*='candidate-list']",
    "[class*='geek-list']",
    "[class*='recommend-list']",
]

# ── 付费/VIP 拦截浮层 ────────────────────────────────────────────────────
PAYWALL_SELECTORS = [
    "[class*='pay-tip']",
    "[class*='vip-tip']",
    ".boss-popup",
]

# ── 同事已沟通标记（跳过这类候选人）────────────────────────────────────
COLLEAGUE_MARK_SELECTORS = [
    ".colleague-collaboration",
    "[class*='colleague']",
]

# ── 确认/继续按钮（索要联系方式流程，目前不使用，预留）────────────────
CONTINUE_BUTTON_SELECTORS = [
    "[class^='btn btn-continue btn-outline']",
]
CONFIRM_BUTTON_SELECTORS = [
    "[class^='boss-btn-primary boss-btn']",
]


__all__ = [
    "START_URL",
    "URL_PATH_MARKER",
    "CARD_SELECTORS",
    "CARD_CLICK_SELECTORS",
    "CARD_NAME_SELECTORS",
    "CARD_AGE_SELECTORS",
    "CARD_EDUCATION_SELECTORS",
    "CARD_SCHOOL_SELECTORS",
    "CARD_STATUS_SELECTORS",
    "CARD_SALARY_SELECTORS",
    "GREET_BUTTON_SELECTORS",
    "CLOSE_BUTTON_SELECTORS",
    "LIST_CONTAINER_SELECTORS",
    "PAYWALL_SELECTORS",
    "COLLEAGUE_MARK_SELECTORS",
    "CONTINUE_BUTTON_SELECTORS",
    "CONFIRM_BUTTON_SELECTORS",
]
