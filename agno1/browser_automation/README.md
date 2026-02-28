# browser_automation 使用说明与维护文档

本文档面向 `agno1/browser_automation/` 模块的使用与维护，覆盖 CDP 连接、运行方式、配置结构、常见故障排查和改造约束。

## 概览

该模块采用 Playwright Sync API，通过 CDP 连接已启动的 Chrome（默认 9222），并复用登录态执行对话/上传/下载等操作。

核心设计理念：
- Agent 决策 + Playwright 确定性执行（输入、点击、轮询、下载）
- 通过 `selectors.py` 统一管理 DOM 选择器
- 通过 `run_from_spec.py` 以 RunSpec（YAML/JSON）实现“改 prompt 不改代码”

## 目录结构

- `base.py`：核心执行逻辑（发送、等待、抽取、下载、重试）
- `manager.py`：Playwright 生命周期与会话管理（CDP attach / launch）
- `selectors.py`：平台选择器与诊断选择器
- `gpt.py` / `gemini.py`：平台适配器（模型选择、上传策略）
- `spec.py`：RunSpec 协议与模板渲染
- `run_from_spec.py`：RunSpec 执行器（推荐入口）
流水线入口位置：
- `agno1/pipelines/chatgpt_continue_loop.py`
- `agno1/pipelines/chatgpt_project_ao_dna.py`
- `utils.py`：通用工具（重试、路径处理、CDP 端点规范化）
- `errors.py`：统一异常类型（CDP/Selector/Page）
- `diagnostics.py`：诊断采集与 debug 产物生成

## 快速开始

### 1) 启动 Chrome (CDP)

流水线通过 CDP（Chrome DevTools Protocol）连接并控制浏览器，需要用一个**已登录目标网站的 Chrome** 以远程调试模式运行。

**macOS：**
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-cdp
```

**Linux：**
```bash
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-cdp
```

**Windows（PowerShell）：**
```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="$env:TEMP\chrome-cdp"
```

> 也可使用项目根目录的 `start_with_chrome.sh` 脚本（已封装上述参数）。

启动后在该 Chrome 窗口中登录目标网站（如 ChatGPT），流水线会复用已登录会话。

#### 获取完整 CDP 端点地址

Chrome 启动后访问：
```
http://127.0.0.1:9222/json/version
```

返回 JSON 示例：
```json
{
  "Browser": "Chrome/120.0.0.0",
  "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/025ea4b5-b6bb-4804-81f7-3b90b28ff4e8"
}
```

`webSocketDebuggerUrl` 就是完整 CDP 端点，其中的 UUID 是**本次启动的实例 ID，每次重启 Chrome 都会变**。

**配置方式（优先级从高到低）：**

| 方式 | 示例 |
|------|------|
| 环境变量 | `CDP_ENDPOINT="http://127.0.0.1:9222"` |
| config.yaml | `cdp: "http://127.0.0.1:9222"` |
| 命令行参数 | `--cdp "http://127.0.0.1:9222"` |

- 填 `http://127.0.0.1:9222`：流水线自动调用 `/json/version` 获取 WebSocket URL，**无需手动填 UUID**（推荐）。
- 填 `ws://127.0.0.1:9222/devtools/browser/<UUID>`：直接指定实例，适合同时开多个 Chrome 的场景。
- **不填**：流水线自动探测本机 `127.0.0.1:9222`，大多数情况下无需配置。

### 2) RunSpec 方式（推荐）

```bash
python -m agno1.browser_automation.run_from_spec --spec jobs/chatgpt_continue_loop.yaml
```

可注入变量：

```bash
python -m agno1.browser_automation.run_from_spec --spec jobs/commodity_pipeline.yaml --set ticker=ao
```

断点恢复（step 级别）：

```bash
python -m agno1.browser_automation.run_from_spec --spec jobs/chatgpt_continue_loop.yaml --resume
```

如需指定 manifest：

```bash
python -m agno1.browser_automation.run_from_spec --spec jobs/chatgpt_continue_loop.yaml --resume --manifest /path/to/.manifest.json
```

### 3) 示例入口（仍可用）

```bash
python -m agno1.pipelines.chatgpt_continue_loop --url "https://chatgpt.com/c/..."
python -m agno1.pipelines.chatgpt_project_ao_dna --mode ao-dna
```

统一入口脚本：

```bash
python scripts/run_pipeline.py --list
python scripts/run_pipeline.py chatgpt_continue_loop --url "https://chatgpt.com/c/..."
```

## RunSpec 使用规范

RunSpec 位于 `spec.py`，支持 YAML/JSON。建议将对话流程写在 `jobs/*.yaml`。

关键字段：
- `defaults.platform`：`chatgpt` / `gemini`
- `defaults.browser`：CDP 端点、超时、下载目录等
- `defaults.exec`：执行策略（超时、稳定窗口、重试等）
- `steps[].prompt`：本轮指令
- `steps[].files`：上传文件列表
- `steps[].download_after`：是否尝试下载附件
- `steps[].repeat`：循环次数

模板语法：
- `${var}`：从 `vars` 中读取变量
- `{{ python_expr }}`：基于上下文表达式（`steps.<id>` 可引用结果）

## 关键配置（ExecutionConfig）

以下字段直接影响稳定性：
- `generation_timeout_s`：单轮生成硬超时
- `stable_text_window_s`：结束判定的稳定窗口
- `prompt_ready_timeout_s`：输入框就绪等待
- `force_end_if_stop_visible_s` / `force_end_min_text_chars`：stop 按钮常驻时的兜底
- `send_ack_timeout_s` / `send_max_attempts`：发送确认与幂等策略

## Selector 维护

所有选择器集中在 `selectors.py`：
- `CHATGPT_SELECTORS`
- `GEMINI_SELECTORS`

新增/修复选择器必须优先修改该文件，避免散落在逻辑层。

若需拓展模型选择按钮识别：
- 使用 `model_switcher_role_names`（正则字符串列表）
- 使用 `model_switcher_role_scopes`（限定搜索范围）

## Debug 与诊断产物

失败时自动生成 `_debug` 目录：
- `*_diagnostics.json`：结构化诊断信息
- `*.png`：截图（可关闭）
- `*.html`：完整 DOM（可关闭）

可在 `BrowserConfig` 中配置：
- `capture_debug_screenshot`
- `capture_debug_html`

## 常见问题排查

1) CDP 连接失败  
确认 Chrome 启动参数包含 `--remote-debugging-port=9222`，并检查端口是否被占用。

2) 输入框找不到  
更新 `selectors.py` 的 `prompt_box`，并适当增大 `prompt_ready_timeout_s`。

3) 生成超时  
检查 `stop_button` 选择器是否命中正确元素，必要时调大 `stable_text_window_s`。

4) 多轮重复或混入历史  
观察 `diagnostics.json` 中 `assistant_blocks` 和 `last_assistant_text_len`，确认虚拟列表行为是否变化。

## 维护规范

- 不直接删除旧文件，使用 `legacy_backup_[timestamp]/` 备份
- 新增功能优先通过 RunSpec 扩展，不直接改脚本入口
- 所有选择器修改必须经过 `selectors.py`
- CDP/页面关闭异常应抛出 `errors.py` 中的专用异常

## 开发建议

- 建议在 CI 中使用 `run_from_spec.py --dry-run` 验证 spec 结构
- 需要重构时先补单测，再拆模块，避免破坏稳定性
