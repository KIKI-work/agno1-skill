# Swirly 弹珠图指南（语法 + 可读性 + 后处理）

本文件合并了：
- marble_swirly_guide.md（叙事映射/语法/命名）
- marble_swirly_clarity.md（可读性样式）
- marble_swirly_postprocess.md（后处理流水线）

**适用范围**：所有 `diagram_format: swirly` 的图。

---

## 1. 叙事到弹珠的映射
- 文档中的 FRP 叙事（Event/Behavior/Dynamic、map/filter/gate）→ 对应 Swirly 中的流与操作符。
- 每个输入流、输出流、操作符必须有文档证据。

## 2. 变量名拆分问题
Swirly 会把变量名逐字符渲染成弹珠：
- 单输入：用**内联弹珠串**，不要变量名（例：`--O--|` 配合 `title = Opened`）。
- 多输入（merge/zipAll）：用**单字符变量名**（x/y/z），用 `title` 显示语义。

## 3. 操作符命名
- 使用文档/代码中的操作符名。
- 中文叙事可用标准 Rx/FRP 名并在 caption 说明。

## 4. 时间轴对齐
- 多流并排时，时间轴对齐。
- 用 `-` 控制节奏，避免过密。

---

## 5. [styles] 推荐配置（必用）

```
[styles]
frame_width = 35
frame_height = 70
event_radius = 12
operator_height = 55
completion_height = 18
event_value_color = black
arrow_fill_color = #374151
stream_title_font_size = 11
stream_title_width = 95
operator_title_font_size = 14
event_value_font_size = 11
canvas_padding = 30
```

- 事件多：`frame_width >= 35`。
- PNG 输出：`--scale=300`。

---

## 6. 可读性问题与对策
- 节点重叠 → 增大 frame_width 或减小 event_radius。
- 字体糊 → 优先 SVG；PNG 提高 scale。
- 标签重叠 → `swirly_labels_above.py`。

---

## 7. 图例与函数关系（必须显示）
Swirly 图必须附带：
1) 图例表：[E]/[B]/[D] 说明
2) 函数关系表：上游 → 函数 → 下游
3) 弹珠符号注释
4) 叙事 ↔ 流程图 对应表

推荐顺序：图例 → 函数关系 → 符号注释 → 叙事对应 → Mermaid → Swirly SVG。

---

## 8. 后处理流水线

```bash
swirly -f <in>.txt <tmp>.svg
python3 swirly_labels_above.py <tmp> <tmp>
python3 swirly_fn_connectors.py --yaml <fn_connectors.yaml> <tmp> <tmp>
python3 swirly_header_legend.py --yaml <header_legend.yaml> <tmp> <out>.svg
```

- `fn_connectors` 必须在 `replace_operator_content` 之前。

---

## 9. 关键配置提示
- legend_line_h = 16
- narrative_line_h = 14
- operator 前加 [f]
- 函数名颜色用 black
- header/top margin 防裁切

---

## 10. 文件命名
- `<figure_id>_marble.txt` → `<figure_id>_marble.svg`
