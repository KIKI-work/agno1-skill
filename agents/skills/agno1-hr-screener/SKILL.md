---
name: agno1-hr-screener
description: "多平台招聘简历筛自动化代理。通过 Playwright 连接已登录的 Chrome，在各招聘平台的推荐候选人列表页自动扫描简历、调用 AI 筛选、对目标候选人点击打招呼。当前已支持：智联招聘、BOSS 直聘（适配器占位）。"
---

# HR 简历筛选代理（agno1-hr-screener）

## 概述

本代理通过 Playwright CDP attach 模式连接已登录的真实 Chrome 浏览器，驱动对应平台的流水线，在推荐人才列表页实现全自动简历筛选：

1. 确认目标平台与 URL
2. 扫描当前页面所有候选人卡片
3. （可选）关键词预筛过滤——命中则直接排除，不调用 AI
4. 点击卡片，打开简历详情弹窗，提取简历信息
5. 调用 AI（OpenAI 兼容协议）判断是否目标候选人
6. 通过 → 点击「打招呼」；拒绝 → 跳过并关闭弹窗
7. 输出 JSON 报告与运行日志

开始执行时先声明：`我正在使用 agno1-hr-screener skill 执行简历筛选任务。`

## 触发条件

以下情况必须使用本 skill：

- 用户要求「开始简历筛选」「扫描候选人」「筛简历」「自动打招呼」
- 需要在任意招聘平台推荐人才页批量处理候选人

## 已支持平台

| 平台 | 流水线入口 | 输出目录 |
|------|-----------|---------|
| 智联招聘 | `agno1/pipelines/zhaopin/zhilian/zhilian_screener.py` | `artifacts/zhaopin/zhilian/` |
| BOSS 直聘 | `agno1/pipelines/zhaopin/boss/boss_screener.py` | `artifacts/zhaopin/boss/` |

> 新增平台时，在对应子目录下添加适配器和流水线，本 skill 无需修改。

## 执行前必须确认的参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `platform` | 目标平台 | `智联招聘` / `BOSS直聘` |
| `url` | 推荐候选人列表页 URL（由用户提供，因平台而异） | — |
| `ai_target` | AI 筛选目标描述 | 见下方默认值 |

**`ai_target` 默认值**（用户未指定时使用）：

```
学校要求：第一学历为985211院校，但排除非强势计算机理工背景的985211学校、或政策照顾性985/211学校。年限要求：年龄22-30岁，排除27年及之后的应届生。专业要求：专业不限，非理工科专业可以放行。备注：如果没有本科学校信息，只有硕士学校信息，且硕士学校符合要求，返回为true。
```

**`excluded_keywords` 默认值**（用户未指定时使用）：

```
教培 电气 嵌入式硬件开发 车辆工程 教师 产品经理 27年应届 材料科学 芯片测试 工艺工程 银行 游戏
```

可选参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `excluded_keywords` | 见上方默认值 | 关键词排除列表，命中则跳过（不调用 AI） |
| `ai_intensity` | `balanced` | 筛选强度：`strict` \| `balanced` \| `loose` |
| `page_stay_time` | `3-5` | 每次处理间隔秒数范围（风控） |
| `dry_run` | `false` | 仅提取信息，不打招呼（首次验证用） |

## 调用方式

### 智联招聘

```bash
# 标准运行（url 由用户提供）
python -m agno1.pipelines.zhaopin.zhilian.zhilian_screener \
    --url "<智联招聘推荐候选人列表页 URL>" \
    --ai-target "学校要求：第一学历为985211院校，但排除非强势计算机理工背景的985211学校、或政策照顾性985/211学校。年限要求：年龄22-30岁，排除27年及之后的应届生。专业要求：专业不限，非理工科专业可以放行。备注：如果没有本科学校信息，只有硕士学校信息，且硕士学校符合要求，返回为true。" \
    --exclude-keywords 教培 电气 嵌入式硬件开发 车辆工程 教师 产品经理 27年应届 材料科学 芯片测试 工艺工程 银行 游戏

# 首次验证（dry-run，不打招呼）
python -m agno1.pipelines.zhaopin.zhilian.zhilian_screener \
    --url "<智联招聘推荐候选人列表页 URL>" \
    --ai-target "学校要求：第一学历为985211院校，但排除非强势计算机理工背景的985211学校、或政策照顾性985/211学校。年限要求：年龄22-30岁，排除27年及之后的应届生。专业要求：专业不限，非理工科专业可以放行。备注：如果没有本科学校信息，只有硕士学校信息，且硕士学校符合要求，返回为true。" \
    --exclude-keywords 教培 电气 嵌入式硬件开发 车辆工程 教师 产品经理 27年应届 材料科学 芯片测试 工艺工程 银行 游戏 \
    --dry-run
```

### BOSS 直聘

```bash
# 标准运行（url 由用户提供）
python -m agno1.pipelines.zhaopin.boss.boss_screener \
    --url "<BOSS 直聘推荐候选人列表页 URL>" \
    --ai-target "学校要求：第一学历为985211院校，但排除非强势计算机理工背景的985211学校、或政策照顾性985/211学校。年限要求：年龄22-30岁，排除27年及之后的应届生。专业要求：专业不限，非理工科专业可以放行。备注：如果没有本科学校信息，只有硕士学校信息，且硕士学校符合要求，返回为true。" \
    --exclude-keywords 教培 电气 嵌入式硬件开发 车辆工程 教师 产品经理 27年应届 材料科学 芯片测试 工艺工程 银行 游戏
```

