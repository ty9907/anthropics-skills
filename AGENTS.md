# AGENTS.md - Anthropic Skills 技能仓库

## 概述

本仓库包含 Claude 技能（Claude Skills）—— 一种自包含的指令集，用于教导 Claude 执行特定任务。**这不是一个软件应用程序**—— 它是一组可复用的技能定义。

## 仓库结构

```
skills/
├── docx/          # Word 文档创建/编辑（专有）
├── pdf/           # PDF 处理（专有）
├── pptx/          # PowerPoint 生成（专有）
├── xlsx/          # Excel/电子表格处理（专有）
├── skill-creator/ # 创建新技能的工具
├── mcp-builder/   # MCP 服务器生成
├── claude-api/    # Claude API/SDK 文档
├── canvas-design/ # HTML5 Canvas 视觉设计
├── algorithmic-art/
├── webapp-testing/
├── theme-factory/
└── ...            # 其他示例技能
```

## 技能包插件

`.claude-plugin/marketplace.json` 定义了三个可安装的插件：
- `document-skills` - docx、pdf、pptx、xlsx 文档技能
- `example-skills` - 创意和技术示例技能
- `claude-api` - API 参考技能

## 添加新技能

1. 在 `skills/` 目录下创建文件夹
2. 添加包含 YAML 头信息的 `SKILL.md` 文件：
   ```markdown
   ---
   name: my-skill
   description: 何时使用此技能及其功能描述
   ---

   # 我的技能
   [此处添加指令]
   ```
3. 可选：添加 scripts、resources、LICENSE.txt

## 关键模式

- **必需的头信息**：YAML 头信息中必须包含 `name` 和 `description` 字段
- **自包含**：每个技能包含所有需要的指令
- **脚本技能**：部分技能包含 Python/JS 脚本（如 `skills/docx/scripts/`、`skills/xlsx/scripts/`）
- **专有技能**：docx、pdf、pptx、xlsx 包含 LICENSE.txt - 源码可用，非开源

## 非构建项目

本仓库没有构建系统、测试或 CI。技能通过 Claude Code 的 `/plugin install` 或 Claude.ai 的技能系统直接加载。

---

> 本仓库是从 [anthropics/skills](https://github.com/anthropics/skills) fork 的副本。
> 原仓库地址：https://github.com/anthropics/skills
> Fork 后的仓库地址：https://github.com/ty9907/anthropics-skills