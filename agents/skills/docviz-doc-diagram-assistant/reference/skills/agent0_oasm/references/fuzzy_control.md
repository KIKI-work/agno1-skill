# 模糊控制 需求深挖参考

目标：明确模糊集、规则库与去模糊方式，保证控制逻辑可画。

## 关键澄清问题（最多选 2-3 个）
1) **输入/输出变量**：控制器输入与输出分别是什么？
2) **隶属度函数**：使用哪些模糊集（高/中/低）？
3) **规则库**：核心规则是否明确？（如果 A 且 B 则 C）
4) **推理方式**：Mamdani / Sugeno / 其他？
5) **去模糊方法**：重心法/最大隶属度/其他？

## 输出字段补充
- inputs: [name, range]
- outputs: [name, range]
- membership_sets: [variable, sets]
- rules: [if, then]
- defuzz_method: ""
