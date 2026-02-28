# browser_automation/selectors.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class PlatformSelectors:
    """
    Keep all platform DOM knowledge here.
    """

    start_url: str

    # root container (optional, used for scoping)
    chat_root: List[str] = field(default_factory=list)

    # prompt input & send
    prompt_box: List[str] = field(default_factory=list)
    send_button: List[str] = field(default_factory=list)

    # generation status
    stop_button: List[str] = field(default_factory=list)

    # responses (assistant/model side)
    assistant_message_blocks: List[str] = field(default_factory=list)           # primary (prefer only model response)
    assistant_message_blocks_fallback: List[str] = field(default_factory=list)  # fallback (broader)

    # user side blocks (used for send-ack / dedupe)
    user_message_blocks: List[str] = field(default_factory=list)

    assistant_text_blocks: List[str] = field(default_factory=list)

    # retry/regenerate buttons (optional)
    regenerate_button: List[str] = field(default_factory=list)
    retry_button: List[str] = field(default_factory=list)

    # copy button (Gemini completion signal / better extraction)
    copy_button: List[str] = field(default_factory=list)

    # upload
    file_input: List[str] = field(default_factory=list)
    attach_button: List[str] = field(default_factory=list)
    upload_menu_items: List[str] = field(default_factory=list)  # Gemini sometimes shows menu after attach click

    # artifact download
    artifact_candidates: List[str] = field(default_factory=list)

    # model selection
    model_switcher: List[str] = field(default_factory=list)     # open model dropdown
    model_switcher_role_names: List[str] = field(default_factory=list)  # role-based name patterns
    model_switcher_role_scopes: List[str] = field(default_factory=list)  # CSS scopes for role search
    new_chat_button: List[str] = field(default_factory=list)    # start fresh chat (if you need model change)

    # message menu: open menu on a message (⋯), then "Branch in new chat"
    message_menu_button: List[str] = field(default_factory=list)   # button on each message to open more actions
    branch_in_new_chat_menuitem: List[str] = field(default_factory=list)  # menu item "Branch in new chat"

    # project page: Sources → 添加 → 弹窗内点击上传 (web 项目上传方式)
    project_sources_tab: List[str] = field(default_factory=list)      # e.g. "Sources" tab/button
    project_add_sources_button: List[str] = field(default_factory=list)  # "添加" button
    project_add_modal: List[str] = field(default_factory=list)   # 点击添加后出现的弹窗/对话框（在其内找上传按钮）
    project_upload_button: List[str] = field(default_factory=list)   # 弹窗内的 Upload/上传 按钮 (opens file chooser)
    project_upload_file_input: List[str] = field(default_factory=list)  # input[type=file] in modal


DIAGNOSTIC_SELECTORS: Dict[str, List[str]] = {
    "captcha_iframes": [
        "iframe[src*='captcha']",
        "iframe[src*='recaptcha']",
    ],
}


