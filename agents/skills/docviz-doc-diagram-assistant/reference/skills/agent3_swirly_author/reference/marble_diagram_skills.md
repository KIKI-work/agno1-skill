# 弹珠图画师高阶 Skill（抽象能力）

当文档叙事涉及「状态机」「聚合」「窗口」「多 ID 拓扑」「逻辑因果」时，画师 Agent 必须具备以下能力。这些 skill 是 弹珠图生成时的设计约束与表现要求。**表述均为抽象概念，不依赖具体业务术语。**

**布局与渲染的硬性约束**（与下文叙事类 Skill 并列、必须同时满足）：  
- **`marble_diagram_layout.md`**：布局硬约束 + 可读性增强（线与文字/框零遮挡、Notes/Legend/标题不重叠、轨道标签不裁切、侧栏统一宽度与换行、段与事件对齐关系、操作框位置、可选事件符号范围等）

生成或后处理图时请一并加载上述 Skill。

---

## Skill 1: 泳道门控（State-Gated Streams）

**抽象难点**：下游流只在某个「背景状态」为 Active 时段内才接纳事件；状态为 Inactive 时，即便上游有事件，下游也应是空的（被过滤）。存在**垂直依赖**：状态流门控其他流。

**画师要求**：
- 必须画出「背景状态条」流（如 `state = --A--I--A--I--|`，A=Active, I=Inactive）
- 被门控的流：在状态 Inactive 时段，必须显式画成空或虚线，表达「被过滤」
- 不能只画输入流和输出流；中间必须有一条 State 流

**Swirly 近似**：
- 用多流表示：`state = ...`、`input = ...`、`output = ...`，output 在 state 非 Active 时段用 `-` 或 ghost 表示空
- 注释 `%` 说明门控语义

---

## Skill 2: ID 拓扑追踪（ID-Based Topology / GroupBy ID）

**抽象难点**：事件挂在特定 partition key（实体 ID、聚合键等）上；Event(key=K1) 只进入 K1 的漏斗，不影响 K2。需要**按 key 分区的动态泳道**。

**画师要求**：
- 不能把所有事件画在一条线上
- 需画出「按 partition key 分组的泳道」：如 partition #1 一组、partition #2 另一组
- 显式表达：Event(key=K1) 只掉进 partition #1，绝不会影响 partition #2

**Swirly 近似**：
- 用多流 + 变量名区分：`p1 = --a--b--|`、`p2 = ---c--|`，避免混合
- 注释说明分区语义；必要时拆成多张图（每 partition 一张）

---

## Skill 3: 窗口可视化（TimeGate & Scope）

**抽象难点**：时间窗口、监控区间是持续的时间范围，不是瞬时事件；需要表达「开窗」「关窗」以及窗口内的作用域。

**画师要求**：
- 当 Open 事件出现时，画 `[`（开窗）
- 当 Close/Confirm 事件出现时，画 `]`（关窗）
- 两括号之间的所有事件，用视觉方式（框/分组）表示属于同一 Scope

**Swirly 近似**：
- Swirly 原生语法可能无括号；用注释 `% [scope: ...]` 或 `% ]` 标注
- 或用多流：`open = --[--|`、`close = ----]--|` 作为辅助流，配合 caption 说明

---

## Skill 4: 非对齐时间的因果表达（Causality over Chronology）

**抽象难点**：B 依赖 A 是逻辑先后，不是严格时间对齐；需要表达「A 触发 B」的因果，即便 A 和 B 在时间轴上不紧邻。

**画师要求**：
- 不仅画时间轴，还要画斜向箭头或因果连线
- 例：Event_A (在 t1 处) ---> 触发 ---> Event_B (在 t2 处)，解释为何 B 会开始

**Swirly 近似**：
- Swirly 为主时间轴图，斜向箭头通常需在 caption/注释中说明
- 或产出「因果说明」文本块，配合弹珠图使用
- 若业务强依赖因果图，可考虑补充一张 Mermaid/T2F 图专门画因果

---

## Skill 5: 层级折叠（Hierarchical Folding / LoD）

**抽象难点**：文档分层、多级抽象；全细节画在一张图会爆炸。需要**抽象层级（Level of Detail）**——何时「隐藏细节」，何时「展开」。

