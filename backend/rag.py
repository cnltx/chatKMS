"""chatKMS RAG 检索引擎（v0.4，双模式）。

引擎由 config.yaml 的 rag.engine 或环境变量 CHATKMS_ENGINE 决定：
- bm25（默认）  纯关键词，零依赖，开机即用
- semantic      语义检索（ChromaDB + sentence-transformers），需安装 requirements-semantic.txt

语料 = 源文件层（读自 .kms 的 sources，可多个源根，只读）+ 工作区 wiki。
- light  简单检索：仅 wiki 层，最快（wiki 优先策略的第一步）
- heavy  深度检索：wiki + 源文件 综合
- auto   自动：wiki 优先，不足再补源文件
"""
from __future__ import annotations
import hashlib
import os
from typing import Optional

import jieba

from .sources import iter_docs

_current_ws: Optional[str] = None
_current_sources: list[str] = []
_bm25 = None
_corpus: list[dict] = []  # {text, source, layer}
_cache: dict[str, list] = {}
_CACHE_MAX = 256


def engine_name() -> str:
    env = os.environ.get("CHATKMS_ENGINE", "").strip().lower()
    if env in ("bm25", "semantic"):
        return env
    from .config import get_config
    return get_config().get("rag", {}).get("engine", "bm25")


def set_engine(engine: str) -> None:
    """持久化切换引擎（写入 config.yaml）。"""
    from .config import get_config, save_config
    if engine not in ("bm25", "semantic"):
        raise ValueError("engine 必须为 bm25 或 semantic")
    cfg = get_config()
    cfg.setdefault("rag", {})["engine"] = engine
    save_config(cfg)
    global _cache
    _cache.clear()


def set_active_kb(workspace: Optional[str], sources: Optional[list[str]] = None) -> None:
    global _current_ws, _current_sources, _bm25, _corpus, _cache
    ws = os.path.normpath(workspace) if workspace else None
    srcs = [os.path.normpath(s) for s in (sources or [])]
    if (ws, srcs) != (_current_ws, _current_sources):
        _bm25 = None
        _corpus = []
        _cache.clear()
        _current_ws = ws
        _current_sources = srcs


def _classify(source: str) -> dict:
    if source.startswith("wiki://"):
        parts = source[len("wiki://"):].split("/", 1)
        d = parts[0] if parts else ""
        if d == "可信":
            return {"layer": "wiki", "type_label": "可信知识", "reliability": "stable"}
        if d == "待确认":
            return {"layer": "wiki", "type_label": "待确认知识", "reliability": "uncertain"}
        return {"layer": "wiki", "type_label": "知识页", "reliability": "stable"}
    return {"layer": "raw", "type_label": "源文件", "reliability": "raw"}


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2]
    return text


# ── BM25 引擎 ──────────────────────────────────────────

def _collect() -> None:
    from rank_bm25 import BM25Okapi
    global _bm25, _corpus
    texts: list[list[str]] = []
    corpus: list[dict] = []
    for ref, text in iter_docs(_current_ws, _current_sources):
        text = _strip_frontmatter(text)
        if len(text.strip()) < 10:
            continue
        texts.append(list(jieba.cut(text[:12000])))
        corpus.append({"text": text[:4000], "source": ref})
    if not texts:
        _bm25 = None
        _corpus = []
        return
    _bm25 = BM25Okapi(texts)
    _corpus = corpus


def _bm25_retrieve(query: str, top_k: int, mode: str) -> list[dict]:
    global _cache
    if _bm25 is None or _current_ws is None:
        _collect()
    if _bm25 is None:
        return []

    cache_key = hashlib.md5(f"bm25|{query}|{top_k}|{mode}".encode()).hexdigest()
    if cache_key in _cache:
        return _cache[cache_key]

    tokens = [t for t in jieba.cut(query) if t.strip()]
    if not tokens:
        return []
    scores = _bm25.get_scores(tokens)
    ranked = [(i, s) for i, s in enumerate(scores) if s > 0]
    ranked.sort(key=lambda x: x[1], reverse=True)

    internal_k = {"light": top_k, "heavy": max(top_k * 2, 20), "auto": 20}.get(mode, 20)
    hits: list[dict] = []
    seen: set[str] = set()
    for idx, score in ranked:
        if len(hits) >= internal_k:
            break
        meta = _corpus[idx]
        if meta["source"] in seen:
            continue
        seen.add(meta["source"])
        hits.append({"source": meta["source"], "content": meta["text"][:800],
                     "score": round(score, 4), **_classify(meta["source"])})

    hits = _ordered(hits, mode, top_k)
    if len(_cache) < _CACHE_MAX:
        _cache[cache_key] = hits
    return hits


# ── 语义引擎 ──────────────────────────────────────────

def _semantic_retrieve(query: str, top_k: int, mode: str) -> list[dict]:
    from . import semantic
    hits = semantic.retrieve(_current_ws, _current_sources, query, top_k * 4)
    hits = [{"source": h["source"], "content": h["content"], "score": h["score"],
             **_classify(h["source"])} for h in hits]
    return _ordered(hits, mode, top_k)


def _ordered(hits: list[dict], mode: str, top_k: int) -> list[dict]:
    if mode == "light":
        return [h for h in hits if h["layer"] == "wiki"][:top_k]
    if mode == "heavy":
        return hits[:top_k]
    wiki = [h for h in hits if h["layer"] == "wiki"][:top_k]
    raw = [h for h in hits if h["layer"] == "raw"]
    return wiki + raw[: top_k - len(wiki)]


# ── 统一入口 ──────────────────────────────────────────

def retrieve(query: str, top_k: int = 5, mode: str = "auto") -> list[dict]:
    if engine_name() == "semantic":
        return _semantic_retrieve(query, top_k, mode)
    return _bm25_retrieve(query, top_k, mode)


def rebuild_index(workspace: Optional[str], sources: Optional[list[str]]) -> dict:
    """重建当前知识库索引（按引擎）。语义引擎为增量构建，BM25 为进程内重建。"""
    if engine_name() == "semantic":
        from . import semantic
        stats = semantic.index(workspace, [os.path.normpath(s) for s in (sources or [])])
        return {"ok": True, "doc_count": stats["total"],
                "detail": f"增量: 新增{stats['added']} 变更{stats['changed']} 复用{stats['reused']} 移除{stats['removed']}"}
    old = (_current_ws, _current_sources)
    set_active_kb(workspace, sources)
    _collect()
    count = len(_corpus)
    ok = _bm25 is not None
    set_active_kb(*old)
    return {"doc_count": count, "ok": ok, "detail": "BM25 进程内索引"}


def reset_index(workspace: str) -> None:
    """清空当前知识库索引（语义引擎用）。"""
    if engine_name() == "semantic":
        from . import semantic
        semantic.reset(workspace)
    else:
        global _bm25, _corpus, _cache
        _bm25 = None
        _corpus = []
        _cache.clear()