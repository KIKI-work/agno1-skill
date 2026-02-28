# 文档图助手（docviz_diagram_chatgpt）

文档图助手在 ChatGPT「生成文档图助手」项目内，按 agent1→2→3→4→5 的顺序生成 figure_plan、歧义检查、Mermaid 图、QA 门禁，最后在本地将通过的 Mermaid 代码插入到你指定的文档。

## 文档来源

**说明**：目前**尚未直接接通 Obsidian**（与 AI 讨论过方案，需写代码实现，尚未落地,后续会接通）。当前可用方式如下。

制图所需的文档必须出现在 ChatGPT 项目的 Sources 中，有两种做法：

| 方式 | 说明 |
|------|------|
| **用户自行上传** | 你在 ChatGPT 里建好项目后，手动把参与制图的文档上传到该项目的 Sources。 |
| **由 Agent 上传** | 你通过参数 `scope_docs` 指明需要的文档范围（本地路径或文档名），流水线会把这些文档打包为 ZIP 并上传到指定的 ChatGPT 项目中。 |

- **若填写了 `scope_docs`**：流水线会打包该范围内的文档并上传到 `project_url` 对应项目，然后再执行 agent1→5。
- **若未填写 `scope_docs`**：默认认为文档**已在项目里**，流水线不会上传文档，直接从 agent1 开始执行。

两种方式在文末「图助手流水线调用方式」中对应如下：
- **方式一（配置文件）**：在 `config.yaml` 中不填 `scope_docs` 表示文档已在项目 Sources；填写 `scope_docs` 则由流水线打包上传。
- **方式二（命令行）**：示例里的 `--scope-docs` 命令行代表由流水线上传；未带 `--scope-docs` 的续跑示例代表文档已在项目 Sources。

## 前提条件

1. **在 ChatGPT 中准备好项目与文档**
   - 使用 ChatGPT 的「生成文档图助手」类项目（或你自建的同类项目）。
   - 确保本次制图所需文档已在项目 Sources 中；来源方式（手动上传或通过 `scope_docs` 由流水线打包上传）详见上一节「文档来源」。
   - **建议**：若有多份文档需反复制图，可一次建好项目并上传全部相关文件，后续多次运行复用同一项目，无需每次填 `scope_docs`。

