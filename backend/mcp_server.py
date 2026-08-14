#!/usr/bin/env python3
"""chatKMS v0.4 — MCP stdio 服务入口。

供各 AI 终端（Claude Code、Codex、Hermes…）接入。
- config.yaml 登记知识库**工作区**（workspace）；源路径由各工作区的 {库名}.kms 提供。
- 源文件层只读访问（source_list / source_find / source_read），永不写入、永不拷贝。
- wiki / log / .rag 全部是工作区生成内容。

运行：python backend/mcp_server.py
"""
from __future__ import annotations
import asyncio
import os
import sys
from pathlib import Path

_script_dir = os.path.dirname(os.path.abspath(__file__))
_app_dir = os.path.dirname(_script_dir)
for d in (_app_dir, _script_dir):
    if d not in sys.path:
        sys.path.insert(0, d)

from backend.kb_manager import KBManager, append_log, git_init, ingest_input, _name_of, read_kms
from backend import rag
from backend import wiki
from backend import sources as src

SERVER_NAME = "chatkms"
SERVER_VERSION = "0.5.0"


# ── 上下文 ─────────────────────────────────────────────

def _active_info():
    kb = KBManager().get_active_kb()
    if not kb:
        raise RuntimeError("没有活跃知识库。请先 kb_create / kb_switch。")
    if not kb.sources:
        raise RuntimeError(f"工作区 {_name_of(kb.workspace)} 的 .kms 缺失或未记录 sources，无法访问源文件")
    return kb


def _sync() -> None:
    kb = KBManager().get_active_kb()
    rag.set_active_kb(kb.workspace if kb else None, kb.sources if kb else None)


def _fmt_kb(kb) -> list[str]:
    lines = [f"- **{kb.name}**",
             f"  工作区: `{kb.workspace}`",
             f"  描述: {kb.description or '（无）'}"]
    if kb.sources:
        lines.append("  源路径(sources):")
        for s in kb.sources:
            lines.append(f"    - `{s}`")
    else:
        lines.append("  源路径: （.kms 缺失/为空，无法引用源文件）")
    lines.append(f"  文档: {kb.doc_count} | 索引: {'有' if kb.has_index else '无'} | git(wiki): {'已初始化' if kb.git_initialized else '未初始化'}")
    return lines


def _fmt_hits(hits: list[dict]) -> str:
    if not hits:
        return "未找到相关知识。"
    icon = {"stable": "🟢", "uncertain": "🟡", "raw": "⚪"}
    out = []
    for i, h in enumerate(hits, 1):
        out.append(f"### [{i}] {h.get('type_label', '')} {icon.get(h.get('reliability'), '⚪')} 分:{h.get('score', 0):.3f}")
        out.append(f"来源: `{h.get('source', '?')}`")
        out.append(h.get("content", ""))
        out.append("---")
    return "\n".join(out)


def _wiki_path(kb_path: str) -> Path:
    return Path(kb_path) / "wiki"


def _resolve_wiki(wiki: Path, path: str) -> Path:
    full = (wiki / path.lstrip("/\\")).resolve()
    if not str(full).startswith(str(wiki.resolve())):
        raise RuntimeError(f"路径越权: {path}")
    return full


def _safe_wiki(path: str, pending: bool) -> str:
    p = path.strip().replace("\\", "/")
    prefix = "待确认/" if pending else "可信/"
    if p.startswith(("可信/", "待确认/")):
        return p
    return prefix + p


# ── MCP 服务 ─────────────────────────────────────────────

