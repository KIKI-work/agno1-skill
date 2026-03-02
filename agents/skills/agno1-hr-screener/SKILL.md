---
name: agno1-hr-screener
description: "智联招聘简历筛自动化代理。通过 Playwright 连接已登录的 Chrome，在推荐候选人列表页自动扫描简历、调用 AI 筛选、对目标候选人点击打招呼。"
---

# HR 简历筛选代理（agno1-hr-screener）

## 概述

本代理通过 Playwright CDP attach 模式连接已登录的真实 Chrome 浏览器，驱动 `agno1/pipelines/zhaopin_resume_screener.py` 流水线，在智联招聘推荐人才列表页实现全自动简历筛选：

1. 扫描当前页面所有候选人卡片
2. （可选）关键词预筛过滤
3. 点击卡片，打开简历详情弹窗，提取简历信息
4. 调用 AI（OpenAI 兼容协议）判断是否目标候选人
5. 通过 → 点击「打招呼」；拒绝 → 跳过并关闭弹窗
6. 输出 JSON 报告

开始执行时先声明：`我正在使用 agno1-hr-screener skill 执行简历筛选任务。`

## 触发条件

以下情况必须使用本 skill：

- 用户要求「开始简历筛选」「扫描候选人」「筛简历」「自动打招呼」
- 需要在智联招聘推荐人才页批量处理候选人

## 执行前必须确认的参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `url` | 推荐候选人列表页 URL | `https://rd6.zhaopin.com/app/talent/recommend` |
| `ai_target` | AI 筛选目标描述 | `985/211本科、3年以上Python经验` |
| `max_greet` | 最大打招呼数量（默认 50） | `20` |

可选参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `excluded_keywords` | 无 | 关键词排除列表，命中则跳过（不调用 AI） |
| `ai_intensity` | `balanced` | 筛选强度：`strict` \| `balanced` \| `loose` |
| `page_stay_time` | `3-5` | 每次处理间隔秒数范围（风控） |
| `dry_run` | `false` | 仅提取信息，不打招呼（首次验证用） |

## 调用方式

```bash
# 标准运行
python -m agno1.pipelines.zhaopin_resume_screener \
    --url "https://rd6.zhaopin.com/app/talent/recommend" \
    --ai-target "985/211本科、3年以上Python经验的候选人" \
    --max-greet 20

# 首次验证（dry-run，不打招呼）
python -m agno1.pipelines.zhaopin_resume_screener \
    --url "https://rd6.zhaopin.com/app/talent/recommend" \
    --ai-target "适合职位的候选人" \
    --dry-run

# 带关键词排除
python -m agno1.pipelines.zhaopin_resume_screener \
    --url "https://rd6.zhaopin.com/app/talent/recommend" \
    --ai-target "Java 后端开发工程师" \
    --exclude-keywords "外包" "实习" \
    --max-greet 30
```

也可以在 Python 中直接调用：

```python
from agno1.pipelines.zhaopin_resume_screener import run_screener

result = run_screener(
    url="https://rd6.zhaopin.com/app/talent/recommend",
    ai_target="985/211本科、3年以上Python经验",
    max_greet=20,
    dry_run=True,   # 首次建议先 dry-run
)
print(result["stats"])
```

## 报告输出

结果写入 `artifacts/zhaopin_screener/screener_report.json`，格式：

```json
{
  "status": "complete",
  "stats": {
    "total": 25,
    "passed": 8,
    "rejected_keyword": 3,
    "rejected_ai": 12,
    "failed": 2
  },
  "results": [
    {
      "card_id": "list_candidate_0",
      "name": "张三",
      "action": "greeted",
      "reason": "985院校本科，5年Python经验，符合要求",
      "ai_result": {"is_target": true, "reason": "...", "_parse_failed": false}
    }
  ],
  "report_path": "artifacts/zhaopin_screener/screener_report.json"
}
```

`action` 字段取值说明：

| 值 | 含义 |
|----|------|
| `greeted` | AI 通过，已点击打招呼 |
| `dry_run_passed` | AI 通过，dry-run 模式未打招呼 |
| `rejected_keyword` | 关键词预筛排除 |
| `rejected_ai` | AI 判断不符合 |
| `failed` | 处理失败（卡片未找到、弹窗异常等） |

## 故障排查

### CDP 连接失败

```
请以 --remote-debugging-port=9222 启动 Chrome：
  Windows: chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\tmp\chrome_debug
```

确认 Chrome 已启动并已登录智联招聘后重试。

### 未识别到候选人卡片

- 确认当前浏览器页面是智联招聘「推荐人才」列表页（URL 包含 `/app/talent/recommend`）
- 确认页面已完全加载，候选人卡片可见
- 若仍无法识别，将问题转交 `agno1-selector-watchdog` 进行诊断

### 选择器失效（智联招聘改版后）

将以下信息提供给 `agno1-selector-watchdog`：
- 当前页面 URL
- 失效的操作（卡片点击 / 弹窗等待 / 打招呼按钮）
- `agno1/browser_automation/selectors.py` 中 `ZHAOPIN_RESUME_SELECTORS` 的当前选择器

watchdog 会抓取页面快照，提供更新后的选择器建议。

## 文件索引

| 文件 | 说明 |
|------|------|
| `agno1/browser_automation/zhaopin_resume.py` | 适配器：DOM 操作封装（卡片获取、弹窗操作、信息提取） |
| `agno1/browser_automation/selectors.py` | `ZHAOPIN_RESUME_SELECTORS`：所有选择器集中维护 |
| `agno1/pipelines/zhaopin_resume_screener.py` | 主流水线：AI 筛选逻辑、主循环、报告输出 |
| `agents/roles/specs.py` | `RoleSpec(id="agno1-hr-screener", ...)` |
| `agents/skills/agno1-hr-screener/SKILL.md` | 本文件 |

## 不做什么

- 不处理非智联招聘平台（BOSS 直聘、猎聘等需单独新增适配器）
- 不跳过「执行前参数确认」步骤
- 不在 `dry_run=False` 时自动推断用户意图，打招呼前必须有明确的 AI 通过结论
- 不修改 `browser_automation` 底层协议
