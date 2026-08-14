"""知识库工作区生命周期管理（v0.4）。

知识库 = 工作区（chatKMS 生成产物）+ 源文件层（只读引用，由 .kms 记录 sources）。
每个工作区目录结构：
{workspace}/
├── {库名}.kms          ← 自描述：name / description / sources(可多个) / created_at
├── .rag/               ← 向量索引（引用源文件相对路径，不拷贝）
├── wiki/               ← 生成内容，git 管理
│   ├── 结构说明.md
│   ├── 可信/
│   └── 待确认/
├── log/                ← 按天 log_YYYY-MM-DD.md
└── AGENTS.md           ← 指引，只读

config.yaml 只登记 workspace；源路径由各工作区的 .kms 提供。
删除工作区不影响源文件层。
"""
from __future__ import annotations
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
import yaml

from .config import get_config, save_config


def _norm(p: str) -> str:
    return os.path.normpath(p).replace("\\", "/")


def _name_of(path: str) -> str:
    return Path(path).name


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def append_log(workspace: Path, note: str) -> None:
    """向工作区 log/ 追加今天的日志（log_YYYY-MM-DD.md）。"""
    (workspace / "log").mkdir(parents=True, exist_ok=True)
    f = workspace / "log" / f"log_{datetime.now().strftime('%Y-%m-%d')}.md"
    with f.open("a", encoding="utf-8") as fh:
        fh.write(f"\n## {_now()}\n- {note}\n")


def _kms_name(name: str) -> str:
    return f"{name}.kms"


def _safe_title(title: str, content: str) -> str:
    """取用户输入的标题/首行作文件名，剔除非法字符。"""
    base = (title or content).strip().splitlines()[0][:40] if (title or content).strip() else "输入"
    base = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", base).strip()
    return base or "输入"


def ingest_input(workspace: str, content: str, mode: str = "auto",
                 title: str = "", pending: bool = True, reference: str = "") -> dict:
    """v0.5：保存用户使用知识库时输入的一句话/一份资料。

    mode:
      wiki/auto → 写入 wiki（pending=True→待确认/ 否则 可信/），返回 wiki 相对路径
      source     → 源层只读，暂存到工作区 inbox/ 并提示用户落盘源路径
      note       → 仅记 log（供 AGENT 判为无关时记录）
    归属判定（有关/无关、wiki 或源资料）由 AI 终端按 AGENTS.md 规范做出，本函数只负责落盘。
    """
    ws = Path(workspace)
    content = (content or "").strip()
    if not content:
        raise ValueError("内容为空")
    name = _safe_title(title, content)

    if mode == "note":
        append_log(ws, f"用户输入(判为无关，仅记录): {content[:80]}")
        return {"saved": None, "target": "log", "note": "仅记录，未落入知识库"}

    if mode == "source":
        inbox = ws / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        fname = f"inbox_{stamp}_{name}.md"
        meta = f"\n> 录入 @ {_now()}"
        if reference:
            meta += f" · 参考/佐证: {reference}"
        (inbox / fname).write_text(f"# {name}\n\n{content}\n\n---\n{meta}", encoding="utf-8")
        append_log(ws, f"用户录入暂存 inbox（源层只读，需用户落盘源路径）: {fname}")
        return {"saved": f"inbox/{fname}", "target": "inbox",
                "note": "源层只读：请把该文件放入源路径，再由 AI 引用"}

    # wiki / auto：默认先入待确认
    sub = "待确认" if pending else "可信"
    wiki = ws / "wiki" / sub
    wiki.mkdir(parents=True, exist_ok=True)
    fname, c = f"{name}.md", 1
    while (wiki / fname).exists():
        c += 1
        fname = f"{name}_{c}.md"
    meta = f"> 录入 @ {_now()}"
    if reference:
        meta += f" · 参考/佐证: {reference}"
    (wiki / fname).write_text(f"# {name}\n\n{content}\n\n---\n{meta}", encoding="utf-8")
    flag = "（待确认）" if pending else "（可信）"
    append_log(ws, f"用户录入→wiki/{sub}/{fname}{flag}")
    return {"saved": f"wiki/{sub}/{fname}", "target": "wiki",
            "note": "默认先入待确认，佐证充分后由 AI 移至可信"}


_SCHEMA_DIRS = ("wiki/可信", "wiki/待确认", "log", ".rag")


def template_dir() -> Path:
    """基础工作空间模板目录：config.yaml 的 template_dir（可相对/绝对），默认 chatKMS/sample/default。"""
    cfg = get_config()
    t = cfg.get("template_dir") or os.environ.get("CHATKMS_TEMPLATE")
    if t:
        p = Path(t)
        return p if p.is_absolute() else Path(__file__).resolve().parents[1] / t
    return Path(__file__).resolve().parents[1] / "sample" / "default"