async def main() -> None:
    from mcp.server import Server
    from mcp.server.models import InitializationOptions
    from mcp.server.stdio import stdio_server
    from mcp.server import NotificationOptions
    from mcp.types import Tool, TextContent

    server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools():
        return [
            Tool(name="kb_list",
                 description="列出所有知识库工作区（名称/工作区/源路径/文档数）。带 dir 可扫描某目录下的候选工作区",
                 inputSchema={"type": "object", "properties": {"dir": {"type": "string"}}}),
            Tool(name="kb_create",
                 description="创建知识库工作区（生成 .kms/wiki/log/.rag/AGENTS.md）并设为当前。sources 为源路径列表，只读引用不拷贝",
                 inputSchema={"type": "object", "properties": {
                     "name": {"type": "string"},
                     "sources": {"type": "array", "items": {"type": "string"},
                                 "description": "源文件根路径列表（可多个），写入 .kms"},
                     "workspace": {"type": "string", "description": "可选，默认放 workspace_root 下"},
                     "description": {"type": "string"}},
                     "required": ["name", "sources"]}),
            Tool(name="kb_switch",
                 description="切换当前知识库（传名称或工作区路径）",
                 inputSchema={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}),
            Tool(name="kb_current",
                 description="获取当前知识库信息（工作区、sources、AGENTS 指引、wiki 结构说明）",
                 inputSchema={"type": "object", "properties": {}}),
            Tool(name="kb_delete",
                 description="删除知识库：从 config.yaml 移除工作区登记（二次确认 confirm='yes'）。delete_files=true 时同时删除工作区文件；源文件层永远不受影响",
                 inputSchema={"type": "object", "properties": {
                     "target": {"type": "string"}, "confirm": {"type": "string"},
                     "delete_files": {"type": "boolean", "default": False}},
                     "required": ["target", "confirm"]}),
            Tool(name="git_commit",
                 description="提交 wiki 目录改动（自动附 git log 到工作区 log/）",
                 inputSchema={"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}),
            Tool(name="wiki_list",
                 description="列出工作区 wiki 全部页面（可信/待确认 分层）",
                 inputSchema={"type": "object", "properties": {}}),
            Tool(name="wiki_read",
                 description="读取 wiki 页面内容（路径可带 可信/ 待确认/ 前缀）",
                 inputSchema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}),
            Tool(name="wiki_write",
                 description="写入/更新 wiki 页面。默认写 可信/；不确定内容设 pending=true 写 待确认/",
                 inputSchema={"type": "object", "properties": {
                     "path": {"type": "string"}, "content": {"type": "string"},
                     "pending": {"type": "boolean", "default": False},
                     "overwrite": {"type": "boolean", "default": False}},
                     "required": ["path", "content"]}),
            Tool(name="append_log",
                 description="向工作区 log/ 追加一条今日日志",
                 inputSchema={"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]}),
            Tool(name="ingest_input",
                 description="v0.5 保存用户使用知识库时输入的一句话/一份资料。AGENT 按 AGENTS.md 先辨别是否有关再路由：wiki/auto→写wiki(默认待确认)；source→源层只读,暂存工作区inbox并提醒用户落盘源路径；note→仅记log(判为无关)。每次自动记log",
                 inputSchema={"type": "object", "properties": {
                     "content": {"type": "string"},
                     "mode": {"type": "string", "enum": ["auto", "wiki", "source", "note"], "default": "auto"},
                     "title": {"type": "string", "description": "可选标题，缺省取内容首行"},
                     "pending": {"type": "boolean", "default": True, "description": "wiki 模式先入待确认"},
                     "reference": {"type": "string", "description": "参考/佐证"}},
                     "required": ["content"]}),
            Tool(name="wiki_search",
                 description="Wiki 优先检索：直接关键词搜索 wiki 页面（文件名+标题+正文），毫秒级。先试这个，不够再 rag_query",
                 inputSchema={"type": "object", "properties": {
                     "query": {"type": "string"}, "top_k": {"type": "integer", "default": 10},
                     "layer": {"type": "string", "description": "可选限定目录：可信 / 待确认"}},
                     "required": ["query"]}),
            Tool(name="wiki_status",
                 description="Wiki 概览：各目录页数、日志、索引状态（wiki 优先策略第一步）",
                 inputSchema={"type": "object", "properties": {}}),
            Tool(name="rag_engine",
                 description="查看/切换 RAG 引擎：bm25(默认,零依赖) / semantic(需装 requirements-semantic.txt)。不带 engine 参数则只查询",
                 inputSchema={"type": "object", "properties": {
                     "engine": {"type": "string", "enum": ["bm25", "semantic"]}}}),
            Tool(name="source_list",
                 description="列出源文件层（sources 全部源路径）下所有文件，返回 名称/相对路径/大小",
                 inputSchema={"type": "object", "properties": {}}),
            Tool(name="source_find",
                 description="按文件名在源文件层查找（精确名或模糊子串）",
                 inputSchema={"type": "object", "properties": {
                     "name": {"type": "string"}, "limit": {"type": "integer", "default": 100}},
                     "required": ["name"]}),
            Tool(name="source_read",
                 description="读取源文件内容（只读）。ref 可为相对路径，或 raw://源根名/相对路径。pdf 尽力抽取文字",
                 inputSchema={"type": "object", "properties": {
                     "ref": {"type": "string"},
                     "max_chars": {"type": "integer", "default": 0}},
                     "required": ["ref"]}),
            Tool(name="rag_query",
                 description="检索知识库。mode: light=仅wiki快查 / heavy=wiki+源文件深度 / auto=wiki优先",
                 inputSchema={"type": "object", "properties": {
                     "query": {"type": "string"}, "top_k": {"type": "integer", "default": 5},
                     "mode": {"type": "string", "enum": ["auto", "light", "heavy"], "default": "auto"}},
                     "required": ["query"]}),
            Tool(name="rag_rebuild",
                 description="重建当前知识库 BM25 索引（新增/修改源文件或 wiki 后调用）",
                 inputSchema={"type": "object", "properties": {}}),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        try:
            if name == "kb_list":
                mgr = KBManager()
                kbs = mgr.list_kbs(dir=arguments.get("dir"))
                active = mgr.get_active_kb()
                head = "当前活跃知识库: " + (f"`{active.workspace}`" if active else "（未设置）") + "\n\n"
                body = "\n".join("\n".join(_fmt_kb(k)) for k in kbs) or "（暂无知识库，用 kb_create 创建）"
                return TextContent(type="text", text=head + body)

            if name == "kb_create":
                mgr = KBManager()
                info = mgr.create_kb(arguments["name"], arguments.get("sources") or [],
                                     arguments.get("workspace"),
                                     arguments.get("description", ""))
                _sync()
                return TextContent(type="text", text=f"✅ 已创建并设为当前\n" + "\n".join(_fmt_kb(info)))

            if name == "kb_switch":
                mgr = KBManager()
                path = mgr.switch_kb(arguments["target"])
                _sync()
                return TextContent(type="text", text=f"✅ 已切换: `{path}`")

            if name == "kb_current":
                mgr = KBManager()
                kb = mgr.get_active_kb()
                if not kb:
                    return TextContent(type="text", text="没有活跃知识库。用 kb_create / kb_switch 设置。")
                _sync()
                kms = read_kms(kb.workspace)
                lines = _fmt_kb(kb)
                if not kms:
                    lines.append("\n⚠️ 工作区未找到 .kms 自描述文件")
                wiki = _wiki_path(kb.workspace)
                sf = wiki / "结构说明.md"
                structure = sf.read_text(encoding="utf-8", errors="ignore") if sf.is_file() else ""
                agents = Path(kb.workspace) / "AGENTS.md"
                agents_txt = agents.read_text(encoding="utf-8", errors="ignore") if agents.is_file() else ""
                return TextContent(type="text", text="\n".join(lines)
                                   + "\n\n# AGENTS.md 指引\n" + agents_txt
                                   + "\n\n# Wiki 结构说明\n" + structure)

            if name == "kb_delete":
                if arguments.get("confirm") != "yes":
                    return TextContent(type="text", text="需要二次确认：请设置 confirm='yes' 再执行删除。")
                mgr = KBManager()
                ws = mgr.resolve(arguments["target"])
                mgr.delete_kb(arguments["target"], arguments.get("delete_files", False))
                _sync()
                return TextContent(type="text", text=f"✅ 已删除工作区登记{'(含工作区文件)' if arguments.get('delete_files') else ''}: `{ws}`（源文件层不受影响）")

            if name == "git_commit":
                ws = _active_info().workspace
                wiki = _wiki_path(ws)
                if not git_init(wiki):
                    return TextContent(type="text", text="错误: git 初始化失败（请确认已安装 Git）")
                import subprocess
                msg = arguments.get("message") or "wiki update"
                subprocess.run(["git", "add", "-A"], cwd=wiki, capture_output=True, text=True, timeout=30)
                changed = subprocess.run(["git", "status", "--porcelain"], cwd=wiki,
                                         capture_output=True, text=True, timeout=30).stdout.strip()
                if not changed:
                    return TextContent(type="text", text="无改动，无需提交。")
                r = subprocess.run(["git", "commit", "-m", msg], cwd=wiki,
                                   capture_output=True, text=True, timeout=30)
                if r.returncode != 0:
                    return TextContent(type="text", text=f"提交失败: {(r.stderr or r.stdout)[:300]}")
                last = (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else "committed"
                append_log(Path(ws) / "log", f"git 提交(wiki/{msg}): {last}")
                return TextContent(type="text", text=f"✅ 已提交: {msg}\n\n改动:\n{changed}")

            kb = _active_info()  # 其余工具需要活跃工作区
            _sync()
            ws = kb.workspace
            sources = kb.sources

            if name == "wiki_list":
                wiki = _wiki_path(ws)
                pages = []
                if wiki.is_dir():
                    for root, dirs, files in os.walk(wiki):
                        dirs[:] = [d for d in dirs if d not in (".git",)]
                        for f in files:
                            if f.endswith(".md"):
                                pages.append(os.path.relpath(os.path.join(root, f), wiki).replace("\\", "/"))
                return TextContent(type="text", text="# Wiki 页面\n" + ("\n".join(f"- `{p}`" for p in pages) or "（空）"))

            if name == "wiki_read":
                wiki = _wiki_path(ws)
                p = arguments["path"].strip()
                target = _resolve_wiki(wiki, p)
                if not target.is_file():
                    for cand in (_resolve_wiki(wiki, _safe_wiki(p, False)),
                                 _resolve_wiki(wiki, _safe_wiki(p, True))):
                        if cand.is_file():
                            target = cand
                            break
                if not target.is_file():
                    return TextContent(type="text", text=f"页面未找到: {p}")
                return TextContent(type="text", text=f"# {os.path.relpath(target, wiki)}\n\n{target.read_text(encoding='utf-8', errors='ignore')}")

            if name == "wiki_write":
                if not (arguments.get("path") or "").strip():
                    return TextContent(type="text", text="错误: path 不能为空")
                wiki = _wiki_path(ws)
                p = _safe_wiki(arguments["path"], arguments.get("pending", False))
                target = _resolve_wiki(wiki, p)
                if target.exists() and not arguments.get("overwrite", False):
                    return TextContent(type="text", text=f"页面已存在: {p}。设置 overwrite=true 以覆盖")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(arguments["content"], encoding="utf-8")
                rel = os.path.relpath(target, wiki).replace("\\", "/")
                append_log(Path(ws) / "log", f"wiki 写入/更新: {rel}{'（待确认）' if arguments.get('pending') else ''}")
                return TextContent(type="text", text=f"✅ 已写入: {rel}")

            if name == "append_log":
                append_log(Path(ws) / "log", arguments["note"])
                return TextContent(type="text", text=f"✅ 已记录: {arguments['note']}")

            if name == "ingest_input":
                r = ingest_input(ws, arguments.get("content", ""),
                                 mode=arguments.get("mode", "auto"),
                                 title=arguments.get("title", ""),
                                 pending=arguments.get("pending", True),
                                 reference=arguments.get("reference", ""))
                if r["target"] == "inbox":
                    srcs = "\n".join(f"    - {s}" for s in sources) or "    （无源路径）"
                    return TextContent(type="text", text=f"✅ 已暂存工作区: `{r['saved']}`\n{r['note']}\n源路径(只读，需你落盘):\n{srcs}")
                if r["target"] == "log":
                    return TextContent(type="text", text=f"已记录(未落入知识库): {r['note']}")
                return TextContent(type="text", text=f"✅ 已写入: `{r['saved']}`\n{r['note']}")

            if name == "wiki_search":
                rows = []
                for m in wiki.search(ws, arguments["query"], arguments.get("top_k", 10),
                                     layer=arguments.get("layer")):
                    rows.append(f"### {m['path']}  (匹配{m['score']}分)\n{m['snippet']}\n")
                return TextContent(type="text", text=f"# Wiki 检索: {arguments['query']}\n\n"
                                                     + ("\n---\n".join(rows) or "wiki 无匹配，可转 rag_query 深度检索"))

            if name == "wiki_status":
                st = wiki.status(ws)
                return TextContent(type="text", text="\n".join([
                    "# Wiki 概览",
                    f"页面总数: {st['wiki_pages']}",
                    f"可信: {st['by_dir'].get('可信', 0)} | "
                    f"待确认: {st['by_dir'].get('待确认', 0)} | "
                    f"其他: {st['by_dir'].get('其他', 0)}",
                    f"日志文件: {st['log_files']}（今日: {'有' if st['today_log_exists'] else '无'}）",
                    f"查询日志: {st['query_log_lines']} 条（夜间构建据此分析高频问题）",
                    f"RAG 引擎: {rag.engine_name()}",
                ]))

            if name == "rag_engine":
                if arguments.get("engine"):
                    rag.set_engine(arguments["engine"])
                    return TextContent(type="text", text=f"✅ 已切换 RAG 引擎: {arguments['engine']}")
                return TextContent(type="text", text=f"当前 RAG 引擎: {rag.engine_name()}")

            if name == "source_list":
                rows = [f"- `{sf.rel}`  ({sf.root_name}, {sf.size} B)" for sf in src.iter_source_files(sources)]
                return TextContent(type="text", text="# 源文件层\n" + ("\n".join(rows) or "（sources 为空或路径不存在）"))

            if name == "source_find":
                rows = [f"- `{sf.rel}`  ({sf.root_name})" for sf in src.source_find(sources, arguments["name"],
                                                                                      arguments.get("limit", 100))]
                return TextContent(type="text", text=f"# 按文件名查找: {arguments['name']}\n" + ("\n".join(rows) or "未找到"))

            if name == "source_read":
                ref = arguments["ref"]
                target = None
                if ref.startswith("raw://"):
                    rest = ref[len("raw://"):]
                    if "/" in rest:
                        rname, rel = rest.split("/", 1)
                        for sf in src.iter_source_files(sources):
                            if sf.root_name == rname and sf.rel == rel:
                                target = sf
                                break
                else:
                    q = ref.strip().lower()
                    for sf in src.iter_source_files(sources):
                        if sf.rel.lower() == q or sf.name.lower() == q:
                            target = sf
                            break
                if not target:
                    return TextContent(type="text", text=f"未找到源文件: {ref}")
                text, readable = src.extract_text(target.abspath, arguments.get("max_chars", 0))
                if not readable:
                    return TextContent(type="text", text=f"文件存在但无法抽取文字（二进制/需人工打开）: `{target.abspath}`")
                return TextContent(type="text", text=f"# {target.rel}  （{target.root_name}）\n\n{text}")

            if name == "rag_query":
                hits = rag.retrieve(arguments["query"], top_k=arguments.get("top_k", 5),
                                    mode=arguments.get("mode", "auto"))
                wiki.log_query(ws, arguments["query"], arguments.get("mode", "auto"),
                               len(hits), rag.engine_name())
                return TextContent(type="text", text=f"# 检索: {arguments['query']} (mode={arguments.get('mode', 'auto')}, engine={rag.engine_name()})\n\n{_fmt_hits(hits)}")

            if name == "rag_rebuild":
                info = rag.rebuild_index(ws, sources)
                return TextContent(type="text", text=f"✅ 索引重建完成: 文档 {info['doc_count']} 篇"
                                                     f"{'（空索引，请确认 sources 有可读文件）' if not info['ok'] else ''}")

            return TextContent(type="text", text=f"未知工具: {name}")
        except Exception as e:
            return TextContent(type="text", text=f"⚠️ {type(e).__name__}: {e}")

    async with stdio_server() as (read, write):
        await server.run(read, write, InitializationOptions(
            server_name=SERVER_NAME,
            server_version=SERVER_VERSION,
            capabilities=server.get_capabilities(
                notification_options=NotificationOptions(), experimental_capabilities=None),
        ))


if __name__ == "__main__":
    asyncio.run(main())