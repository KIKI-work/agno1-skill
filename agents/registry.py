"""Agent setup and configuration for agno1.

This module is intentionally outside agno1/ so agent definitions are easy to manage.
"""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
import os
import shlex
import shutil
import urllib.error
import urllib.request
from http.client import RemoteDisconnected
from pathlib import Path
from typing import Dict, List, Optional

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.models.openai.like import OpenAILike
from agno.tools.local_file_system import LocalFileSystemTools
from agno.tools.mcp import MCPTools
from mcp.client.stdio import StdioServerParameters
from agno.tools.python import PythonTools
from agno.utils.log import log_debug, log_error, log_info

from agents.roles import ROLE_SPECS, RoleSpec


@dataclass
class AgentBundle:
    agents: List[Agent]
    by_id: Dict[str, Agent]
    playwright_mcp_tools: Optional[MCPTools]


async def setup_agents(db: SqliteDb) -> AgentBundle:
    """Setup agents for the AgentOS instance."""
    log_info("🔧 Starting agent setup process...")

    model_base_url = os.getenv(
        "OPENBUDDY_BASE_URL", "http://127.0.0.1:3101/openai/v1"
    )
    await _ensure_model_endpoint_ready(model_base_url)

    model = OpenAILike(
        id="gpt-5.1-codex",
        api_key=os.getenv("OPENBUDDY_API_KEY"),
        base_url=model_base_url,
    )

    repo_root = Path(__file__).resolve().parents[1]
    ws_path = repo_root / "ws"
    python_tools = PythonTools(base_dir=ws_path)
    file_tools = LocalFileSystemTools(
        target_directory=str(ws_path), enable_write_file=True, all=True
    )

    playwright_mcp_tools = _build_playwright_mcp_tools()
    mcp_tools = None

    agno_knowledge = None

    agents: List[Agent] = []
    by_id: Dict[str, Agent] = {}

    for spec in ROLE_SPECS:
        tools = _select_tools(
            spec,
            python_tools=python_tools,
            file_tools=file_tools,
            mcp_tools=mcp_tools,
            playwright_mcp_tools=playwright_mcp_tools,
        )
        instructions = _resolve_instructions(spec, repo_root=repo_root)
        knowledge = agno_knowledge if spec.id == "agno1-expert" else None
        agent = Agent(
            name=spec.name,
            id=spec.id,
            model=model,
            db=db,
            knowledge=knowledge,
            tools=tools,
            instructions=instructions,
            enable_user_memories=spec.enable_user_memories,
            add_history_to_context=spec.add_history_to_context,
            num_history_runs=spec.num_history_runs,
            markdown=spec.markdown,
            debug_mode=spec.debug_mode,
        )
        agents.append(agent)
        by_id[spec.id] = agent
        log_info("✅ Agent created", extra={"agent_id": spec.id, "agent_name": spec.name})

    return AgentBundle(
        agents=agents,
        by_id=by_id,
        playwright_mcp_tools=playwright_mcp_tools,
    )


def _select_tools(
    spec: RoleSpec,
    *,
    python_tools: PythonTools,
    file_tools: LocalFileSystemTools,
    mcp_tools: Optional[MCPTools],
    playwright_mcp_tools: Optional[MCPTools],
) -> List[object]:
    if spec.tools_profile == "none":
        return []
    if spec.tools_profile == "playwright":
        return [file_tools] if playwright_mcp_tools is None else [playwright_mcp_tools, file_tools]
    base = [python_tools, file_tools]
    if mcp_tools is not None:
        base.append(mcp_tools)
    return base


def _resolve_instructions(spec: RoleSpec, *, repo_root: Path) -> List[str]:
    if spec.skill_path:
        skill_path = (repo_root / spec.skill_path).resolve()
        if skill_path.exists():
            content = skill_path.read_text(encoding="utf-8").strip()
            if content:
                return [content]
    return list(spec.instructions)


