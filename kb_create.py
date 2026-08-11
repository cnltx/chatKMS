#!/usr/bin/env python3
"""kb_create — 交互式创建知识库工作区。

数据全部从文件读取，**不硬编码任何路径**：
  - 工作区默认位置   ← config.yaml 的 workspace_root
  - 源路径快速选择   ← 已有知识库 .kms 的 sources（去重），也可自定义输入（可多个）
  - 现有知识库列表   ← config.yaml + 磁盘 .kms 自动发现

运行：python kb_create.py
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

_APP = Path(__file__).resolve().parent
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

from backend.config import reload_config
from backend.kb_manager import KBManager, read_kms, template_dir


def _show_existing() -> None:
    kbs = KBManager().list_kbs()
    print("\n当前知识库：")
    if not kbs:
        print("  （无）")
    active = KBManager().get_active_kb()
    active_ws = active.workspace if active else None
    for k in kbs:
        mark = "  ◀ 当前" if k.workspace == active_ws else ""
        srcs = ", ".join(Path(s).name for s in k.sources) or "（无 sources）"
        print(f"  - {k.name}  [{Path(k.workspace).name}]  源: {srcs}{mark}")
    print()


def _pick_name(cfg) -> str:
    while True:
        name = input("知识库名称: ").strip()
        if not name:
            print("⚠️  名称不能为空")
            continue
        if any(kb.get("name") == name for kb in cfg.get("knowledge_bases", [])):
            print(f"⚠️  [ {name} ] 已在 config.yaml 登记，请用 kb_switch 切换而非重建。")
            continue
        root = cfg.get("workspace_root") or ""
        if root and os.path.isdir(root):
            hit = [c for c in (os.path.join(root, name), os.path.join(root, f"{name}_1"))
                   if read_kms(c)]
            if hit:
                print(f"⚠️  磁盘已存在同名工作区: {hit[0]}（未登记）。请用 kb_switch 切换到它。")
                continue
        return name


def _pick_workspace(cfg, name: str) -> str:
    ws_root = cfg.get("workspace_root") or ""
    default = os.path.join(ws_root, name) if ws_root else os.path.abspath(name)
    raw = input(f"工作区路径 [回车用默认: {default}]：").strip()
    if not raw:
        return os.path.abspath(default)
    return os.path.abspath(raw)


def _pick_sources() -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()
    for k in KBManager().list_kbs():  # 从 .kms 读取（文件驱动）
        for s in k.sources:
            n = os.path.normpath(s)
            if n not in seen:
                seen.add(n)
                hints.append(s)

    chosen: list[str] = []
    if hints:
        print("源路径快速选择（来自已有知识库 .kms，去重）：")
        for i, s in enumerate(hints, 1):
            flag = "  ◆存在" if os.path.isdir(s) else "  ✗路径不存在"
            print(f"  {i}) {s}{flag}")
        raw = input("输入编号可多选(逗号分隔，如 1,2)；回车=不选: ").strip()
        for part in raw.replace("；", ",").replace(";", ",").split(","):
            part = part.strip()
            if part.isdigit() and 1 <= int(part) <= len(hints):
                chosen.append(hints[int(part) - 1])
    else:
        print("（暂无已知源路径，可自定义输入）")

    custom_raw = input("自定义源路径（可多个，逗号分隔；回车结束）: ").strip()
    for part in custom_raw.replace("；", ",").replace(";", ",").split(","):
        p = part.strip()
        if p and p not in chosen:
            chosen.append(os.path.abspath(p))

    return list(dict.fromkeys(chosen))  # 去重保序


def main() -> None:
    if not os.environ.get("CHATKMS_CONFIG"):
        os.environ["CHATKMS_CONFIG"] = str(_APP / "config" / "config.yaml")
    cfg = reload_config()
    print("== chatKMS — 创建知识库工作区 ==")
    print(f"workspace_root: {cfg.get('workspace_root', '（未设置）')}")
    _show_existing()

    name = _pick_name(cfg)
    desc = input("描述(一句话说明库的用途，回车跳过): ").strip()
    workspace = _pick_workspace(cfg, name)
    sources = _pick_sources()
    if not sources:
        print("⚠️  未提供源路径，工作区将只能检索 wiki。确认继续？(y/N): ", end="")
        if input().strip().lower() != "y":
            print("已取消。")
            return

    print("\n== 确认 ==")
    print(f"  名称    : {name}")
    print(f"  描述    : {desc or '（无）'}")
    print(f"  工作区  : {workspace}")
    print(f"  来源模板: {template_dir()}")
    print(f"  源路径  : {len(sources)} 个")
    for s in sources:
        mark = "◆存在" if os.path.isdir(s) else "✗缺失"
        print(f"    - {s}  {mark}")
    if input("输入 y 确认创建，其他任意键取消: ").strip().lower() != "y":
        print("已取消。")
        return

    info = KBManager().create_kb(name, sources=sources, workspace=workspace, description=desc)
    print(f"\n✅ 已创建并设为当前: {info.name}")
    print(f"   工作区: {info.workspace}")
    print(f"   源路径: {len(info.sources)} 个")
    print(f"   下一步：通过 MCP 的 rag_rebuild 建索引（或接入后直接提问，首次检索自动建）")


if __name__ == "__main__":
    main()