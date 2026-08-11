---
描述: chatKMS：知识管理系统设计
版本: v0.5
变更: 源文件与工作区分离，raw 层改为按文件名引用源路径（不存放文件）
---
# chatKMS——知识管理系统设计稿

## 变更说明

### v0.4

v0.3 中每个知识库是一个"知识管理系统"目录，其中 `raw/` 存放原资料。
v0.4 的核心变更：**源文件不进入工作区，只被引用**。

1. 知识库不再拥有 `raw/` 存储目录。源文件层是工作区外的**只读路径集合**（如 `D:\0_Work\SVN\知识库`），记录的**位置在工作区自己的 `{库名}.kms` 里（`sources` 字段，可引用多个源路径），而非 config.yaml**；chatKMS 只在**工作区**内生成产物。
2. `config.yaml` **只登记知识库的工作区路径**（如 `D:\0_Work\kmsWorkspace\charger`），不记录源文件路径。工作区 = 索引(wiki) + 向量(.rag) + 日志(log) + 指引(AGENTS.md) + 自描述(.kms)，是 chatKMS 生成的全部内容所在地。
3. **检索源文件靠文件名**：chatKMS 扫描源路径，建立 `文件名 → 相对路径` 映射；读取源文件由 MCP 工具按文件名/相对路径完成，全程只读，不拷贝到工作区。
4. 工作区目录内生成一个 `{知识库名}.kms` 自描述文件，**就地记录该工作区绑定的一个或多个源路径(sources)与作用**。config.yaml 不存 sources，chatKMS 通过读取工作区里的 `.kms` 才知道源文件在哪。
5. `wiki/`、`log/`、`.rag/` 全部是工作区生成的内容，与源文件彻底分离，任何写入都不会出现在源目录里。

其余设计（AGENTS.md 规范、wiki 可信/待确认机制、git 管理、无界面、后端 Python）沿用 v0.3。

---

### v0.5

v0.5 需要保存用户使用知识空间的时候对知识库输入的内容：

1、用户收集一句话或者一个资料需要加入变更，AGENT需要辨别是否有关，加入到wiki或者源资料里面，这点需要写入到示例的agent.md
2、

## 背景场景

优先个人使用，调整后可以分享给 10 人左右团队，要求可以接入 mcp 服务提供给各个 AI 终端（claude code、codex、hermes，后文统一称呼为 AI 终端），AI 终端接入 mcp 后会获取到如何与知识管理系统沟通的能力，当用户提出问题，AI 终端可以通过 mcp 进行知识管理系统的检索。

## 选型设计

- 简单检索使用 wiki，深度检索使用本地 rag + wiki 综合检索。
- wiki 设计参考 https://github.com/nashsu/llm_wiki 借鉴其思路。
- rag 检索模式由 AI 终端选择，尽量使用轻量级 RAG 服务（默认纯 BM25，零模型依赖），保证响应速度与多设备兼容。

## 核心概念：源文件层 + 工作区层

一个知识库由**两个分离的位置**构成：工作区由 `config.yaml` 登记，源路径由工作区内的 `.kms` 记录。

```
源文件层（只读，用户/团队维护）
  D:\0_Work\SVN\知识库\...        ← chatKMS 绝不写入此目录
        ▲
        │ 按文件名引用（source_list / source_read / source_find）
        │
工作区层（chatKMS 生成与维护）
  D:\0_Work\kmsWorkspace\charger\
        ├── charger.kms           ← 自描述：绑定源路径 + 作用
        ├── .rag/                 ← 向量索引（引用源文件相对路径，不拷贝）
        ├── wiki/                 ← 生成内容（LLM 与用户共同维护）
        │   ├── 结构说明.md
        │   ├── 可信/
        │   └── 待确认/
        ├── log/                  ← 生成内容（按天 log_日期.md）
        └── AGENTS.md             ← 指引，只读
```

分离原则：

- 源文件层的内容**永不被工作区改动**。工作区只读出源文件内容，用于生成 wiki、建索引。
- 工作区删除/重建**不影响源文件层**；反之源文件层变更只需重建索引。
- 一个知识库可以引用**多个源路径**（`.kms` 的 `sources` 数组），同一源路径也可被多个知识库共用（同一批原始资料，产出不同主题的 wiki）。