CHATGPT_SELECTORS = PlatformSelectors(
    start_url="https://chatgpt.com/",
    chat_root=[
        "main",
        "div[role='main']",
        "article",
    ],
    prompt_box=[
        # Newer ChatGPT composer is ProseMirror-based (div#prompt-textarea)
        'div#prompt-textarea[contenteditable="true"]',
        "div#prompt-textarea.ProseMirror",
        'div[data-testid="prompt-textarea"][contenteditable="true"]',
        'div.ProseMirror[contenteditable="true"]',
        "div#prompt-textarea",
        "div.ProseMirror#prompt-textarea",
        "div[contenteditable='true']#prompt-textarea",
        # Legacy / fallback textarea
        "textarea[data-testid='prompt-textarea']",
        "textarea#prompt-textarea",
        "textarea[name='prompt-textarea']",
        "textarea[placeholder*='Message']",
        # Generic contenteditable textbox fallback
        "div[contenteditable='true'][role='textbox']",
        "textarea",
    ],
    send_button=[
        "button[data-testid='send-button']",
        'button[aria-label*="发送" i]',
        'button:has-text("发送")',
        "button[aria-label='Send prompt']",
        "button[aria-label*='Send']",
        "button:has-text('Send')",
    ],
    stop_button=[
        "button[data-testid='stop-button']",
        'button[aria-label*="停止" i]',
        'button[aria-label*="停止思考" i]',
        'button[aria-label*="停止推理" i]',
        'button[aria-label*="流式" i]',
        'button:has-text("停止")',
        'button:has-text("停止思考")',
        'button:has-text("停止推理")',
        "button[aria-label*='Stop']",
        "button[aria-label*='Stop thinking' i]",
        "button[aria-label*='Stop reasoning' i]",
        "button:has-text('Stop generating')",
        "button:has-text('Stop thinking')",
        "button:has-text('Stop reasoning')",
        "[role='button']:has-text('Stop thinking')",
        "[role='button']:has-text('Stop reasoning')",
        "[role='button']:has-text('停止思考')",
        "[role='button']:has-text('停止推理')",
    ],
    assistant_message_blocks=[
        # 必须带 role=assistant，避免把用户消息（如主控 prompt）当成助手回复
        "[data-message-author-role='assistant']",
        "[data-message-id][data-message-author-role='assistant']",
        "[data-message-author-role='assistant']:has(div.markdown)",
        "[data-message-author-role='assistant']:has(div.prose)",
        "[data-message-id][data-message-author-role='assistant']:has(div[class*='markdown'])",
    ],
    assistant_message_blocks_fallback=[
        # 仅在没有 role 时使用，且排除明确为用户的消息
        "[data-message-id]:not([data-message-author-role='user']):has(div.markdown)",
        "[data-message-id]:not([data-message-author-role='user']):has(div.prose)",
        "article[data-message-id]:not([data-message-author-role='user'])",
    ],
    user_message_blocks=[
        # Most ChatGPT threads
        "[data-message-author-role='user']",
        "[data-message-id][data-message-author-role='user']",
        # Fallback: user blocks without prose/markdown containers
        "[data-message-id]:not(:has(div.markdown)):not(:has(div.prose)):not(:has(div[class*='markdown']))",
    ],
    assistant_text_blocks=[
        "div.markdown",
        "div.prose",
        "div[class*='markdown']",
        ":scope",
    ],
    regenerate_button=[
        "button[data-testid='regenerate-button']",
        "button:has-text('Regenerate')",
        "button:has-text('Regenerate response')",
        "button:has-text('Try again')",
        "button:has-text('重新生成')",
        "button:has-text('重试')",
        "button:has-text('再试一次')",
    ],
    retry_button=[
        "button:has-text('Retry')",
        "button:has-text('Try again')",
        "button:has-text('重试')",
        "button:has-text('再试一次')",
        "button:has-text('重新连接')",
    ],
    file_input=[
        "input[type='file']",
    ],
    attach_button=[
        "button[data-testid*='attachment']",
        "button[data-testid*='file']",
        "button[aria-label*='Attach']",
        "button[aria-label*='Attach files']",
        "button[aria-label*='Add']",
        "button[aria-label*='Upload']",
        "button[aria-label*='附件']",
        "button[aria-label*='添加']",
        "button[aria-label*='上传']",
        "button:has-text('Attach')",
        "button:has(svg[aria-label*='Attach'])",
        "button:has(svg[aria-label*='Add'])",
    ],
    copy_button=[
        "button[data-testid*='copy']",
        "button[aria-label*='复制' i]",
        "button[aria-label*='Copy' i]",
        "button[aria-label*='Copy code' i]",
        "button[title*='复制' i]",
        "button[title*='Copy' i]",
        "button:has(svg[aria-label*='Copy'])",
    ],
    upload_menu_items=[
        '[role="menuitem"]:has-text("Upload")',
        '[role="menuitem"]:has-text("Upload file")',
        '[role="menuitem"]:has-text("Upload files")',
        '[role="menuitem"]:has-text("Add files")',
        '[role="menuitem"]:has-text("上传文件")',
        '[role="menuitem"]:has-text("上传")',
        '[role="menuitem"]:has-text("添加文件")',
        'button:has-text("Upload")',
        'button:has-text("Upload file")',
        'button:has-text("Upload files")',
        'button:has-text("Add files")',
        'button:has-text("上传文件")',
        'button:has-text("上传")',
        'button:has-text("添加文件")',
    ],
    artifact_candidates=[
        # links / buttons
        "a[download]",
        "a[href^='blob:']",
        "a[href*='/backend-api/files/']",
        "a[href*='download']",
        # ChatGPT file cards: often <a target=_blank rel=noreferrer> WITHOUT href
        "a[target='_blank'][rel*='noreferrer']:has-text('文件')",
        "a[target='_blank'][rel*='noreferrer']:has-text('File')",
        "a:has-text('Download')",
        "a:has-text('Download file')",
        "a:has-text('下载')",
        "a:has-text('下载文件')",
        "a:has-text('下载 CP')",
        "a:has-text('下载 CP (Markdown)')",
        "a:has-text('导出')",
        "a:has-text('Markdown')",
        "button:has-text('Download')",
        "button:has-text('Download file')",
        "button:has-text('下载')",
        "button:has-text('下载文件')",
        "button:has-text('下载 CP')",
        "button:has-text('下载 CP (Markdown)')",
        "button:has-text('导出')",
        "button[aria-label*='Download']",
        "button[aria-label*='下载']",
        "button[aria-label*='导出']",
        "button[title*='Download']",
        "button[title*='下载']",
        "button[title*='导出']",
        "[role='button']:has-text('下载')",
        "[role='button']:has-text('Download')",
        "[data-testid*='download']",
        "[data-testid*='Download']",
        # 卡片内可点击区域（ChatGPT 有时用 div 包一层，内部为可点链接/按钮）
        "[class*='artifact'] a",
        "[class*='file-card'] a",
        "[class*='attachment'] a",
    ],
    # model selector (ChatGPT 会经常改 DOM，这里多放一些候选，后续只需要改这块)
    model_switcher=[
        "button[data-testid*='model-switcher']",
        "button[aria-label*='Model']",
        "button:has-text('GPT')",
        "button:has-text('5.2')",
        "button:has-text('Thinking')",
        "button:has-text('思考')",
        "button:has-text('Pro')",
        "button:has-text('o1')",
        "button:has-text('4o')",
    ],
    model_switcher_role_names=[
        r"(model|gpt|o1|4o|5\.2|thinking|pro)",
    ],
    model_switcher_role_scopes=[
        "header",
    ],
    new_chat_button=[
        "a[aria-label*='New chat']",
        "button[aria-label*='New chat']",
        "a:has-text('New chat')",
        "button:has-text('New chat')",
        "a:has-text('新对话')",
        "button:has-text('新对话')",
    ],
    message_menu_button=[
        "button[aria-label*='More' i]",
        "button[aria-label*='更多' i]",
        "button[aria-label*='Message actions' i]",
        "[data-testid*='message-menu']",
        "button:has(svg):near([data-message-author-role='assistant'])",
    ],
    branch_in_new_chat_menuitem=[
        "[role='menuitem']:has-text('Branch in new chat')",
        "[role='menuitem']:has-text('新聊天中的分支')",
        "[role='menuitem']:has-text('在新对话中分支')",
        "button:has-text('Branch in new chat')",
        "button:has-text('新聊天中的分支')",
        "button:has-text('在新对话中分支')",
        "a:has-text('新聊天中的分支')",
        "[role='menuitem'][aria-label*='Branch' i]",
        "[role='menuitem']:has-text('分支')",
    ],
    # project page: Sources → Add sources → upload
    project_sources_tab=[
        "button:has-text('Sources')",
        "a:has-text('Sources')",
        "[role='tab']:has-text('Sources')",
        "[data-testid*='sources']",
        "button:has-text('来源')",
        "a:has-text('来源')",
    ],
    # Sources 面板内「添加」按钮：点击后出现弹窗
    project_add_sources_button=[
        "button:has-text('添加')",
        "a:has-text('添加')",
        "[role='button']:has-text('添加')",
        "button:has-text('Add sources')",
        "button:has-text('Add source')",
        "a:has-text('Add sources')",
        "[role='button']:has-text('Add sources')",
        "button:has-text('添加来源')",
        "a:has-text('添加来源')",
        "[data-testid*='add-source']",
        "[data-testid*='add-sources']",
    ],
    # 点击添加后出现的弹窗，在其内找「上传」按钮
    project_add_modal=[
        "[role='dialog']",
        "[role='alertdialog']",
        "[data-state='open']",
        "[class*='modal']",
        "[class*='Modal']",
        "[class*='dialog']",
        "[class*='Dialog']",
        "[class*='overlay']",
        "div[role='dialog']",
    ],
    project_upload_button=[
        "button:has-text('Upload')",
        "button:has-text('上传')",
        "[role='button']:has-text('Upload')",
        "[role='button']:has-text('上传')",
        "button:has(svg)[aria-label*='Upload' i]",
        "button:has(svg)[aria-label*='上传' i]",
    ],
    project_upload_file_input=[
        "input[type='file']",
    ],
)

