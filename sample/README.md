# 基础工作空间模板（示例文件夹，可修改）

chatKMS 创建任何知识库工作区时，都以 `sample/default/` 为基础复制生成。
**你可以直接改这里**：修改 AGENTS.md 措辞、增删结构文件，所有之后创建的工作区都会继承这些改动。

## 机制
1. 创建时先复制本目录到目标工作区（`shutil.copytree`）。
2. 对复制后所有 `.md`/`.txt`/`.kms` 文件做占位符替换：`{{name}}` → 实际知识库名。
3. 再由 chatKMS 生成动态部分：`{库名}.kms`（name/description/sources）、`log/` 首条、git init。

## 必需结构（由代码兜底保证，不会因模板删改而缺失）
`wiki/可信/`、`wiki/待确认/`、`log/`、`.rag/`
——你仍可在模板里增加额外目录/文件。

## 换成自定义模板
在 `chatKMS/config/config.yaml` 设置：
```yaml
template_dir: D:\我的自定义模板目录
```
不设置（或空）时使用本示例目录。
