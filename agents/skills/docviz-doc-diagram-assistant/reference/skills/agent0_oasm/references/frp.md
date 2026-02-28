# FRP / Reactive 需求深挖参考

目标：把“事件/状态/动态”从叙述中抽出来，形成可画图的工程化表达。

## 关键澄清问题（最多选 2-3 个）
1) **信号类型划分**：哪些是 Event（离散事件）/ Behavior（连续状态）/ Dynamic（事件+状态）？
2) **核心算子**：是否存在 map/filter/gate/hold/merge/switch/sample/attach/fold？
3) **对齐策略**：事件对齐、段对齐，还是事件对齐段的起止？
4) **窗口/门控**：是否有“只在某状态/时间窗内生效”？
5) **分区/ID**：是否按 key/wall_id/session 分泳道？
6) **可选事件**：哪些事件可能不存在？如何表达（虚线/空心/缺失）？

## 输出字段补充
- event_list: [name, meaning, trigger]
- behavior_list: [name, meaning, lifecycle]
- dynamic_list: [name, meaning, carrier]
- operators: [name, input, output]
- alignment_policy: event|segment|event_to_segment
- gating_rules: [state, window, effect]
