# Changelog

所有显著变更均记录于此文件。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

---

## [0.0.1.0] — 2026-02-25 · dev/0.0.1.0

### Added

#### Agent 注册体系
- `agents/registry.py`：新 agent 注册入口，`setup_agents()` 由 `ROLE_SPECS` 驱动，不再硬编码 agent 列表
- `agents/roles/specs.py`：`RoleSpec` 数据类 + `ROLE_SPECS` 列表，单一位置维护所有角色定义
- `agents/__init__.py`、`agents/roles/__init__.py`：模块导出

#### 浏览器自动化基础层（`agno1/browser_automation/`）
- `base.py`：`BaseChatAdapter`、`ExecutionConfig`、`ExecuteMode` 抽象
- `gpt.py`：`ChatGPTAdapter`（send-prompt / extract-latest / download-artifact）
- `gemini.py`：`GeminiAdapter`
- `manager.py`：`BrowserManager`、`BrowserConfig`、`PageHandle`、`SessionState`
- `selectors.py`：`CHATGPT_SELECTORS`、`GEMINI_SELECTORS`、`PlatformSelectors`
- `spec.py`：`RunSpec` YAML 协议定义
- `run_from_spec.py`：RunSpec 执行器入口
- `runspec_executor.py`：RunSpec 步骤执行逻辑
- `errors.py`：`AutomationError`、`CDPConnectionError`、`SelectorNotFoundError` 等
- `diagnostics.py`：浏览器连接与 selector 诊断工具
- `utils.py`：公共工具函数

#### 文档图助手
- `agno1/pipelines/docviz_diagram_chatgpt.py`：图助手流水线（agent1_figure_plan → agent2_ambiguity_checker → agent3_mermaid_author → agent4_mermaid_qa → agent5 本地渲染），支持 `--resume`、`--no-reply` 断点续跑
- `agents/skills/docviz-doc-diagram-assistant/`：各 agent SKILL 文件（agent1~5、agent0_oasm、agent0_orchestrator、sub_agent）及 PATCH_NOTES
- `jobs/docviz_diagram/config.yaml`：图助手 job 配置模板

#### 流水线基础设施
- `scripts/run_pipeline.py`：统一流水线运行入口，`--list` 列出所有可用流水线
- `docs/pipelines/PIPELINES.md`：流水线索引（含功能描述与运行示例）
- `docs/pipelines/docviz_diagram.md`：图助手使用说明（前提条件、调用方式、运行过程）

### Changed

- `agno1/agent.py`：改为兼容包装层，`setup_agents` 与 `AgentBundle` 委托至 `agents/registry.py`
- `agno1/agent_os.py`：适配新 `AgentBundle` 结构
- `agno1/app.py`：适配新 agent 注册入口

---

## 历史提交（main 分支，未整理）

| commit | 说明 |
|--------|------|
| ae5e321 | docup |
| 4c52efd | kb fixup |
| 49126f7 | minor fix |
| 3ef68e1 | fixup |
| 0adc49d | kb fixup |
| 5665ecc | typeup |
| 4aa263a | knowledge base working with nanovecdb from lightrag |
| 4896058 | add doubao embedder |