# --------------------------
# Gemini: 100% 采用你当前在用的 selectors
# 并做 primary/fallback 拆分，避免把用户消息 article 也当成模型回复
# --------------------------
_GEMINI_RESPONSE_PRIMARY = [
    "message-content",
    "[data-test-id*='model-response']",
    "[data-testid*='model-response']",
    "[class*='model-response']",
    "[class*='message-content']",
    "md-article",
    # 更精确的选择器：排除用户消息，只匹配助手回复
    "div[role='feed'] article:has([class*='model-response']):not(:has([class*='user-message']))",
    "article:has([data-testid*='model-response'])",
    "article:has([data-test-id*='model-response'])",
]
_GEMINI_RESPONSE_FALLBACK = [
    # 更保守的 fallback：确保不是用户消息
    "div[role='feed'] article:not(:has([class*='user'])):not(:has([aria-label*='user' i]))",
    "article[role='article']:not(:has([class*='user']))",
    "[aria-live='polite'] article:not(:has([class*='user']))",
    "md-content article:not(:has([class*='user']))",
    # 最后 resort：所有 article，但需要后续过滤
    "div[role='feed'] article",
    "article[role='article']",
]

GEMINI_SELECTORS = PlatformSelectors(
    start_url="https://gemini.google.com/app",

    chat_root=[
        "main",
        "div[role='main']",
        "div[role='feed']",
    ],

    prompt_box=[
        ".ql-editor",  # Gemini uses quill editor
        '[contenteditable="true"][role="textbox"]',
        '[contenteditable="true"]',
        'rich-textarea [contenteditable="true"]',
        'textarea[placeholder*="Message" i]',
        'textarea[placeholder*="消息" i]',
    ],

    send_button=[
        'button[aria-label*="发送" i]',
        "button.send-button",
        'button[class*="send-button"]',
        'button[aria-label*="Send" i]',
        "button:has(svg)",
        'button[aria-label*="Submit" i]',
        'button[type="submit"]',
    ],

    attach_button=[
        'button[aria-label*="打开文件上传菜单" i]',
        'button[aria-label*="文件上传" i]',
        "button.upload-card-button",
        'button[class*="upload-card-button"]',
        'button[aria-label*="Add" i]',
        'button[aria-label*="添加" i]',
        'button[aria-label*="Attach" i]',
        'button[aria-label*="附件" i]',
        'button[title*="Add" i]',
        'button[title*="添加" i]',
        'button[title*="Attach" i]',
        '[role="button"][aria-label*="Add" i]',
        '[role="button"][aria-label*="Attach" i]',
    ],

    upload_menu_items=[
        '[role="menuitem"]:has-text("上传文件")',
        '[role="menuitem"]:has-text("Upload")',
        '[role="menuitem"]:has-text("Add photos")',
        '[role="menuitem"]:has-text("Add photos & files")',
        'button:has-text("上传文件")',
        'button:has-text("Upload")',
    ],

    assistant_message_blocks=_GEMINI_RESPONSE_PRIMARY,
    assistant_message_blocks_fallback=_GEMINI_RESPONSE_FALLBACK,

    copy_button=[
        'button[aria-label*="复制" i]',
        'button[aria-label*="Copy" i]',
        'button[title*="复制" i]',
        'button[title*="Copy" i]',
        "[data-testid*='copy']",
        ".action-button",
    ],

    stop_button=[
        'button[aria-label*="停止" i]',
        'button[aria-label*="Stop" i]',
        'button[title*="停止" i]',
        'button[title*="Stop" i]',
        "[data-testid*='stop']",
    ],

    assistant_text_blocks=[
        "div.markdown",
        "div[class*='markdown']",
        ":scope",
    ],

    file_input=[
        "input[type='file']",
    ],

    artifact_candidates=[
        # links / buttons
        "a[download]",
        "a[href^='blob:']",
        "a[href*='/backend-api/files/']",
        "a[href*='download']",
        # ChatGPT file cards: often <a target=_blank rel=noreferrer> WITHOUT href
        "a[target='_blank'][rel*='noreferrer']:has-text('文件')",
        "a[target='_blank'][rel*='noreferrer']:has-text('File')",
        "a:has-text('Download')",
        "a:has-text('Download file')",
        "a:has-text('下载')",
        "a:has-text('下载文件')",
        "a:has-text('下载 CP')",
        "a:has-text('下载 CP (Markdown)')",
        "a:has-text('导出')",
        "button:has-text('Download')",
        "button:has-text('Download file')",
        "button:has-text('下载')",
        "button:has-text('下载文件')",
        "button:has-text('下载 CP')",
        "button:has-text('下载 CP (Markdown)')",
        "button:has-text('导出')",
        "button[aria-label*='Download']",
        "button[aria-label*='下载']",
        "button[aria-label*='导出']",
        "button[title*='Download']",
        "button[title*='下载']",
        "button[title*='导出']",
        "[role='button']:has-text('下载')",
        "[role='button']:has-text('Download')",
        "a:has-text('Markdown')",
        "[data-testid*='download']",
        "[data-testid*='Download']",
    ],

    # Gemini 模型选择：同样放多一些候选
    model_switcher=[
        "button[aria-label*='模型' i]",
        "button[aria-label*='Model' i]",
        "button:has-text('Pro')",
        "button:has-text('Flash')",
        "button:has-text('Advanced')",
        "button:has-text('1.5')",
        "button:has-text('2.0')",
    ],
    model_switcher_role_names=[
        r"(模型|model|pro|flash|advanced|1\.5|2\.0|3)",
    ],
    model_switcher_role_scopes=[
        "header",
    ],
    new_chat_button=[
        "a:has-text('New chat')",
        "button:has-text('New chat')",
        "a:has-text('新对话')",
        "button:has-text('新对话')",
    ],
)


