---
name: docviz-swirly-qa
description: DocViz Swirly 弹珠图 QA 门禁技能。用于检查 Swirly ASCII 是否满足图计划与弹珠图规范。
---

# DocViz Swirly 弹珠图 QA 门禁

当 figure_plan 中某图 `diagram_format: swirly` 或 `template_id: T2M` 时，你负责对该图的 Swirly ASCII 进行 QA。

------------------------------------------------------------
## 输入
- figure_plan.yaml
- figure_specs.yaml（含 diagram_format=swirly 的图）
- swirly_draft（Agent3_swirly 输出的 Swirly ASCII）
- ambiguity_report（含 guardrails）

------------------------------------------------------------
## 输出
- qa_report 中该图的 checks 条目（status/notes）
- swirly_final：通过 QA 的 Swirly ASCII（可写入 swirly_final.md 或作为 qa_report 的一部分）

------------------------------------------------------------
## 门禁规则
1) **语法可渲染**：Swirly ASCII 符合 Swirly/RxJS marble 语法，能被 `swirly` CLI 解析并产出 SVG
2) **证据覆盖**：流/操作符有文档证据，不编造
3) **guardrails 合规**：遵守 ambiguity_report.guardrails_for_agent3

------------------------------------------------------------
## 与 agent4_mermaid_qa 的关系
- 编排器根据 figure_specs 中每图的 diagram_format 分别调用：mermaid 图 → agent4_mermaid_qa；swirly 图 → 本 agent（agent4_swirly_qa）
- qa_report 可合并：summary 覆盖所有图，checks 按 figure_id 分列
