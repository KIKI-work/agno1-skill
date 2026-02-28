# Development Tracking - agno1

> 仅保留“当前最新状态与计划”，不保留历史。随代码演进持续更新。

## 当前状态

### ✅ 已完成

- 模块化后端结构（agno1/ 下 app、agent、agent_os、database、knowledge、nano_vecdb、utils）
- 统一 FastAPI 服务器（/api 挂载 AgentOS，非 /api 路径提供导航页）
- SQLite 本地数据库（data/database.db），启动时自动创建
- 知识库管线：支持 knowledge/ 下 Markdown 扫描、Hash 增量、重建逻辑
- 自研本地向量库实现 NanoVecDb（基于 LightRAG NanoVectorDBStorage）
- Doubao 向量嵌入已集成（volcenginesdk Ark SDK），支持 query 指令增强与归一化
- 两类 Agent 已注入 AgentOS：
  - General Assistant（工作区工具：Python、文件系统、MCP）
  - Agno Expert（接入 knowledge/agno 知识库）
- 统一入口 main.py（uvicorn 工厂模式加载 create_app_factory）
- 导航页模板 agno1/templates/navigation.html（UI 构建产物缺失时提供导航）
- CORS 配置与多地址访问信息显示（utils.display_access_info）

## 进行中

- 暂无

## 技术决策快照

- 数据库存储：SQLite 文件（data/database.db）
- 向量库：LightRAG NanoVectorDBStorage + 自定义 NanoVecDb 封装
- 嵌入模型：Doubao（volcenginesdk Ark），query 指令增强、可选维度裁剪
- Web 服务：FastAPI 统一进程挂载 AgentOS 应用
- 前端集成：已从 deps/agno-ui/out 提供静态资源（子模块已配置并构建完成）

## 目录结构

```
.
├── AGENTS.md
├── DevTracking.md
├── README.md
├── main.py
├── agno1/
│   ├── app.py
│   ├── agent.py
│   ├── agent_os.py
│   ├── database.py
│   ├── knowledge.py
│   ├── nano_vecdb.py
│   ├── utils.py
│   ├── embeddings/
│   │   └── doubao.py
│   └── templates/
│       └── navigation.html
├── data/
│   └── vectors/
│       └── agno/
├── docs/
├── knowledge/
│   └── agno/...
└── scripts/  (如存在)
```

## 风险与依赖

- 需要设置 ARK_API_KEY 以调用 Doubao 嵌入
- OpenAI 兼容服务依赖 OPENBUDDY_BASE_URL 可用
- LightRAG 阈值过低可能引入噪声，过高可能召回不足，需基于语料调参