## 配置模型 config.yaml

config.yaml 位于 chatKMS 根目录，是知识库工作区的唯一注册表。属性：

```yaml
name: chatKMS
version: 0.5.0
# 知识库工作区默认根目录（创建时也可指定任意其他路径）
workspace_root: D:\0_Work\kmsWorkspace
knowledge_bases:
  - name: charger
    description: 充电团队知识库（ISO15118 / OCPP / GB-T / 嵌入式模板）
    workspace: D:\0_Work\kmsWorkspace\charger   # 工作区路径，chatKMS 生成产物
    created_at: '2026-08-10T...'
active_kb: D:\0_Work\kmsWorkspace\charger
```

- config.yaml **不记录源文件路径**。源路径只存在于对应工作区的 `{库名}.kms` 里，chatKMS 工作前先读工作区的 `.kms` 取得 `source`。
- `knowledge_bases[i].workspace`：工作区根，chatKMS 生成的全部内容与 `{库名}.kms` 都在这里，用绝对路径。
- 缺 `workspace` 视为无效知识库条目；`{库名}.kms` 缺失则该工作区无法访问源文件（只能检索 wiki）。
- 新增知识库时：config.yaml 登记 workspace + 在工作区生成骨架（含 `.kms`）。打开任意目录时若发现 `{名}.kms` 且对应工作区不在注册表，可自动发现并补登记。

## 知识库工作区生成目录

创建知识库时按以下结构生成**工作区**（源文件层不生成任何文件）：

```
{工作区路径}\   ← 例：D:\0_Work\kmsWorkspace\charger
├── {库名}.kms      ← 自描述文件（见下）
├── .rag            ← 本地向量索引存放（引用源文件相对路径）
├── wiki/           ← wiki 目录
│   ├── 结构说明.md
│   ├── 可信/       ← 有大量佐证、判定可信
│   └── 待确认/     ← 不确定或未经证实，写入后提醒用户确认
├── log/            ← 按天汇总 AI 改动，命名 log_日期.md
└── AGENTS.md       ← 教会新的 AI 终端如何使用 MCP 与本知识库
```

### 基础工作空间模板（示例文件夹，用户可改）

chatKMS 自带一个**示例基础工作空间** `sample/default/`（AGENTS.md、wiki/结构说明.md 及可信/待确认/log/.rag 骨架）。
创建任何新工作区时，chatKMS 的流程是：**复制模板 → 占位符替换（`{{name}}`/`{{description}}`）→ 生成 `{库名}.kms` → git init**。
模板文件不是写死在代码里，用户可以**直接修改模板目录**（改 AGENTS.md 规范、增删结构文件），之后创建的所有工作区都会继承这些改动。
模板路径可用 config.yaml 的 `template_dir` 覆盖（默认 `chatKMS/sample/default`）。

## .kms —— 工作区自描述文件

生成于工作区根目录，用于：

- 让 chatKMS 能识别并自动发现一个工作区（即使 config.yaml 里还没登记）。
- 让 AI 终端读一个工作区目录时马上知道它绑定哪个源路径、作用是什么。

格式（YAML）：

```yaml
name: charger
description: 充电团队知识库
sources:                          # 源文件根路径数组，可多个；全部只读
  - D:\0_Work\SVN\知识库
  - D:\0_Work\充电补充资料
created_at: '2026-08-10T...'
```

- **`sources` 只写在 `.kms` 里**（数组，可引用多个源路径），config.yaml 不重复记录——源路径跟着工作区走，整个工作区复制/迁移到新机器时 `.kms` 自带来源信息，可被新环境的 chatKMS 自动发现。
- 创建时 chatKMS 在 config.yaml 登记 `workspace`，并在该工作区写入 `.kms`；二者以工作区路径关联，不复用同一份 sources 字段。
- AI 终端通过 `kb_current` / `kb_list` 获得 `sources` 列表：chatKMS 读对应工作区的 `.kms` 返回。缺 `.kms` 时返回"源路径未知（.kms 缺失）"。

