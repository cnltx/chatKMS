"""Wiki 优先层（v0.4）— 白天快速读 wiki，不够才走 RAG。

wiki 是工作区生成内容，直接读 markdown，毫秒级：
- search:  文件名+标题+正文 关键词匹配，返回带摘要
- status:  概览（各目录页数、最近日志、索引状态）
- log_query: 把 RAG 查询写入 log/query_log.jsonl，供夜间构建分析高频/缺口
"""
from __future__ import annotations
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

_HIDDEN = {".git"}


def _wiki(workspace: str) -> Path:
    return Path(workspace) / "wiki"


def _iter_md(workspace: str):
    wiki = _wiki(workspace)
    if not wiki.is_dir():
        return
    for root, dirs, files in os.walk(wiki):
        dirs[:] = [d for d in dirs if d not in _HIDDEN]
        for f in files:
            if f.endswith(".md"):
                yield os.path.relpath(os.path.join(root, f), wiki).replace("\\", "/"), Path(root) / f


def search(workspace: str, query: str, top_k: int = 10, layer: Optional[str] = None) -> list[dict]:
    """关键词搜索：文件名(3分)+标题(2分)+正文(1分)。返回带摘要。"""
    q = query.lower()
    q_tokens = [t for t in q.replace("，", " ").replace("、", " ").split() if t]
    out = []
    for rel, fp in _iter_md(workspace):
        if layer and not (rel.startswith(layer.rstrip("/") + "/") if layer else True):
            continue
        try:
            raw = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        title = raw.strip().splitlines()[0].lstrip("#").strip() if raw.strip() else rel
        score = 0
        hay = (rel + " " + title + " " + raw[:4000]).lower()
        for t in q_tokens:
            if t in rel.lower():
                score += 3
            elif t in title.lower():
                score += 2
            if t in hay:
                score += 1
        if score == 0:
            continue
        out.append({"path": rel, "title": title, "score": score,
                    "snippet": _excerpt(raw, q, 240)})
    out.sort(key=lambda x: -x["score"])
    return out[:top_k]


def _excerpt(text: str, q: str, length: int) -> str:
    low = text.lower()
    i = low.find(q.lower())
    if i < 0 and q:
        ts = [t for t in q.replace("，", " ").split() if t]
        i = next((low.find(t) for t in ts if low.find(t) >= 0), -1)
    if i < 0:
        return text[:length].replace("\n", " ")
    start = max(0, i - length // 3)
    return ("…" if start else "") + text[start:start + length].replace("\n", " ") + "…"


def status(workspace: str) -> dict:
    """工作区 wiki 概览。"""
    counts: dict[str, int] = {"可信": 0, "待确认": 0, "其他": 0}
    pages = 0
    for rel, _ in _iter_md(workspace):
        pages += 1
        top = rel.split("/", 1)[0]
        if top in counts:
            counts[top] += 1
        else:
            counts["其他"] += 1
    log_dir = Path(workspace) / "log"
    today_log = log_dir / f"log_{datetime.now().strftime('%Y-%m-%d')}.md"
    logs = 0
    if log_dir.is_dir():
        logs = len([p for p in log_dir.glob("log_*.md")])
    qlog = log_dir / "query_log.jsonl"
    qlog_lines = 0
    if qlog.is_file():
        try:
            qlog_lines = sum(1 for _ in qlog.open(encoding="utf-8"))
        except Exception:
            qlog_lines = 0
    return {"wiki_pages": pages, "by_dir": counts, "log_files": logs,
            "today_log_exists": today_log.is_file(), "query_log_lines": qlog_lines}


def log_query(workspace: str, query: str, mode: str, hit_count: int, engine: str) -> None:
    """追加一条 RAG 查询到 log/query_log.jsonl（供夜间分析）。失败静默。"""
    import json
    try:
        log_dir = Path(workspace) / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "query_log.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                                "query": query, "mode": mode, "engine": engine,
                                "hits": hit_count}, ensure_ascii=False) + "\n")
    except Exception:
        pass