"""智联招聘简历筛流水线。

流程：
1. 连接已登录 Chrome（CDP attach 模式）
2. 导航到智联招聘推荐人才列表页
3. 获取当前页候选人卡片列表
4. 逐一处理每个候选人：
   a. 关键词预筛（可选，在 AI 前执行，速度快）
   b. 打开简历详情弹窗，提取简历信息
   c. 调用 AI（OpenAI 兼容协议）判断是否目标候选人
   d. AI 通过 → 点击打招呼；拒绝 → 跳过
   e. 关闭弹窗，随机等待（风控）
5. 输出 JSON 报告（每个候选人的处理结果）

用法：
    # 直接运行
    python -m agno1.pipelines.zhaopin.zhilian.zhilian_screener \\
        --url "https://rd6.zhaopin.com/app/recommend?tab=recommend&jobNumber=<你的jobNumber>" \\
        --ai-target "985/211本科、3年以上Python经验" \\
        --max-greet 20

    # dry-run（只提取信息，不打招呼）
    python -m agno1.pipelines.zhaopin.zhilian.zhilian_screener \\
        --url "https://rd6.zhaopin.com/app/recommend?tab=recommend&jobNumber=<你的jobNumber>" \\
        --ai-target "适合职位的候选人" \\
        --dry-run

参考来源：
    F:/KIKI/代码库/chrome插件/HRchat/workspace/zhaopin-im-automation/zhaopin-resume-screener-skill.js
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _kill_stale_playwright() -> None:
    """杀掉上次 Ctrl+C 留下的僵死 playwright driver 进程，防止下次启动卡住。

    playwright Python 包在 Windows 上会启动一个名为 'playwright.exe'（或 node 内嵌驱动）
    的后台 gRPC 进程。Ctrl+C 时若 bm.close() 未正常调用，该进程会变成孤儿，
    导致下次 sync_playwright().start() 无限等待。
    """
    try:
        import psutil  # type: ignore
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                if "playwright" in name or ("playwright" in cmdline and "driver" in cmdline):
                    proc.kill()
            except Exception:
                pass
    except ImportError:
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "playwright.exe"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agno1.browser_automation.manager import BrowserConfig, BrowserManager
from agno1.browser_automation.utils import ensure_dir, normalize_cdp_endpoint
from agno1.browser_automation.zhaopin.zhilian.zhilian_resume import (
    CardSummary,
    CandidateInfo,
    ScreenResult,
    ZhilianResumeAdapter,
)
from agno1.pipelines.zhaopin.notify import (
    notify_ai_failure,
    notify_all_complete,
    notify_batch_complete,
)


# ---------------------------------------------------------------------------
# AI 筛选（OpenAI 兼容协议）
# ---------------------------------------------------------------------------

def _build_ai_prompt(info: CandidateInfo, target: str, intensity: str = "balanced") -> str:
    """构建发给大模型的 Prompt。"""
    resume_text = "\n".join(filter(bool, [
        f"姓名：{info.name}",
        f"年龄：{info.age}" if info.age else "",
        f"教育背景：{info.education}" if info.education else "",
        f"工作经历：{info.work_experience}" if info.work_experience else "",
        f"期望薪资：{info.expected_salary}" if info.expected_salary else "",
        f"技能标签：{info.skills}" if info.skills else "",
        f"\n简历全文（摘要）：\n{info.full_text}" if info.full_text else "",
    ]))

    intensity_map = {
        "strict":   f"请根据以下简历信息判断是否明显适合[{target}]职位。只有当简历明显匹配时才推荐。",
        "balanced": f"请根据以下简历信息判断是否适合[{target}]职位。",
        "loose":    f"请根据以下简历信息判断是否可能适合[{target}]职位，可以适当放宽标准。",
    }

    prefix = intensity_map.get(intensity, intensity_map["balanced"])
    return (
        f"{prefix}\n"
        f"输出 JSON 格式：{{\"is_target\": true/false, \"reason\": \"原因说明\"}}\n\n"
        f"简历信息：\n{resume_text}"
    )


def _call_ai(
    info: CandidateInfo,
    *,
    api_url: str,
    api_key: str,
    model: str,
    target: str,
    intensity: str = "balanced",
    max_tokens: int = 7000,
) -> Dict[str, Any]:
    """
    调用大模型 API 判断候选人是否符合目标。

    Returns:
        {"is_target": bool, "reason": str, "_parse_failed": bool}
    """
    try:
        import urllib.request

        prompt = _build_ai_prompt(info, target=target, intensity=intensity)
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": max_tokens,
            "stream": False,
        }).encode("utf-8")

        req = urllib.request.Request(
            api_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        content: str = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        finish_reason: str = data.get("choices", [{}])[0].get("finish_reason", "")
        if not content.strip():
            return {"is_target": False, "reason": "AI 返回内容为空", "_parse_failed": True, "raw_content": "", "finish_reason": finish_reason}

        # 解析 JSON（先剥离 AI 可能返回的 markdown 代码块包裹）
        import re
        result = None
        stripped = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped.strip())
        try:
            result = json.loads(stripped)
        except Exception:
            # 再尝试从内容中提取第一个完整 JSON 对象
            m = re.search(r"\{[\s\S]*\}", stripped)
            if m:
                try:
                    result = json.loads(m.group(0))
                except Exception:
                    pass

        if not result or not isinstance(result.get("is_target"), bool):
            _trunc = "[输出被截断] " if finish_reason == "length" else ""
            return {
                "is_target": False,
                "reason": f"{_trunc}格式异常（无法解析为 JSON）",
                "_parse_failed": True,
                "raw_content": content,
                "finish_reason": finish_reason,
            }

        result["_parse_failed"] = False
        result["raw_content"] = content
        result["finish_reason"] = finish_reason
        return result

    except Exception as e:
        return {"is_target": False, "reason": f"请求失败: {e}", "_parse_failed": True, "raw_content": "", "finish_reason": ""}


# ---------------------------------------------------------------------------
# 主流水线函数
# ---------------------------------------------------------------------------

def run_screener(
    *,
    url: str,
    ai_target: str,
    cdp_endpoint: str = "http://127.0.0.1:9222",
    ai_api_url: str = "http://127.0.0.1:33101/openai/v1/chat/completions",
    ai_api_key: str = "tencent-is-watching",
    ai_model: str = "gemini-2.5-flash",
    ai_intensity: str = "balanced",
    ai_max_tokens: int = 7000,
    excluded_keywords: Optional[List[str]] = None,
    max_greet: int = 9999,
    page_stay_time: str = "3-5",    # 每次处理间隔秒数范围 "min-max"
    dry_run: bool = False,
    out_dir: str = "artifacts/zhaopin/zhilian",
    session_id: str = "zhilian_screener",
) -> Dict[str, Any]:
    """
    执行智联招聘简历筛流水线。

    Args:
        url:              智联招聘推荐人才列表页 URL
        ai_target:        AI 筛选目标描述，例如"985/211本科、3年以上Java经验"
        cdp_endpoint:     Chrome CDP 地址（需以 --remote-debugging-port=9222 启动 Chrome）
        ai_api_url:       AI API 地址（OpenAI 兼容协议）
        ai_api_key:       AI API Key
        ai_model:         模型名称
        ai_intensity:     筛选强度：strict | balanced | loose
        ai_max_tokens:    最大 token 数
        excluded_keywords: 关键词排除列表（命中则跳过，不调用 AI）
        max_greet:        最大打招呼数量（达到后停止）
        page_stay_time:   每次处理间隔秒数范围，格式 "min-max"
        dry_run:          仅提取信息，不点打招呼按钮
        out_dir:          输出目录（JSON 报告）
        session_id:       浏览器 session 标识

    Returns:
        {
          "status": "complete" | "error",
          "stats": {"total", "passed", "rejected_ai", "rejected_keyword", "skipped", "failed"},
          "results": [...],
          "report_path": str,
          "log_path": str,
          "error": str | None,
        }
    """
    ensure_dir(out_dir)
    excluded_kw = [kw.lower() for kw in (excluded_keywords or [])]

    # log 文件初始化
    log_dir = str(_REPO_ROOT / "logs" / "zhaopin" / "zhilian")
    ensure_dir(log_dir)
    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = str(Path(log_dir) / f"zhilian_screener_{run_ts}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )
    log = logging.getLogger("screener")

    # 解析间隔范围
    try:
        stay_min, stay_max = [int(x) for x in page_stay_time.split("-")]
    except Exception:
        stay_min, stay_max = 3, 5

    stats: Dict[str, int] = {
        "total": 0, "passed": 0,
        "rejected_ai": 0, "rejected_keyword": 0, "skipped": 0, "failed": 0,
    }
    results: List[Dict[str, Any]] = []

    # JSON 报告路径提前确定，运行中实时写入，Ctrl+C 也能保留进度
    report_path = str(Path(out_dir) / f"zhilian_screener_{run_ts}.json")

    def _flush(status: str = "running") -> None:
        """将当前 results/stats 实时写出到 JSON 文件。"""
        try:
            with open(report_path, "w", encoding="utf-8") as _f:
                json.dump(
                    {"status": status, "stats": stats, "results": results, "error": None},
                    _f, ensure_ascii=False, indent=2,
                )
        except Exception as _e:
            log.info(f"[警告] JSON 实时写入失败: {_e}")

    browser_config = BrowserConfig(
        mode="attach",
        cdp_endpoint=normalize_cdp_endpoint(cdp_endpoint),
        base_artifacts_dir=out_dir,
        accept_downloads=False,
        navigation_timeout_ms=60_000,
    )
    bm = BrowserManager(browser_config)
    bm.start()
    adapter = ZhilianResumeAdapter(browser=bm)
    last_captured_name: Optional[str] = None

    try:
        page = adapter.get_page(session_id=session_id, url=url)
        time.sleep(2.0)

        cards: List[CardSummary] = adapter.get_candidate_cards(page)
        if not cards:
            return {
                "status": "error",
                "stats": stats,
                "results": results,
                "report_path": None,
                "log_path": None,
                "error": "未识别到候选人卡片，请确认已在「推荐人才」列表页",
            }

        processed_ids: set[str] = set()   # 已处理的 card_id，用于去重
        batch = 0

        while cards:
            batch += 1
            log.info(f"── 第 {batch} 批，共 {len(cards)} 张新卡片 ──")

            for card in cards:
                processed_ids.add(card.card_id)
                stats["total"] += 1
                log.info(f"[总第 {stats['total']} 张] 处理: {card.name}")

                # ── 风控：随机跳过（概率 10%，模拟人工浏览行为）────────
                if random.random() < 0.10:
                    log.info(f"  → [风控] 随机跳过")
                    stats["skipped"] += 1
                    results.append({
                        "card_id": card.card_id, "name": card.name,
                        "action": "skipped", "reason": "风控随机跳过",
                    })
                    _flush()
                    continue

                detail_opened = False
                try:
                    # ── 步骤 1：关键词预筛 ──────────────────────────
                    if excluded_kw:
                        card_text = f"{card.name} {card.age} {card.salary} {card.status}".lower()
                        hit_kw = next((kw for kw in excluded_kw if kw in card_text), None)
                        if hit_kw:
                            log.info(f"  → [关键词排除] 命中关键词: 「{hit_kw}」（卡片摘要）")
                            stats["rejected_keyword"] += 1
                            results.append({
                                "card_id": card.card_id, "name": card.name,
                                "action": "rejected_keyword",
                                "reason": f"卡片摘要命中关键词: 「{hit_kw}」",
                                "hit_keyword": hit_kw,
                            })
                            _flush()
                            continue

                    # ── 步骤 2：打开弹窗并提取简历 ──────────────────
                    info = adapter.open_detail_and_extract(page, card, last_captured_name=last_captured_name)
                    detail_opened = True

                    # 全文关键词预筛（弹窗内容更丰富）
                    if excluded_kw:
                        full_text_lower = (info.full_text or "").lower()
                        hit_kw = next((kw for kw in excluded_kw if kw in full_text_lower), None)
                        if hit_kw:
                            log.info(f"  → [关键词排除] 命中关键词: 「{hit_kw}」（简历全文）")
                            stats["rejected_keyword"] += 1
                            results.append({
                                "card_id": card.card_id, "name": info.name,
                                "action": "rejected_keyword",
                                "reason": f"简历全文命中关键词: 「{hit_kw}」",
                                "hit_keyword": hit_kw,
                            })
                            _flush()
                            continue

                    # ── 步骤 3：AI 筛选 ──────────────────────────────
                    _ai_prompt = _build_ai_prompt(info, target=ai_target, intensity=ai_intensity)
                    log.info(f"  → [AI Prompt]\n{'─'*60}\n{_ai_prompt}\n{'─'*60}")
                    ai_result = _call_ai(
                        info,
                        api_url=ai_api_url,
                        api_key=ai_api_key,
                        model=ai_model,
                        target=ai_target,
                        intensity=ai_intensity,
                        max_tokens=ai_max_tokens,
                    )
                    _raw = ai_result.get("raw_content", "")
                    _finish = ai_result.get("finish_reason", "")
                    _truncated = "[警告：输出被截断 finish_reason=length] " if _finish == "length" else ""
                    log.info(f"  → [AI 原始回答] {_truncated}\n{'─'*60}\n{_raw}\n{'─'*60}")

                    if info.name and info.name != "未知候选人" and not info.is_fallback:
                        last_captured_name = info.name

                    # ── 步骤 4：根据 AI 结果执行动作 ─────────────────
                    if ai_result["_parse_failed"]:
                        _raw_fail = ai_result.get("raw_content", "")
                        _fin_fail = ai_result.get("finish_reason", "")
                        _trunc_warn = " [输出被截断 finish_reason=length]" if _fin_fail == "length" else ""
                        log.info(
                            f"  → AI 调用失败: {ai_result['reason']}{_trunc_warn}\n"
                            f"  → [AI 原始回答（失败）]\n{'─'*60}\n{_raw_fail}\n{'─'*60}"
                        )
                        stats["failed"] += 1
                        results.append({
                            "card_id": card.card_id, "name": info.name,
                            "action": "failed", "reason": ai_result["reason"],
                            "ai_result": ai_result,
                        })
                        _flush()
                        notify_ai_failure("智联招聘", info.name, ai_result["reason"])

                    elif ai_result["is_target"]:
                        log.info(f"  → AI 通过: {ai_result['reason']}")
                        if not dry_run:
                            adapter.click_greet_button(page)
                            log.info(f"  → 已打招呼")
                        else:
                            log.info(f"  → [dry-run] 跳过打招呼")
                        stats["passed"] += 1
                        results.append({
                            "card_id": card.card_id, "name": info.name,
                            "action": "greeted" if not dry_run else "dry_run_passed",
                            "reason": ai_result["reason"],
                            "ai_result": ai_result,
                        })
                        _flush()

                    else:
                        log.info(f"  → AI 未通过: {ai_result['reason']}")
                        stats["rejected_ai"] += 1
                        results.append({
                            "card_id": card.card_id, "name": info.name,
                            "action": "rejected_ai", "reason": ai_result["reason"],
                            "ai_result": ai_result,
                        })
                        _flush()

                except Exception as e:
                    log.info(f"  → 处理失败: {e}")
                    stats["failed"] += 1
                    results.append({
                        "card_id": card.card_id, "name": card.name,
                        "action": "failed", "reason": str(e),
                    })
                    _flush()

                finally:
                    # 确保弹窗总是被关闭
                    if detail_opened:
                        try:
                            adapter.close_detail(page)
                        except Exception as e:
                            log.info(f"  → 关闭弹窗失败: {e}")

                # ── 风控：随机休眠 ────────────────────────────────────
                sleep_s = random.randint(stay_min, stay_max)
                log.info(f"  → 等待 {sleep_s} 秒...")
                time.sleep(sleep_s)

            # ── 当前批次处理完，滚动加载下一批 ──────────────────────
            log.info(f"── 第 {batch} 批处理完，滚动加载更多候选人...")
            notify_batch_complete("智联招聘", stats, batch)
            cards = adapter.scroll_and_get_new_cards(page, processed_ids)
            if not cards:
                log.info("── 已滚动到底，没有新候选人，筛选结束")

    finally:
        bm.close()
        # Ctrl+C 或任何异常退出时，确保最终状态写入 JSON
        _flush("interrupted")

    # 正常完成：以 complete 状态覆盖写一次
    _flush("complete")
    notify_all_complete("智联招聘", stats)

    # 运行结束统计摘要
    rejected = stats["rejected_ai"]
    rej_kw = stats["rejected_keyword"]

    # 汇总被哪些关键词排除过（去重，保留顺序）
    kw_hits: List[str] = []
    seen_kw: set[str] = set()
    for r in results:
        if r.get("action") == "rejected_keyword":
            kw = r.get("hit_keyword", "")
            if kw and kw not in seen_kw:
                kw_hits.append(kw)
                seen_kw.add(kw)
    kw_summary = "、".join(f"「{k}」" for k in kw_hits) if kw_hits else "无"

    summary = (
        f"\n{'═'*60}\n"
        f"  本次筛选完成\n"
        f"{'─'*60}\n"
        f"  筛选总数：{stats['total']}\n"
        f"  通过（已打招呼）：{stats['passed']}\n"
        f"  未通过（AI拒绝）：{rejected}\n"
        f"  未通过（关键词排除）：{rej_kw}  命中关键词：{kw_summary}\n"
        f"  跳过（风控随机）：{stats['skipped']}\n"
        f"  失败（AI调用失败）：{stats['failed']}\n"
        f"{'─'*60}\n"
        f"  验证：{stats['total']} = {stats['passed']}+{rejected}+{rej_kw}+{stats['skipped']}+{stats['failed']} "
        f"{'✓' if stats['total'] == stats['passed'] + rejected + rej_kw + stats['skipped'] + stats['failed'] else '✗ 数据异常'}\n"
        f"{'─'*60}\n"
        f"  JSON报告：{report_path}\n"
        f"  运行日志：{log_path}\n"
        f"{'═'*60}"
    )
    log.info(summary)
    return {
        "status": "complete",
        "stats": stats,
        "results": results,
        "report_path": report_path,
        "log_path": log_path,
        "error": None,
    }


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="智联招聘简历筛流水线")
    ap.add_argument("--url", required=True, help="推荐人才列表页 URL")
    ap.add_argument("--ai-target", required=True, help="AI 筛选目标描述")
    ap.add_argument("--cdp", default=None, help="Chrome CDP 地址，默认 http://127.0.0.1:9222")
    ap.add_argument("--ai-url", default=None, help="AI API 地址（OpenAI 兼容）")
    ap.add_argument("--ai-key", default=None, help="AI API Key")
    ap.add_argument("--ai-model", default=None, help="模型名称")
    ap.add_argument("--ai-intensity", default="balanced", choices=["strict", "balanced", "loose"])
    ap.add_argument("--ai-max-tokens", type=int, default=7000)
    ap.add_argument("--exclude-keywords", nargs="+", default=None, help="关键词排除列表")
    ap.add_argument("--max-greet", type=int, default=9999, help="最大打招呼数量上限（默认不限制，处理完当前页所有候选人为止）")
    ap.add_argument("--page-stay-time", default="3-5", help="每次处理间隔秒数范围，格式 min-max")
    ap.add_argument("--dry-run", action="store_true", help="仅提取信息，不执行打招呼")
    ap.add_argument("--out-dir", default=None, help="输出目录")
    ap.add_argument("--session-id", default="zhilian_screener")
    args = ap.parse_args()

    # 清理上次 Ctrl+C 可能留下的僵死 playwright driver 进程
    _kill_stale_playwright()

    out_dir = ensure_dir(os.path.abspath(
        args.out_dir or str(_REPO_ROOT / "artifacts" / "zhaopin" / "zhilian")
    ))

    result = run_screener(
        url=args.url,
        ai_target=args.ai_target,
        cdp_endpoint=normalize_cdp_endpoint(
            args.cdp or os.getenv("CDP_ENDPOINT") or os.getenv("CDP") or "http://127.0.0.1:9222"
        ),
        ai_api_url=args.ai_url or os.getenv("AI_API_URL") or "http://127.0.0.1:33101/openai/v1/chat/completions",
        ai_api_key=args.ai_key or os.getenv("AI_API_KEY") or "tencent-is-watching",
        ai_model=args.ai_model or os.getenv("AI_MODEL") or "gemini-2.5-flash",
        ai_intensity=args.ai_intensity,
        ai_max_tokens=args.ai_max_tokens,
        excluded_keywords=args.exclude_keywords,
        max_greet=args.max_greet,
        page_stay_time=args.page_stay_time,
        dry_run=args.dry_run,
        out_dir=out_dir,
        session_id=args.session_id,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
