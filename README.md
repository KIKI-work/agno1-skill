# agno1 - AgentOS 开发实例

基于 Agno 框架的 AgentOS 开发实例，专注于本地开发和演示。

## 项目概述

agno1 是一个基于 [Agno](https://github.com/agno-ai/agno) 框架构建的 AgentOS 实例，集成了自定义的前端界面，支持本地文件存储和向量数据库。项目采用 `gpt-5-mini` 模型，通过 OpenAI 兼容的本地提供商进行推理。

# 开发环境搭建与运行步骤

面向开发者的最短路径，按顺序执行即可本地启动后端与前端。

## 1. 先决条件

- uv（负责 Python 解释器及依赖的安装和版本更新）
  - 官方脚本: curl -LsSf https://astral.sh/uv/install.sh | sh
- Node.js 18+ 与 pnpm（用于构建前端 UI）

  - 建议通过 nvm 安装并使用 LTS 版本：

    ```bash
    # 安装 nvm（macOS/Linux）
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash

    # 新开终端使 nvm 生效

    # 安装并使用 Node LTS（推荐）
    nvm install --lts
    nvm use --lts
    nvm alias default lts/*
    node -v

    # 启用 pnpm
    npm i -g pnpm
    pnpm -v
    ```

- Git（含子模块）

## 2. 获取代码与子模块

```bash
git clone --recursive https://gogs.dw/aiwo/agno1.git agno1
```

若你在同一工作区没有 Agno Cookbook，请在本项目同级克隆：

```bash
git clone https://github.com/agno-agi/agno.git
# 确保当前项目为 ../agno1，Cookbook 在 ../agno/cookbook/
```

如果 IDE 无法访问 ../agno/，请将该目录添加进工作区。

## 3. Python 依赖安装

项目使用 pyproject.toml + uv.lock 管理依赖。uv 会自动安装并管理所需的 Python 版本，无需手动满足 Python 版本要求。

```bash
# 在仓库根目录
uv sync
```

## 4. 必需环境变量

至少配置以下变量，否则部分能力不可用：

```bash
# 端口（默认 7777，可省略）
export AGNO_OS_PORT=7777

# 模型提供商（OpenAI 兼容端点），示例为本地/私有部署
export OPENBUDDY_API_KEY=your_openbuddy_key
export OPENBUDDY_BASE_URL=http://127.0.0.1:3101/openai/v1

# 向量嵌入（Doubao / ARK）
export ARK_API_KEY=your_ark_key

# AgentOS 安全密钥（Agno 框架鉴权用，必需）
export OS_SECURITY_KEY=your_security_key
```

建议将上述写入 .bashrc，并在终端生效后再运行。

## 5. 构建前端

后端会从 deps/agno-ui/out 提供静态资源。

```bash
# 安装依赖与构建
cd deps/agno-ui
pnpm install
pnpm build   # 生成 out 目录
```

## 6. 运行后端+前端的统一 Web 服务

```bash
# 开发模式（自动 reload）
AGNO_RELOAD=true uv run main.py
```

访问控制台输出的 WebUI url，含鉴权信息的 url 打开 会自动完成鉴权。

首次运行会初始化本地数据库与必要资源。

## 7. 常见问题排查

- 未找到 UI
  - 执行 git submodule update --init --recursive
  - 在 deps/agno-ui 运行 pnpm build，确保生成 out/
- 模型不可用或报 401/404
  - 检查 OPENBUDDY_API_KEY 与 OPENBUDDY_BASE_URL 是否正确，服务是否可达
- 向量检索无结果或报认证错误
  - 检查 ARK_API_KEY 是否配置，网络可达
- 访问 /api/ 报鉴权失败
  - 确认已设置 OS_SECURITY_KEY，且客户端按 Agno 规范携带
- 端口被占用
  - 调整 AGNO_OS_PORT 或释放端口后重试

## 8. 下一步：定制与开发

- 入口与服务：
  - main.py（Uvicorn 工厂模式）
  - agno1/app.py（FastAPI 装配与静态挂载）
- Agent 与能力：
  - agno1/agent.py（内置 General 与 Agno Expert，可按需新增工具/路由）
  - agno1/agent_os.py（注册 Agents，元信息）
- 数据与知识：
  - agno1/database.py（SQLite 初始化）
  - agno1/knowledge.py、agno1/nano_vecdb.py（知识入库与向量检索）

使用 AI 开发此项目，要求 Agent 参考 ../agno/cookbook/ 的 Agno 范式进行扩展。