# --------------------------
# 智联招聘简历筛：推荐候选人列表页
# 选择器来源：content-jianlishai-selectors.js（插件项目）
# 平台：https://rd6.zhaopin.com
# --------------------------
ZHAOPIN_RESUME_SELECTORS = PlatformSelectors(
    start_url="https://rd6.zhaopin.com/",

    # 候选人列表容器
    chat_root=[
        'div[class*="recommend-list"]',
        '[role="list"]',
        '[role="listitem"]',
    ],

    # 候选人卡片（每张卡片对应一个候选人）
    # 来源：ZhaopinAdapter.card_selector
    assistant_message_blocks=[
        "div.resume-item__content.resume-card-exp",
        'div[class*="recommend-item__inner-content"]',
        'div[class*="resume-item__content resume-card-exp"]',
    ],
    assistant_message_blocks_fallback=[
        'div[class*="resume-item__content"]',
        'div[class*="recommend-item__"]',
    ],

    # 候选人姓名（点击可跳转详情）
    # 来源：ZhaopinAdapter.name_selector
    prompt_box=[
        'div[class*="talent-basic-info__name--inner"]',
        'div[class*="talent-basic-info__name--inner"][title]',
    ],

    # 打招呼按钮（详情页中）
    send_button=[
        'div[class*="resume-btn__inner"]:has-text("打招呼")',
        'div[class*="resume-btn__text"]:has-text("打招呼")',
        'button:has(div[class*="resume-btn__text"]):has-text("打招呼")',
        'button:has-text("打招呼")',
    ],

    # 详情弹窗/容器（打开后等待其出现）
    stop_button=[
        ".km-dialog__wrapper",
        ".km-overlay",
        ".modal-wrapper",
        ".candidate-detail",
        ".detail-modal",
        'div[class*="resume-detail-container"]',
        'div[class*="resume-detail-modal"]',
    ],

    # 关闭详情按钮
    regenerate_button=[
        ".close-btn",
        ".modal-close",
        'button[class*="close"]',
        ".km-dialog__close",
        '[aria-label*="关闭"]',
        '[aria-label*="Close"]',
    ],

    # 候选人基本信息字段
    assistant_text_blocks=[
        'div[class*="talent-basic-info"]',
        'div[class*="resume-detail"]',
        ":scope",
    ],

    # 工作经历
    user_message_blocks=[
        'table[class*="talent-experience"] tr:not(.edu-exp-tr)',
        'div[class*="work-section"]',
        'div[class*="experience"]',
    ],

    # 教育经历
    copy_button=[
        'div[class*="new-education-experiences__item"]',
        'div[class*="education-section"]',
        'div[class*="education"]',
    ],

    # 下一页/加载更多（翻页用）
    retry_button=[
        'button[class*="km-button"]:has-text("下一页")',
        'button:has-text("下一页")',
        '[aria-label*="下一页"]',
        'button[class*="km-button"]:has-text("加载更多")',
    ],

    # 付费/敏感词提示（遇到时跳过）
    artifact_candidates=[
        'div[class*="pay-tip"]',
        'div[class*="vip-tip"]',
        'span:has-text("金币")',
        'span:has-text("VIP")',
        'span:has-text("付费")',
    ],
)
