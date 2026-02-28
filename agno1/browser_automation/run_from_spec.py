from __future__ import annotations

"""
Run a browser_automation pipeline from a YAML/JSON RunSpec.

CLI:
  python -m agno1.browser_automation.run_from_spec --spec jobs/x.yaml

This module is "only-add" glue:
- It does NOT modify existing BaseChatAdapter/BrowserManager.
- It filters kwargs dynamically to stay compatible across code versions.
"""

import argparse
import json
import inspect
import os
import sys
import time
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .spec import (
    Platform,
    RunSpec,
    StepsAccessor,
    load_runspec,
    render_template,
)
from .utils import ensure_dir, normalize_cdp_endpoint, sanitize_filename

# Import Playwright adapters lazily for testability.
from .base import ExecutionConfig
from .manager import BrowserConfig, BrowserManager


def _filter_kwargs_for_callable(fn: Callable[..., Any], kwargs: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Return (filtered_kwargs, dropped_keys) based on callable signature."""
    try:
        sig = inspect.signature(fn)
    except Exception:
        return kwargs, []

    accepted = set()
    has_var_kw = False
    for p in sig.parameters.values():
        if p.kind == inspect.Parameter.VAR_KEYWORD:
            has_var_kw = True
        elif p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
            accepted.add(p.name)

    if has_var_kw:
        return kwargs, []

    filtered: Dict[str, Any] = {}
    dropped: List[str] = []
    for k, v in kwargs.items():
        if k in accepted:
            filtered[k] = v
        else:
            dropped.append(k)
    return filtered, dropped


def _filter_kwargs_for_dataclass(dc_type: Any, kwargs: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Filter kwargs by dataclass fields. If not a dataclass, return as-is."""
    try:
        if not is_dataclass(dc_type):
            return kwargs, []
        allowed = {f.name for f in fields(dc_type)}
        filtered = {k: v for k, v in kwargs.items() if k in allowed}
        dropped = [k for k in kwargs.keys() if k not in allowed]
        return filtered, dropped
    except Exception:
        return kwargs, []


def _auto_model(platform: Platform) -> str:
    # Policy requested by you:
    # - ChatGPT default: GPT-5.2 Thinking
    # - Gemini default: Gemini 3 Pro
    return "gpt-5.2-thinking" if platform == "chatgpt" else "gemini-3-pro"


def _resolve_model(platform: Platform, model: Optional[str]) -> Optional[str]:
    if model is None:
        return None
    m = (model or "").strip()
    if not m:
        return None
    if m.lower() == "auto":
        return _auto_model(platform)
    return m


def _manifest_paths(output_dir: str) -> Tuple[str, str]:
    manifest_path = os.path.join(output_dir, ".manifest.json")
    log_path = os.path.join(output_dir, ".pipeline.log")
    return manifest_path, log_path


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _write_json(path: str, data: Dict[str, Any]) -> None:
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=True, indent=2)


def _resolve_vars(spec: RunSpec) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {"vars": dict(spec.vars)}
    ctx.update(spec.vars)
    try:
        rendered_vars = render_template(dict(spec.vars), ctx) or {}
        if isinstance(rendered_vars, dict):
            ctx["vars"] = dict(rendered_vars)
            ctx.update(rendered_vars)
    except Exception:
        pass
    return ctx


def _resolve_output_dir_for_resume(spec: RunSpec, spec_path: str) -> Optional[str]:
    if not spec.steps:
        return None
    step = spec.steps[0]
    step_id = step.id or "step1"
    platform: Platform = step.platform or spec.defaults.platform
    session_id: str = step.session_id or spec.defaults.session_id
    base_artifacts_dir = spec.defaults.browser.base_artifacts_dir
    output_dir_template = (
        step.output_dir
        or spec.defaults.output_dir
        or os.path.join(base_artifacts_dir, platform, session_id)
    )
    ctx = _resolve_vars(spec)
    ctx.update(
        {
            "platform": platform,
            "session_id": session_id,
            "step_id": step_id,
            "step_index": 1,
            "repeat_index": 1,
            "index": 1,
        }
    )
    try:
        output_dir_raw = render_template(output_dir_template, ctx)
    except Exception:
        output_dir_raw = output_dir_template
    return os.path.abspath(str(output_dir_raw))


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


