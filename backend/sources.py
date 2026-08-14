"""源文件层只读访问（v0.4）。

源文件层是工作区外的只读路径集合（记录在 .kms 的 `sources` 中），
chatKMS 绝不写入/拷贝。本模块提供：
- 递归列出所有源路径文件（文件名 → 相对路径）
- 按文件名查找
- 按相对路径/引用读取内容（text 直读，pdf 尽力抽取文字）
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


_TEXT_EXTS = {".md", ".txt"}


def iter_docs(workspace: str, sources: list[str]):
    """产出 (source_ref, text)：源文件层(.md/.txt/.pdf 可抽取) + 工作区 wiki(.md)。

    BM25 与语义引擎共用同一套遍历。source_ref 形如 raw://根名/相对路径 或 wiki://相对路径。
    """
    for sf in iter_source_files(sources):
        if not sf.name.lower().endswith((".md", ".txt", ".pdf")):
            continue
        text, ok = extract_text(sf.abspath)
        if not ok or len(text.strip()) < 10:
            continue
        yield f"raw://{sf.root_name}/{sf.rel}", text
    wiki = Path(workspace) / "wiki"
    if wiki.is_dir():
        for root, dirs, files in os.walk(wiki):
            dirs[:] = [d for d in dirs if d not in (".git",)]
            for f in files:
                if not f.endswith((".md", ".txt")):
                    continue
                fp = Path(root) / f
                try:
                    text = fp.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                if len(text.strip()) < 10:
                    continue
                rel = os.path.relpath(fp, wiki).replace("\\", "/")
                yield f"wiki://{rel}", text


@dataclass
class SourceFile:
    root: str            # 绝对路径（所属源根）
    root_name: str       # 源根目录名（用于 raw://root_name/rel 引用）
    rel: str             # 相对源根的路径，正斜杠
    name: str            # 文件名
    abspath: str         # 绝对路径
    size: int


def _skip_dir(name: str) -> bool:
    return name.startswith(".") or name in ("__pycache__", "node_modules", "bin", "obj")


def iter_source_files(sources: list[str]) -> Iterator[SourceFile]:
    """递归列出所有源路径下的全部文件，跳过隐藏目录。"""
    for root in sources or []:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        root_name = root_path.name or root_path.drive
        for dp, dirs, files in os.walk(root_path):
            dirs[:] = [d for d in dirs if not _skip_dir(d)]
            for f in files:
                fp = Path(dp) / f
                rel = os.path.relpath(fp, root_path).replace("\\", "/")
                yield SourceFile(root=root, root_name=root_name, rel=rel,
                                 name=f, abspath=str(fp), size=fp.stat().st_size)


def source_find(sources: list[str], query: str, limit: int = 100) -> list[SourceFile]:
    """按文件名查找：支持精确名或子串模糊匹配（不区分大小写）。"""
    q = query.strip().lower()
    if not q:
        return []
    out = []
    for sf in iter_source_files(sources):
        if q in sf.name.lower() or q in sf.rel.lower():
            out.append(sf)
            if len(out) >= limit:
                break
    return out


def extract_text(abspath: str, max_chars: int = 0) -> tuple[str, bool]:
    """读取源文件内容。返回 (text, readable)。pdf 尽力抽取，text 直读。"""
    p = Path(abspath)
    ext = p.suffix.lower()
    text = ""
    if ext in _TEXT_EXTS:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            text = ""
    elif ext == ".pdf":
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(str(p))
            for page in doc:
                text += page.get_text()
            doc.close()
        except Exception:
            text = ""
    text = text.strip()
    readable = bool(text)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars]
    return text, readable