def _substitute(root: Path, subs: dict) -> None:
    """把复制出的模板文本文件中的 {{key}} 占位符替换为实际值。"""
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in (".md", ".txt", ".kms"):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        new = text
        for k, v in subs.items():
            new = new.replace("{{" + k + "}}", str(v))
        if new != text:
            p.write_text(new, encoding="utf-8")


def read_kms(workspace_dir: str) -> Optional[dict]:
    """读取工作区内的 {库名}.kms 自描述文件。."""
    d = Path(workspace_dir)
    if not d.is_dir():
        return None
    for f in d.glob("*.kms"):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if isinstance(data, dict) and data.get("name"):
            return data
    return None


def create_workspace_skeleton(workspace: str, name: str,
                              sources: list[str], description: str) -> None:
    """基于基础工作空间模板（sample/default）生成工作区。

    流程：必需结构兜底 → 复制模板 → 占位符替换({{name}} 等) → 写 {库名}.kms → 日志 → git init。
    """
    base = Path(workspace)
    tpl = template_dir()
    if not tpl.is_dir():
        raise RuntimeError(f"基础工作空间模板缺失: {tpl}（可在 config.yaml 设 template_dir）")
    base.mkdir(parents=True, exist_ok=True)
    for sub in _SCHEMA_DIRS:  # 必需结构兜底，模板增删不影响根目录结构
        (base / sub).mkdir(parents=True, exist_ok=True)
    shutil.copytree(tpl, base, dirs_exist_ok=True)  # 复制模板（含用户自定义改动）

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    _substitute(base, {"name": name, "description": description, "created_at": now})

    kms = {"name": name, "description": description,
           "sources": [os.path.abspath(s) for s in sources], "created_at": now}
    (base / _kms_name(name)).write_text(yaml.dump(kms, allow_unicode=True, sort_keys=False),
                                        encoding="utf-8")
    append_log(base, f"创建知识库工作区 {name}（来源: {len(sources)} 个源路径，模板: {tpl.name}）")
    git_init(base / "wiki")


