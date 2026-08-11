# chatKMS

一个用于描述给AI的知识库，你可以集成任何你想要的想法来定制这个知识库

## 特色

1、项目采用的是RAG增强检索，基础检索使用llm-wiki的设计，为了确保速度，优先使用wiki，夜间定时使用RAG自更新

2、wiki目录维护的是AI+用户的知识，用户可以不必依赖源文件，加入定制化或者自己的知识放入wiki，AI根据RAG进行知识确诊和边界判断

3、工作空间保存wiki+rag+log，源文件可以多绑定工作空间，可以做到跨PC知识库移植

4、集成mcp服务，定制mcp给你的AGENT

5、内部集成skill及agent.md，在sample里面，你可以规划你的示例知识库，你可以在这里开始你的知识库示例，如何定制你完全可以随你自己开始

## 技术栈 / Tech Stack

| 层 | 选型 |
|---|---|
| 语言 | Python ≥ 3.10（实测 3.11） |
| 接口协议 | MCP（stdio）— 纯后端，无 Web / 无前端 |
| 核心依赖 | `mcp`、`pyyaml`、`jieba`（中文分词）、`rank-bm25` |
| RAG 检索 | 双引擎：`bm25`（默认，零外部依赖）/ `semantic`（可选：`chromadb` + `sentence-transformers` + `numpy`，嵌入模型 `bge-small-zh-v1.5`） |
| 文档解析（可选） | `PyMuPDF`（PDF 文字抽取） |
| 配置 / 存储 | YAML（`config/config.yaml` 工作区注册表 + 各工作区 `{库名}.kms` 自描述；源文件层只读引用、不拷贝） |
| 版本管理 | git（工作区 `wiki/` 层） |

> 选型取向：**轻量优先、默认零模型可用**；语义引擎与 PDF 解析作为可选依赖按需启用。

## 目录

```
config/    配置文件(config.yaml=工作区注册表)
env/       环境依赖(requirements*.txt)
design/    设计稿 / 搭建 / 评审记录 / 踩坑记录
sample/    基础工作空间模板(可改，新工作区由此派生)
tests/     全逻辑自检(不污染根逻辑)
backend/   后端逻辑(MCP 服务 / 双引擎RAG / wiki优先 / 夜间构建)
kb_create.py   交互式创建知识库工作区
mcp.json       客户端接入配置
```

## 快速开始

```bash
pip install -r env\requirements.txt
python tests\self_check.py          # 自检，应 ✅
python kb_create.py                 # 交互式建库
# 接入 MCP：mcp.json 并入 .mcp.json（CHATKMS_CONFIG 指向 config\config.yaml）
```

## 设计评审机制

设计/代码变更须经**独立评审**，记录维护于 `design/评审记录.md`：

- 评审以独立个体身份进行，须指出设计或评审自身的不合理之处（过度设计/误报/风险高估），并记录处置（已修复/已评估暂缓/待处理）。
- **自检门槛**：任何代码改动必须通过 `tests/self_check.py`，否则不合并。

## 踩坑

见 `design/踩坑记录.md`。
