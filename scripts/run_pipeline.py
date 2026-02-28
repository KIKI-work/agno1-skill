#!/usr/bin/env python3
"""运行可复用流水线入口。

用法：
  python scripts/run_pipeline.py chatgpt_continue_loop --url "https://chatgpt.com/c/..."
  python scripts/run_pipeline.py chatgpt_project_ao_dna --mode ao-dna
  python scripts/run_pipeline.py commodity_analysis --commodity cu --commodity al
  python scripts/run_pipeline.py company_analysis_office --ticker AAPL --ticker MSFT
  python scripts/run_pipeline.py iterative_coding_loop --url "https://chatgpt.com/c/..." --prompt "..."
  python scripts/run_pipeline.py --list
"""

from __future__ import annotations

import argparse
import importlib
import sys
from typing import Dict


PIPELINES: Dict[str, str] = {
    "chatgpt_continue_loop": "agno1.pipelines.chatgpt_continue_loop",
    "chatgpt_project_ao_dna": "agno1.pipelines.chatgpt_project_ao_dna",
    "commodity_analysis": "agno1.pipelines.commodity_analysis",
    "company_analysis_office": "agno1.pipelines.company_analysis_office",
    "iterative_coding_loop": "agno1.pipelines.iterative_coding_loop",
    "binder_project_remediation": "agno1.pipelines.binder_project_remediation",
    "binder_project_codex_loop": "agno1.pipelines.binder_project_codex_loop",
    "binder_project_clarification": "agno1.pipelines.binder_project_clarification",
    "binder_project_clarification_loop": "agno1.pipelines.binder_project_clarification_loop",
    "download_pack_upload_route": "agno1.pipelines.download_pack_upload_route",
    "docviz_diagram_chatgpt": "agno1.pipelines.docviz_diagram_chatgpt",
}


def _normalize_name(name: str) -> str:
    return (name or "").strip().replace("-", "_")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="运行可复用流水线入口")
    ap.add_argument("pipeline", nargs="?", help="流水线名称（例如 chatgpt_continue_loop）")
    ap.add_argument("--list", action="store_true", help="列出可用流水线")
    args, rest = ap.parse_known_args(argv)

    if args.list:
        print("可用流水线：")
        for name in sorted(PIPELINES.keys()):
            print(f"- {name}")
        return 0

    if not args.pipeline:
        ap.print_help()
        return 2

    name = _normalize_name(args.pipeline)
    module_path = PIPELINES.get(name)
    if not module_path:
        print(f"未知流水线：{args.pipeline}")
        print("使用 --list 查看可用流水线。")
        return 2

    mod = importlib.import_module(module_path)
    if not hasattr(mod, "main"):
        print(f"流水线模块缺少 main(): {module_path}")
        return 2

    sys.argv = [module_path] + rest
    return int(mod.main())


if __name__ == "__main__":
    raise SystemExit(main())
