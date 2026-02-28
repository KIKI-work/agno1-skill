# 流水线指南

本文档描述可复用流水线的组织方式与运行方法。
如需更低学习成本的入口，请先阅读：
- `docs/PIPELINES_COMMON.md`
- `docs/PIPELINES_DOMAINS.md`

## 概念

- 流水线是可复用的流程模板（逻辑稳定、步骤完整）。
- 任务卡片（jobs）是用户参数实例（小、易替换）。
- 流水线代码在 `agno1/pipelines/`，任务卡片在 `jobs/`。

## 可用流水线（功能描述）

| 名称 | 入口模块 | 功能描述 |
| --- | --- | --- |
| chatgpt_continue_loop | `agno1/pipelines/chatgpt_continue_loop.py` | 在指定 ChatGPT 会话中循环发送“继续”，用于稳定性/超时回归测试（可配置轮数与超时）。 |
| chatgpt_project_ao_dna | `agno1/pipelines/chatgpt_project_ao_dna.py` | ChatGPT 项目多轮测试：AO/DNA 指令链或 issues-review 模式，可下载附件。 |
| commodity_analysis | `agno1/pipelines/commodity_analysis.py` | 入口包装：初始化 AgentOS 后运行商品多阶段分析编排（commodity_workflow）。 |
| commodity_workflow | `agno1/pipelines/commodity_workflow.py` | 商品分析全流程编排：DNA/宏观/选面/战役/战术分阶段执行与迭代，结果写入 results/commodity/<name>。 |
| company_analysis_office | `agno1/pipelines/company_analysis_office.py` | 入口包装：注册代理后执行公司分析工作流（workflow_office）。 |
| workflow_office | `agno1/pipelines/workflow_office.py` | 公司分析多子任务编排，按步骤上传前序输出并落盘到日期目录，支持续跑与 URL 覆盖。 |
| iterative_coding_loop | `agno1/pipelines/iterative_coding_loop.py` | “网页对话→Codex 编码→测试→回传”的迭代闭环，生成报告并按策略判断是否继续。 |
| binder_project_remediation | `agno1/pipelines/binder_project_remediation.py` | binder_project_0.1.36 工程修复讨论流程，最终生成工程文档并下载压缩包。 |
| binder_project_codex_loop | `agno1/pipelines/binder_project_codex_loop.py` | binder_project 修复循环：网页流水线 + Codex 处理 + 反馈 RunSpec，直到 ready 或达最大轮次。 |
| binder_project_clarification | `agno1/pipelines/binder_project_clarification.py` | binder_project_0.1.36 四阶段澄清与优化，最终生成实施指南压缩包。 |
| binder_project_clarification_loop | `agno1/pipelines/binder_project_clarification_loop.py` | 澄清流水线 + Codex + loop_orchestrator 判断的迭代循环，必要时生成新流水线。 |
| download_pack_upload_route | `agno1/pipelines/download_pack_upload_route.py` | 从会话下载附件→打包→上传到项目→解析结果→转发到备选 URL。 |
| docviz_diagram_chatgpt | `agno1/pipelines/docviz_diagram_chatgpt.py` | 文档图助手：在 ChatGPT 项目内 agent1~5 生成 figure_plan→Mermaid→QA→插入文档。**使用说明**：[docviz_diagram.md](docviz_diagram.md)（前提条件、调用方式、运行过程）。 |

## 运行方式

推荐入口：

```bash
python scripts/run_pipeline.py --list
python scripts/run_pipeline.py chatgpt_continue_loop --url "https://chatgpt.com/c/..."
python scripts/run_pipeline.py chatgpt_project_ao_dna --mode ao-dna
python scripts/run_pipeline.py commodity_analysis --commodity cu --commodity al
python scripts/run_pipeline.py company_analysis_office --ticker AAPL --ticker MSFT
python scripts/run_pipeline.py iterative_coding_loop --url "https://chatgpt.com/c/..." --prompt "..."
python scripts/run_pipeline.py binder_project_remediation --url "https://chatgpt.com/c/..."
python scripts/run_pipeline.py binder_project_clarification --url "https://chatgpt.com/c/..."
python scripts/run_pipeline.py binder_project_clarification_loop --url "https://chatgpt.com/c/..."
python scripts/run_pipeline.py download_pack_upload_route --source-url "https://chatgpt.com/c/..." --project-url "https://chatgpt.com/g/.../project" --prompt-to-project "..." --alternative-urls "https://chatgpt.com/c/url1" "https://chatgpt.com/c/url2"
python scripts/run_pipeline.py docviz_diagram_chatgpt --scope-docs path/to/doc1.md path/to/doc2.md --out-dir artifacts/docviz_diagram
python scripts/run_pipeline.py docviz_diagram_chatgpt --resume --no-reply --out-dir artifacts/docviz_diagram --insert-doc path/to/target.md
```

## 新增流水线

1) 在 `agno1/pipelines/` 下创建新模块。
2) 实现 `main()` 并接收 CLI 参数。
3) 在 `scripts/run_pipeline.py` 注册入口。
4) 在本文件与 `DOCS_INDEX.md` 补充文档。
