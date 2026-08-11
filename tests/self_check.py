"""chatKMS 全逻辑自检（放在 tests/，不污染根逻辑）。

验证 v0.4 核心：工作区/源文件分离、多 sources、.kms、模板派生、按文件名源访问、
wiki 优先、RAG 双引擎默认 bm25 的 light/auto/heavy。

运行：python tests/self_check.py
不依赖 MCP 客户端，直接调用底层模块断言。测试内容为中性占位数据，不含任何领域术语。
"""
from __future__ import annotations
import os
import shutil
import sys
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))   # chatKMS/tests
_ROOT = os.path.dirname(_HERE)                        # chatKMS
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.kb_manager import KBManager, read_kms
from backend import rag, wiki, sources as src


def main() -> None:
    work = tempfile.mkdtemp(prefix="chatkms_selfcheck_")
    os.environ["CHATKMS_CONFIG"] = str(Path(work) / "config.yaml")
    from backend.config import reload_config, save_config
    cfg = reload_config()
    cfg["workspace_root"] = str(Path(work) / "workspaces")
    save_config(cfg)

    # 两个中性"源文件根"
    src1 = Path(work) / "sourceA"
    src2 = Path(work) / "sourceB"
    (src1 / "concepts").mkdir(parents=True)
    (src1 / "concepts" / "alpha.md").write_text(
        "# Alpha\n\nAlpha 是一种通用通信协议，支持即插即用。", encoding="utf-8")
    (src2 / "devices").mkdir(parents=True)
    (src2 / "devices" / "beta.md").write_text(
        "# Beta\n\nBeta 是一个带控制器功能的通用组件。", encoding="utf-8")
    (src2 / "devices" / "ignored.tmp").write_text("noise", encoding="utf-8")  # 非索引扩展名

    # 1. 创建工作区（.kms 登记 sources 两个源根）
    mgr = KBManager()
    info = mgr.create_kb("generic", sources=[str(src1), str(src2)], description="中性自检库")
    ws = Path(info.workspace)
    assert (ws / "generic.kms").is_file(), ".kms 缺失"
    kms = read_kms(str(ws))
    assert kms and len(kms["sources"]) == 2, f"sources 应为 2 个: {kms}"
    assert (ws / "wiki" / "可信").is_dir() and (ws / "wiki" / "待确认").is_dir()
    assert (ws / "AGENTS.md").is_file() and (ws / "log").is_dir()

    # 2. 模板派生：AGENTS.md 来自 sample/default，占位符已替换、名称已注入
    agents_txt = (ws / "AGENTS.md").read_text(encoding="utf-8")
    assert "{{" not in agents_txt, "模板占位符 {{...}} 未替换"
    assert "generic" in agents_txt, "AGENTS.md 未注入知识库名"
    assert (ws / "wiki" / "结构说明.md").is_file(), "模板 wiki/结构说明.md 未复制"

    # 3. config.yaml 只登记 workspace，不存 sources（关键校验）
    cfg = reload_config()
    entry = cfg["knowledge_bases"][0]
    assert "sources" not in entry, "config.yaml 不应包含 sources"
    assert "workspace" in entry, "config.yaml 应登记 workspace"

    # 4. 源文件层只读访问（按文件名）
    found = src.source_find(info.sources, "alpha")
    assert found and found[0].rel.endswith("alpha.md"), f"source_find 失败: {found}"
    text, ok = src.extract_text(found[0].abspath)
    assert ok and "即插即用" in text, "source_read 应能读到源文件内容"

    # 5. wiki 写入 + RAG 三层检索（默认引擎 bm25）
    trusted_dir = ws / "wiki" / "可信"
    (trusted_dir / "通用概述.md").write_text(
        "# 通用概述\n\n协议层面的即插即用指设备接入后自动完成识别与配置。", encoding="utf-8")
    rag.set_active_kb(info.workspace, info.sources)
    assert rag.rebuild_index(info.workspace, info.sources)["ok"], "索引构建失败"
    assert not any("ignored.tmp" in m["source"] for m in rag._corpus), "非索引扩展名进入了语料"

    auto = rag.retrieve("即插即用 协议", top_k=5, mode="auto")
    assert any(h["layer"] == "wiki" for h in auto), f"auto 应命中 wiki: {auto}"

    heavy = rag.retrieve("Alpha 通信 协议", top_k=5, mode="heavy")
    assert any(h["layer"] == "raw" for h in heavy), f"heavy 应命中源文件: {heavy}"
    raw_ref = next(h["source"] for h in heavy if h["layer"] == "raw")
    assert raw_ref.startswith("raw://"), f"源文件 source 格式应为 raw://: {raw_ref}"

    light = rag.retrieve("配置 识别", top_k=5, mode="light")
    assert not light or all(h["layer"] == "wiki" for h in light), "light 应只查 wiki"

    # 6. wiki 优先层（默认引擎应为 bm25）
    assert rag.engine_name() == "bm25", f"默认引擎应为 bm25，实际 {rag.engine_name()}"
    ws_m = wiki.search(info.workspace, "协议")
    assert any(m["path"].endswith("通用概述.md") for m in ws_m), f"wiki_search 未命中: {ws_m}"
    st = wiki.status(info.workspace)
    assert st["wiki_pages"] >= 1 and st["by_dir"].get("可信", 0) >= 1, f"wiki_status 异常: {st}"

    shutil.rmtree(work, ignore_errors=True)
    print("✅ 全部自检通过（工作区/源文件分离、多 sources、.kms、模板派生、按文件名源访问、wiki优先、RAG bm25 light/auto/heavy）")


if __name__ == "__main__":
    main()