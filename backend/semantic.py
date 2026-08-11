"""语义检索引擎（可选）— ChromaDB + sentence-transformers。

默认不启用：config.yaml 的 rag.engine 或环境变量 CHATKMS_ENGINE 设为 semantic 时使用。
- 索引持久化：工作区 `.rag/chroma/`（向量表）+ `.rag/file_meta.json`（文件签名，供增量）
- 增量：按 mtime+size 签名跳过未变文件，只嵌入新增/修改；source 已消失则清理对应向量
- 依赖未安装时抛明确错误，不影响默认 BM25 路径
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional

_CHUNK = 512
_OVERLAP = 128
_COLLECTION = "chatkms"


def _model_name() -> str:
    from .config import get_config
    return get_config().get("rag", {}).get("semantic_model", "BAAI/bge-small-zh-v1.5")


def _load():
    try:
        import chromadb
        from sentence_transformers import SentenceTransformer
        from chromadb.config import Settings
    except ImportError:
        raise RuntimeError(
            "语义检索引擎未安装。运行: pip install -r chatKMS\\requirements-semantic.txt "
            "（或 pip install -e .[semantic]）")
    return chromadb, SentenceTransformer, Settings


def _chunks(text: str, size: int = _CHUNK, overlap: int = _OVERLAP) -> list[str]:
    if len(text) <= size:
        return [text]
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + size])
        i += max(1, size - overlap)
    return out


def _client(workspace: str):
    chromadb, _, Settings = _load()
    path = str(Path(workspace) / ".rag" / "chroma")
    return chromadb.PersistentClient(path=path, settings=Settings(anonymized_telemetry=False))


def _meta_path(workspace: str) -> Path:
    return Path(workspace) / ".rag" / "file_meta.json"


def _load_meta(workspace: str) -> dict:
    import json
    p = _meta_path(workspace)
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _save_meta(workspace: str, meta: dict) -> None:
    import json
    (Path(workspace) / ".rag").mkdir(parents=True, exist_ok=True)
    _meta_path(workspace).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def _abs_of(ref: str, workspace: str, sources: list[str]) -> Optional[str]:
    """把 raw://根名/相对路径 或 wiki://相对路径 解析为源文件绝对路径。"""
    if ref.startswith("raw://"):
        rest = ref[len("raw://"):]
        if "/" not in rest:
            return None
        rname, rel = rest.split("/", 1)
        for root in sources:
            if Path(root).name == rname:
                p = Path(root) / rel
                return str(p) if p.is_file() else None
        return None
    if ref.startswith("wiki://"):
        p = Path(workspace) / "wiki" / ref[len("wiki://"):]
        return str(p) if p.is_file() else None
    return None


def _signature(ref: str, workspace: str, sources: list[str]) -> Optional[str]:
    ab = _abs_of(ref, workspace, sources)
    if not ab:
        return None
    try:
        st = os.stat(ab)
        return f"{st.st_mtime_ns},{st.st_size}"
    except OSError:
        return None


def reset(workspace: str) -> None:
    import shutil
    d = Path(workspace) / ".rag" / "chroma"
    if d.exists():
        shutil.rmtree(d)
    mp = _meta_path(workspace)
    if mp.exists():
        mp.unlink()


def index(workspace: str, sources: list[str]) -> dict:
    """增量构建/刷新语义索引。返回统计。"""
    from .sources import iter_docs
    chromadb, SentenceTransformer, _ = _load()
    model = SentenceTransformer(_model_name())
    coll = _client(workspace).get_or_create_collection(_COLLECTION, metadata={"hnsw:space": "cosine"})

    meta = _load_meta(workspace)
    docs = {ref: text for ref, text in iter_docs(workspace, sources)}
    current = set(docs)
    stats = {"added": 0, "changed": 0, "reused": 0, "removed": 0}

    for ref, text in docs.items():
        sig = _signature(ref, workspace, sources)
        if meta.get(ref) == sig:
            stats["reused"] += 1
            continue
        # 重新嵌入该文件（先删旧向量）
        coll.delete(where={"source": ref})
        chunks = _chunks(text)
        emb = model.encode(chunks).tolist()
        coll.upsert(ids=[f"{ref}#{i}" for i in range(len(chunks))],
                    documents=chunks,
                    metadatas=[{"source": ref}] * len(chunks),
                    embeddings=emb)
        stats["changed" if ref in meta else "added"] += 1

    # source 已消失的文件 → 清理向量与元数据
    for ref in set(meta) - current:
        coll.delete(where={"source": ref})
        stats["removed"] += 1

    _save_meta(workspace, {ref: _signature(ref, workspace, sources) for ref in docs})
    stats["total"] = coll.count()
    return stats


def _has_first_index(workspace: str) -> bool:
    try:
        return _client(workspace).get_collection(_COLLECTION).count() > 0
    except Exception:
        return False


def retrieve(workspace: str, sources: list[str], query: str, top_n: int) -> list[dict]:
    """语义检索。返回 [{source, content, score}] 按相似度降序。"""
    from sentence_transformers import SentenceTransformer
    _load()
    if not _has_first_index(workspace):
        index(workspace, sources)  # 首次检索懒构建
    coll = _client(workspace).get_collection(_COLLECTION)
    model = SentenceTransformer(_model_name())
    q = model.encode([query]).tolist()[0]
    try:
        res = coll.query(query_embeddings=[q], n_results=top_n)
    except Exception:
        return []
    ids = (res.get("ids") or [[]])[0]
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    out = []
    for doc, meta, dist in zip(docs, metas, dists):
        out.append({"source": meta.get("source", ""), "content": doc[:800],
                    "score": round(1.0 - float(dist), 4)})
    return out