**画师要求**：
- 视图 A（微观）：聚焦单节，画细粒度流（如 raw events -> aggregated）
- 视图 B（宏观）：将中间流视为单一输入，展示聚合后的转换
- 懂得把大量细粒度事件简化为一个聚合块；知道何时该折叠、何时该展开

**实现**：
- 同一叙事拆成多张图：`F0_macro`、`F1_micro`
- figure_plan 中为每张图指定 `level_of_detail: macro | micro` 和 `focus_scope`
- 微观图只画局部；宏观图用聚合流（如 `burst = --X--|` 代替细粒度 `raw = -a-b-c-d-|`）

---

## Skill 6: 叙事范围（Scenario Scope）

**抽象难点**：弹珠图需覆盖文档定义的完整叙事弧，而非随机片段；视角与情节必须与文档一致。

**画师要求**：
- **剧情主线**：严格按文档结构编排（如 section 1.1 -> 1.2 -> 1.3 对应「准备 -> 核心 -> 结局」）
- **角色视角**：仅绘制文档指定的角色/层级的微观行为，不混入其他视角
- **关键情节**：必须包含文档定义的关键 plot beats（建立阶段、侦察/探测、主攻/转换、收尾、终态确认等），缺一不可

---

## Skill 7: 示意性时间（Illustrative Time Scaling）

**抽象难点**：真实时间比例会导致图过长或关键事件难以辨认；需在有限宽度内完整展示叙事。

**画师要求**：
- **自主假设**：文档未给出具体参数时，自行假设合理数值，不需向用户询问
- **视觉压缩**：不按真实 tick 比例绘制；使用「示意性时间」，压缩长静默期
- **单屏宽度**：约 50–80 字符内完整展示整个故事
- **突出顺序**：保证关键事件的交互顺序正确，而非时间绝对值准确

---

## Skill 8: 多层泳道与符号图例（Multi-Lane Structure & Symbol Legend）

**抽象难点**：多层级叙事需要多条泳道；事件种类多，需单字符符号以保持可读。

**画师要求**：
- **工具兼容**：输出必须兼容 Swirly 解析器
- **多层泳道**：至少包含以下类型的泳道（具体名称依文档）：
  - [Raw Input]：原始信号/大单/成交
  - [State]：关键状态（如 Active/Inactive）
  - [Events]：中间事件（按文档分类，如 W1–W4, B1–B3）
  - [Segments]：聚合段（如 Burst/Grind）
  - [Anchors]：最终叙事锚点（如 Opened / Fate / Confirmed）
- **符号设计**：为不同事件设计单字符符号（如 B=Build, A=Attack, X=Terminal），并在输出前先列出 Legend

**实现**：
- 在 Swirly 文本块开头用 `% Legend:` 或独立注释块列出 `符号 = 含义`
- 泳道用 `title = ...` 或变量名 + 注释标注

---

## 何时启用这些 Skill

| 文档/叙事特征（抽象） | 启用的 Skill |
|----------------------|--------------|
| 「只有在 X 状态下才接纳」「门控」「过滤」 | Skill 1 泳道门控 |
| 按 ID/键聚合、分区、多泳道 | Skill 2 ID 拓扑 |
| 时间窗口、开窗/关窗、监控区间 | Skill 3 窗口可视化 |
| 「依赖」「触发」「逻辑先后」「因果」 | Skill 4 因果表达 |
| 分层文档、多级抽象、细节爆炸 | Skill 5 层级折叠 |
| 分节叙事、关键情节、角色视角 | Skill 6 叙事范围 |
| 长静默期、参数未定、单屏展示 | Skill 7 示意性时间 |
| 多层级流、多事件类型、需图例 | Skill 8 多层泳道与符号图例 |

---

## 与 figure_plan 的衔接

agent1_figure_plan 在产出 T2M 图计划时，应检测上述**抽象叙事特征**，并在 figure_plan 中标注：
- `marble_skills_required: [state_gated, id_topology, timegate, causality, hierarchical, scenario_scope, illustrative_time, multi_lane]`
- **布局硬约束**：`marble_layout_constraints: required`（对应 `marble_diagram_layout.md`，所有弹珠图必须满足）
- 供 agent3_swirly_author / agent5_renderer 按需加载本 skill 并应用对应表现约束。