## AGENTS.md 内容

1、当新的知识入库后，不要进行全部编译，先进行部分的简单编译，确保用户查询的时候，回答时间简短，有依据，之后记录有关事件，AI 终端交由子 agent 建立定时机制，空闲时自己编译之前的有关事件

2、WIKI 层由 llm 生成，用户和 LLM 一起维护，用户可以指出 LLM 的错误，LLM 辨别是否正确后综合写入

3、WIKI 进行 LINK 后的文件放入 WIKI 目录后 LLM 需要辨别此次知识是否可以考，有大量佐证是否正确，如果正确放入可信的 WIKI 目录，如果不可信或不确定，放入待确认的目录并提醒用户进行确认

4、可以注册 MCP 服务与 AGENT 沟通，wiki 目录使用 git 进行管理，log 文件内部保存 git 提交记录及变更设计

5、AI 终端来**补充**

## 知识库目录及文件作用

1、AGENTS.md：供 AI 终端使用，介绍知识管理系统用法及设计，帮助新的终端掌握知识管理系统并了解 mcp 的用法，同时开始需要列出 AI 对这个知识管理系统拥有哪些权限

2、**源文件层（记录在 {库名}.kms 的 sources 字段，可多个源路径，config.yaml 不存）**：团队维护的原始资料，AI 终端**只有阅读权**。文件不进入工作区，通过文件名/相对路径检索（见下节）

3、wiki 目录：内部存放所有 link 后的 wiki 文档，LLM 有这个目录的全部权限，尽量保证可读性，目录内部有结构说明.md，AI 终端在修改时需要一并维护它

4、log 目录：操作日志（按时间顺序，log_日期.md，含 git 提交记录）

5、.rag 目录：存放本地向量模型在本知识库的索引及产生的有关文件及向量模型配置（索引指向源文件相对路径，不存文件副本）

6、.hermes 目录：内部包含后续产生的 skills，rules 文件，并在 AGENTS.md 里面注册它

7、如果 AI 终端需要加入其他文件，说明作用后**必须**询问用户是否加入

8、用户可以通过目录选择删除知识库工作区，删除的时候需要进行二次确认才可以删除（只删工作区，源文件层不受影响）

## 检索方式：按文件名引用源文件

源文件层不进入工作区，工作区对源文件的访问一律通过以下 MCP 工具（只读，作用于 `.kms` 里 `sources` 的**全部源路径**）：

- `source_list`：递归列出所有源路径下的全部文件（`文件名 → 相对路径`），跳过隐藏目录
- `source_find(name)`：按文件名在所有源路径下查找（精确名或模糊关键字，返回相对路径及所属源根）
- `source_read(rel_path)`：按相对路径读取源文件内容（只读；在全部源根内校验不越权）
- 建索引时（`rag_rebuild`）扫描所有源路径 + 工作区 wiki：源文件以 `raw:任意源根相对路径` 参与检索；wiki 以 `wiki:相对路径` 参与检索
- 检索结果的 `source` 字段形如 `raw://SVN知识库/ISO15118/标准原文.pdf` 或 `wiki://可信/iso15118.md`，AI 终端可据此用 `source_read` 取全文

这样源文件层保持"零写入、零拷贝"，档案归档案，知识归工作区。

## v0.4.1 设计补充（已拍板 2026-08-10）

**1. RAG 双引擎**（config.yaml `rag.engine` 或环境变量 `CHATKMS_ENGINE` 决定）

- `bm25`（默认）：纯关键词，零依赖，开机即用；light/auto/heavy 语义不变
- `semantic`（可选）：ChromaDB + sentence-transformers（bge-small-zh-v1.5），语义检索，对协议 PDF / 英文术语更准；需安装 `requirements-semantic.txt`；索引持久化于工作区 `.rag/chroma/`，按文件 mtime+size 增量构建

**2. Wiki 优先流程（白天毫秒级，避免每次走向量检索）**