### Python 直接调用

```python
_AI_TARGET = (
    "学校要求：第一学历为985211院校，但排除非强势计算机理工背景的985211学校、或政策照顾性985/211学校。"
    "年限要求：年龄22-30岁，排除27年及之后的应届生。"
    "专业要求：专业不限，非理工科专业可以放行。"
    "备注：如果没有本科学校信息，只有硕士学校信息，且硕士学校符合要求，返回为true。"
)
_EXCLUDE_KW = [
    "教培", "电气", "嵌入式硬件开发", "车辆工程", "教师", "产品经理",
    "27年应届", "材料科学", "芯片测试", "工艺工程", "银行", "游戏",
]

# 智联招聘
from agno1.pipelines.zhaopin.zhilian.zhilian_screener import run_screener
result = run_screener(
    url="<智联招聘推荐候选人列表页 URL>",
    ai_target=_AI_TARGET,
    excluded_keywords=_EXCLUDE_KW,
    dry_run=True,
)
print(result["stats"])

# BOSS 直聘
from agno1.pipelines.zhaopin.boss.boss_screener import run_screener as boss_run
result = boss_run(
    url="<BOSS 直聘推荐候选人列表页 URL>",
    ai_target=_AI_TARGET,
    excluded_keywords=_EXCLUDE_KW,
    dry_run=True,
)
print(result["stats"])
```

## 报告输出

JSON 报告写入对应平台目录，格式统一：

```json
{
  "status": "complete",
  "stats": {
    "total": 25,
    "passed": 8,
    "rejected_keyword": 3,
    "rejected_ai": 12,
    "skipped": 0,
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
  "report_path": "artifacts/zhaopin/zhilian/zhilian_screener_时间戳.json"
}
```

`action` 字段取值说明：

| 值 | 含义 |
|----|------|
| `greeted` | AI 通过，已点击打招呼 |
| `dry_run_passed` | AI 通过，dry-run 模式未打招呼 |
| `rejected_keyword` | 关键词预筛排除（不调用 AI） |
| `rejected_ai` | AI 判断不符合 |
| `skipped` | 风控随机跳过 |
| `failed` | 处理失败（卡片未找到、弹窗异常等） |

## 故障排查

### CDP 连接失败

```
请以 --remote-debugging-port=9222 启动 Chrome：
  Windows: chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\tmp\chrome_debug
```

确认 Chrome 已启动并已登录对应平台后重试。

### 未识别到候选人卡片

- 确认当前浏览器页面是对应平台的「推荐人才」列表页
- 确认页面已完全加载，候选人卡片可见
- 若仍无法识别，将问题转交 `agno1-selector-watchdog` 进行诊断

### 选择器失效（平台改版后）

将以下信息提供给 `agno1-selector-watchdog`：
- 当前页面 URL 及平台名称
- 失效的操作（卡片点击 / 弹窗等待 / 打招呼按钮）
- 对应平台的 `*_selectors.py` 中当前选择器内容

watchdog 会抓取页面快照，提供更新后的选择器建议。

## 文件索引

### 智联招聘

| 文件 | 说明 |
|------|------|
| `agno1/browser_automation/zhaopin/zhilian/zhilian_resume.py` | 适配器：DOM 操作封装（卡片获取、弹窗操作、信息提取） |
| `agno1/browser_automation/zhaopin/zhilian/zhilian_selectors.py` | `ZHILIAN_RESUME_SELECTORS`：所有选择器集中维护 |
| `agno1/pipelines/zhaopin/zhilian/zhilian_screener.py` | 主流水线：AI 筛选逻辑、主循环、报告输出 |

### BOSS 直聘

| 文件 | 说明 |
|------|------|
| `agno1/browser_automation/zhaopin/boss/boss_resume.py` | 适配器（待实现） |
| `agno1/browser_automation/zhaopin/boss/boss_selectors.py` | 选择器（待实现） |
| `agno1/pipelines/zhaopin/boss/boss_screener.py` | 主流水线（待实现） |

### 公共

| 文件 | 说明 |
|------|------|
| `agents/roles/specs.py` | `RoleSpec(id="agno1-hr-screener", ...)` |
| `agents/skills/agno1-hr-screener/SKILL.md` | 本文件 |

## 输出目录

| 平台 | JSON 报告 | 运行日志 |
|------|-----------|---------|
| 智联招聘 | `artifacts/zhaopin/zhilian/zhilian_screener_时间戳.json` | `logs/zhaopin/zhilian/zhilian_screener_时间戳.log` |
| BOSS 直聘 | `artifacts/zhaopin/boss/boss_screener_时间戳.json` | `logs/zhaopin/boss/boss_screener_时间戳.log` |

## 不做什么

- 不在未确认平台的情况下自行选择流水线
- 不跳过「执行前参数确认」步骤
- 不在 `dry_run=False` 时自动推断用户意图，打招呼前必须有明确的 AI 通过结论
- 不修改 `browser_automation` 底层协议
