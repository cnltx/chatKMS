"""chatKMS 配置加载/保存（v0.4）。

config.yaml 位于 chatKMS/config/ 目录，是知识库**工作区**的唯一注册表，
只登记 workspace，不存源路径。源路径(sources) 由各工作区内的 {库名}.kms 记录（可多个）。
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any, Optional
import yaml

_APP_DIR = Path(__file__).resolve().parents[1]
_DEFAULT_CONFIG = _APP_DIR / "config" / "config.yaml"
# 兜底默认：仅当 config.yaml 缺失 workspace_root 字段时使用（中性值，随用户主目录）。
# 运行时路径的权威来源是 config.yaml（见 get_config / load_config）。
_DEFAULT_WORKSPACE_ROOT = str(Path.home() / "kmsWorkspace")

_cfg: Optional[dict[str, Any]] = None


def config_path() -> Path:
    env = os.environ.get("CHATKMS_CONFIG")
    return Path(env).resolve() if env else _DEFAULT_CONFIG


def _default_config() -> dict[str, Any]:
    return {
        "name": "chatKMS",
        "version": "0.4.0",
        "workspace_root": _DEFAULT_WORKSPACE_ROOT,
        "knowledge_bases": [],
        "rag": {"engine": "bm25", "semantic_model": "BAAI/bge-small-zh-v1.5",
                "chunk_size": 512, "chunk_overlap": 128, "top_k": 5},
    }


def load_config() -> dict[str, Any]:
    global _cfg
    if _cfg is not None:
        return _cfg
    p = config_path()
    if not p.exists():
        _cfg = _default_config()
        return _cfg
    _cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    _cfg.setdefault("workspace_root", _DEFAULT_WORKSPACE_ROOT)
    _cfg.setdefault("knowledge_bases", [])
    _cfg.setdefault("rag", {"engine": "bm25", "semantic_model": "BAAI/bge-small-zh-v1.5",
                            "chunk_size": 512, "chunk_overlap": 128, "top_k": 5})
    return _cfg


def get_config() -> dict[str, Any]:
    return load_config()


def reload_config() -> dict[str, Any]:
    global _cfg
    _cfg = None
    return load_config()


def save_config(cfg: dict[str, Any]) -> None:
    global _cfg
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    _cfg = cfg