- 先在 wiki 层查：`wiki_status`（概览）→ `wiki_search`（文件名+标题+正文关键词，直接读 markdown）
- wiki 不够再 `rag_query`：light=仅 wiki / auto=wiki优先 / heavy=wiki+源文件
- `rag_query` 自动把查询写入 `log/query_log.jsonl`，供夜间分析

**3. 夜间构建** `backend/nightly_build.py`（注册为凌晨3点计划任务，重负荷异步）

- 增量刷新 RAG 索引（语义引擎按文件签名，只嵌入新增/变更）
- 分析 query_log：高频问题 + 多次 0命中缺口 → 写 `log/nightly_report_日期.md`，AI 终端据此补写 wiki 可信/待确认
- 清理 30 天前的查询日志

## 任务提示词

这是我对你（AI 终端）发出的指令

我需要一个可以创建多个知识管理系统的程序它叫 chatKMS，使用这个软件需要严格遵守我上面的服务设计及选型设计，并且你需要上网寻找类似资料参考并研究，优化我的设计，但是服务设计不可以偏离

## 应用目录设计

### chatKMS 应用（代码，独立部署）

```
chatKMS\    ← 应用根目录
├── readme.md                 ← 项目说明（简洁）
├── mcp.json                  ← MCP 客户端接入配置（Claude Code 等在项目根自动发现）
├── kb_create.py              ← 交互式创建知识库工作区
├── config/                   ← 配置文件
│   └── config.yaml           ← 工作区注册表（workspace + active_kb；sources 由 .kms 提供）
├── env/                      ← 环境依赖
│   ├── requirements.txt      ← 核心依赖
│   ├── requirements-semantic.txt ← 可选语义引擎依赖
│   └── pyproject.toml
├── design/                   ← 设计文档与评审
│   ├── chatkms设计稿.md
│   ├── 知识管理系统搭建.md
│   ├── 评审记录.md           ← 独立设计评审记录（见"设计评审机制"）
│   └── 踩坑记录.md
├── sample/default/           ← 基础工作空间模板（示例，用户可改，创建工作区的底座）
├── tests/self_check.py       ← 全逻辑自检（独立目录，不污染根逻辑）
└── backend/                  ← 后端逻辑（Python）
    ├── mcp_server.py         ← MCP stdio 服务入口
    ├── kb_manager.py         ← 工作区生命周期
    ├── rag.py                ← 双引擎 RAG（bm25/semantic）
    ├── sources.py            ← 源文件层只读访问
    ├── wiki.py               ← wiki 优先层
    ├── semantic.py           ← 语义检索引擎（可选）
    ├── nightly_build.py      ← 夜间构建
    └── config.py             ← 配置加载/保存
```

后端默认使用 Python 框架。

## 设计评审机制

chatKMS 的设计变更须经过**独立评审**，评审由 AI 以独立个体身份维护，纳入项目运转：

1. **评审记录**：`design/评审记录.md` 持续维护，每次设计/代码变更后由评审方增补一条（问题、严重度、处置状态）。
2. **独立立场**：评审方不预设"代码即合理"，须指出设计或评审自身的不合理之处（过度设计、误报、被高估的风险等），并记录在案。
3. **自检门槛**：任何涉及代码的改动，必须能通过 `tests/self_check.py`；评审把"自检不通过"列为 P0。
4. **处置闭环**：每个发现标注 已修复 / 已评估暂缓（给出理由）/ 待处理，变更记录写明缘由。

## 复用规则

当你每完成一份工作后，再该目录的同级目录下，生成一个知识管理系统搭建.md 文档，内部记载实现方法，帮助下一个用户及 AI 终端复用搭建整套环境，当你完成所有的工作后，将整个环境依赖打包进 APP 里面，用户使用文件后可以一键安装完成，后端的逻辑需要与前端分离，后端逻辑默认使用 python 框架，如果你有更好的设计，或者不得不用其他框架的理由说明出来

## 踩坑记录

每当你遇到问题时，回顾并总结到踩坑记录.md 里面

## 界面设计

该版本无需设计界面，所有的配置基于文件生成，AI 终端参与读取
