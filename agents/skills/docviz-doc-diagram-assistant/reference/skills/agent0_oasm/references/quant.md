# 量化 / 投资 需求深挖参考

目标：明确信号、特征、触发与执行链路，避免“策略图画成流程图”。

## 关键澄清问题（最多选 2-3 个）
1) **信号来源**：数据频率/粒度是什么？（tick/min/day）
2) **特征工程**：关键特征/指标是什么？如何计算？
3) **触发条件**：信号触发阈值/窗口条件是什么？
4) **执行路径**：从信号到下单的步骤链路？是否包含风控/过滤？
5) **状态边界**：仓位状态如何变化（空仓/持仓/退出）？

## 输出字段补充
- data_sources: [name, frequency]
- features: [name, calc]
- signals: [name, trigger]
- execution_flow: [step, input, output]
- state_transitions: [from, to, condition]
