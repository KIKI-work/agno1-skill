"""AgentOS initialization and management for agno1."""

__all__ = ["create_agent_os", "start_mcp_daemon"]

import asyncio
from typing import Optional

from agno.os import AgentOS
from agno.agent import Agent
from agno.tools.python import PythonTools
from agno.tools.local_file_system import LocalFileSystemTools
from pathlib import Path
from agno.utils.log import log_info, log_error

from agents import setup_agents
from .database import setup_database
from .qa_playbooks import record_solution, find_solution, list_solutions


_agent_os_lock = asyncio.Lock()
_agent_os_instance: Optional[AgentOS] = None
_mcp_daemon_task: Optional[asyncio.Task] = None
_playwright_mcp_tools = None


async def create_agent_os(force_reload: bool = False) -> AgentOS:
    """Initialize AgentOS instance asynchronously."""
    global _agent_os_instance

    if _agent_os_instance is not None and not force_reload:
        return _agent_os_instance

    async with _agent_os_lock:
        if _agent_os_instance is not None and not force_reload:
            return _agent_os_instance

        log_info("🚀 Initializing AgentOS instance...")

        # Setup database
        db = await setup_database()

        # Setup agents
        bundle = await setup_agents(db)
        general_agent = bundle.by_id["agno1-general"]
        agno_expert_agent = bundle.by_id["agno1-expert"]
        playwright_agent = bundle.by_id["agno1-playwright"]
        pipeline_builder_agent = bundle.by_id["agno1-pipeline-builder"]
        pipeline_writer_agent = bundle.by_id["agno1-pipeline-writer"]
        loop_orchestrator_agent = bundle.by_id["agno1-loop-orchestrator"]
        
        # Store MCP tools for daemon startup
        global _playwright_mcp_tools
        _playwright_mcp_tools = bundle.playwright_mcp_tools

        # Setup AgentOS
        try:
            from .pipelines.workflow_office import register_agents_for_workflow, register_workflows

            # Create workflow monitor agent
            ws_path = Path(__file__).parent.parent / "ws"
            monitor_python = PythonTools(base_dir=ws_path)
            monitor_files = LocalFileSystemTools(target_directory=str(ws_path), enable_write_file=True, all=True)
            monitor_agent = Agent(
                name="Workflow Monitor",
                id="agno1-workflow-monitor",
                model=general_agent.model,
                db=db,
                knowledge=None,
                tools=[monitor_python, monitor_files],
                instructions=[
                    "你负责评估流水线状态，读取 results/commodity/* 下的 manifest 与日志。",
                    "可用时用 Python 请求 http://127.0.0.1:{AGNO_OS_PORT}/monitor/api/commodity/{commodity}。",
                    "判断阶段是否 OK/RUNNING/FAILED，并给出 resume_from、stage_limit 的建议。",
                ],
                enable_user_memories=True,
                add_history_to_context=True,
                num_history_runs=3,
                markdown=True,
            )

            qa_guardian_python = PythonTools(base_dir=ws_path)
            qa_guardian_files = LocalFileSystemTools(target_directory=str(ws_path), enable_write_file=True, all=True)
            qa_guardian = Agent(
                name="QA Guardian",
                id="agno1-qa-guardian",
                model=general_agent.model,
                db=db,
                knowledge=None,
                tools=[qa_guardian_python, qa_guardian_files],
                instructions=[
                    "你通过检查输出与日志判断某阶段是否按预期完成。",
                    "发现错误时，通过 Python 函数 find_solution 在 ws/qa_playbooks.json 中查找匹配方案。",
                    "若无方案，先询问指导，再用 record_solution(stage, error_key, steps) 持久化。",
                    "对外提供 3 个动作：add_solution(stage,error_key,steps), find_solution(stage,error_key), list_solutions(stage)。",
                ],
                enable_user_memories=True,
                add_history_to_context=True,
                num_history_runs=5,
                markdown=True,
            )

            doc_reader_python = PythonTools(base_dir=ws_path)
            doc_reader_files = LocalFileSystemTools(target_directory=str(ws_path), enable_write_file=True, all=True)
            doc_reader_agent = Agent(
                name="Doc Reader",
                id="agno1-doc-reader",
                model=general_agent.model,
                db=db,
                knowledge=None,
                tools=[doc_reader_python, doc_reader_files],
                instructions=[
                    "你读取本地文档（md/txt/pdf 使用 Python 解析），提取“面”名称并生成深入分析的简短 prompt。",
                    "使用 Python 调用 agno1.pipelines.commodity_workflow.generate_surface_prompts_from_doc(document_path, commodity_name)，并将结果保存到 results/commodity/{commodity}。",
                ],
                enable_user_memories=True,
                add_history_to_context=True,
                num_history_runs=3,
                markdown=True,
            )

            register_agents_for_workflow(bundle.agents + [monitor_agent, doc_reader_agent, qa_guardian])
            workflows = register_workflows(
                db=db,
                agents=bundle.agents + [monitor_agent, doc_reader_agent, qa_guardian],
            )
        except Exception:
            log_info("Workflow registry setup skipped due to import error")
            workflows = []

        # Create error handler agent (handles common failures like upload/navigation/send)
        error_python = PythonTools(base_dir=ws_path)
        error_files = LocalFileSystemTools(target_directory=str(ws_path), enable_write_file=True, all=True)
        error_agent = Agent(
            name="Workflow Error Handler",
            id="agno1-error-handler",
            model=general_agent.model,
            db=db,
            knowledge=None,
            tools=[error_python, error_files],
            instructions=[
                "当出现错误（如 'Failed to upload file'、'navigation failed'、'send button disabled'）时，",
                "通过读取流水线 manifest 与日志进行诊断，并给出具体修复建议：",
                "1) 若可用，改用 input[type=file] 直连上传（UPLOAD_USE_INPUT_ONLY=1）",
                "2) 增加超时或延迟后重试",
                "3) 将附件合并为单个文件再上传",
                "4) 宏观阶段改为仅抽取模式",
                "5) 若为代理错误，建议检查 CDP 与网络连通性",
                "输出简短方案：动作清单 + 需要设置的环境变量 + resume_from 阶段",
            ],
            enable_user_memories=True,
            add_history_to_context=True,
            num_history_runs=3,
            markdown=True,
        )

        # Create orchestrator agent to coordinate monitor and error handler
        orchestrator_python = PythonTools(base_dir=ws_path)
        orchestrator_files = LocalFileSystemTools(target_directory=str(ws_path), enable_write_file=True, all=True)
        orchestrator_agent = Agent(
            name="Workflow Orchestrator",
            id="agno1-orchestrator",
            model=general_agent.model,
            db=db,
            knowledge=None,
            tools=[orchestrator_python, orchestrator_files],
            instructions=[
                "通过 /monitor/api/commodity/{commodity} 获取状态并决定下一步动作。",
                "如有阶段 FAILED 或输出缺失，依据失败类型给出修复方案。",
                "返回结构化方案：resume_from、stage_limit、环境变量与执行命令。",
            ],
            enable_user_memories=True,
            add_history_to_context=True,
            num_history_runs=3,
            markdown=True,
        )

        agent_os = AgentOS(
            id="agno1",
            name="AgentOS Instance - agno1",
            description="A comprehensive AI agent platform with integrated frontend capabilities",
            version="1.0.0",
            agents=bundle.agents
            + [
                monitor_agent,
                error_agent,
                orchestrator_agent,
                doc_reader_agent,
                qa_guardian,
            ],
            workflows=workflows,
        )

        _agent_os_instance = agent_os

        log_info(
            "✅ AgentOS instance initialized successfully with general and Agno expert agents"
        )
        return agent_os