def _find_resume_start_at(spec: RunSpec, manifest_path: str) -> Optional[str]:
    manifest = _read_json(manifest_path)
    if not manifest:
        return None
    items = list(manifest.items())
    items.sort(key=lambda kv: _entry_sort_key(kv[0], kv[1]))
    last_key, last_entry = items[-1]
    last_step_id = last_entry.get("step_id")
    if not last_step_id:
        return None
    if last_entry.get("status") != "complete":
        return str(last_step_id)
    step_ids = [s.id or f"step{i}" for i, s in enumerate(spec.steps, start=1)]
    try:
        idx = step_ids.index(last_step_id)
    except ValueError:
        return None
    if idx + 1 >= len(step_ids):
        return "__DONE__"
    return step_ids[idx + 1]


def _record_manifest_entry(output_dir: str, entry: Dict[str, Any]) -> None:
    if not output_dir:
        return
    manifest_path, log_path = _manifest_paths(output_dir)

    try:
        ensure_dir(output_dir)
    except Exception:
        return

    key = f"{int(entry.get('index') or 0):04d}_{entry.get('step_id')}_r{int(entry.get('repeat_index') or 1)}"
    manifest = _read_json(manifest_path)
    manifest[key] = entry
    _write_json(manifest_path, manifest)

    try:
        line = json.dumps(entry, ensure_ascii=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def run_pipeline(
    spec: RunSpec,
    adapter_factory: Callable[[Platform, ExecutionConfig], Any],
    page_provider: Optional[Callable[[Platform, str], Any]] = None,
    initial_page_map: Optional[Dict[Tuple[Platform, str], Any]] = None,
    spec_path: Optional[str] = None,
    start_at: Optional[str] = None,
    dry_run: bool = False,
    on_step_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Execute a RunSpec using the provided adapter_factory.

    initial_page_map: optional (platform, session_id) -> Page; when set, that step
        uses the given page instead of get_or_create_page (e.g. for branch-in-new-chat flows).
    This function is pure-ish (no direct Playwright usage), so unit tests can run with FakeAdapter.
    """
    spec_dir = os.path.dirname(os.path.abspath(spec_path)) if spec_path else os.getcwd()

    # Template context: vars are available as top-level names.
    # We render `vars` once so users can define derived vars like:
    #   instruction_dir: "{{ instruction_root }}/指令汇总"
    # and then reference it in steps.
    ctx: Dict[str, Any] = {"vars": dict(spec.vars)}
    ctx.update(spec.vars)

    try:
        rendered_vars = render_template(dict(spec.vars), ctx) or {}
        if isinstance(rendered_vars, dict):
            ctx["vars"] = dict(rendered_vars)
            ctx.update(rendered_vars)
    except Exception:
        # If var rendering fails, keep raw vars (best-effort).
        pass

    steps_ctx = StepsAccessor()
    ctx["steps"] = steps_ctx

    results: List[Dict[str, Any]] = []
    dropped_params: List[Dict[str, Any]] = []

    # Track first-use per (platform, session_id) so we only navigate once by default.
    seen_sessions: set[Tuple[str, str]] = set()

    global_run_index = 0
    started = start_at is None

    for step_index, step in enumerate(spec.steps, start=1):
        step_id = step.id or f"step{step_index}"

        if not started:
            if start_at == step_id or start_at == str(step_index):
                started = True
            else:
                continue

        # Resolve effective config by inheriting defaults
        platform: Platform = step.platform or spec.defaults.platform
        session_id: str = step.session_id or spec.defaults.session_id

        url_raw = step.url if step.url is not None else spec.defaults.url
        mode = step.mode

        # model policy
        ensure_model = step.ensure_model if step.ensure_model is not None else spec.defaults.ensure_model
        model_eff = _resolve_model(platform, step.model if step.model is not None else spec.defaults.model)

        reset_chat = step.reset_chat if step.reset_chat is not None else spec.defaults.reset_chat
        download_after = step.download_after if step.download_after is not None else spec.defaults.download_after

        bring_to_front = step.bring_to_front if step.bring_to_front is not None else spec.defaults.bring_to_front
        force_goto = step.force_goto if step.force_goto is not None else spec.defaults.force_goto

        new_tab = step.new_tab if step.new_tab is not None else spec.defaults.new_tab
        close_tab = step.close_tab if step.close_tab is not None else spec.defaults.close_tab

        # Resolve output_dir template for this step (also where debug artifacts go).
        # NOTE: actual output_dir will be rendered per-run inside the repeat loop.
        base_artifacts_dir = spec.defaults.browser.base_artifacts_dir
        output_dir_template = step.output_dir or spec.defaults.output_dir or os.path.join(base_artifacts_dir, platform, session_id)

        # Save policy
        save_reply = step.save_reply if step.save_reply is not None else spec.defaults.save_reply
        save_spec_template = step.save or spec.defaults.save

        # Per-step execution config override
        exec_model = step.exec or spec.defaults.exec
        exec_cfg = ExecutionConfig(**_filter_kwargs_for_dataclass(ExecutionConfig, exec_model.model_dump(exclude_none=True))[0])

        # Repeat runs
        for rep_i in range(1, int(step.repeat) + 1):
            global_run_index += 1

            # Build per-run template context (vars + step metadata)
            run_ctx: Dict[str, Any] = dict(ctx)
            run_ctx.update(
                {
                    "platform": platform,
                    "session_id": session_id,
                    "step_id": step_id,
                    "step_index": step_index,
                    "repeat_index": rep_i,
                    "index": global_run_index,
                }
            )

            # Render output_dir (allows e.g. output_dir: "results/{{ ticker }}")
            output_dir_raw = render_template(output_dir_template, run_ctx)
            output_dir = os.path.abspath(str(output_dir_raw))
            run_ctx["output_dir"] = output_dir

            # Render save spec strings (dir/pattern) so later steps can reference saved_reply paths.
            # We keep the object mostly dict-based to stay compatible across pydantic versions.
            try:
                save_spec_eff: Any = save_spec_template.model_dump(exclude_none=True)
            except Exception:
                try:
                    save_spec_eff = dict(save_spec_template)  # type: ignore[arg-type]
                except Exception:
                    save_spec_eff = save_spec_template

            if isinstance(save_spec_eff, dict):
                if isinstance(save_spec_eff.get("dir"), str):
                    save_spec_eff["dir"] = render_template(save_spec_eff.get("dir"), run_ctx)
                if isinstance(save_spec_eff.get("pattern"), str):
                    save_spec_eff["pattern"] = render_template(save_spec_eff.get("pattern"), run_ctx)

            # Only navigate on first usage of this (platform, session_id), unless step explicitly gives url.
            use_url: Optional[str]
            if (platform, session_id) not in seen_sessions:
                use_url = url_raw
            else:
                use_url = step.url  # explicit step override only

            if use_url is not None:
                use_url = render_template(use_url, run_ctx)

            # Prompt/files templates
            prompt = render_template(step.prompt, run_ctx) if step.prompt is not None else None
            files: List[str] = []
            if step.files:
                rendered_files = render_template(list(step.files), run_ctx)
                files = [str(x) for x in (rendered_files or []) if str(x).strip()]

                # Resolve relative paths against spec dir (so YAML can live anywhere)
                resolved: List[str] = []
                for p in files:
                    if p.startswith(("http://", "https://")):
                        resolved.append(p)
                        continue
                    if os.path.isabs(p):
                        resolved.append(p)
                        continue
                    # If user gave a relative path, make it relative to spec file
                    resolved.append(os.path.abspath(os.path.join(spec_dir, p)))

                # Expand glob patterns (useful for "choose the first md under P*/" kinds of rules)
                expanded: List[str] = []
                for p in resolved:
                    if p.startswith(("http://", "https://")):
                        expanded.append(p)
                        continue

                    # Detect wildcard patterns
                    if any(ch in p for ch in ("*", "?", "[")):
                        import glob

                        matches = sorted(glob.glob(p, recursive=True))
                        matches = [m for m in matches if os.path.isfile(m)]
                        if not matches:
                            # In dry-run we keep the pattern so user can inspect/fix paths without hard failing.
                            if dry_run:
                                expanded.append(p)
                                continue
                            raise FileNotFoundError(f"No files matched glob: {p}")
                        # By default we pick the first match (deterministic, avoids huge uploads)
                        expanded.append(os.path.abspath(matches[0]))
                        continue

                    expanded.append(p)

                files = expanded

            # Compose adapter kwargs
            page_inst = None
            if initial_page_map:
                key = (platform, session_id)
                if key in initial_page_map:
                    page_inst = initial_page_map[key]
            kwargs: Dict[str, Any] = {
                "mode": mode,
                "session_id": session_id,
                "url": use_url,
                "page_instance": page_inst,
                "output_dir": output_dir,
                "download_after": bool(download_after),
                "reset_chat": bool(reset_chat),
            }

            if mode == "send-prompt":
                kwargs["instruction"] = prompt
                kwargs["files"] = files or None

            # Only pass model when we really intend to click UI
            if step.model is not None:
                # explicit per-step model: treat as intent to ensure unless ensure_model is explicitly False
                if step.ensure_model is not False and model_eff:
                    kwargs["model"] = model_eff
            else:
                if ensure_model and model_eff:
                    kwargs["model"] = model_eff

            # "new tab" / "bring to front" / "force goto" are optional across code versions.
            if kwargs.get("page_instance") is None and page_provider and new_tab and (platform, session_id) not in seen_sessions:
                try:
                    kwargs["page_instance"] = page_provider(platform, session_id)
                except Exception:
                    kwargs["page_instance"] = None

            kwargs["bring_to_front"] = bool(bring_to_front)
            kwargs["force_goto"] = bool(force_goto)
            kwargs["ensure_model"] = bool(ensure_model)
            if step.wait_for is not None:
                kwargs["wait_for"] = step.wait_for

            if dry_run:
                item: Dict[str, Any] = {
                    "status": "dry-run",
                    "index": global_run_index,
                    "step_id": step_id,
                    "platform": platform,
                    "session_id": session_id,
                    "mode": mode,
                    "url": use_url,
                    "prompt": prompt,
                    "files": files,
                    "download_after": bool(download_after),
                    "output_dir": output_dir,
                    "repeat_index": rep_i,
                }

                if save_reply and mode == "send-prompt":
                    try:
                        predicted = _predict_reply_path(
                            base_output_dir=output_dir,
                            save_spec=save_spec_eff,
                            index=global_run_index,
                            step_id=step_id,
                            platform=platform,
                            session_id=session_id,
                        )
                        if predicted:
                            item["saved_reply"] = predicted
                    except Exception:
                        pass

                results.append(item)
                steps_ctx.record(step_id, item)
                if on_step_complete is not None:
                    try:
                        on_step_complete(item)
                    except Exception:
                        pass
                seen_sessions.add((platform, session_id))
                continue

            adapter = adapter_factory(platform, exec_cfg)

            filtered_kwargs, dropped = _filter_kwargs_for_callable(adapter.execute, kwargs)
            if dropped:
                dropped_params.append({"step_id": step_id, "dropped": dropped})

            t0 = time.time()
            res = adapter.execute(**filtered_kwargs)
            dt = time.time() - t0

            # standardize a little
            if not isinstance(res, dict):
                res = {"status": "error", "text": "", "files": [], "error": f"Adapter returned non-dict: {type(res)}"}

            res.setdefault("status", "error")
            res.setdefault("text", "")
            res.setdefault("files", [])
            res.setdefault("error", None)

            res_meta = {
                "index": global_run_index,
                "step_id": step_id,
                "step_index": step_index,
                "platform": platform,
                "session_id": session_id,
                "mode": mode,
                "url": use_url,
                "repeat_index": rep_i,
                "elapsed_s": dt,
                "started_at": t0,
                "finished_at": t0 + dt,
                "output_dir": output_dir,
            }

            combined: Dict[str, Any] = {**res_meta, **res}

            # save reply (if enabled and run succeeded) and expose saved path to later steps
            if save_reply and res.get("status") == "complete":
                try:
                    saved = _save_reply(
                        base_output_dir=output_dir,
                        save_spec=save_spec_eff,
                        index=global_run_index,
                        step_id=step_id,
                        platform=platform,
                        session_id=session_id,
                        prompt=prompt or "",
                        reply=str(res.get("text") or ""),
                    )
                    if saved:
                        combined["saved_reply"] = saved
                except Exception:
                    # Saving should not fail the run; keep going.
                    pass

            results.append(combined)

            # record for future templates
            steps_ctx.record(step_id, combined)
            if on_step_complete is not None:
                try:
                    on_step_complete(combined)
                except Exception:
                    pass
            _record_manifest_entry(output_dir, combined)

            seen_sessions.add((platform, session_id))

            # stop on first failure/timeout
            if res.get("status") != "complete":
                return {
                    "status": res.get("status"),
                    "results": results,
                    "dropped_params": dropped_params,
                }

            # close tab if requested (only possible if adapter exposes it; runner can't always)
            # (Handled in main() when we control BrowserManager)

    return {"status": "complete", "results": results, "dropped_params": dropped_params}


def _predict_reply_path(
    base_output_dir: str,
    save_spec: Any,
    index: int,
    step_id: str,
    platform: str,
    session_id: str,
) -> Optional[str]:
    """Predict reply save path without writing (used for --dry-run)."""

    def _get(key: str, default: Any = None) -> Any:
        try:
            if isinstance(save_spec, dict):
                return save_spec.get(key, default)
        except Exception:
            pass
        try:
            return getattr(save_spec, key)
        except Exception:
            return default

    try:
        enabled = bool(_get("enabled", True))
    except Exception:
        enabled = True
    if not enabled:
        return None

    fmt = _get("format", "md")
    fmt = (fmt or "md").lower().strip()
    if fmt not in {"md", "txt", "json"}:
        fmt = "md"

    ext = fmt
    pattern = _get("pattern", "{index:02d}_{step_id}.{ext}")

    save_dir = _get("dir", None) or os.path.join(base_output_dir, "replies")
    save_dir = os.path.abspath(str(save_dir))

    filename = str(pattern).format(
        index=index,
        step_id=step_id,
        platform=platform,
        session_id=session_id,
        ext=ext,
    )
    filename = sanitize_filename(filename)
    return os.path.join(save_dir, filename)


def _save_reply(
    base_output_dir: str,
    save_spec: Any,
    index: int,
    step_id: str,
    platform: str,
    session_id: str,
    prompt: str,
    reply: str,
) -> Optional[str]:
    """Save reply to disk, returns path or None."""
    def _get(key: str, default: Any = None) -> Any:
        try:
            if isinstance(save_spec, dict):
                return save_spec.get(key, default)
        except Exception:
            pass
        try:
            return getattr(save_spec, key)
        except Exception:
            return default

    try:
        enabled = bool(_get("enabled", True))
    except Exception:
        enabled = True
    if not enabled:
        return None

    fmt = _get("format", "md")
    fmt = (fmt or "md").lower().strip()
    if fmt not in {"md", "txt", "json"}:
        fmt = "md"

    ext = fmt
    pattern = _get("pattern", "{index:02d}_{step_id}.{ext}")
    include_prompt = bool(_get("include_prompt", False))

    save_dir = _get("dir", None) or os.path.join(base_output_dir, "replies")
    save_dir = ensure_dir(os.path.abspath(save_dir))

    # Use format string for easy naming
    filename = pattern.format(
        index=index,
        step_id=step_id,
        platform=platform,
        session_id=session_id,
        ext=ext,
    )
    filename = sanitize_filename(filename)
    path = os.path.join(save_dir, filename)

    if fmt == "json":
        import json

        payload = {"step_id": step_id, "platform": platform, "session_id": session_id, "prompt": prompt, "reply": reply}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path

    body = reply
    if include_prompt:
        body = f"## Prompt\n\n{prompt}\n\n## Reply\n\n{reply}\n"

    with open(path, "w", encoding="utf-8") as f:
        f.write(body)

    return path


def _real_adapter_factory(bm: BrowserManager) -> Callable[[Platform, ExecutionConfig], Any]:
    # Import site-specific adapters lazily so unit tests can run with FakeAdapter
    # without importing Playwright-heavy or UI-fragile modules.
    from .gpt import ChatGPTAdapter
    from .gemini import GeminiAdapter

    def _factory(platform: Platform, exec_cfg: ExecutionConfig) -> Any:
        if platform == "chatgpt":
            return ChatGPTAdapter(browser=bm, exec_cfg=exec_cfg)
        if platform == "gemini":
            return GeminiAdapter(browser=bm, exec_cfg=exec_cfg)
        raise ValueError(f"Unknown platform: {platform}")
    return _factory


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Run browser_automation pipeline from YAML RunSpec")
    ap.add_argument("--spec", required=True, help="YAML/JSON spec path (e.g. jobs/x.yaml)")
    ap.add_argument(
        "--start-at",
        default=None,
        help="Skip steps before this step id or 1-based index (useful for resume).",
    )
    ap.add_argument(
        "--resume",
        action="store_true",
        help="Auto-resume from the last completed step using the manifest.",
    )
    ap.add_argument(
        "--manifest",
        default=None,
        help="Explicit manifest path for resume (defaults to output_dir/.manifest.json).",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print resolved plan, don't touch browser.")
    ap.add_argument(
        "--set",
        action="append",
        default=[],
        help="Override vars: --set key=value (can be repeated)",
    )
    args = ap.parse_args(argv)

    spec_path = os.path.abspath(args.spec)
    spec = load_runspec(spec_path)

    # Apply CLI var overrides
    for kv in args.set or []:
        if "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        k = k.strip()
        if not k:
            continue
        spec.vars[k] = v

    resume_start_at = args.start_at
    if resume_start_at is None and args.resume:
        manifest_path = args.manifest
        if not manifest_path:
            output_dir = _resolve_output_dir_for_resume(spec, spec_path)
            if output_dir:
                manifest_path = os.path.join(output_dir, ".manifest.json")
        if manifest_path and os.path.exists(manifest_path):
            resume_start_at = _find_resume_start_at(spec, manifest_path)
            if resume_start_at == "__DONE__":
                print("[runspec] All steps already completed. Nothing to resume.")
                return 0
            if resume_start_at is None:
                print("[runspec] Resume failed: could not infer next step from manifest.")
        else:
            print("[runspec] Resume failed: manifest not found.")
            print("Provide --manifest or ensure output_dir/.manifest.json exists.")

    # Build browser config (filter to the actual dataclass fields)
    browser_kwargs = spec.defaults.browser.model_dump(exclude_none=True)
    if browser_kwargs.get("mode") == "attach":
        if not browser_kwargs.get("cdp_endpoint"):
            browser_kwargs["cdp_endpoint"] = normalize_cdp_endpoint(
                os.getenv("CDP_ENDPOINT") or os.getenv("CDP") or "http://127.0.0.1:9222"
            )
        else:
            browser_kwargs["cdp_endpoint"] = normalize_cdp_endpoint(str(browser_kwargs["cdp_endpoint"]))

    filtered_browser_kwargs, _ = _filter_kwargs_for_dataclass(BrowserConfig, browser_kwargs)
    browser_cfg = BrowserConfig(**filtered_browser_kwargs)

    bm = BrowserManager(browser_cfg)

    # For new_tab support we need access to context, so start browser before running.
    if not args.dry_run:
        bm.start()

    page_instances: Dict[Tuple[str, str], Any] = {}

    def _page_provider(platform: Platform, session_id: str) -> Any:
        # One page per (platform, session) when new_tab is enabled.
        key = (platform, session_id)
        if key in page_instances:
            return page_instances[key]
        p = bm.context.new_page()
        try:
            p.bring_to_front()
        except Exception:
            pass
        page_instances[key] = p
        return p

    try:
        res = run_pipeline(
            spec,
            adapter_factory=_real_adapter_factory(bm),
            page_provider=_page_provider,
            spec_path=spec_path,
            start_at=resume_start_at,
            dry_run=bool(args.dry_run),
        )
    finally:
        # In dry-run we didn't start BM; close() should still be safe.
        try:
            bm.close()
        except Exception:
            pass

    status = res.get("status")
    if status != "complete":
        print(f"[runspec] status={status}")
        # best-effort surface last error
        for item in reversed(res.get("results") or []):
            if item.get("status") != "complete":
                err = item.get("error")
                if err:
                    print("[runspec] error:")
                    print(err)
                break
        return 2

    # Print a concise summary
    items = res.get("results") or []
    print(f"[runspec] complete. steps_ran={len(items)}")
    for it in items:
        elapsed = it.get("elapsed_s")
        try:
            elapsed_str = f"{float(elapsed):.1f}s" if elapsed is not None else "-"
        except Exception:
            elapsed_str = "-"

        print(
            f"- {it.get('step_id')} [{it.get('platform')}] "
            f"status={it.get('status')} elapsed={elapsed_str} "
            f"reply_chars={len((it.get('text') or '').strip())} files={len(it.get('files') or [])}"
        )

    dropped = res.get("dropped_params") or []
    if dropped:
        print("[runspec] NOTE: some spec params were dropped (your codebase may be older than this runner expects):")
        for d in dropped[:10]:
            print(f"  - {d.get('step_id')}: {d.get('dropped')}")
        if len(dropped) > 10:
            print(f"  ... ({len(dropped)-10} more)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
