"""智联招聘简历筛流水线。

功能：
- 连接已登录的 Chrome（CDP attach 模式），打开智联招聘推荐候选人列表页
- 逐张点击候选人卡片 → 提取简历信息 → 调用 AI 筛选 → 符合条件则打招呼
- 支持多页翻页、每日打招呼上限、关键词预过滤
- 结果写入 JSON 报告

用法：
    # 基础用法（自动探测本机 CDP 9222）
    python -m agno1.pipelines.zhaopin_resume_screener \\
        --url "https://rd6.zhaopin.com/app/talent/recommend" \\
        --ai-target "第一学历为985/211、年龄小于30岁的候选人"

    # 指定 CDP 端点
    CDP_ENDPOINT="http://127.0.0.1:9222" python -m agno1.pipelines.zhaopin_resume_screener \\
        --url "https://rd6.zhaopin.com/app/talent/recommend" \\
        --max-greet 20

配置（也可通过环境变量覆盖）：
    CDP_ENDPOINT          Chrome 远程调试地址（默认自动探测 127.0.0.1:9222）
    AI_API_URL            AI 接口地址（默认 http://127.0.0.1:33101/openai/v1/chat/completions）
    AI_API_KEY            AI 接口密钥
    AI_MODEL              模型名称（默认 gpt-5.1）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agno1.browser_automation.manager import BrowserConfig, BrowserManager
from agno1.browser_automation.utils import normalize_cdp_endpoint
from agno1.browser_automation.zhaopin_resume import (
    CandidateInfo,
    ResumeInfo,
    ZhaopinResumeAdapter,
    build_resume_text,
)

# 默认目标：可通过 --ai-target 覆盖
_DEFAULT_AI_TARGET = "第一学历为985/211、年龄小于30岁的候选人"

# 默认关键词过滤（含这些词直接跳过，不调 AI）
_DEFAULT_SKIP_KEYWORDS = ["外包", "兼职", "实习", "远程"]


# ------------------------------------------------------------------
# 结果数据类
# ------------------------------------------------------------------

@dataclass
class ScreeningResult:
    candidate_id: str
    name: str
    card_index: int
    status: str                         # "greeted" | "rejected_ai" | "rejected_keyword" | "skipped" | "failed"
    reason: str = ""
    resume_summary: str = ""
    ai_response: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class ScreeningReport:
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    total_scanned: int = 0
    greeted: int = 0
    rejected: int = 0
    skipped: int = 0
    failed: int = 0
    results: List[ScreeningResult] = field(default_factory=list)


# ------------------------------------------------------------------
# AI 筛选（直接调 OpenAI-compatible 接口）
# ------------------------------------------------------------------

def _call_ai(
    resume_text: str,
    *,
    ai_target: str,
    api_url: str,
    api_key: str,
    model: str,
    timeout: int = 30,
) -> str:
    """调用 AI 接口，返回模型回复文本。失败时返回空字符串。"""
    system_prompt = (
        "你是一位严格的 HR 简历筛选助手。\n"
        "根据用户提供的简历信息，判断候选人是否符合目标要求。\n"
        "只回答「符合」或「不符合」，并给出一句理由。\n"
        f"目标要求：{ai_target}"
    )
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": resume_text},
        ],
        "max_tokens": 200,
        "temperature": 0.1,
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
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[智联简历筛] AI 调用失败: {e}")
        return ""


def _is_ai_pass(ai_response: str) -> bool:
    """判断 AI 回复是否表示"符合"。"""
    lowered = ai_response.lower()
    if "符合" in lowered:
        return True
    if "不符合" in lowered or "不满足" in lowered or "不合适" in lowered:
        return False
    # 无法判断时保守处理：不打招呼
    return False


def _keyword_filter(resume_text: str, keywords: List[str]) -> Optional[str]:
    """关键词预过滤。命中任一关键词返回该词，否则返回 None。"""
    for kw in keywords:
        if kw in resume_text:
            return kw
    return None


# ------------------------------------------------------------------
# 主流水线
# ------------------------------------------------------------------

def run_screener(
    *,
    list_url: str,
    cdp_endpoint: Optional[str] = None,
    ai_target: str = _DEFAULT_AI_TARGET,
    ai_api_url: str = "http://127.0.0.1:33101/openai/v1/chat/completions",
    ai_api_key: str = "tencent-is-watching",
    ai_model: str = "gpt-5.1",
    max_greet: int = 50,
    max_pages: int = 5,
    skip_keywords: Optional[List[str]] = None,
    out_dir: str = "artifacts/zhaopin_resume_screener",
    dry_run: bool = False,
) -> ScreeningReport:
    """执行完整简历筛流水线，返回筛选报告。

    Args:
        list_url:      智联招聘推荐候选人列表页 URL
        cdp_endpoint:  CDP 地址，为 None 时自动探测 127.0.0.1:9222
        ai_target:     AI 筛选目标描述
        ai_api_url:    AI API 地址
        ai_api_key:    AI API 密钥
        ai_model:      AI 模型名称
        max_greet:     每次运行最大打招呼数量（含上限保护）
        max_pages:     最大翻页数
        skip_keywords: 关键词黑名单（命中则跳过，不调 AI）
        out_dir:       结果输出目录
        dry_run:       仅扫描不执行打招呼（调试用）
    """
    if skip_keywords is None:
        skip_keywords = list(_DEFAULT_SKIP_KEYWORDS)

    report = ScreeningReport()
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # 规范化 CDP 地址
    if cdp_endpoint:
        cdp_endpoint = normalize_cdp_endpoint(cdp_endpoint)

    # 初始化浏览器（attach 到已登录的 Chrome）
    browser_cfg = BrowserConfig(
        mode="attach",
        cdp_endpoint=cdp_endpoint or "http://127.0.0.1:9222",
        accept_downloads=False,
        base_artifacts_dir=str(out_path),
    )
    manager = BrowserManager(config=browser_cfg)
    adapter = ZhaopinResumeAdapter(browser=manager)

    try:
        manager.start()
        handle = manager.get_or_create_page(
            platform="zhaopin",
            session_id="resume_screener",
            url=list_url,
            bring_to_front=True,
        )
        page = handle.page

        print(f"[智联简历筛] 流水线启动，目标: {ai_target}")
        print(f"[智联简历筛] dry_run={dry_run}, max_greet={max_greet}, max_pages={max_pages}")

        greet_count = 0
        page_num = 0

        while page_num < max_pages:
            page_num += 1
            print(f"\n[智联简历筛] === 第 {page_num} 页 ===")
            time.sleep(1.5)  # 等待页面稳定

            candidates = adapter.get_candidate_cards(page)
            if not candidates:
                print("[智联简历筛] 本页无候选人，停止")
                break

            report.total_scanned += len(candidates)
            print(f"[智联简历筛] 本页共 {len(candidates)} 位候选人")

            for candidate in candidates:
                if greet_count >= max_greet:
                    print(f"[智联简历筛] 已达到打招呼上限 {max_greet}，停止")
                    break

                print(f"\n[智联简历筛] 处理候选人 [{candidate.card_index}] {candidate.name}")
                result = ScreeningResult(
                    candidate_id=candidate.id,
                    name=candidate.name,
                    card_index=candidate.card_index,
                    status="failed",
                )

                # 1) 打开详情
                opened = adapter.open_candidate_detail(page, candidate)
                if not opened:
                    result.status = "failed"
                    result.reason = "打开详情失败"
                    report.failed += 1
                    report.results.append(result)
                    continue

                # 2) 提取简历
                resume = adapter.extract_resume_info(page)

                # 3) 付费内容检测
                if resume.has_sensitive_content:
                    print(f"[智联简历筛] 跳过（付费限制）: {resume.sensitive_reason}")
                    result.status = "skipped"
                    result.reason = resume.sensitive_reason
                    report.skipped += 1
                    report.results.append(result)
                    adapter.close_detail(page)
                    continue

                resume_text = build_resume_text(resume)
                result.resume_summary = resume_text[:500]  # 截断，只存摘要

                # 4) 关键词预过滤
                hit_kw = _keyword_filter(resume_text, skip_keywords)
                if hit_kw:
                    print(f"[智联简历筛] 关键词过滤跳过（命中: {hit_kw}）")
                    result.status = "rejected_keyword"
                    result.reason = f"命中关键词: {hit_kw}"
                    report.rejected += 1
                    report.results.append(result)
                    adapter.close_detail(page)
                    continue

                # 5) AI 筛选
                ai_resp = _call_ai(
                    resume_text,
                    ai_target=ai_target,
                    api_url=ai_api_url,
                    api_key=ai_api_key,
                    model=ai_model,
                )
                result.ai_response = ai_resp
                print(f"[智联简历筛] AI 回复: {ai_resp[:100]}")

                if not _is_ai_pass(ai_resp):
                    result.status = "rejected_ai"
                    result.reason = ai_resp
                    report.rejected += 1
                    report.results.append(result)
                    adapter.close_detail(page)
                    continue

                # 6) 打招呼
                if dry_run:
                    print(f"[智联简历筛] [dry_run] 跳过打招呼: {candidate.name}")
                    result.status = "greeted"
                    result.reason = "dry_run，未实际打招呼"
                else:
                    greeted = adapter.click_greet_button(page)
                    result.status = "greeted" if greeted else "failed"
                    result.reason = "打招呼成功" if greeted else "打招呼按钮未找到"

                if result.status == "greeted":
                    greet_count += 1
                    report.greeted += 1
                else:
                    report.failed += 1

                report.results.append(result)
                adapter.close_detail(page)
                time.sleep(0.5)

            # 翻页
            if greet_count >= max_greet:
                break
            if page_num < max_pages:
                has_next = adapter.go_to_next_page(page)
                if not has_next:
                    print("[智联简历筛] 已到最后一页，停止")
                    break

    finally:
        report.end_time = time.time()
        manager.close()

        # 写入报告
        report_file = out_path / f"report_{int(report.start_time)}.json"
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(_report_to_dict(report), f, ensure_ascii=False, indent=2)
        print(f"\n[智联简历筛] 报告已保存: {report_file}")
        _print_summary(report)

    return report


def _report_to_dict(report: ScreeningReport) -> Dict[str, Any]:
    d = asdict(report)
    d["results"] = [asdict(r) for r in report.results]
    return d


def _print_summary(report: ScreeningReport) -> None:
    elapsed = report.end_time - report.start_time
    print("\n========== 简历筛结果摘要 ==========")
    print(f"耗时: {elapsed:.1f}s")
    print(f"扫描候选人: {report.total_scanned}")
    print(f"打招呼:     {report.greeted}")
    print(f"AI 拒绝:    {report.rejected}")
    print(f"跳过:       {report.skipped}")
    print(f"失败:       {report.failed}")
    print("=====================================")


# ------------------------------------------------------------------
# CLI 入口
# ------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="智联招聘简历筛流水线")
    parser.add_argument("--url", required=True, help="推荐候选人列表页 URL")
    parser.add_argument("--cdp", default=None, help="CDP 端点地址（默认自动探测 127.0.0.1:9222）")
    parser.add_argument("--ai-target", default=_DEFAULT_AI_TARGET, help="AI 筛选目标描述")
    parser.add_argument("--ai-api-url", default=os.getenv("AI_API_URL", "http://127.0.0.1:33101/openai/v1/chat/completions"))
    parser.add_argument("--ai-api-key", default=os.getenv("AI_API_KEY", "tencent-is-watching"))
    parser.add_argument("--ai-model", default=os.getenv("AI_MODEL", "gpt-5.1"))
    parser.add_argument("--max-greet", type=int, default=50, help="最大打招呼数量")
    parser.add_argument("--max-pages", type=int, default=5, help="最大翻页数")
    parser.add_argument("--out-dir", default="artifacts/zhaopin_resume_screener", help="结果输出目录")
    parser.add_argument("--dry-run", action="store_true", help="仅扫描不打招呼（调试用）")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_screener(
        list_url=args.url,
        cdp_endpoint=args.cdp or os.getenv("CDP_ENDPOINT"),
        ai_target=args.ai_target,
        ai_api_url=args.ai_api_url,
        ai_api_key=args.ai_api_key,
        ai_model=args.ai_model,
        max_greet=args.max_greet,
        max_pages=args.max_pages,
        out_dir=args.out_dir,
        dry_run=args.dry_run,
    )
