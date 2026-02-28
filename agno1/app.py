"""FastAPI app creation and configuration for AgentOS."""

__all__ = ["create_app_factory"]

import asyncio
import os
from pathlib import Path
import json

from agno.utils.log import log_error, log_info
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from .agent_os import create_agent_os, start_mcp_daemon
from .utils import display_access_info, get_project_root, get_relative_path
from .pipelines.commodity_workflow import (
    COMMODITY_RESULTS_ROOT,
    COMMODITY_STAGES,
    ROOT_PATH,
    ensure_commodity_results_directory,
)


def create_navigation_page() -> str:
    """Create HTML content for the navigation page by reading from template file."""
    template_path = Path(__file__).parent / "templates" / "navigation.html"

    if template_path.exists():
        return template_path.read_text(encoding="utf-8")
    else:
        # Fallback to a simple error page if template is missing
        return """
        <!DOCTYPE html>
        <html>
        <head><title>Navigation Template Missing</title></head>
        <body>
            <h1>Error: Navigation template not found</h1>
            <p>The navigation template file is missing. Please check the templates directory.</p>
        </body>
        </html>
        """


async def create_app() -> FastAPI:
    """Create and configure the FastAPI application asynchronously."""
    # Initialize AgentOS directly
    log_info("🔄 Initializing AgentOS...")
    agent_os = await create_agent_os()
    agno_app = agent_os.get_app()
    log_info("✅ AgentOS initialized successfully")

    app = FastAPI(
        title="AgentOS - agno1",
        description="A comprehensive AI agent platform built on the Agno framework",
        version="0.1.0",
    )

    # Add startup event to initialize MCP daemon
    @app.on_event("startup")
    async def startup_event():
        """Start the Playwright MCP daemon after FastAPI starts."""
        log_info("🚀 FastAPI startup: Starting MCP daemon...")
        await start_mcp_daemon()

    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://os.agno.com",
            "http://os.agno.com",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:7777",
            "http://127.0.0.1:7777",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount the AgentOS app under /api/
    app.mount("/api", agno_app)

    @app.get("/agents")
    async def list_agents_all():
        """List all agents (including internal/workflow agents)."""
        agents = getattr(agent_os, "agents", None) or []

        def _model_dict(model_obj):
            if model_obj is None:
                return None
            model_id = (
                getattr(model_obj, "id", None)
                or getattr(model_obj, "model", None)
                or getattr(model_obj, "name", None)
            )
            provider = (
                getattr(model_obj, "provider", None)
                or getattr(model_obj, "model_provider", None)
            )
            name = getattr(model_obj, "name", None) or model_id
            return {
                "name": name,
                "model": model_id or name or "",
                "provider": provider or "",
            }

        payload = []
        for agent in agents:
            agent_id = getattr(agent, "id", None) or ""
            if not agent_id:
                continue
            db_obj = getattr(agent, "db", None)
            payload.append(
                {
                    "id": agent_id,
                    "name": getattr(agent, "name", None) or agent_id,
                    "db_id": getattr(db_obj, "id", None) or getattr(agent, "db_id", None),
                    "model": _model_dict(getattr(agent, "model", None)),
                }
            )
        return payload

    # n8n trigger endpoint: POST /api/workflow/run
    from fastapi import Body
    from .pipelines.workflow_office import run_workflow_for_tickers

    @app.post("/workflow/run")
    async def run_workflow_endpoint(payload: dict = Body(...)):
        """Trigger the autonomous workflow for a list of tickers.
        Expected JSON: {"tickers": ["AAPL", "MSFT" ]}
        """
        tickers = payload.get("tickers") or []
        if not isinstance(tickers, list) or not tickers:
            return {"error": "tickers must be a non-empty list"}
        try:
            results = await run_workflow_for_tickers(tickers)
            return {"status": "ok", "results": results}
        except Exception as exc:
            log_error("Workflow execution failed", exc_info=True)
            return {"status": "error", "message": str(exc)}

    from fastapi import Path as FastAPIPath, Query as FastAPIQuery
    from fastapi.responses import PlainTextResponse

    def _read_json(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _tail_text(path: Path, lines: int = 200) -> str:
        if not path.exists():
            return ""
        try:
            content = path.read_text(encoding="utf-8")
            parts = content.splitlines()
            return "\n".join(parts[-max(1, lines):])
        except Exception:
            return ""

    def _candidate_result_dirs(commodity: str) -> list[Path]:
        commodity = commodity.lower()
        dirs: list[Path] = []
        try:
            dirs.append(ensure_commodity_results_directory(commodity))
        except Exception:
            pass
        legacy_root = Path(ROOT_PATH) / "品种分析结果"
        dirs.append(legacy_root / commodity)
        # dedupe preserving order
        seen: set[str] = set()
        unique: list[Path] = []
        for d in dirs:
            s = str(d)
            if s not in seen:
                unique.append(d)
                seen.add(s)
        return unique

    def _resolve_paths(commodity: str) -> tuple[Path, Path, Path]:
        for d in _candidate_result_dirs(commodity):
            manifest = d / ".manifest.json"
            logp = d / ".pipeline.log"
            if manifest.exists() or logp.exists():
                return d, manifest, logp
        d = _candidate_result_dirs(commodity)[0]
        return d, d / ".manifest.json", d / ".pipeline.log"

    @app.get("/monitor/api/commodity/{commodity}")
    async def get_commodity_status(
        commodity: str = FastAPIPath(...),
        log_lines: int = FastAPIQuery(200, ge=1, le=2000),
    ):
        commodity = commodity.lower()
        results_dir, manifest_path, log_path = _resolve_paths(commodity)
        manifest = _read_json(manifest_path)

        total = 0
        ok = 0
        failed = 0
        running = 0
        entries = []
        for k, v in manifest.items():
            if not isinstance(v, dict):
                continue
            s = str(v.get("status") or "").upper()
            total += 1
            if s == "OK":
                ok += 1
            elif s == "FAILED":
                failed += 1
            elif s == "RUNNING":
                running += 1
            entries.append({
                "key": k,
                "status": s,
                "output": v.get("output"),
                "started_at": v.get("started_at"),
                "finished_at": v.get("finished_at"),
            })

        ordered_stage_ids = [s.identifier for s in COMMODITY_STAGES]
        stage_status = {}
        for sid in ordered_stage_ids:
            latest = None
            latest_started = -1
            for k, v in manifest.items():
                if isinstance(v, dict) and k.endswith(f"::{sid}"):
                    ts = float(v.get("started_at") or 0)
                    if ts >= latest_started:
                        latest = v
                        latest_started = ts
            if latest:
                stage_status[sid] = {
                    "status": str(latest.get("status") or "").upper(),
                    "output": latest.get("output"),
                    "started_at": latest.get("started_at"),
                    "finished_at": latest.get("finished_at"),
                }
            else:
                stage_status[sid] = {"status": "PENDING"}

        return {
            "status": "ok",
            "commodity": commodity,
            "results_root": str(results_dir),
            "summary": {"total": total, "ok": ok, "failed": failed, "running": running},
            "stage_status": stage_status,
            "manifest_entries": entries,
            "log_tail": _tail_text(log_path, log_lines),
        }

    @app.get("/monitor/api/commodity/{commodity}/log", response_class=PlainTextResponse)
    async def get_commodity_log(
        commodity: str = FastAPIPath(...),
        lines: int = FastAPIQuery(200, ge=1, le=10000),
    ):
        commodity = commodity.lower()
        _, _, log_path = _resolve_paths(commodity)
        return _tail_text(log_path, lines)

    @app.get("/monitor/commodity/{commodity}", response_class=HTMLResponse)
    async def monitor_commodity_page(
        commodity: str = FastAPIPath(...),
        refresh: int = FastAPIQuery(5, ge=1, le=60),
        log_lines: int = FastAPIQuery(200, ge=1, le=2000),
    ):
        commodity = commodity.lower()
        status = await get_commodity_status(commodity=commodity, log_lines=log_lines)
        ordered_stage_ids = [s.identifier for s in COMMODITY_STAGES]
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
          <meta http-equiv=\"refresh\" content=\"{refresh}\">
          <title>Commodity Pipeline Monitor - {commodity}</title>
          <style>
            body {{ font-family: system-ui, sans-serif; margin: 24px; }}
            .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
            .card {{ border: 1px solid #ddd; border-radius: 8px; padding: 16px; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ border-bottom: 1px solid #eee; padding: 8px; text-align: left; }}
            .status-OK {{ color: #0a7; }}
            .status-FAILED {{ color: #c00; }}
            .status-RUNNING {{ color: #f80; }}
            .status-PENDING {{ color: #666; }}
            pre {{ max-height: 360px; overflow: auto; background: #fafafa; padding: 12px; border: 1px solid #eee; }}
          </style>
        </head>
        <body>
          <h1>品种流水线监控：{commodity}</h1>
          <p>结果目录：{status['results_root']}</p>
          <div class=\"grid\">
            <div class=\"card\">
              <h2>阶段进度</h2>
              <table>
                <tr><th>阶段</th><th>状态</th><th>输出</th></tr>
                {''.join(f"<tr><td>{sid}</td><td class='status-{status['stage_status'][sid]['status']}'>{status['stage_status'][sid]['status']}</td><td>{status['stage_status'][sid].get('output','')}</td></tr>" for sid in ordered_stage_ids)}
              </table>
            </div>
            <div class=\"card\">
              <h2>汇总</h2>
              <p>总条目：{status['summary']['total']}｜完成：{status['summary']['ok']}｜失败：{status['summary']['failed']}｜运行中：{status['summary']['running']}</p>
              <h3>最近日志</h3>
              <pre>{status['log_tail']}</pre>
            </div>
          </div>
          <p>API：<a href=\"/monitor/api/commodity/{commodity}\">/monitor/api/commodity/{commodity}</a>｜日志：<a href=\"/monitor/api/commodity/{commodity}/log\">/monitor/api/commodity/{commodity}/log</a></p>
        </body>
        </html>
        """
        return HTMLResponse(content=html)

    # Log OpenAPI availability
    port = int(os.getenv("AGNO_OS_PORT", "7777"))
    log_info(f"OpenAPI available at: http://127.0.0.1:{port}/api/")

    # Get the UI distribution path
    ui_dist_path = Path(get_project_root()) / "deps" / "agno-ui" / "out"
    log_info(f"📁 UI path: {ui_dist_path} (exists={ui_dist_path.exists()})")

    if True:
        log_info(f"📁 Serving UI from: {get_relative_path(str(ui_dist_path))}")

        # Mount static files for UI assets
        app.mount(
            "/_next",
            StaticFiles(directory=ui_dist_path / "_next"),
            name="next_static",
        )

        # Serve favicon
        @app.get("/favicon.ico", response_model=None)
        async def serve_favicon() -> Response | object:
            favicon_file = ui_dist_path / "favicon.ico"
            if favicon_file.exists():
                return FileResponse(favicon_file)
            return {"error": "Favicon not found"}

        # Serve index.html for root path only
        @app.get("/", response_model=None)
        async def serve_root() -> Response | object:
            index_file = ui_dist_path / "index.html"
            if index_file.exists():
                return FileResponse(index_file)
            return {"error": "UI not found"}

        # Catch-all route for other paths (excluding /api/*) - show navigation page
        @app.get("/{path:path}", response_model=None)
        async def serve_navigation(path: str = "") -> Response | object:
            # Skip API paths - they should be handled by the mounted AgentOS app
            if path.startswith("api/") or path == "api":
                # This should not be reached due to mounting, but just in case
                return {"error": "API endpoint not found"}

            # For specific files, try to serve them directly
            if path and "." in path:
                requested_file = ui_dist_path / path
                if requested_file.exists() and requested_file.is_file():
                    return FileResponse(requested_file)

            # For all other paths, show navigation page
            html_content = create_navigation_page()
            return HTMLResponse(content=html_content)

        display_access_info()
    else:
        log_info("UI out folder not found. Build the frontend first.")

    return app


# Factory function for uvicorn --factory mode
def create_app_factory() -> FastAPI:
    """Factory function that returns a FastAPI app instance synchronously."""
    import asyncio

    # Check if we're already in an event loop
    try:
        loop = asyncio.get_running_loop()
        # If we're in a running loop, we need to create a new thread
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, create_app())
            return future.result()
    except RuntimeError:
        # No running loop, we can use asyncio.run directly
        return asyncio.run(create_app())
