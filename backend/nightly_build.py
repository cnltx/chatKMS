#!/usr/bin/env python3
"""chatKMS 夜间构建 — 重负荷放夜间（凌晨），白天只读 wiki 快查。

功能：
  1. 构建/增量刷新 RAG 索引（语义引擎增量，BM25 进程内重建）
  2. 分析 log/query_log.jsonl：高频问题 + 0 命中缺口，输出建议
  3. 清理超过 RETENTION_DAYS 天的查询日志

用法：
  python backend/nightly_build.py                  → 对当前活跃知识库
  python backend/nightly_build.py --kb mykb     → 指定知识库
  python backend/nightly_build.py --schedule       → 打印 Windows 计划任务安装命令
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.config import reload_config
from backend.kb_manager import KBManager
from backend import rag
from backend import wiki

RETENTION_DAYS = 30


def _resolve_kb(kb: str) -> str:
    mgr = KBManager()
    if kb:
        ws = mgr.resolve(kb)
    else:
        active = mgr.get_active_kb()
        ws = active.workspace if active else None
    if not ws:
        raise SystemExit("找不到活跃知识库。用 --kb <名称/路径> 指定。")
    return os.path.abspath(ws)


def _analyze_queries(workspace: str) -> list[dict]:
    """读 query_log.jsonl，统计高频与 0 命中。"""
    qlog = Path(workspace) / "log" / "query_log.jsonl"
    rows = []
    if qlog.is_file():
        for line in qlog.open(encoding="utf-8", errors="ignore"):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    if not rows:
        return []
    by_q: dict[str, dict] = {}
    for r in rows:
        q = (r.get("query") or "").strip()
        if not q:
            continue
        e = by_q.setdefault(q, {"query": q, "count": 0, "zero_hits": 0})
        e["count"] += 1
        if r.get("hits", 0) == 0:
            e["zero_hits"] += 1
    top = sorted(by_q.values(), key=lambda x: (-x["count"], x["query"]))[:12]
    return top


def _clean_query_log(workspace: str) -> int:
    qlog = Path(workspace) / "log" / "query_log.jsonl"
    if not qlog.is_file():
        return 0
    cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).isoformat()
    keep, removed = [], 0
    for line in qlog.open(encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            ts = json.loads(line).get("ts", "")
        except Exception:
            ts = ""
        if ts and ts < cutoff:
            removed += 1
            continue
        keep.append(line)
    qlog.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    return removed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default="", help="知识库名称或工作区路径")
    ap.add_argument("--schedule", action="store_true", help="只打印 Windows 计划任务安装命令")
    args = ap.parse_args()

    if args.schedule:
        ws = _resolve_kb(args.kb)
        cmd = (f'schtasks /create /tn "chatKMS-nightly" '
               f'/tr "python {os.path.join(_ROOT, "backend", "nightly_build.py")} --kb {Path(ws).name}" '
               f'/sc daily /st 03:00')
        print("在 PowerShell(管理员) 执行以下命令注册为每日 03:00 计划任务：\n")
        print("  " + cmd)
        print("\n验证：schtasks /query /tn chatKMS-nightly")
        return

    reload_config()
    ws = _resolve_kb(args.kb)
    kb = KBManager().get_kb(ws)
    if not kb:
        raise SystemExit(f"工作区不可用: {ws}")
    sources = kb.sources if kb.sources else []
    engine = rag.engine_name()
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 知识库: {kb.name}  engine={engine}")

    # 1. 构建索引
    if sources:
        info = rag.rebuild_index(ws, sources)
        print(f"索引: {info['doc_count']} 篇  ({info.get('detail', '')})")
    else:
        print("索引: 跳过（sources 为空/.kms 缺失）")

    # 2. 查询日志分析
    top = _analyze_queries(ws)
    report_path = Path(ws) / "log" / f"nightly_report_{datetime.now().strftime('%Y-%m-%d')}.md"
    lines = [f"# 夜间构建报告 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"]
    if top:
        lines.append("## 高频问题 Top12")
        for t in top:
            flag = "（缺口:多次0命中，建议补wiki）" if t["zero_hits"] >= 2 else ""
            lines.append(f"- `{t['query']}` x{t['count']}  0命中{t['zero_hits']}{flag}")
        lines.append("\n> 高频/缺口知识：由 AI 终端读取本条报告后，综合佐证写入 `wiki/可信/` 或 `wiki/待确认/`。")
    else:
        lines.append("暂无查询日志（白天由 rag_query 自动记录）。")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告: {report_path}")

    # 3. 清理过期查询日志
    removed = _clean_query_log(ws)
    if removed:
        print(f"清理过期查询日志: {removed} 条")


if __name__ == "__main__":
    main()