2. **本地环境**
   - 已安装依赖（`uv sync` 或 `pip install -e .`）。
   - 流水线会自动打开/控制浏览器，**必须**使用 CDP 启动的 Chrome，且该 Chrome 已登录 ChatGPT；否则流程无法继续。CDP 启动方式与端点配置详见 [browser_automation 使用说明](../../agno1/browser_automation/README.md#1-启动-chrome-cdp)。

## 运行过程简述

| 步骤 | 说明 |
|------|------|
| **准备** | 若配置了 `scope_docs`，将所列文档打 ZIP 并上传到项目 Sources；否则假定文档已在项目中（参见上文「文档来源」）。 |
| **agent1_figure_plan** | 在项目对话中发送 SKILL + prompt，结合项目内文档生成 figure_plan（及可选「需用户确认的问题」）。 |
| **（可选）等待用户** | 默认在 agent1 完成后返回，由你确认或修改需求后，再以 `--resume --no-reply` 继续。若希望全自动，可首次就加 `--no-reply` 或后续只跑 `--resume --no-reply`。 |
| **agent2_ambiguity_checker** | 对 figure_plan 做歧义检查。 |
| **agent3_mermaid_author** | 根据 figure_plan 生成 Mermaid 图代码。 |
| **agent4_mermaid_qa** | 对 Mermaid 做质量检查，输出「通过」或「不通过」；不通过会重试（有次数上限）。 |
| **agent5（本地）** | 从 agent3/agent4 回复中提取 Mermaid 代码，写入 `out_dir/mermaid_final.md`；若配置了 `insert_doc`，在目标文档的 `<!-- DOC_VIZ_MERMAID -->` 处插入或追加到文末。 |

状态与中间结果保存在 `out_dir`（及可选的 `state_dir`），用于 `--resume` 续跑。

## 各 agent SKILL 文档位置

流水线各步使用的 SKILL 指令来自以下文件（路径相对于仓库根；可通过 config 的 `skill_dir` 或命令行 `--skill-dir` 覆盖根目录）：

| 步骤 | SKILL 路径 |
|------|------------|
| agent1_figure_plan | `agents/skills/docviz-doc-diagram-assistant/reference/skills/agent1_figure_plan/SKILL.md` |
| agent2_ambiguity_checker | `agents/skills/docviz-doc-diagram-assistant/reference/skills/agent2_ambiguity_checker/SKILL.md` |
| agent3_mermaid_author | `agents/skills/docviz-doc-diagram-assistant/reference/skills/agent3_mermaid_author/SKILL.md` |
| agent3_swirly_author | `agents/skills/docviz-doc-diagram-assistant/reference/skills/agent3_swirly_author/SKILL.md` |
| agent4_mermaid_qa | `agents/skills/docviz-doc-diagram-assistant/reference/skills/agent4_mermaid_qa/SKILL.md` |
| agent4_swirly_qa | `agents/skills/docviz-doc-diagram-assistant/reference/skills/agent4_swirly_qa/SKILL.md` |
| agent5_renderer（本地渲染） | `agents/skills/docviz-doc-diagram-assistant/reference/skills/agent5_renderer/SKILL.md`（流水线内该步由本地逻辑执行，不读取此文件；SKILL 供参考或它处使用）。 |

顶层入口 SKILL：`agents/skills/docviz-doc-diagram-assistant/SKILL.md`（用于 AgentOS 中「生成文档图助手」agent 的指令）。

## 图助手流水线调用方式

### 方式一：配置文件 + 模块入口（推荐）

复制并修改 `jobs/docviz_diagram/config.yaml`，至少配置：    

- `project_url`：你的 ChatGPT 项目 URL（项目页，不是某条对话的 URL）。
- `user_context`：本次制图的需求背景（会传给 agent1）。
- `out_dir`：产出目录（回复、状态、`mermaid_final.md` 等）。
- 若需要把最终 Mermaid 插入到某文档：设置 `insert_doc` 为目标文档路径。
- 若文档**已**在项目 Sources 中，无需填 `scope_docs`；若是首次上传或要更新项目中的文档，可配置 `scope_docs` 列表，流水线会打包为 ZIP 并上传到项目。

然后执行：

```bash
# 首次运行（会打开项目并执行 agent1，默认会停等用户确认）
uv run python -m agno1.pipelines.docviz_diagram_chatgpt -c jobs/docviz_diagram/config.yaml

# 使用已登录 Chrome（CDP）
CDP_ENDPOINT="http://127.0.0.1:9222" uv run python -m agno1.pipelines.docviz_diagram_chatgpt -c jobs/docviz_diagram/config.yaml
```

**断点续跑（不等待用户回复，直接跑完 agent2→3→4→5）：**

```bash
uv run python -m agno1.pipelines.docviz_diagram_chatgpt -c jobs/docviz_diagram/config.yaml --resume --no-reply
```

### 方式二：run_pipeline.py 统一入口

```bash
# 指定参与制图的本地文档（会打包上传到项目）、产出目录
python scripts/run_pipeline.py docviz_diagram_chatgpt \
  --project-url "https://chatgpt.com/g/.../project" \
  --scope-docs path/to/doc1.md path/to/doc2.md \
  --out-dir artifacts/docviz_diagram

# 续跑（不等待用户回复）
python scripts/run_pipeline.py docviz_diagram_chatgpt --resume --no-reply \
  --out-dir artifacts/docviz_diagram --insert-doc path/to/target.md
```

命令行参数会覆盖 config 文件中的同名字段；未提供的仍从 config 或环境变量读取。

## 配置说明摘要

- **project_url**：必填。ChatGPT 项目页 URL。
- **user_context**：需求背景，传给 agent1。
- **out_dir**：产出目录（默认如 `artifacts/docviz_diagram`）。
- **state_dir**：状态目录，默认与 `out_dir` 相同。
- **insert_doc**：最终 Mermaid 要插入的文档路径；不填则只生成文件不改文档。
- **scope_docs**：参与制图的文档范围（本地路径列表）。填了则打包并上传到项目；不填则默认文档已在项目中，Agent 不会上传。
- **resume / no_reply**：见上；也可在命令行用 `--resume`、`--no-reply` 覆盖。

更多字段与默认值见 `jobs/docviz_diagram/config.yaml` 内注释。