def _build_playwright_mcp_tools() -> Optional[MCPTools]:
    use_playwright_mcp = os.getenv("USE_PLAYWRIGHT_MCP", "0")
    if use_playwright_mcp != "1":
        return None

    cdp_endpoint = os.getenv("CDP_ENDPOINT")
    if not cdp_endpoint:
        host_ip = os.getenv("CDP_HOST_IP")
        browser_id = os.getenv("CDP_BROWSER_INSTANCE_ID", "").lstrip("/")
        if host_ip and browser_id:
            cdp_endpoint = f"ws://{host_ip}:9222/devtools/browser/{browser_id}"

    if not cdp_endpoint:
        cdp_endpoint_base = os.getenv("CDP_ENDPOINT_BASE")
        cdp_browser_instance_id = os.getenv("CDP_BROWSER_INSTANCE_ID")
        built_from_components = False

        if cdp_endpoint_base or cdp_browser_instance_id:
            base_endpoint = (
                cdp_endpoint_base or "ws://127.0.0.1:9222/devtools/browser"
            ).rstrip("/")
            if cdp_browser_instance_id:
                cdp_endpoint = f"{base_endpoint}/{cdp_browser_instance_id.lstrip('/')}"
            else:
                cdp_endpoint = base_endpoint
            built_from_components = True

        if built_from_components:
            log_info(
                "CDP endpoint built from CDP_ENDPOINT_BASE/CDP_BROWSER_INSTANCE_ID environment variables."
            )

    if not cdp_endpoint:
        probe_urls = [
            "http://[::1]:9222/json/version",
            "http://127.0.0.1:9222/json/version",
        ]
        for probe_url in probe_urls:
            try:
                with urllib.request.urlopen(probe_url, timeout=2) as response:
                    version_meta = json.loads(response.read().decode("utf-8"))
                    detected = version_meta.get("webSocketDebuggerUrl")
                    if detected:
                        cdp_endpoint = detected
                        log_info(
                            f"Detected CDP endpoint from remote debugger ({probe_url}): {cdp_endpoint}"
                        )
                        break
            except (TimeoutError, RemoteDisconnected, urllib.error.URLError) as exc:
                log_error(
                    f"Failed to auto-detect CDP endpoint from {probe_url}",
                    exc_info=True,
                )
                log_debug(f"Probe error detail: {exc}")
            except Exception:
                log_error(
                    f"Unexpected error probing CDP endpoint {probe_url}",
                    exc_info=True,
                )

    if not cdp_endpoint:
        fallback_base_endpoint = (
            os.getenv("CDP_ENDPOINT_BASE") or "ws://127.0.0.1:9222/devtools/browser"
        ).rstrip("/")
        fallback_instance_id = os.getenv("CDP_BROWSER_INSTANCE_ID")
        if fallback_instance_id:
            cdp_endpoint = f"{fallback_base_endpoint}/{fallback_instance_id.lstrip('/')}"
            log_info(
                "CDP endpoint not provided; falling back to "
                f"{cdp_endpoint} using CDP_ENDPOINT_BASE/CDP_BROWSER_INSTANCE_ID."
            )
        else:
            cdp_endpoint = fallback_base_endpoint
            log_info(
                "CDP endpoint not provided; falling back to "
                f"{cdp_endpoint}."
            )

    playwright_mcp_command = os.getenv("PLAYWRIGHT_MCP_COMMAND")
    playwright_mcp_args_env = os.getenv("PLAYWRIGHT_MCP_ARGS")

    if use_playwright_mcp == "1":
        if playwright_mcp_command:
            playwright_mcp_args = shlex.split(playwright_mcp_args_env or "")
        elif shutil.which("mcp-server-playwright"):
            playwright_mcp_command = "mcp-server-playwright"
            playwright_mcp_args = ["--cdp-endpoint", cdp_endpoint]
        else:
            playwright_mcp_command = "npx"
            playwright_mcp_args = [
                "-y",
                "@playwright/mcp@latest",
                "--cdp-endpoint",
                cdp_endpoint,
            ]

    log_info(
        "Configuring Playwright MCP via stdio",
        extra={
            "command": playwright_mcp_command,
            "command_args": playwright_mcp_args,
            "cdp_endpoint": cdp_endpoint,
        },
    )

    playwright_mcp_tools = MCPTools(
        transport="stdio",
        server_params=StdioServerParameters(
            command=playwright_mcp_command,
            args=playwright_mcp_args,
        ),
        timeout_seconds=600,
    )

    log_info("🔄 Playwright MCP tools configured (connection pending)")
    return playwright_mcp_tools


async def _ensure_model_endpoint_ready(base_url: str) -> None:
    """Wait until the model endpoint responds with HTTP 200 or raise after retries."""
    if os.getenv("SKIP_MODEL_ENDPOINT_CHECK", "0") == "1":
        log_info("Skipping model endpoint check (SKIP_MODEL_ENDPOINT_CHECK=1)")
        return

    max_attempts = 10
    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(base_url, timeout=3) as response:
                status_code = response.getcode()
        except urllib.error.URLError as exc:
            log_error(f"Model endpoint unreachable on attempt {attempt}: {exc}")
        except Exception:
            log_error(
                "Unexpected error probing model endpoint",
                exc_info=True,
            )
        else:
            if status_code == 200:
                log_info(f"Model endpoint ready after {attempt} attempt(s)")
                return
            log_error(
                f"Model endpoint returned HTTP {status_code} on attempt {attempt}"
            )

        await asyncio.sleep(2)

    raise RuntimeError(
        f"Model endpoint at {base_url} did not become ready after {max_attempts} attempts"
    )
