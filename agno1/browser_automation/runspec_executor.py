from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import ExecutionConfig
from .gpt import ChatGPTAdapter
from .gemini import GeminiAdapter
from .manager import BrowserConfig, BrowserManager
from .spec import BrowserSpec, DefaultsSpec, ExecutionSpec, RunSpec, SaveSpec, StepSpec
from .run_from_spec import run_pipeline
from .utils import normalize_cdp_endpoint


@dataclass(frozen=True)
class RunSpecStepResult:
    status: str
    output_path: Optional[str]
    error: Optional[str]
    details: Dict[str, Any]


def manifest_path(output_dir: Path) -> Path:
    return Path(output_dir) / ".manifest.json"


def read_manifest(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _entry_sort_key(entry_key: str, entry: Dict[str, Any]) -> Tuple[int, int]:
    idx = entry.get("index")
    rep = entry.get("repeat_index")
    try:
        idx_val = int(idx)
    except Exception:
        try:
            idx_val = int(str(entry_key).split("_", 1)[0])
        except Exception:
            idx_val = -1
    try:
        rep_val = int(rep)
    except Exception:
        rep_val = 0
    return idx_val, rep_val


def sorted_manifest_entries(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = list(manifest.items())
    items.sort(key=lambda kv: _entry_sort_key(kv[0], kv[1]))
    return [entry for _, entry in items]


def last_status_for_step(manifest: Dict[str, Any], step_id: str) -> Optional[str]:
    status = None
    for entry in sorted_manifest_entries(manifest):
        if entry.get("step_id") == step_id:
            status = entry.get("status")
    return status


def infer_next_step_id(step_ids: List[str], output_dir: Path) -> Optional[str]:
    manifest = read_manifest(manifest_path(output_dir))
    if not manifest:
        return None
    last_entry = None
    for entry in sorted_manifest_entries(manifest):
        if entry.get("step_id") in step_ids:
            last_entry = entry
    if not last_entry:
        return None
    last_step_id = last_entry.get("step_id")
    if not last_step_id:
        return None
    if last_entry.get("status") != "complete":
        return str(last_step_id)
    try:
        idx = step_ids.index(str(last_step_id))
    except ValueError:
        return None
    if idx + 1 >= len(step_ids):
        return "__DONE__"
    return step_ids[idx + 1]

def _infer_format(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    if ext in {"md", "txt", "json"}:
        return ext
    return "md"


def _build_browser_config(base_artifacts_dir: str) -> BrowserConfig:
    cdp = normalize_cdp_endpoint(
        os.getenv("CDP_ENDPOINT") or os.getenv("CDP") or "http://127.0.0.1:9222"
    )
    return BrowserConfig(
        mode="attach",
        cdp_endpoint=cdp,
        base_artifacts_dir=base_artifacts_dir,
        accept_downloads=True,
    )


class RunSpecStepExecutor:
    def __init__(
        self,
        *,
        browser_cfg: Optional[BrowserConfig] = None,
        base_artifacts_dir: Optional[str] = None,
        exec_spec: Optional[ExecutionSpec] = None,
    ) -> None:
        artifacts_dir = base_artifacts_dir or (browser_cfg.base_artifacts_dir if browser_cfg else "./artifacts")
        self._browser_cfg = browser_cfg or _build_browser_config(artifacts_dir)
        self._browser_cfg.base_artifacts_dir = artifacts_dir
        self._bm = BrowserManager(self._browser_cfg)
        self._bm.start()
        self._page_instances: Dict[Tuple[str, str], Any] = {}
        self._exec_spec = exec_spec or ExecutionSpec()

    def close(self) -> None:
        try:
            self._bm.close()
        except Exception:
            pass

    def _adapter_factory(self, platform: str, exec_cfg: ExecutionConfig) -> Any:
        if platform == "chatgpt":
            return ChatGPTAdapter(browser=self._bm, exec_cfg=exec_cfg)
        if platform == "gemini":
            return GeminiAdapter(browser=self._bm, exec_cfg=exec_cfg)
        raise ValueError(f"Unknown platform: {platform}")

    def _page_provider(self, platform: str, session_id: str) -> Any:
        key = (platform, session_id)
        if key in self._page_instances:
            return self._page_instances[key]
        p = self._bm.context.new_page()
        try:
            p.bring_to_front()
        except Exception:
            pass
        self._page_instances[key] = p
        return p

    def run_step(
        self,
        *,
        name: str,
        step_id: str,
        platform: str,
        session_id: str,
        url: str,
        mode: str,
        prompt: Optional[str],
        files: Optional[list[str]],
        output_path: Path,
        output_dir: Path,
        download_after: bool = False,
        ensure_model: Optional[bool] = None,
        model: Optional[str] = None,
        bring_to_front: bool = True,
        force_goto: bool = True,
        new_tab: bool = False,
        close_tab: bool = False,
        reset_chat: Optional[bool] = None,
        exec_spec: Optional[ExecutionSpec] = None,
        include_prompt: bool = False,
    ) -> RunSpecStepResult:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        save_spec = SaveSpec(
            enabled=True,
            dir=str(output_dir),
            format=_infer_format(output_path),
            pattern=output_path.name,
            include_prompt=include_prompt,
        )
        defaults = DefaultsSpec(
            platform=platform,
            session_id=session_id,
            url=url,
            download_after=download_after,
            save_reply=True,
            output_dir=str(output_dir),
            bring_to_front=bring_to_front,
            force_goto=force_goto,
            browser=BrowserSpec(base_artifacts_dir=str(output_dir)),
            exec=exec_spec or self._exec_spec,
            ensure_model=ensure_model if ensure_model is not None else True,
            model=model,
            reset_chat=reset_chat if reset_chat is not None else False,
        )
        step = StepSpec(
            id=step_id,
            platform=platform,
            session_id=session_id,
            mode=mode,  # type: ignore[arg-type]
            url=url,
            prompt=prompt,
            files=files or [],
            download_after=download_after,
            save_reply=True,
            save=save_spec,
            output_dir=str(output_dir),
            bring_to_front=bring_to_front,
            force_goto=force_goto,
            new_tab=new_tab,
            close_tab=close_tab,
            reset_chat=reset_chat,
        )
        spec = RunSpec(
            name=name,
            defaults=defaults,
            steps=[step],
        )
        res = run_pipeline(
            spec,
            adapter_factory=self._adapter_factory,
            page_provider=self._page_provider,
            spec_path=None,
            start_at=None,
            dry_run=False,
        )
        status = res.get("status") or "error"
        details = (res.get("results") or [{}])[-1]
        error = details.get("error")
        saved = details.get("saved_reply") or (str(output_path) if output_path.exists() else None)
        return RunSpecStepResult(
            status=str(status),
            output_path=saved,
            error=str(error) if error else None,
            details=details,
        )
