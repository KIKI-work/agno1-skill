# Agents 角色说明

本目录用于集中管理 AgentOS 的角色定义与技能文件。

- 角色元数据：`agents/roles/specs.py`
- 角色注册与初始化：`agents/registry.py`
- 角色技能文档：`agents/skills/<agent_id>/SKILL.md`

## 角色总览

| agent_id | 角色名称 | 主要职责 |
| --- | --- | --- |
| agno1-team-lead | 团队协调 | 接收新任务、分阶段拆解、分配角色与交付物。 |
| agno1-requirements | 需求澄清 | 收集需求、定义验收标准、输出风险与假设。 |
| agno1-general | 通用助理 | 执行通用任务，作为执行助手，不做需求澄清与分配。 |
| agno1-expert | Agno 专家 | 提供 Agno/AgentOS 架构与最佳实践建议，优先引用本地文档。 |
| agno1-playwright | Playwright 代理 | 提供浏览器自动化规范与诊断建议，不输出固定执行脚本。 |
| agno1-selector-watchdog | Selector 巡检代理 | 检测前端 selector 漂移并输出候选选择器与最小修复建议。 |
| agno1-pipeline-builder | 流水线架构师 | 设计流水线架构，判断 RunSpec/YAML 或 Python wrapper。 |
| agno1-pipeline-writer | 流水线开发者 | 编写/维护流水线并同步文档、输入输出与恢复策略。 |
| agno1-loop-orchestrator | 循环编排者 | 编排网页流水线+Codex+回传的多轮循环流程。 |
| agno1-workflow-orchestrator | 工作流编排者 | 处理复杂依赖、分支与并行策略，定义异常处理方案。 |
| agno1-qa | 质量保证 | 制定测试用例，验证输入输出与集成/端到端结果。 |
| agno1-docs | 文档管理 | 维护流水线文档、使用指南与故障排查说明。 |
| agno1-monitoring | 监控告警 | 监控运行状态与性能指标，提出告警与趋势分析。 |
| agno1-resource-manager | 资源管理 | 管理并发与资源池，优化资源分配与成本。 |
| agno1-release-change | 发布与变更 | 制定发布流程、变更记录与回滚策略。 |
| agno1-security | 安全与合规 | 审查密钥/权限/数据处理风险并提出整改建议。 |
| agno1-integration | 集成与工具 | 管理外部工具依赖与接入规范，处理兼容性问题。 |
| agno1-config-env | 配置与环境 | 规范环境变量与配置模板，校验运行时配置。 |
| agno1-prompt-curator | 指令管理 | 维护指令索引与版本一致性，提升复用与可追踪性。 |

## 团队协作流程（默认工作流）

这是一条端到端的默认协作链。你只需要描述目标与约束，系统按阶段调用角色。

1) 新任务接收与派单
- 负责人：`agno1-team-lead`
- 协作：`agno1-requirements`
- 输入：任务目标、约束、优先级
- 输出：阶段划分 + 角色分配 + 交接点

2) 需求阶段
- 负责人：`agno1-requirements`
- 协作：`agno1-general`、`agno1-expert`
- 输入：任务描述与派单信息
- 输出：需求摘要 + 验收标准 + 风险/假设清单

3) 设计阶段
- 负责人：`agno1-pipeline-builder`
- 协作：`agno1-workflow-orchestrator`
- 输入：需求摘要
- 输出：流水线设计方案（RunSpec 或 Python wrapper 选择）+ 节点摘要

4) 实现阶段
- 负责人：`agno1-pipeline-writer`
- 协作：`agno1-integration`、`agno1-config-env`、`agno1-prompt-curator`
- 输入：设计方案
- 输出：流水线实现 + 文档同步 + 配置清单

5) 测试阶段
- 负责人：`agno1-qa`
- 协作：`agno1-playwright`
- 输入：实现与配置
- 输出：测试用例 + 报告 + 问题清单

6) 发布阶段
- 负责人：`agno1-release-change`
- 协作：`agno1-security`、`agno1-docs`
- 输入：测试报告 + 变更内容
- 输出：发布清单 + 变更记录 + 回滚策略

7) 运行阶段
- 负责人：`agno1-monitoring`
- 协作：`agno1-resource-manager`
- 输入：运行日志/指标
- 输出：告警规则 + 运行报告 + 优化建议

8) 维护阶段
- 负责人：`agno1-team-lead`
- 协作：`agno1-general`、`agno1-docs`、`agno1-config-env`、`agno1-integration`
- 输入：监控反馈与问题清单
- 输出：修复建议 + 变更草案 + 文档更新建议

## 职责边界（去重与分工）

- 团队协调只派单与组织协作；需求澄清只产出需求与验收标准。
- 流水线架构师只出设计与实现形式选择；流水线开发者只实现与同步文档。
- 工作流编排者负责通用 DAG/分支/并行；循环编排者只负责网页流水线+Codex 的特定循环场景。
- 质量保证负责发布前验证；监控告警负责运行期监控与趋势分析。
- 文档管理维护 docs/pipelines；指令管理维护 agno1/instructions 指令资产。
- 资源管理基于监控数据做配额/并发策略；监控不做策略决策。
- 集成与工具关注接入与兼容性；安全与合规关注权限/密钥/审计。

## 维护约定

- 每个角色的详细职责与流程以 `agents/skills/<agent_id>/SKILL.md` 为准。
- 新增角色时：新增 `SKILL.md`、补充 `agents/roles/specs.py` 的 `skill_path`，并在此 README 增加说明。