async def start_mcp_daemon():
    """Start the Playwright MCP daemon task at application startup."""
    global _mcp_daemon_task, _playwright_mcp_tools
    
    if _playwright_mcp_tools is None:
        log_error("⚠️ Playwright MCP tools not initialized")
        return
    
    if _mcp_daemon_task is not None and not _mcp_daemon_task.done():
        log_info("🔄 MCP daemon already running")
        return
    
    async def mcp_daemon():
        """Long-running task to maintain MCP connection."""
        try:
            log_info("🔄 Starting Playwright MCP daemon...")
            await _playwright_mcp_tools.connect()
            tools_count = len(_playwright_mcp_tools.functions)
            if tools_count > 0:
                log_info(f"✅ Playwright MCP daemon started with {tools_count} tools")
            else:
                log_error("⚠️ MCP connected but no tools found")
            
            # Keep the daemon alive indefinitely
            while True:
                await asyncio.sleep(3600)  # Sleep for 1 hour, repeat
        except asyncio.CancelledError:
            log_info("🔄 Playwright MCP daemon cancelled")
            raise
        except Exception as e:
            log_error(f"❌ Playwright MCP daemon failed: {e}", exc_info=True)
    
    # Start daemon
    _mcp_daemon_task = asyncio.create_task(mcp_daemon())
    log_info("🚀 MCP daemon task created")
    
    # Wait briefly for connection to establish
    await asyncio.sleep(2)
