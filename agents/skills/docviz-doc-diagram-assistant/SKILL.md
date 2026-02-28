---
name: docviz-doc-diagram-assistant
description: "文档关系图助手的总控 skill。将用户需求拆解为多步技能，发送至 ChatGPT 项目 AI 执行：分解意图、画图计划、调用工具画图、渲染、插入到文档指定位置。项目与文档可更换。"
---

# 文档关系图助手（DocViz Diagram Assistant）

## 概述

文档关系图助手是一套**技能驱动的协作流程**，运行在 ChatGPT 项目中。核心模式是：

- **项目可更换**：使用 [生成文档图助手](https://chatgpt.com/g/g-p-698ad99f54848191ad82a3dc6f887b08-sheng-cheng-wen-dang-tu-zhu-shou/project) 作为执行环境，项目内包含的文档可随任务更换；
- **技能发送执行**：本 skill 将任务拆解为多个子技能，按序发送给项目内的 AI 执行；
- **多技能协作**：图关系助手包含五个主要技能，依次完成从意图理解到图落地的完整链路。

开始执行时先声明：`我正在使用 docviz-doc-diagram-assistant skill 执行文档关系图任务。`

## 正确执行流程（必须：先导航到项目，再跑流水线）

**不得跳过「导航 → 流水线」顺序**：先通过 **Playwright/浏览器** 导航到对应项目（如 ChatGPT「生成文档图助手」项目），再按图助手流水线处理。禁止在未进入项目、未走流水线的情况下，仅在本地生成 Mermaid/图并写文件。

1. **导航到项目**  
   使用 Playwright 或本仓库的 browser_automation（BrowserManager + ChatGPTAdapter）打开目标项目页：
   - 默认项目 URL：<https://chatgpt.com/g/g-p-698ad99f54848191ad82a3dc6f887b08-sheng-cheng-wen-dang-tu-zhu-shou/project>
   - 在项目内新开对话或复用已有对话，以便后续步骤在项目上下文中执行。
2. **按图助手流水线处理**  
   在已打开的项目内依次执行：入口确认（文档范围 + 画图意图）→ 范围判断（单文档/文件夹）→ 分解意图 → 画图计划 → 歧义检查 → 画图 → QA → 渲染与插入。

**本仓库的自动化实现**：`agno1/pipelines/docviz_diagram_chatgpt.py` 已实现上述流程——内部用 Playwright（BrowserManager）导航到上述项目 URL，打包 `--scope-docs` 指定文档并上传，再在项目内驱动 agent1～agent5。执行图助手任务时，应优先调用该流水线（或与之等价的「导航 + 流水线」组合），而不是在本地直接生成图。

```bash
# 示例：指定文档范围与产出目录（需已启动 Chrome 并暴露 CDP，见仓库文档）
CDP_ENDPOINT="ws://127.0.0.1:9222/..." python -m agno1.pipelines.docviz_diagram_chatgpt \
  --scope-docs path/to/doc1.md path/to/doc2.md --out-dir artifacts/docviz_diagram
```

## 图助手需要做的事情（入口流程）

1. **触发**：用户提出画图需求；当检索/识别到该需求时，调用文档关系画图助手。
2. **先与用户确认**：
   - **文档范围**：要基于哪些文档画图（哪些文件/目录）；
   - **画图意图**：想表达什么关系、结构或流程。
3. **意图不清晰时**：若画图意图模糊不清，调用 **Agent0_oasm** 帮助用户梳理并计划画图需求（细化要画什么、给谁看、约束等）。
4. **细化后做范围判断**：在画图需求细化之后，根据用户给出的文档范围判断：
   - **单文档**（scope=doc）：针对一个文档出图；
   - **文件夹**（scope=folder）：针对一个文件夹内多文档出图。
5. **文件夹时再确认**：若判断为文件夹范围，需再次与用户确认该文件夹及目标（例如入口页、多文档关系、Atlas 等），再进入后续技能链。

上述确认完成后再进入「五个核心技能」流程（分解意图 → 画图计划 → 歧义检查 → 画图 → QA → 渲染与插入）。

## 执行环境

- **ChatGPT 项目**：<https://chatgpt.com/g/g-p-698ad99f54848191ad82a3dc6f887b08-sheng-cheng-wen-dang-tu-zhu-shou/project>
- **项目内容**：包含待分析文档、画图所需材料；可根据新任务替换为不同文档集合。
- **执行方式**：将本 skill 及对应子技能描述发送给项目 AI，由其在项目上下文中执行。

## 五个核心技能

按顺序执行，**详细规则与工作步骤见 `reference/` 下对应文档**。图格式按 `figure_plan.diagram_format` 路由：**mermaid**（默认）→ Mermaid 链；**swirly**（弹珠图）→ Swirly 链。

| 技能 | 职责 | 详见 |
|------|------|------|
| 1. 分解意图 | 理解需求，产出 run_request | agent1 输入段 |
| 2. 得到画图计划 | 基于 intent/scope 生成 figure_plan（含 diagram_format） | `reference/skills/agent1_figure_plan/SKILL.md` |
| 3a. 歧义检查 | 检查歧义与视角错配，产出 guardrails | `reference/skills/agent2_ambiguity_checker/SKILL.md` |
| 3b. 调用工具画图 | mermaid → Mermaid；swirly → Swirly ASCII | `reference/skills/agent3_mermaid_author/SKILL.md` / `reference/skills/agent3_swirly_author/SKILL.md` |
| 3c. QA 门禁 | mermaid → mermaid_final；swirly → swirly_final | `reference/skills/agent4_mermaid_qa/SKILL.md` / `reference/skills/agent4_swirly_qa/SKILL.md` |
| 4. 把图进行渲染 | Mermaid 内嵌；Swirly → swirly CLI → SVG | `reference/skills/agent5_renderer/SKILL.md` |
| 5. 插入到文档指定位置 | Mermaid transclude；Swirly 用 `![[*.svg]]` 引用 | `reference/skills/agent5_renderer/SKILL.md` |

## 完整流程串联

```
用户请求（画图需求）
    ↓
入口：确认文档范围 + 画图意图（意图模糊时 → Agent0_oasm 帮助计划）
    ↓
范围判断：单文档 / 文件夹（文件夹时与用户再确认）
    ↓
Playwright/浏览器：导航到对应项目（如 ChatGPT 生成文档图助手项目）
    ↓
1. 分解意图 → run_request
    ↓
2. 得到画图计划 → figure_plan.yaml
    ↓
3a. 歧义检查（Agent2）→ ambiguity_report + guardrails
    ↓
3b. 调用工具画图（Agent3）→ figure_specs + mermaid_draft / swirly_draft
    ↓
3c. QA 门禁（Agent4）→ qa_report + mermaid_final / swirly_final
    ↓
4. 把图进行渲染
    ↓
5. 插入到文档指定位置
```

## 何时触发

在以下情况必须使用本 skill：

- 用户要求“生成文档关系图/结构图/Mermaid 图”；
- 需要在 ChatGPT 项目中执行完整画图流水线；
- 项目内文档已准备好，需要按技能链生成并落盘。

## 不做什么

- **不跳过「导航到项目」**：不得在未通过 Playwright/浏览器进入对应项目的情况下，仅在本地生成 Mermaid 或图并写文件；必须按「先导航到项目，再按图助手流水线处理」执行。
- 不替代各子技能的提示设计（Agent1~5 的规则由对应 prompt 定义）；
- 不跳过 QA 门禁直接写回；
- 不处理与 DocViz 无关的治理/编排产物。

## 输入与输出

**输入：**

- 用户自然语言请求；
- 项目内文档（doc/folder/zip，可随项目更换）；
- `scope`、`scope_value`、可选 `user_role`、`intent_picker`、`requested_figures`、`constraints`；
- 可选 `existing_artifacts`、`insert_doc`。

**输出：**

- `run_request`；
- `figure_plan.yaml`；
- `ambiguity_report`、`guardrails_for_agent3`；
- `figure_specs`、`mermaid_draft` → `mermaid_final`；`swirly_draft` → `swirly_final`；
- `qa_report`；
- `render_manifest`、`insert_plan`；
- 最终插入到文档的图及备份（若发生）。

## 质量门禁（必须满足）

- QA（Agent4）未通过时禁止写回正文；
- 所有边必须有 rel 且出现在 edge_legend；
- `optional_questions` 不阻塞，无回复时按 default_assumptions 继续；
- 正文内插图必须先备份、高置信锚点，否则退回文末新增。
- **弹珠图**：产出须满足 `reference/skills/marble_diagram_layout.md` 中的布局约束（线与文字/框零遮挡、Notes/Legend/标题与图不重叠、轨道标签不裁切、图中用词引用文档原文）。

## 何时停止并求助

出现以下任一情况立即停止并请求澄清：

- 项目或文档不可达、无法读取；
- 歧义检查 blocking 且无法用 assumptions 化解；
- QA 连续失败且 patch 后仍不可过门禁；
- 目标文档不可写或写回冲突。

## 项目与文档更换

- 更换项目：在 ChatGPT 中切换到包含新文档的项目；
- 更换文档：在项目内替换/更新文档集合后，重新发送本 skill 及任务描述；
- 每次更换后，从「分解意图」开始重新执行完整技能链。

## Reference（技能文档）

技能文档位于本 skill 的 `reference/` 文件夹：

```
docviz-doc-diagram-assistant/
├── SKILL.md
└── reference/
    ├── skills/agent0_oasm/SKILL.md
    ├── skills/agent1_figure_plan/SKILL.md
    ├── skills/agent2_ambiguity_checker/SKILL.md
    ├── skills/agent3_mermaid_author/SKILL.md
    ├── skills/agent3_swirly_author/SKILL.md    # 弹珠图 Swirly ASCII 生成
    ├── skills/agent4_mermaid_qa/SKILL.md
    ├── skills/agent4_swirly_qa/SKILL.md        # 弹珠图 Swirly QA
    ├── skills/agent5_renderer/SKILL.md
    ├── skills/agent0_orchestrator/SKILL.md
    ├── PATCH_NOTES.md
    └── skills/
        ├── marble_swirly_guide.md               # 弹珠图基础细节
        ├── marble_diagram_skills.md             # 弹珠图画师高阶 Skill（泳道门控/ID 拓扑/窗口/因果/层级折叠）
        └── marble_diagram_layout.md             # 弹珠图布局与渲染硬性约束（线不挡字、Notes/Legend/标题不重叠、轨道不裁切、用词原文）
```

执行各核心技能时，按 `diagram_format` 加载对应文档：

| 核心技能 | 对应 skill 文档 | 说明 |
|---------|-----------------|------|
| 1. 分解意图 | （由 run_request 模板与 agent1 输入约定覆盖） | 见 agent1 输入段 |
| 2. 得到画图计划 | `reference/skills/agent1_figure_plan/SKILL.md` | 图计划生成器：Lens、ROI/Risk、模板库（含 T2M 弹珠图）、diagram_format |
| 3a. 歧义检查 | `reference/skills/agent2_ambiguity_checker/SKILL.md` | 歧义与证据门禁：Glossary/Lens/Paradigm、T2M 覆盖 |
| 3b. 调用工具画图 | `reference/skills/agent3_mermaid_author/SKILL.md` 或 `reference/skills/agent3_swirly_author/SKILL.md` | Mermaid / Swirly 弹珠图 |
| 3c. QA 门禁 | `reference/skills/agent4_mermaid_qa/SKILL.md` 或 `reference/skills/agent4_swirly_qa/SKILL.md` | Mermaid / Swirly QA |
| 4+5. 渲染与插入 | `reference/skills/agent5_renderer/SKILL.md` | Mermaid 内嵌；Swirly → swirly CLI → SVG → `![[*.svg]]` |
| 编排规范 | `reference/skills/agent0_orchestrator/SKILL.md` | 总控流程、工件传递、失败降级 |
-