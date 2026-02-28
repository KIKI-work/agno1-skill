# DSL / 领域语言 需求深挖参考

目标：明确 DSL 的语法边界、语义映射与使用场景。

## 关键澄清问题（最多选 2-3 个）
1) **语法范围**：DSL 支持哪些核心表达式/语句？
2) **语义映射**：每条语句对应的执行/含义是什么？
3) **上下文约束**：有哪些前置状态或上下文依赖？
4) **错误处理**：非法输入如何处理？
5) **目标用户**：谁会使用 DSL，使用场景是什么？

## 输出字段补充
- grammar_core: [construct, meaning]
- semantic_mapping: [construct, runtime_effect]
- context_constraints: [rule]
- error_handling: [rule]
