# AgentOS Instance - agno1

## 总览

agno1 是基于 Agno 框架的 AgentOS 实例，后端已模块化，内置两类 Agent，支持本地 SQLite + 本地向量库，并可接入知识库。服务端已静态挂载 deps/agno-ui/out 构建产物，前端可用。

## 本地 Agno Cookbook 参考

优先参考 `../agno/cookbook/` 获取 AgentOS 开发范式与示例。

- 若目录不存在：请在本项目同级克隆 `https://github.com/agno-agi/agno.git`
- 若 IDE 无法访问：请将 `../agno/` 加为当前项目的额外工作区根

## 架构

### 后端模块

- 入口：`main.py`（uvicorn 工厂模式）
- 模块：`agno1/`
  - `app.py` FastAPI 创建与路由挂载（/api 为 AgentOS，其他路径导航页/静态）
  - `agent.py` 两类 Agent（General / Agno Expert），工具含 Python/FS/MCP
  - `agent_os.py` AgentOS 实例化并注册 Agents
  - `database.py` SQLite 初始化（data/database.db）
  - `knowledge.py` 知识库准备：MarkdownReader + MarkdownChunking，增量/重建
  - `nano_vecdb.py` 本地向量库封装（LightRAG Storage + Doubao 嵌入）
  - `embeddings/doubao.py` Doubao 嵌入（volcenginesdk Ark）
  - `templates/navigation.html` 导航页（无前端构建产物时兜底）

目录快照（当前仓库实际）

```
.
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
└── knowledge/
    └── agno/...
```

### 依赖与集成

- Agno 框架（agent、os、knowledge、db 等）
- FastAPI / Uvicorn
- MCP（工具协议）
- OpenAI-like 模型（OpenBuddy 兼容端点）
- SQLAlchemy（由 Agno SqliteDb 依赖）
- LightRAG（NanoVectorDBStorage）
- numpy
- volcenginesdkarkruntime（Doubao 嵌入 SDK）

示例（pyproject 中应包含，不确保与你本地 lock 完全一致）

```toml
dependencies = [
  "agno",
  "fastapi[standard]>=0.118.3",
  "uvicorn>=0.37.0",
  "mcp>=1.17.0",
  "openai>=2.3.0",
  "anthropic>=0.69.0",
  "sqlalchemy>=2.0.44",
  "lightrag",
  "numpy",
  "volcenginesdkarkruntime",
]
```

### 前端集成（当前状态）

- 服务端支持从 `deps/agno-ui/out` 提供静态资源（/\_next、index.html、favicon 等）
- 仓库已配置 `deps/agno-ui` 为 git submodule；首次需初始化并构建后方可生效

初始化与构建流程（如需重建）：

```bash
# 初始化子模块（首次或需要拉取更新）
git submodule update --init --recursive

# 安装与构建（需要 Node 18+ 与 pnpm）
cd deps/agno-ui
pnpm install
pnpm build    # 产出 out 目录
```

完成后，运行后端并访问 http://localhost:${AGNO_OS_PORT:-7777}/ 应显示前端 UI。

## Agent 能力

- 记忆：会话历史（num_history_runs=5）与本地持久化
- 工具：Python 执行、工作区文件系统、MCP（当前示例配置来自 agent.py，建议改为环境变量）
- 知识：集成 knowledge/agno 文档，使用本地向量库检索
- 多 Agent：General 与 Agno Expert 并存，可按场景分工

## 知识与向量检索

- Ingest：扫描 knowledge/<kb> 下 Markdown，计算 SHA256 与快照 ingested_meta.json
- 策略：若有缺失/变更 => 全量重建；否则增量跳过未变文件
- 嵌入：Doubao（Ark SDK），query 检索采用指令增强；向量 L2 归一化
- 阈值：LightRAG 存储内部基于 cosine_better_than_threshold 过滤（默认 0.01，可调）

## 运行

环境变量（示例）：

```bash
export AGNO_OS_PORT=7777
export OPENBUDDY_API_KEY=your_openbuddy_key
export OPENBUDDY_BASE_URL=http://127.0.0.1:3101/openai/v1
export ARK_API_KEY=your_ark_key
# 必需：Agno 框架层鉴权令牌（同时用于日志便捷 URL）
export OS_SECURITY_KEY=your_security_key
```

启动：

```bash
uv run main.py
# 访问
# 前端（若已构建）:  http://localhost:7777/
# API 文档:          http://localhost:7777/api/docs
# API 根:            http://localhost:7777/api/
```

说明：

- 鉴权由 Agno 框架基于 OS_SECURITY_KEY 生效；如需路由级额外策略，可在 FastAPI 层添加依赖/中间件
- 导航页会在未构建前端时为其它路径提供友好入口

## 开发指南

- 遵循 async 原则：涉及 I/O/网络/DB 的函数均为 async，不直接操作事件循环
- `__all__` 紧随模块 docstring，明确模块导出
- 变更 async 签名需沿调用链传播 await
- 优先阅读 `../agno/cookbook/` 的 agents / teams / workflows 示例

## 参考

- Agno Cookbook: `../agno/cookbook/`
- Agent UI: https://github.com/agno-agi/agent-ui
- LightRAG: https://github.com/StellarCN/LightRAG
- Volcengine ARK: https://www.volcengine.com/docs/82379/1521766
