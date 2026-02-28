# 非线性动力学 需求深挖参考

目标：确定状态变量、控制输入、稳定性与相位关系，避免“概念图”替代系统图。

## 关键澄清问题（最多选 2-3 个）
1) **状态变量**：系统的核心状态变量有哪些？
2) **控制输入**：外部驱动/控制量是什么？
3) **动力学方程**：有无明确的更新规则或方程描述？
4) **稳定性**：是否关心稳态、吸引子、分岔或临界点？
5) **相位关系**：是否需要相位图/轨迹/吸引域？

## 输出字段补充
- state_variables: [name, meaning]
- control_inputs: [name, meaning]
- dynamics_rules: [equation_or_rule]
- stability_points: [type, condition]
- phase_view_needed: true|false