def git_init(wiki: Path) -> bool:
    wiki.mkdir(parents=True, exist_ok=True)
    if (wiki / ".git").is_dir():
        return True
    try:
        r = subprocess.run(["git", "init"], cwd=wiki, capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


@dataclass
class KBInfo:
    name: str
    workspace: str
    description: str = ""
    sources: list[str] = field(default_factory=list)
    created_at: str = ""
    doc_count: int = 0
    has_index: bool = False
    git_initialized: bool = False


def _count_docs(kb: KBInfo) -> int:
    from .sources import iter_source_files
    n = 0
    for sf in iter_source_files(kb.sources):
        if sf.name.lower().endswith((".md", ".txt", ".pdf", ".doc", ".docx")):
            n += 1
    wiki = Path(kb.workspace) / "wiki"
    if wiki.is_dir():
        for root, dirs, files in os.walk(wiki):
            dirs[:] = [d for d in dirs if d not in (".git",)]
            n += sum(1 for f in files if f.endswith(".md"))
    return n


def _enrich(info: KBInfo) -> KBInfo:
    ws = Path(info.workspace)
    if ws.is_dir():
        kms = read_kms(info.workspace)
        if kms:
            info.description = info.description or kms.get("description", "")
            info.sources = info.sources or [os.path.abspath(s) for s in kms.get("sources", [])]
            info.created_at = info.created_at or kms.get("created_at", "")
        info.doc_count = _count_docs(info) if info.sources else 0
        info.has_index = any(Path(info.workspace, ".rag").iterdir()) \
            if Path(info.workspace, ".rag").is_dir() else False
        info.git_initialized = Path(info.workspace, "wiki", ".git").is_dir()
    return info


class KBManager:
    """知识库工作区：创建、列表（发现）、切换、删除。"""

    def create_kb(self, name: str, sources: Optional[list[str]] = None,
                  workspace: Optional[str] = None, description: str = "") -> KBInfo:
        cfg = get_config()
        if not workspace:
            base = os.path.join(cfg.get("workspace_root", ""), name)
            workspace = base
            c = 1
            while os.path.exists(workspace):
                workspace = f"{base}_{c}"
                c += 1
        workspace = os.path.abspath(workspace)
        if (Path(workspace) / _kms_name(name)).exists() and (Path(workspace) / "AGENTS.md").exists():
            raise ValueError(f"已存在同名知识库工作区: {workspace}")
        # 源路径只写到 .kms，config.yaml 不登记
        create_workspace_skeleton(workspace, name, sources or [], description)
        cfg.setdefault("knowledge_bases", []).append({
            "name": name, "workspace": workspace,
            "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        })
        cfg["active_kb"] = workspace
        save_config(cfg)
        return _enrich(KBInfo(name=name, workspace=workspace, description=description,
                              sources=[os.path.abspath(s) for s in (sources or [])]))

    def list_kbs(self, dir: Optional[str] = None) -> list[KBInfo]:
        """注册工作区 + workspace_root 发现 + 可选目录发现；按路径去重。"""
        cfg = get_config()
        seen: dict[str, KBInfo] = {}
        for kb in cfg.get("knowledge_bases", []):
            ws = kb.get("workspace", "")
            if ws:
                seen[_norm(ws)] = KBInfo(name=kb.get("name", _name_of(ws)),
                                         workspace=ws,
                                         created_at=kb.get("created_at", ""))
        for scan_dir in (cfg.get("workspace_root"), dir):
            if not scan_dir or not os.path.isdir(scan_dir):
                continue
            for entry in sorted(os.listdir(scan_dir)):
                candidate = os.path.join(scan_dir, entry)
                if not os.path.isdir(candidate) or _norm(candidate) in seen:
                    continue
                kms = read_kms(candidate)
                if kms:
                    seen[_norm(candidate)] = KBInfo(
                        name=kms.get("name", entry), workspace=candidate,
                        description=kms.get("description", ""),
                        sources=[os.path.abspath(s) for s in kms.get("sources", [])])
        return [_enrich(v) for v in seen.values()]

    def resolve(self, target: str) -> Optional[str]:
        """把名称或路径解析为工作区绝对路径。名称优先匹配已注册项或 .kms。"""
        target = target.strip()
        for kb in get_config().get("knowledge_bases", []):
            if kb.get("name") == target:
                return os.path.abspath(kb["workspace"])
        p = os.path.abspath(os.path.expanduser(target))
        if os.path.isdir(p):
            if read_kms(p) or self._looks_like_workspace(p):
                return p
            # 允许直接给工作区路径（即使还没 .kms）
            return p
        # 按目录名在工作区根查找
        root = get_config().get("workspace_root")
        if root and os.path.isdir(root):
            cand = os.path.join(root, target)
            if os.path.isdir(cand):
                return os.path.abspath(cand)
        return None

    def switch_kb(self, target: str) -> str:
        cfg = get_config()
        ws = self.resolve(target)
        if not ws:
            raise ValueError(f"找不到知识库工作区: {target}")
        ws = os.path.abspath(ws)
        registered = {_norm(k.get("workspace", "")) for k in cfg.get("knowledge_bases", [])}
        if _norm(ws) not in registered:
            kms = read_kms(ws)
            cfg.setdefault("knowledge_bases", []).append({
                "name": (kms or {}).get("name", _name_of(ws)), "workspace": ws,
                "created_at": (kms or {}).get("created_at",
                                              datetime.now().strftime("%Y-%m-%dT%H:%M:%S")),
            })
        cfg["active_kb"] = ws
        save_config(cfg)
        return ws

    def get_kb(self, workspace: str) -> Optional[KBInfo]:
        if os.path.isdir(workspace):
            return _enrich(KBInfo(name=_name_of(workspace), workspace=os.path.abspath(workspace)))
        return None

    def get_active_kb(self) -> Optional[KBInfo]:
        cfg = get_config()
        active = cfg.get("active_kb")
        if active and os.path.isdir(active):
            return _enrich(KBInfo(name=_name_of(active), workspace=active))
        return None

    def delete_kb(self, target: str, delete_files: bool = False) -> None:
        """从 config.yaml 移除；delete_files=True 时删除工作区（绝不碰源文件层）。"""
        cfg = get_config()
        ws = self.resolve(target)
        if not ws:
            raise ValueError(f"找不到知识库工作区: {target}")
        ws = os.path.abspath(ws)
        cfg["knowledge_bases"] = [k for k in cfg.get("knowledge_bases", [])
                                  if _norm(k.get("workspace", "")) != _norm(ws)]
        if cfg.get("active_kb") and _norm(cfg.get("active_kb")) == _norm(ws):
            cfg.pop("active_kb", None)
        if delete_files and os.path.isdir(ws):
            shutil.rmtree(ws)
        save_config(cfg)

    def _looks_like_workspace(self, path: str) -> bool:
        p = Path(path)
        return any(p.glob("*.kms")) or (p / "wiki").is_dir()