"""文档图助手流水线：在 ChatGPT 项目内驱动 agent1~5 生成 figure_plan → Mermaid → QA → 插入文档。

流程简述：
1) 需要分析的文档已经作为项目文件存在于 ChatGPT 项目 Sources 中（由用户手动上传，或通过 --scope-docs 上传到 Sources）
2) 在项目对话中，每个 agent 步骤将对应的 SKILL.md 作为 chat 附件上传，并发送 prompt
   - agent1_figure_plan  : 上传 SKILL.md → 生成 figure_plan
   - agent2_ambiguity_checker : 上传 SKILL.md → 歧义检查
   - agent3_mermaid_author    : 上传 SKILL.md → 生成 Mermaid 草图
   - agent4_mermaid_qa        : 上传 SKILL.md → QA 门禁
3) agent4 通过后，由本地 agno agent（agent5_renderer）从回复中提取 Mermaid 代码并插入目标文档；若不通过则重试

用法（建议复用已登录 Chrome）：
  CDP_ENDPOINT="ws://127.0.0.1:9222/..." python -m agno1.pipelines.docviz_diagram_chatgpt \\
    --project-url "https://chatgpt.com/.../project" \\
    --out-dir artifacts/docviz_diagram

恢复（无用户回复路径）：
  python -m agno1.pipelines.docviz_diagram_chatgpt --resume --no-reply --state-dir artifacts/docviz_diagram
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agno1.browser_automation.base import ExecutionConfig
from agno1.browser_automation.gpt import ChatGPTAdapter
from agno1.browser_automation.manager import BrowserConfig, BrowserManager
from agno1.browser_automation.utils import ensure_dir, normalize_cdp_endpoint

# ChatGPT 生成文档图助手项目页（新 chat = 在此 URL 下开始新对话）
DOC_VIZ_PROJECT_URL = (
    "https://chatgpt.com/g/g-p-698ad99f54848191ad82a3dc6f887b08-sheng-cheng-wen-dang-tu-zhu-shou/project"
)

# 各 agent SKILL.md 相对路径（相对于仓库根或 --skill-dir）
SKILL_REL_PATHS = {
    "agent1": "agents/skills/docviz-doc-diagram-assistant/reference/skills/agent1_figure_plan/SKILL.md",
    "agent2": "agents/skills/docviz-doc-diagram-assistant/reference/skills/agent2_ambiguity_checker/SKILL.md",
    "agent3_mermaid": "agents/skills/docviz-doc-diagram-assistant/reference/skills/agent3_mermaid_author/SKILL.md",
    "agent3_swirly": "agents/skills/docviz-doc-diagram-assistant/reference/skills/agent3_swirly_author/SKILL.md",
    "agent4_mermaid": "agents/skills/docviz-doc-diagram-assistant/reference/skills/agent4_mermaid_qa/SKILL.md",
    "agent4_swirly": "agents/skills/docviz-doc-diagram-assistant/reference/skills/agent4_swirly_qa/SKILL.md",
    # agent5 由本地 agno agent 执行，不走 Web UI，无需 SKILL 文件路径
}

# 各步骤发送的 prompt（SKILL 内容在运行时内联追加，见 _build_prompt_with_skill）
PROMPT_AGENT1_FIGURE_PLAN = (
    "{user_context}"
    "请按照以下 SKILL 规范执行 **agent1_figure_plan**，\n"
    "结合项目 Sources 中已有的文档，生成 figure_plan。\n"
    "输出：figure_plan YAML + 需要向用户确认的问题（如有）。"
)
PROMPT_AGENT2_AMBIGUITY = (
    "请按照以下 SKILL 规范执行 **agent2_ambiguity_checker**，对上一步的 figure_plan 进行歧义检查。\n"
    "若未收到用户回复，请按无回复路径处理，输出需要向用户发送的澄清问题。"
)
PROMPT_AGENT3_MERMAID = (
    "请按照以下 SKILL 规范执行 **agent3_mermaid_author**，\n"
    "根据前述 figure_plan 生成 Mermaid 图代码。"
)
PROMPT_AGENT4_QA = (
    "请按照以下 SKILL 规范执行 **agent4_mermaid_qa**，\n"
    "对当前 Mermaid 图进行质量检查，明确输出「通过」或「不通过」。"
)
# agent4 回复中判定通过的关键词
QA_PASS_MARKERS = ["通过", "通过。", "通过，", "pass", "PASS", "合格"]
QA_FAIL_MARKERS = ["不通过", "不通过。", "不通过，", "fail", "FAIL", "不合格"]


def _bundle_docs(doc_paths: List[str], out_zip_path: str) -> str:
    """将若干文档打成 zip，返回 zip 绝对路径。"""
    ensure_dir(os.path.dirname(out_zip_path))
    with zipfile.ZipFile(out_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in doc_paths:
            path = Path(p)
            if not path.is_file():
                continue
            zf.write(path, path.name)
    return os.path.abspath(out_zip_path)


def _parse_questions_from_reply(reply: str) -> List[str]:
    """从助手回复中解析「需要向用户发送的问题」列表（启发式）。"""
    if not (reply or "").strip():
        return []
    questions: List[str] = []
    # 常见模式：问题：、1. … 2. …、请确认：…
    for pattern in [
        r"问题[：:]\s*(.+?)(?=\n\n|\n\d+\.|$)",
        r"需要[向对]?用户[发送]?的问题[：:]?\s*(.+?)(?=\n\n|$)",
        r"请[向对]?用户确认[：:]?\s*(.+?)(?=\n\n|$)",
    ]:
        for m in re.finditer(pattern, reply, re.DOTALL):
            block = (m.group(1) or "").strip()
            if block:
                questions.append(block)
    if not questions and len(reply.strip()) > 50:
        questions.append(reply.strip()[:500])
    return questions[:10]


def _is_qa_passed(reply: str) -> bool:
    """根据 agent4 回复判断是否通过。"""
    if not (reply or "").strip():
        return False
    r = reply.strip()
    for m in QA_PASS_MARKERS:
        if m in r:
            return True
    for m in QA_FAIL_MARKERS:
        if m in r:
            return False
    # 默认：若含「通过」字样则通过
    return "通过" in r


def _extract_mermaid_code(reply: str) -> Optional[str]:
    """从 agent5 回复中提取 Mermaid 代码块。"""
    if not (reply or "").strip():
        return None
    # ```mermaid ... ``` 或 ``` ... ```
    for pattern in [
        r"```mermaid\s*\n(.*?)```",
        r"```\s*\n(.*?)```",
    ]:
        m = re.search(pattern, reply, re.DOTALL)
        if m:
            code = (m.group(1) or "").strip()
            if code and ("graph" in code or "flowchart" in code or "sequenceDiagram" in code):
                return code
    return None


def _insert_mermaid_into_doc(doc_path: str, mermaid_code: str, marker: str = "<!-- DOC_VIZ_MERMAID -->") -> bool:
    """在文档中 marker 处插入或替换 Mermaid 块；若无 marker 则追加到文末。"""
    path = Path(doc_path)
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    block = f"\n\n```mermaid\n{mermaid_code}\n```\n\n"
    if marker in text:
        # 替换已有占位与代码块
        text = re.sub(
            rf"{re.escape(marker)}\s*\n?```mermaid.*?```",
            marker + "\n" + block.strip(),
            text,
            flags=re.DOTALL,
        )
    else:
        text = text.rstrip() + "\n\n" + marker + block
    path.write_text(text, encoding="utf-8")
    return True


def load_state(state_dir: str) -> Dict[str, Any]:
    """从 state_dir 读取 pipeline 状态。"""
    p = Path(state_dir) / "docviz_diagram_state.json"
    if not p.is_file():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state_dir: str, state: Dict[str, Any]) -> None:
    """将 pipeline 状态写入 state_dir。"""
    ensure_dir(state_dir)
    p = Path(state_dir) / "docviz_diagram_state.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _resolve_skill_path(key: str, skill_base_dir: Optional[str] = None) -> Optional[str]:
    """返回 SKILL.md 的绝对路径；找不到返回 None。"""
    rel = SKILL_REL_PATHS.get(key)
    if not rel:
        return None
    base = skill_base_dir or str(_REPO_ROOT)
    p = Path(base) / rel
    return str(p) if p.is_file() else None


def _read_skill(key: str, skill_base_dir: Optional[str] = None) -> str:
    """读取 SKILL.md 文本内容并返回；找不到返回空字符串。"""
    p = _resolve_skill_path(key, skill_base_dir)
    if p:
        try:
            return Path(p).read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return ""


def _build_prompt_with_skill(base_prompt: str, skill_text: str) -> str:
    """将 SKILL 内容内联到 prompt 中，避免文件上传。"""
    if not skill_text:
        return base_prompt
    return f"{base_prompt}\n\n---\n以下是本步骤的执行规范（SKILL）：\n\n{skill_text}"


def run(
    *,
    scope_docs: List[str],
    out_dir: str,
    state_dir: Optional[str] = None,
    cdp_endpoint: str,
    project_url: Optional[str] = None,
    skill_base_dir: Optional[str] = None,
    user_context: Optional[str] = None,
    session_id: str = "docviz_diagram",
    resume: bool = False,
    no_reply: bool = False,
    insert_doc_path: Optional[str] = None,
    generation_timeout_s: int = 600,
    stable_text_window_s: float = 6.0,
) -> Dict[str, Any]:
    """
    执行文档图助手流水线。

    - scope_docs: 参与制图的文档路径列表（已经在项目 Sources 里时可为空）
    - out_dir: 产出目录（各步回复、state 等）
    - state_dir: 状态目录，默认等于 out_dir
    - resume + no_reply: 从上次保存状态继续，按「用户未回复」路径执行 agent2→3→4，通过则 agent5 并插入
    - insert_doc_path: 最终图代码要插入的文档路径（可选）
    - project_url: ChatGPT 项目 URL，未传则用默认「生成文档图助手」；可改为「通用测试」等
    - skill_base_dir: SKILL.md 的根目录，默认为仓库根
    返回: { "status", "questions_for_user", "figure_plan_path", "replies", "mermaid_inserted", "error" }
    """
    target_url = (project_url or "").strip() or DOC_VIZ_PROJECT_URL
    state_dir = state_dir or out_dir
    ensure_dir(out_dir)
    ensure_dir(state_dir)

    state = load_state(state_dir) if resume else {}
    run_id = state.get("run_id") or f"docviz_{os.getpid()}_{len(scope_docs)}"
    if not state:
        state = {"run_id": run_id, "session_id": session_id, "stage": None}

    browser_config = BrowserConfig(
        mode="attach",
        cdp_endpoint=cdp_endpoint,
        base_artifacts_dir=out_dir,
        accept_downloads=True,
        navigation_timeout_ms=600_000,
    )
    exec_cfg = ExecutionConfig(
        generation_timeout_s=generation_timeout_s,
        stable_text_window_s=stable_text_window_s,
        require_reply_visible=True,
        reply_visible_timeout_s=120,
    )
    bm = BrowserManager(browser_config)
    bm.start()
    adapter = ChatGPTAdapter(browser=bm, exec_cfg=exec_cfg)

    result: Dict[str, Any] = {
        "status": "complete",
        "questions_for_user": [],
        "figure_plan_path": None,
        "replies": {},
        "mermaid_inserted": False,
        "error": None,
    }

    try:
        # ----- 非恢复：导航到项目 → 等待跳入对话页 → agent1（SKILL.md 作为 chat 附件） -----
        if not resume:
            state["scope_docs"] = scope_docs

            # agent1：SKILL 内容内联到 prompt，单次调用，不上传文件（避免 Sources vs chat 混淆）
            ctx_block = (f"## 用户需求背景\n{user_context.strip()}\n\n") if user_context else ""
            agent1_base = PROMPT_AGENT1_FIGURE_PLAN.format(user_context=ctx_block)
            agent1_prompt = _build_prompt_with_skill(
                agent1_base, _read_skill("agent1", skill_base_dir)
            )
            print("[docviz] 发送 agent1 prompt（SKILL 已内联）...")
            res = adapter.execute(
                mode="send-prompt",
                session_id=session_id,
                url=target_url,
                instruction=agent1_prompt,
                files=None,
                output_dir=out_dir,
                reset_chat=False,
                force_goto=True,
                ensure_model=False,
            )
            if res.get("status") != "complete":
                result["status"] = "error"
                result["error"] = res.get("error") or "agent1_figure_plan 未完成"
                save_state(state_dir, state)
                return result

            text1 = (res.get("text") or "").strip()
            reply_path = Path(out_dir) / "reply_agent1_figure_plan.md"
            reply_path.write_text(text1, encoding="utf-8")
            state["stage"] = "agent1_done"
            state["reply_agent1"] = str(reply_path)
            state["figure_plan"] = text1
            result["figure_plan_path"] = str(reply_path)
            result["replies"]["agent1"] = text1
            result["questions_for_user"] = _parse_questions_from_reply(text1)
            save_state(state_dir, state)

            # no_reply=True：全自动跑，agent1 完成后直接继续 agent2~5，不停下来等用户确认
            # no_reply=False（默认）：在此返回，由调用方（人工/外部循环）确认后再以 --resume --no-reply 继续
            if not no_reply:
                return result
            # 落穿：继续执行 agent2~5

        # ----- 恢复 或 agent1 落穿：agent2 → agent3 → agent4 [→ 不通过则重试] → agent5 -----
        # resume 模式下，若 no_reply=False 直接返回（等用户确认）
        if resume and not no_reply:
            result["status"] = "complete"
            result["questions_for_user"] = state.get("questions_for_user", [])
            return result

        session_id = state.get("session_id", session_id)
        max_qa_retries = 2
        qa_passed = False
        last_agent4_reply = ""

        for qa_round in range(max_qa_retries + 1):
            # agent2：SKILL 内联
            res2 = adapter.execute(
                mode="send-prompt",
                session_id=session_id,
                instruction=_build_prompt_with_skill(
                    PROMPT_AGENT2_AMBIGUITY, _read_skill("agent2", skill_base_dir)
                ),
                files=None,
                output_dir=out_dir,
                reset_chat=False,
                force_goto=False,
                ensure_model=False,
            )
            if res2.get("status") != "complete":
                result["status"] = "error"
                result["error"] = res2.get("error") or "agent2 未完成"
                return result
            text2 = (res2.get("text") or "").strip()
            (Path(out_dir) / "reply_agent2_ambiguity.md").write_text(text2, encoding="utf-8")
            result["replies"]["agent2"] = text2
            result["questions_for_user"] = _parse_questions_from_reply(text2)

            # agent3（默认 mermaid）：SKILL 内联
            res3 = adapter.execute(
                mode="send-prompt",
                session_id=session_id,
                instruction=_build_prompt_with_skill(
                    PROMPT_AGENT3_MERMAID, _read_skill("agent3_mermaid", skill_base_dir)
                ),
                files=None,
                output_dir=out_dir,
                reset_chat=False,
                force_goto=False,
                ensure_model=False,
            )
            if res3.get("status") != "complete":
                result["status"] = "error"
                result["error"] = res3.get("error") or "agent3 未完成"
                return result
            text3 = (res3.get("text") or "").strip()
            (Path(out_dir) / "reply_agent3_mermaid.md").write_text(text3, encoding="utf-8")
            result["replies"]["agent3"] = text3

            # agent4：SKILL 内联
            res4 = adapter.execute(
                mode="send-prompt",
                session_id=session_id,
                instruction=_build_prompt_with_skill(
                    PROMPT_AGENT4_QA, _read_skill("agent4_mermaid", skill_base_dir)
                ),
                files=None,
                output_dir=out_dir,
                reset_chat=False,
                force_goto=False,
                ensure_model=False,
            )
            if res4.get("status") != "complete":
                result["status"] = "error"
                result["error"] = res4.get("error") or "agent4 未完成"
                return result
            last_agent4_reply = (res4.get("text") or "").strip()
            (Path(out_dir) / "reply_agent4_qa.md").write_text(last_agent4_reply, encoding="utf-8")
            result["replies"]["agent4"] = last_agent4_reply

            if _is_qa_passed(last_agent4_reply):
                qa_passed = True
                break

        if not qa_passed:
            result["status"] = "qa_failed"
            result["error"] = "agent4_mermaid_qa 多次未通过"
            return result

        # agent5：本地执行（agno agent），不走 Web UI
        # 从 agent3（mermaid_draft）和 agent4（qa 通过的最终确认）中提取 Mermaid 代码并插入文档
        # 优先从 agent4 reply 中提取（QA 通过时 agent4 可能输出了修正后的 final 代码）；
        # 若 agent4 没有代码块，则取 agent3 的输出
        mermaid_code = _extract_mermaid_code(last_agent4_reply)
        if not mermaid_code:
            mermaid_code = _extract_mermaid_code(result["replies"].get("agent3", ""))

        mermaid_final_path = Path(out_dir) / "mermaid_final.md"
        if mermaid_code:
            mermaid_final_path.write_text(f"```mermaid\n{mermaid_code}\n```\n", encoding="utf-8")
            print(f"[agent5/local] mermaid_final 已写出: {mermaid_final_path}")

            insert_path = insert_doc_path or (state.get("scope_docs") or [None])[0]
            if insert_path and Path(insert_path).is_file():
                result["mermaid_inserted"] = _insert_mermaid_into_doc(insert_path, mermaid_code)
                print(f"[agent5/local] 已插入到文档: {insert_path}, inserted={result['mermaid_inserted']}")
            result["mermaid_code"] = mermaid_code
        else:
            print("[agent5/local] 警告：未能从 agent3/agent4 回复中提取到 Mermaid 代码块")

        state["stage"] = "agent5_done"
        save_state(state_dir, state)
        return result

    finally:
        bm.close()

    return result


def _load_yaml_config(path: str) -> Dict[str, Any]:
    """加载 YAML 配置文件；缺少 PyYAML 时返回空字典并打印警告。"""
    try:
        import yaml  # type: ignore
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        print("[docviz] 警告：未安装 PyYAML，无法读取 --config，请 `uv add pyyaml`")
        return {}
    except Exception as e:
        print(f"[docviz] 警告：读取 config 失败 ({e})，忽略")
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description="文档图助手：ChatGPT 项目内 agent1~5 流水线")
    ap.add_argument("-c", "--config", default=None, help="YAML 配置文件路径（jobs/docviz_diagram/config.yaml）；命令行参数优先于 YAML")
    ap.add_argument("--cdp", default=None)
    ap.add_argument("--scope-docs", nargs="+", default=None, help="参与制图的文档路径")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--state-dir", default=None, help="状态目录，默认与 out-dir 相同")
    ap.add_argument("--session-id", default=None)
    ap.add_argument("--resume", action="store_true", help="从 state-dir 恢复状态")
    ap.add_argument("--no-reply", action="store_true", help="按用户未回复路径继续（agent2→3→4→5）")
    ap.add_argument("--insert-doc", default=None, help="最终图代码插入的文档路径")
    ap.add_argument("--project-url", default=None, help="ChatGPT 项目 URL，默认用生成文档图助手")
    ap.add_argument("--skill-dir", default=None, help="SKILL.md 根目录")
    ap.add_argument("--user-context", default=None, help="用户需求描述，嵌入到 agent1 prompt 开头")
    ap.add_argument("--timeout-s", type=int, default=None)
    ap.add_argument("--stable-text-window-s", type=float, default=None)
    args = ap.parse_args()

    # 加载 YAML（若提供），命令行显式传入的参数优先
    cfg: Dict[str, Any] = {}
    if args.config:
        cfg = _load_yaml_config(args.config)

    def _get(cli_val: Any, key: str, default: Any) -> Any:
        """命令行 > YAML > 环境变量/默认值"""
        if cli_val is not None:
            return cli_val
        return cfg.get(key, default)

    cdp = normalize_cdp_endpoint(
        _get(args.cdp, "cdp", os.getenv("CDP_ENDPOINT") or os.getenv("CDP") or "http://127.0.0.1:9222")
    )
    out_dir = ensure_dir(os.path.abspath(
        _get(args.out_dir, "out_dir", os.getenv("OUT_DIR") or str(_REPO_ROOT / "artifacts" / "docviz_diagram"))
    ))
    state_dir = os.path.abspath(
        _get(args.state_dir, "state_dir", None) or out_dir
    )

    res = run(
        scope_docs=_get(args.scope_docs, "scope_docs", []) or [],
        out_dir=out_dir,
        state_dir=state_dir,
        cdp_endpoint=cdp,
        project_url=_get(args.project_url, "project_url", None),
        skill_base_dir=_get(args.skill_dir, "skill_dir", None),
        user_context=_get(args.user_context, "user_context", None),
        session_id=_get(args.session_id, "session_id", os.getenv("SESSION_ID") or "docviz_diagram"),
        resume=args.resume or bool(cfg.get("resume", False)),
        no_reply=args.no_reply or bool(cfg.get("no_reply", False)),
        insert_doc_path=_get(args.insert_doc, "insert_doc", None),
        generation_timeout_s=int(_get(args.timeout_s, "timeout_s", int(os.getenv("GENERATION_TIMEOUT_S") or "600"))),
        stable_text_window_s=float(_get(args.stable_text_window_s, "stable_text_window_s", float(os.getenv("STABLE_TEXT_WINDOW_S") or "6"))),
    )

    # 输出机器可读结果到 stdout（便于 Agent 解析）
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if res.get("status") not in ("complete", "qa_failed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
