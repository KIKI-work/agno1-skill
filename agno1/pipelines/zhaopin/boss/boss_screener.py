"""BOSS 直聘简历筛流水线。

流程：
1. 连接已登录 Chrome（CDP attach 模式）
2. 导航到 BOSS 直聘推荐牛人列表页（/web/chat/recommend）
3. 获取候选人卡片列表
4. 逐一处理：关键词预筛 → 提取卡片信息 → AI 筛选 → 立即沟通 / 跳过
5. 滚动加载更多，直至无新候选人
6. 输出 JSON 报告

BOSS 直聘特点（与智联版的差异）：
- 简历信息直接从卡片 DOM 提取，无需打开简历详情弹窗
- 打招呼通过 mouseover 触发显示 → 点击 .btn-greet
- 无需关闭弹窗（但 finally 仍调用 close_detail 清理可能出现的消息浮层）

用法：
    python -m agno1.pipelines.zhaopin.boss.boss_screener \\
        --url "https://www.zhipin.com/web/chat/recommend" \\
        --ai-target "985/211本科、3年以上Python经验" \\
        --exclude-keywords 教培 电气 嵌入式
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
        # 尝试用 psutil（可选依赖），若没有则跳过
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
        # psutil 不可用时，降级用 taskkill 按名称批量清理（Windows only）
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
from agno1.browser_automation.zhaopin.boss.boss_screener_adapter import (
    BossResumeAdapter,
    CardSummary,
    CandidateInfo,
    ScreenResult,
)
from agno1.pipelines.zhaopin.notify import (
    notify_ai_failure,
    notify_all_complete,
    notify_batch_complete,
)


# ---------------------------------------------------------------------------
# AI 筛选（与智联招聘流水线完全相同，直接复用逻辑）
# ---------------------------------------------------------------------------

def _build_ai_prompt(info: CandidateInfo, target: str, intensity: str = "balanced") -> str:
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
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content: str = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        finish_reason: str = data.get("choices", [{}])[0].get("finish_reason", "")
        if not content.strip():
            return {"is_target": False, "reason": "AI 返回内容为空", "_parse_failed": True, "raw_content": "", "finish_reason": finish_reason}
        result = None
        # 剥离 AI 可能返回的 markdown 代码块包裹（```json ... ``` 或 ``` ... ```）
        import re
        stripped = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped.strip())
        try:
            result = json.loads(stripped)
        except Exception:
            # 再尝试从原始内容中提取第一个完整 JSON 对象
            m = re.search(r"\{[\s\S]*\}", stripped)
            if m:
                try:
                    result = json.loads(m.group(0))
                except Exception:
                    pass
        if not result or not isinstance(result.get("is_target"), bool):
            _trunc = "[输出被截断] " if finish_reason == "length" else ""
            return {"is_target": False, "reason": f"{_trunc}格式异常（无法解析为 JSON）", "_parse_failed": True, "raw_content": content, "finish_reason": finish_reason}
        result["_parse_failed"] = False
        result["raw_content"] = content
        result["finish_reason"] = finish_reason
        return result
    except Exception as e:
        return {"is_target": False, "reason": f"请求失败: {e}", "_parse_failed": True, "raw_content": "", "finish_reason": ""}


# ---------------------------------------------------------------------------
# 主流水线函数（TODO: BossScreenerAdapter 实现后功能可用）
# ---------------------------------------------------------------------------

def run_screener(
    *,
    url: str = "https://www.zhipin.com/web/chat/recommend",
    ai_target: str,
    cdp_endpoint: str = "http://127.0.0.1:9222",
    ai_api_url: str = "http://127.0.0.1:33101/openai/v1/chat/completions",
    ai_api_key: str = "tencent-is-watching",
    ai_model: str = "gemini-2.5-flash",
    ai_intensity: str = "balanced",
    ai_max_tokens: int = 7000,
    excluded_keywords: Optional[List[str]] = None,
    max_greet: int = 9999,
    page_stay_time: str = "3-5",
    dry_run: bool = False,
    out_dir: str = "artifacts/zhaopin/boss",
    session_id: str = "boss_screener",
) -> Dict[str, Any]:
    """
    执行 BOSS 直聘简历筛流水线。

    Args:
        url:              BOSS 直聘推荐牛人列表页 URL（默认 /web/chat/recommend）
        ai_target:        AI 筛选目标描述
        cdp_endpoint:     Chrome CDP 地址
        ai_api_url:       AI API 地址（OpenAI 兼容协议）
        ai_api_key:       AI API Key
        ai_model:         模型名称
        ai_intensity:     筛选强度：strict | balanced | loose
        ai_max_tokens:    最大 token 数
        excluded_keywords: 关键词排除列表
        max_greet:        最大打招呼数量（默认不限）
        page_stay_time:   每次处理间隔秒数范围（格式 "min-max"）
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

    log_dir = str(_REPO_ROOT / "logs" / "zhaopin" / "boss")
    ensure_dir(log_dir)
    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = str(Path(log_dir) / f"boss_screener_{run_ts}.log")
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
    log = logging.getLogger("boss_screener")

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
    report_path = str(Path(out_dir) / f"boss_screener_{run_ts}.json")

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
    adapter = BossResumeAdapter(browser=bm)
    last_captured_name: Optional[str] = None

    try:
        page = adapter.get_page(session_id=session_id, url=url)
        log.info(f"[boss] 已连接页面，URL: {page.url}")
        time.sleep(2.0)  # 等待 iframe 内容完全加载

        # ── 诊断：输出当前页面 URL 及 DOM class 快照 ──────────────────
        # 注意：诊断在 get_candidate_cards 之前执行，get_candidate_cards
        # 内部会调用 _wait_for_cards 等待卡片渲染完成
        current_url = page.url
        log.info(f"[诊断] 当前页面 URL: {current_url}")
        log.info(f"[诊断] 页面 frames 数量: {len(page.frames)}")
        for i, f in enumerate(page.frames):
            try:
                log.info(f"[诊断]   frame[{i}] url={f.url}")
            except Exception:
                pass

        cards: List[CardSummary] = adapter.get_candidate_cards(page)

        # 卡片等待完成后再采集 class 快照（在包含卡片的 frame 里）
        content_frame = adapter._get_content_frame(page)
        dom_diag: dict = content_frame.evaluate("""
        () => {
            const allClasses = new Set();
            document.querySelectorAll('body *').forEach(el => {
                if (el.className && typeof el.className === 'string') {
                    el.className.split(' ').forEach(c => { if (c) allClasses.add(c); });
                }
            });
            const cardRelated = Array.from(allClasses).filter(c =>
                /card|geek|candidate|job|recommend|boss/i.test(c)
            ).slice(0, 80);
            return { total: allClasses.size, card_related: cardRelated };
        }
        """)
        log.info(f"[诊断] 内容 frame URL: {content_frame.url}")
        log.info(f"[诊断] 页面 class 总数: {dom_diag.get('total', '?')}")
        log.info(f"[诊断] 候选卡片相关 class: {dom_diag.get('card_related', [])}")
        if not cards:
            diag_msg = (
                f"未识别到候选人卡片。\n"
                f"  当前 URL: {current_url}\n"
                f"  候选 class: {dom_diag.get('card_related', [])}\n"
                f"  页面 class 总数: {dom_diag.get('total', '?')}\n"
                f"  请确认：① 已在 BOSS 直聘「推荐牛人」页面（/web/chat/recommend）"
                f"；② 页面已完全加载（有候选人卡片可见）"
            )
            log.info(f"[错误] {diag_msg}")
            return {
                "status": "error",
                "stats": stats,
                "results": results,
                "report_path": None,
                "log_path": None,
                "error": diag_msg,
            }

        processed_ids: set[str] = set()
        batch = 0

        while cards:
            batch += 1
            log.info(f"── 第 {batch} 批，共 {len(cards)} 张新卡片 ──")

            for card in cards:
                processed_ids.add(card.card_id)
                stats["total"] += 1
                log.info(f"[总第 {stats['total']} 张] 处理: {card.name}")

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

                    # ── 步骤 2：打开弹窗并提取简历（弹窗保持打开）──────
                    info = adapter.open_detail_and_extract(
                        page, card,
                        last_captured_name=last_captured_name,
                        keep_open=True,   # 弹窗保持打开，AI 分析完再关闭
                    )
                    detail_opened = True

                    if excluded_kw:
                        full_text_lower = (info.full_text or "").lower()
                        hit_kw = next((kw for kw in excluded_kw if kw in full_text_lower), None)
                        if hit_kw:
                            log.info(f"  → [关键词排除] 命中关键词: 「{hit_kw}」（简历全文）")
                            # 关键词命中：先关弹窗再 continue
                            adapter.close_detail(page)
                            stats["rejected_keyword"] += 1
                            results.append({
                                "card_id": card.card_id, "name": info.name,
                                "action": "rejected_keyword",
                                "reason": f"简历全文命中关键词: 「{hit_kw}」",
                                "hit_keyword": hit_kw,
                            })
                            _flush()
                            continue

                    # ── 步骤 3：AI 筛选（简历弹窗仍然打开中）───────────
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

                    # ── 步骤 4：AI 出结果后关闭弹窗 ──────────────────
                    adapter.close_detail(page)
                    time.sleep(0.5)

                    # ── 步骤 5：根据 AI 结果执行动作 ─────────────────
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
                        notify_ai_failure("BOSS直聘", info.name, ai_result["reason"])

                    elif ai_result["is_target"]:
                        log.info(f"  → AI 通过: {ai_result['reason']}")
                        if not dry_run:
                            # BOSS 版需传入 card 以定位打招呼按钮
                            adapter.click_greet_button(page, card)
                            log.info(f"  → 已打招呼（立即沟通）")
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
                    if detail_opened:
                        try:
                            # 兜底：若步骤中途异常（如关键词 continue、AI 失败）导致弹窗未关，
                            # 此处确保关闭，同时清理打招呼后可能残留的浮层
                            adapter.close_detail(page)
                        except Exception as e:
                            log.info(f"  → 清理残留浮层失败: {e}")

                sleep_s = random.randint(stay_min, stay_max)
                log.info(f"  → 等待 {sleep_s} 秒...")
                time.sleep(sleep_s)

            log.info(f"── 第 {batch} 批处理完，滚动加载更多候选人...")
            notify_batch_complete("BOSS直聘", stats, batch)
            cards = adapter.scroll_and_get_new_cards(page, processed_ids)
            if not cards:
                log.info("── 已滚动到底，没有新候选人，筛选结束")

    finally:
        bm.close()
        # Ctrl+C 或任何异常退出时，确保最终状态写入 JSON
        _flush("interrupted")

    # 正常完成：以 complete 状态覆盖写一次
    _flush("complete")
    notify_all_complete("BOSS直聘", stats)

    # 运行结束统计摘要
    rejected = stats["rejected_ai"]
    rej_kw = stats["rejected_keyword"]
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
        f"  本次筛选完成（BOSS 直聘）\n"
        f"{'─'*60}\n"
        f"  筛选总数：{stats['total']}\n"
        f"  通过（已立即沟通）：{stats['passed']}\n"
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
    ap = argparse.ArgumentParser(description="BOSS 直聘简历筛流水线")
    ap.add_argument("--url", default="https://www.zhipin.com/web/chat/recommend",
                    help="推荐牛人列表页 URL，默认 https://www.zhipin.com/web/chat/recommend")
    ap.add_argument("--ai-target", required=True, help="AI 筛选目标描述")
    ap.add_argument("--cdp", default=None, help="Chrome CDP 地址，默认 http://127.0.0.1:9222")
    ap.add_argument("--ai-url", default=None, help="AI API 地址（OpenAI 兼容）")
    ap.add_argument("--ai-key", default=None, help="AI API Key")
    ap.add_argument("--ai-model", default=None, help="模型名称")
    ap.add_argument("--ai-intensity", default="balanced", choices=["strict", "balanced", "loose"])
    ap.add_argument("--ai-max-tokens", type=int, default=7000)
    ap.add_argument("--exclude-keywords", nargs="+", default=None, help="关键词排除列表")
    ap.add_argument("--max-greet", type=int, default=9999)
    ap.add_argument("--page-stay-time", default="3-5")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--session-id", default="boss_screener")
    args = ap.parse_args()

    # 清理上次 Ctrl+C 可能留下的僵死 playwright driver 进程
    _kill_stale_playwright()

    out_dir = ensure_dir(os.path.abspath(
        args.out_dir or str(_REPO_ROOT / "artifacts" / "zhaopin" / "boss")
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
