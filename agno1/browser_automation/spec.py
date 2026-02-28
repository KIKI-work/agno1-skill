from __future__ import annotations

"""
RunSpec: a YAML/JSON protocol for browser_automation pipelines.

Design goals:
- "改 prompt 不改代码"：prompt/文件/是否下载/是否保存 全部写在 YAML。
- "只新增、不破坏"：不修改现有 adapter/base，只在 runner 侧做参数兼容过滤。
- 可扩展：schema 版本化 + defaults/vars + step override。

Template syntax (both supported):
- ${var}            : simple variable substitution from `vars` (string.Template)
- {{ python_expr }} : expression evaluated against a safe context:
    - vars are available as top-level names (e.g. {{ ticker }})
    - steps.<id> refers to the *latest* result of that step (Dot-access)
    - steps.history("<id>") returns a list of all results for that step

Example:
  vars:
    ticker: ao
  steps:
    - id: s1
      prompt: "Analyze {{ ticker }}"
    - id: s2
      files: ["{{ steps.s1.files[0] }}"]
      prompt: "Use the uploaded file and summarize"

NOTE: This file is intentionally dependency-light (no Playwright import),
so it can be imported in unit tests without touching browsers.
"""

from dataclasses import dataclass
import json
import os
import re
from string import Template
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Platform = Literal["chatgpt", "gemini"]
ExecuteMode = Literal["send-prompt", "extract-latest", "download-artifact"]


# ----------------------------
# Small helper types
# ----------------------------

class StepResult(dict):
    """Dict with dot-access (best-effort)."""

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError as e:
            raise AttributeError(item) from e

    # Keep repr short (helps debugging in logs/tests)
    def __repr__(self) -> str:  # pragma: no cover
        keys = ", ".join(list(self.keys())[:6])
        return f"StepResult({{{keys}...}})"


class StepsAccessor:
    """Expose previous step results to templates.

    - steps.<id>  -> latest StepResult for that id (or None)
    - steps["id"] -> same
    - steps.history("id") -> list[StepResult]
    """

    def __init__(self) -> None:
        self._last: Dict[str, StepResult] = {}
        self._hist: Dict[str, List[StepResult]] = {}

    def record(self, step_id: str, result: Dict[str, Any]) -> None:
        r = StepResult(result)
        self._last[step_id] = r
        self._hist.setdefault(step_id, []).append(r)

    def history(self, step_id: str) -> List[StepResult]:
        return list(self._hist.get(step_id, []))

    def __getitem__(self, step_id: str) -> StepResult:
        return self._last[step_id]

    def __getattr__(self, step_id: str) -> Any:
        # Return None instead of raising (friendlier in templates)
        return self._last.get(step_id)


SAFE_EVAL_GLOBALS: Dict[str, Any] = {
    "__builtins__": {},
    # allow a few harmless builtins for convenience
    "str": str,
    "int": int,
    "float": float,
    "len": len,
    "min": min,
    "max": max,
    "sorted": sorted,
}


_TEMPLATE_EXPR_RE = re.compile(r"\{\{\s*(.*?)\s*\}\}")


def render_string(template: str, ctx: Dict[str, Any]) -> str:
    """Render one string using ${var} + {{expr}}."""
    if template is None:
        return ""

    s = str(template)

    # 1) ${var} expansion (simple)
    try:
        s = Template(s).safe_substitute(ctx)
    except Exception:
        # ignore template errors; expression layer may still handle it
        pass

    # 2) {{ python_expr }} expansion
    def _repl(m: re.Match[str]) -> str:
        expr = (m.group(1) or "").strip()
        if not expr:
            return ""
        try:
            val = eval(expr, SAFE_EVAL_GLOBALS, ctx)  # noqa: S307 (user-controlled spec file)
        except Exception as e:
            raise ValueError(f"Template eval failed for expr={expr!r}: {e}") from e
        return "" if val is None else str(val)

    return _TEMPLATE_EXPR_RE.sub(_repl, s)


def render_template(value: Any, ctx: Dict[str, Any]) -> Any:
    """Recursively render strings inside lists/dicts."""
    if value is None:
        return None
    if isinstance(value, str):
        return render_string(value, ctx)
    if isinstance(value, list):
        return [render_template(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: render_template(v, ctx) for k, v in value.items()}
    return value


# ----------------------------
# Pydantic schema
# ----------------------------

class BrowserSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # NOTE: These fields are a superset; runner will filter unknown keys when creating BrowserConfig.
    mode: Literal["attach", "launch"] = "attach"

    # attach mode
    cdp_endpoint: Optional[str] = None

    # launch mode
    headless: bool = True
    channel: Optional[str] = None
    executable_path: Optional[str] = None
    args: List[str] = Field(default_factory=list)

    # auth
    storage_state_path: Optional[str] = None

    # navigation (some codebases support; runner filters if not)
    navigation_timeout_ms: Optional[int] = None
    navigation_wait_until: Optional[str] = None

    # debug & downloads
    accept_downloads: bool = True
    base_artifacts_dir: str = "./artifacts"
    capture_debug_screenshot: Optional[bool] = None
    capture_debug_html: Optional[bool] = None


class ExecutionSpec(BaseModel):
    # Pydantic reserves `model_` namespace; allow our fields like model_switch_strict.
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    # minimal fields (present in almost all versions)
    generation_timeout_s: int = 300
    stable_text_window_s: float = 2.0
    poll_interval_s: float = 0.5
    artifact_wait_s: int = 30
    after_upload_wait_s: float = 0.8
    after_model_switch_wait_s: float = 0.8

    # extended fields (newer codebases)
    prompt_ready_timeout_s: Optional[int] = None
    force_end_if_stop_visible_s: Optional[float] = None
    force_end_min_text_chars: Optional[int] = None
    post_generation_extract_retry_s: Optional[float] = None
    require_non_empty_reply: Optional[bool] = None
    send_ack_timeout_s: Optional[float] = None
    send_max_attempts: Optional[int] = None
    require_send_ack: Optional[bool] = None
    no_output_timeout_s: Optional[float] = None
    min_reply_chars: Optional[int] = None
    retry_on_short_reply: Optional[bool] = None
    regenerate_max_attempts: Optional[int] = None
    model_switch_strict: Optional[bool] = None

    # Ensure reply is actually rendered before next send (virtualized UIs may lag).
    require_reply_visible: Optional[bool] = None
    reply_visible_timeout_s: Optional[float] = None
    reply_visible_min_chars: Optional[int] = None

    # Require specific completion marker in reply before proceeding.
    require_completion_marker: Optional[str] = None

    # If reply not visible in time, reload the page and re-check.
    refresh_on_reply_not_visible: Optional[bool] = None
    refresh_max_attempts: Optional[int] = None
    refresh_cooldown_s: Optional[float] = None


class SaveSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    # None => runner decides (usually <output_dir>/replies)
    dir: Optional[str] = None
    # For human review we default to markdown
    format: Literal["md", "txt", "json"] = "md"
    # You can change naming without touching code
    pattern: str = "{index:02d}_{step_id}.{ext}"
    include_prompt: bool = False


class DefaultsSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Platform = "chatgpt"
    session_id: str = "default"
    url: Optional[str] = None  # default chat url (first step)

    # model policy:
    # - model="auto" means: chatgpt => gpt-5.2-thinking, gemini => gemini-3-pro
    # - ensure_model=false by default so we don't click UIs unless user asks
    model: Optional[str] = "auto"
    ensure_model: bool = False

    reset_chat: bool = False
    download_after: bool = False

    # save policy
    save_reply: bool = True
    save: SaveSpec = Field(default_factory=SaveSpec)

    # output
    output_dir: Optional[str] = None

    # navigation/ux (newer base versions support; runner filters if not)
    bring_to_front: bool = True
    force_goto: bool = False
    new_tab: bool = False
    close_tab: bool = False

    # base configs
    browser: BrowserSpec = Field(default_factory=BrowserSpec)
    exec: ExecutionSpec = Field(default_factory=ExecutionSpec)


class StepSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: Optional[str] = None

    platform: Optional[Platform] = None
    session_id: Optional[str] = None

    mode: ExecuteMode = "send-prompt"

    url: Optional[str] = None

    # Use "prompt" as the public name; allow "instruction" alias for back-compat
    prompt: Optional[str] = Field(default=None, alias="instruction")

    files: List[str] = Field(default_factory=list)

    # If None => inherit defaults.download_after
    download_after: Optional[bool] = None

    # save policy overrides
    save_reply: Optional[bool] = None
    save: Optional[SaveSpec] = None

    repeat: int = 1

    # model override
    model: Optional[str] = None
    ensure_model: Optional[bool] = None

    reset_chat: Optional[bool] = None

    # output dir override (also affects debug artifacts)
    output_dir: Optional[str] = None

    # navigation/ux overrides (newer base versions support)
    bring_to_front: Optional[bool] = None
    force_goto: Optional[bool] = None
    new_tab: Optional[bool] = None
    close_tab: Optional[bool] = None

    # per-step execution override
    exec: Optional[ExecutionSpec] = None

    # Optional: wait for selector before running step (e.g. before extract-latest)
    # Dict with keys: selector (str), timeout_ms (int, optional), visible (bool, optional)
    wait_for: Optional[Dict[str, Any]] = None

    @field_validator("repeat")
    @classmethod
    def _repeat_ge_1(cls, v: int) -> int:
        if v < 1:
            raise ValueError("repeat must be >= 1")
        return v

    @model_validator(mode="after")
    def _validate_prompt_for_mode(self) -> "StepSpec":
        if self.mode == "send-prompt":
            if not (self.prompt or "").strip():
                raise ValueError("send-prompt step requires prompt/instruction")
        return self


class RunSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    name: str = "run"
    description: Optional[str] = None

    vars: Dict[str, Any] = Field(default_factory=dict)
    defaults: DefaultsSpec = Field(default_factory=DefaultsSpec)
    steps: List[StepSpec] = Field(default_factory=list)

    # --- shorthand form (for very simple runs) ---
    platform: Optional[Platform] = None
    session_id: Optional[str] = None
    url: Optional[str] = None
    prompt: Optional[str] = None
    files: List[str] = Field(default_factory=list)
    repeat: int = 1
    save_reply: Optional[bool] = None
    download_after: Optional[bool] = None
    model: Optional[str] = None
    ensure_model: Optional[bool] = None
    reset_chat: Optional[bool] = None

    @model_validator(mode="after")
    def _apply_shorthand_and_defaults(self) -> "RunSpec":
        # Root-level platform/session/url override defaults
        if self.platform:
            self.defaults.platform = self.platform
        if self.session_id:
            self.defaults.session_id = self.session_id
        if self.url:
            self.defaults.url = self.url

        # If steps are empty, allow shorthand (url+prompt)
        if not self.steps:
            if (self.prompt or "").strip() or (self.defaults.url or "").strip() or self.files:
                st = StepSpec(
                    id="step1",
                    platform=self.platform,
                    session_id=self.session_id,
                    url=self.url,
                    prompt=self.prompt,
                    files=list(self.files),
                    repeat=self.repeat,
                    download_after=self.download_after,
                    save_reply=self.save_reply,
                    model=self.model,
                    ensure_model=self.ensure_model,
                    reset_chat=self.reset_chat,
                )
                self.steps = [st]
            else:
                raise ValueError("RunSpec.steps is empty. Provide `steps:` or shorthand fields (url+prompt).")

        return self


def load_runspec(path: str) -> RunSpec:
    """Load YAML/JSON into RunSpec."""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    data: Any
    if path.lower().endswith(".json"):
        data = json.loads(raw)
    else:
        data = yaml.safe_load(raw)

    if not isinstance(data, dict):
        raise ValueError("Spec file must be a YAML/JSON mapping (dict at top-level)")

    return RunSpec.model_validate(data)
