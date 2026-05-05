---
name: repo-sentinel
description: 并行下载GitHub仓库、敏感信息扫描和大模型分析确认。当用户需要克隆仓库、扫描代码中的密钥/凭证/个人隐私信息、或对仓库进行安全合规审计时触发。适用于仓库安全审计、代码泄露检测、跨多仓库敏感数据发现等任务。
---

# Repo Sentinel（仓库哨兵）

每个仓库由独立 subagent 完成全流程（下载→扫描→分析），独立输出最终分析报告。

## 运行配置

以下配置项控制 Agent 编排任务时的行为，修改后立即生效，无需更改脚本代码。

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `parallelism` | `2` | 同时运行的最大 subagent 数量。控制下载、扫描阶段的并行度。Agent 将仓库列表按此值分批，每批并行执行，批内完成后启动下一批。设为 `1` 即串行执行。 |
| `git_clone_timeout` | `600` | 单个仓库 git clone 超时时间（秒），适用于 git clone 命令的 --timeout 参数 |
| `git_clone_retry` | `2` | git clone 失败后最大重试次数 |
| `analysis_batch_size` | `10` | 单次分析 Finding 的批量大小（0=不分批） |

**使用方式**：Agent 在编排任务前读取此配置表，按 `parallelism` 值决定并行 subagent 数量。如需调整，直接修改上表中的默认值即可。

## OpenCode 环境适配

> **⚠️ 本章节内容仅在 OpenCode 环境下生效。非 OpenCode 环境（如 Claude Code、Trae AI 等）请忽略本章节全部内容，继续使用原有编排方式。**

### 环境检测

Agent 在执行编排前，应先判断当前运行环境是否为 OpenCode。检测方法：

- **OpenCode 环境**：Agent 可使用 `task()` 工具（即 `task(subagent_type="general", description="...")` 调用方式可用）
- **非 OpenCode 环境**：Agent 无 `task()` 工具，需通过并行运行终端命令或串行执行来模拟 subagent 行为

如果检测到当前环境支持 `task()` 工具，则按本章节后续描述执行；否则跳过本章节，使用原有编排指令。

### OpenCode task() 工具说明

在 OpenCode 环境中，通过 `task()` 工具创建 subagent 来并行处理多个仓库。每个 subagent 是一个独立的 AI 代理，拥有自己的上下文和工具访问权限。

**工具签名**：

```
task(subagent_type="general", description="<任务描述>")
```

**参数说明**：

| 参数 | 值 | 说明 |
|------|-----|------|
| `subagent_type` | `"general"` | **必须固定为 `"general"`**。指定 subagent 类型为通用型，使其具备完整的代码执行、文件读写和推理分析能力 |
| `description` | 任务描述字符串 | 传递给 subagent 的完整指令，包含该仓库需要执行的全部步骤（下载→扫描→提取→分析→写回） |

**关键约束**：
- `subagent_type` 必须设为 `"general"`，不可使用其他类型，否则 subagent 可能缺少必要的工具权限
- 每个 `task()` 调用创建一个独立 subagent，拥有独立的工作上下文
- subagent 之间互不依赖、互不通信，各自独立输出报告
- 同一批次内的 `task()` 调用并行执行，批次间串行等待

### OpenCode 并行编排模式

```
parallelism=2 时，使用 task() 分批创建 subagent：

批次1: task(subagent_type="general", description="仓库1完整流程指令")
       task(subagent_type="general", description="仓库2完整流程指令")
       → 等待批次1全部完成

批次2: task(subagent_type="general", description="仓库3完整流程指令")
       task(subagent_type="general", description="仓库4完整流程指令")
       → 等待批次2全部完成
...以此类推
```

### subagent description 模板

每个 subagent 的 `description` 应包含该仓库的完整流程指令，确保 subagent 无需回读 SKILL.md 即可独立执行：

```
你是仓库哨兵（Repo Sentinel）的 subagent，负责完成以下仓库的完整安全扫描流程。

## 目标仓库
- 仓库 URL: {repo_url}
- 仓库名称: {repo_name}
- 工作目录: {workspace}
- 规则文件: {rules_path}

## 执行步骤

### 步骤1：下载仓库
执行命令（直接使用 git clone，无需 Python 脚本）：

```bash
# 创建 mirrors 和 repos 目录
mkdir -p {workspace}/mirrors {workspace}/repos

# 镜像完整仓库（所有分支、标签、提交历史）
git clone --mirror {repo_url} {workspace}/mirrors/{repo_name}.git

# 创建工作副本
cd {workspace}/repos
git clone {repo_url} {workspace}/repos/{repo_name}
```

### 步骤2：扫描仓库
执行命令：
python -m scripts.scan_sensitive --workspace {workspace} --rules {rules_path} --repo-name {repo_name}

注意：{rules_path} 可以是规则文件路径或规则目录路径。如果是目录，将自动加载目录下所有规则文件。

### 步骤3：提取 Finding 数据
执行命令：
python -m scripts.extract_findings --report {workspace}/reports/{repo_name}_scan.xlsx --format analysis --batch-size {analysis_batch_size} --output {workspace}/reports/{repo_name}_findings.json

### 步骤4：分析每条 Finding
读取 {workspace}/reports/{repo_name}_findings.json，逐条分析每条 Finding。
仅基于扫描报告中的匹配代码片段及上下文进行判断，不要访问原始代码文件。

对每条 Finding，判断：
1. 这是否是真实的敏感信息泄露？（是/否）
2. 如果是，实际风险等级是什么？（高/中/低）
3. 提供分析依据。
4. 如果确认为敏感信息，提供处理建议。

每条 Finding 的分析结果格式：
{"index": <索引>, "is_sensitive": "是/否", "confirmed_risk_level": "High/Medium/Low", "llm_reasoning": "分析依据", "recommendation": "处理建议"}

### 步骤5：写回分析结果
将所有分析结果汇总为 JSON 数组，保存至 {workspace}/reports/{repo_name}_analysis_results.json，然后执行：
python -m scripts.write_analysis --report {workspace}/reports/{repo_name}_scan.xlsx --results-file {workspace}/reports/{repo_name}_analysis_results.json --workspace {workspace}

## 完成标志
当 {workspace}/reports/{repo_name}_analysis.xlsx 生成后，任务完成。汇报最终结果摘要。
```

## 架构总览

### Subagent 并行模式

每个仓库由一个独立的 subagent 完成从下载到分析的全部流程，各 subagent 完全独立、互不依赖。并行度由 `parallelism` 配置项控制（默认 2）。

```
parallelism=2 时，10个仓库分5批执行：

批次1: Subagent 1: [下载 repo1] → [扫描 repo1] → [分析 repo1] → repo1_analysis.xlsx
       Subagent 2: [下载 repo2] → [扫描 repo2] → [分析 repo2] → repo2_analysis.xlsx
批次2: Subagent 3: [下载 repo3] → [扫描 repo3] → [分析 repo3] → repo3_analysis.xlsx
       Subagent 4: [下载 repo4] → [扫描 repo4] → [分析 repo4] → repo4_analysis.xlsx
...以此类推
```

**关键特性**：
- 每个 subagent 独立完成一个仓库的完整流程（下载→扫描→分析）
- 并行度由 SKILL.md 中 `parallelism` 配置项控制，无需修改脚本代码
- 扫描完成后立即生成该仓库的扫描报告，无需等待其他仓库
- 分析仅基于扫描报告中的匹配代码片段及上下文，不依赖原始代码文件
- 每个仓库独立输出最终分析报告，无汇总合并步骤
- 无需外部 API Key，分析由 Agent 自身推理能力完成

## 快速开始

```bash
# 安装扫描和分析脚本的依赖（下载步骤使用原生 git clone，无需 Python 依赖）
pip install -r scripts/requirements.txt
```

## Subagent 并行编排指令

> **⚠️ 核心原则：每个 subagent 必须原子性地完成一个仓库的全部流程（下载→扫描→提取→分析→写回），禁止将流程拆分到不同 subagent 或按节点阶段批量执行。**

### 第一步：读取配置

Agent 在编排任务前，必须先读取上方「运行配置」表中的参数值，特别是 `parallelism`（并行度）。后续所有编排行为均以此配置为准。

### 第二步：准备输入文件

读取用户提供的仓库列表和规则文件，确认文件存在且格式正确。

### 第三步：按仓库维度分批创建 subagent

**编排原则**：以仓库为最小调度单位，每个 subagent 绑定一个仓库并完成其全部生命周期。**绝不允许**按节点阶段（如"先下载所有仓库，再扫描所有仓库"）组织执行。

将仓库列表按 `parallelism` 值分批，每批最多 `parallelism` 个 subagent 并行执行。每批全部完成后，再启动下一批。

**正确的编排方式**（按仓库维度）：

```
parallelism=2，4个仓库：

批次1: Subagent A: repo1 下载→扫描→提取→分析→写回 → repo1_analysis.xlsx  ✅
       Subagent B: repo2 下载→扫描→提取→分析→写回 → repo2_analysis.xlsx  ✅
       → 等待批次1全部完成

批次2: Subagent C: repo3 下载→扫描→提取→分析→写回 → repo3_analysis.xlsx  ✅
       Subagent D: repo4 下载→扫描→提取→分析→写回 → repo4_analysis.xlsx  ✅
```

**❌ 错误的编排方式**（按节点阶段，必须避免）：

```
阶段1: 下载所有仓库（repo1, repo2, repo3, repo4）  ← 错误！
阶段2: 扫描所有仓库                                  ← 错误！
阶段3: 分析所有仓库                                  ← 错误！
```

**分批示例**（parallelism=2，10个仓库）：
- 批次1：repo1, repo2（并行，每个完成全流程）
- 批次2：repo3, repo4（并行，每个完成全流程）
- 批次3：repo5, repo6（并行，每个完成全流程）
- 批次4：repo7, repo8（并行，每个完成全流程）
- 批次5：repo9, repo10（并行，每个完成全流程）

#### 非OpenCode环境（默认）

每个仓库由一个独立的执行序列完成全流程。Agent 对每个仓库依次执行下载→扫描→提取→分析→写回，同批次内的仓库并行启动。**一个仓库的全部步骤必须在同一个执行序列中连续完成，不得中断去处理其他仓库。**

#### OpenCode环境

> **⚠️ 以下内容仅在 OpenCode 环境下适用。非 OpenCode 环境请使用上方默认方式。**

在 OpenCode 环境中，使用 `task(subagent_type="general")` 工具为每个仓库创建独立 subagent。每个 subagent 接收完整的流程指令（下载→扫描→提取→分析→写回），独立执行并输出报告。

**编排逻辑**：

1. 将仓库列表按 `parallelism` 值分批
2. 对每批中的每个仓库，调用 `task(subagent_type="general", description="<该仓库的完整流程指令>")` 
3. 同一批次内的 `task()` 调用并行发出
4. 等待当前批次所有 subagent 完成后，再启动下一批次
5. 所有批次完成后，汇总各仓库报告路径

**OpenCode task() 调用示例**（parallelism=2，批次1）：

```
# 同时创建2个 subagent，每个负责一个仓库的完整流程
task(subagent_type="general", description="你是仓库哨兵的 subagent...\n## 目标仓库\n- 仓库 URL: https://github.com/keycloak/keycloak.git\n- 仓库名称: keycloak_keycloak\n- 工作目录: /path/to/workspace\n- 规则文件: /path/to/rules\n\n## 执行步骤\n### 步骤1：下载仓库\n...")

task(subagent_type="general", description="你是仓库哨兵的 subagent...\n## 目标仓库\n- 仓库 URL: https://github.com/jeecgboot/JeecgBoot.git\n- 仓库名称: jeecgboot_JeecgBoot\n- 工作目录: /path/to/workspace\n- 规则文件: /path/to/rules\n\n## 执行步骤\n### 步骤1：下载仓库\n...")
```

> **注意**：`description` 参数的完整模板见「OpenCode 环境适配 → subagent description 模板」章节。Agent 应将模板中的 `{repo_url}`、`{repo_name}`、`{workspace}`、`{rules_path}` 等占位符替换为实际值后传入。

### 第四步：每个 subagent 的完整执行流程

以下步骤是**单个 subagent 处理单个仓库时必须连续完成的全部操作**，不得拆分或与其他仓库的操作交替执行。

#### 4.1 下载仓库

直接使用 `git clone` 命令下载，无需 Python 脚本：

```bash
# 创建 mirrors 和 repos 目录
mkdir -p <工作目录>/mirrors <工作目录>/repos

# 镜像完整仓库（包含所有分支、标签和提交历史）
git clone --mirror <仓库URL> <工作目录>/mirrors/<仓库名称>.git

# 创建工作副本
git clone <仓库URL> <工作目录>/repos/<仓库名称>
```

**重试逻辑**：如果 clone 失败，subagent 应重复尝试（最多 {git_clone_retry} 次），每次等待 5 秒后重试。

**仓库名称提取规则**：从 URL 路径部分提取，如 `https://github.com/org/my-repo` → `org_my-repo`

#### 4.2 扫描仓库（立即生成扫描报告）

```bash
python -m scripts.scan_sensitive \
  --workspace <工作目录> \
  --rules <规则文件路径或规则目录路径> \
  --repo-name <仓库名称>
```

`--rules` 参数支持以下三种输入方式：
- **规则目录路径**（推荐）：指向 `rules/` 目录，自动加载目录下所有规则文件（`.xlsx`、`.xls`、`.csv`、`.json`）
- **单个规则文件路径**：指向单个规则文件
- **逗号分隔的多文件路径**：如 `rules1.xlsx,rules2.json`

扫描完成后立即输出 `workspace/reports/<repo_name>_scan.xlsx`，无需等待其他仓库。

#### 4.3 提取 Finding 数据

```bash
python -m scripts.extract_findings \
  --report <工作目录>/reports/<repo_name>_scan.xlsx \
  --format analysis \
  --batch-size <analysis_batch_size> \
  --output <工作目录>/reports/<repo_name>_findings.json
```

#### 4.4 分析每条 Finding

每个 sub-agent 读取 Finding 数据，**仅基于扫描报告中的匹配代码片段及上下文进行分析**，不访问原始代码文件。

**非OpenCode环境**：Agent 自身逐条读取 Finding JSON 并进行推理分析，将结果汇总后通过步骤 4.5 写回。

**OpenCode环境**：

> **⚠️ 以下内容仅在 OpenCode 环境下适用。非 OpenCode 环境请使用上方默认方式。**

在 OpenCode 环境中，分析阶段由第三步中创建的 subagent **在其完整流程内自行完成**，无需额外创建分析专用 subagent。subagent 在执行完步骤 4.3（提取 Finding）后，直接读取 findings.json 并逐条分析，然后将结果保存为 analysis_results.json，再执行步骤 4.5（写回）。**分析是 subagent 生命周期的一部分，不得交由其他 subagent 或主 Agent 处理。**

如果 Finding 数量较多（超过 `analysis_batch_size`），subagent 可分批分析，每批处理 `analysis_batch_size` 条，所有批次结果合并后统一写回。

**单条 Finding 分析指令**（sub-agent 收到如下格式的数据）：

```
## 发现详情
- 索引: {index}
- 仓库: {repo_name}
- 文件: {file_path}
- 行号: {line_start}-{line_end}
- 来源类型: {source_type}
- 匹配规则: {rule_id} - {rule_description}
- 规则类型: {rule_type}
- 规则组: {rule_group}
- 规则定义的风险等级: {risk_level}

## 匹配内容（来自扫描报告）
{context_before}
>>> {matched_content} <<<
{context_after}

## 分析任务
请仅基于以上扫描报告中的匹配代码片段及上下文进行分析，不要访问原始代码文件。
1. 这是否是真实的敏感信息泄露？（是/否）
2. 如果是，实际风险等级是什么？（高/中/低）
3. 提供你的分析依据。
4. 如果确认为敏感信息，提供处理建议。

请严格按照以下 JSON 格式回复，不要包含其他内容：
{"index": <索引>, "is_sensitive": "是/否", "confirmed_risk_level": "High/Medium/Low", "llm_reasoning": "分析依据", "recommendation": "处理建议"}
```

**Sub-agent 返回格式**：

```json
{
  "index": 0,
  "is_sensitive": "是",
  "confirmed_risk_level": "High",
  "llm_reasoning": "该文件包含硬编码的 AWS Access Key ID（AKIA...），非占位符格式，属于真实凭证泄露。",
  "recommendation": "立即轮换该 AWS Access Key，检查 CloudTrail 日志确认是否已被滥用。"
}
```

#### 4.5 写回分析结果并生成最终分析报告

```bash
# 批量写回（推荐：所有 sub-agent 完成后汇总写入）
python -m scripts.write_analysis \
  --report <工作目录>/reports/<repo_name>_scan.xlsx \
  --results-file <工作目录>/reports/<repo_name>_analysis_results.json \
  --workspace <工作目录>
```

执行后自动生成最终分析报告：`workspace/reports/<repo_name>_analysis.xlsx`

## 节点一：仓库下载

### 功能说明
下载 GitHub 仓库，包含完整历史、所有分支和标签。

### 运行方式

```bash
# 1. 镜像完整仓库（所有分支、标签、提交历史）
git clone --mirror https://github.com/org/repo ./workspace/mirrors/org_repo.git

# 2. 创建工作副本
git clone https://github.com/org/repo ./workspace/repos/org_repo
```

### 认证方式

**公开仓库**：无需认证，直接 clone。

**私有仓库**：使用 `GIT_ASKPASS` 环境变量传递 token：

```bash
# 创建一个 askpass 脚本
echo '#!/bin/sh
echo "$GIT_TOKEN"' > git-askpass.sh
chmod +x git-askpass.sh

# 设置环境变量并执行 clone
export GIT_ASKPASS=./git-askpass.sh
export GIT_TOKEN=ghp_xxxxxxxxxxxx
git clone --mirror https://github.com/org/private-repo ./workspace/mirrors/org_private-repo.git
```

### 行为说明

- **完整克隆**：使用 `git clone --mirror` 捕获所有分支、标签和提交历史
- **工作副本**：直接从远程 clone 工作副本（不依赖 mirror，两者各自独立下载可并行执行）
- **认证机制**：公开仓库无需 token。私有仓库通过 `GIT_ASKPASS` 传递 token
- **超时设置**：使用 git 的 `--timeout` 参数（单位：秒），默认每个仓库 600 秒
- **错误处理**：subagent 应实现重试逻辑，失败时等待 5 秒后重试
- **已有目录处理**：如果 `mirrors/<name>.git` 或 `repos/<name>` 已存在且非空，跳过该步骤

### 输出目录结构

```
workspace/
├── mirrors/
│   ├── repo1.git/          # 裸镜像（所有分支/标签/历史）
│   └── repo2.git/
└── repos/
    ├── repo1/              # 工作副本
    └── repo2/
```

## 节点二：敏感信息扫描

### 功能说明
对下载的代码文件、提交信息和标签元数据，按照规则文件进行扫描。扫描完成后立即生成该仓库的独立扫描报告。

### 运行方式

```bash
# 指定规则目录（推荐，自动加载目录下所有规则文件）
python -m scripts.scan_sensitive \
  --workspace ./workspace \
  --rules ./rules \
  --repo-name org_my-repo

# 指定单个规则文件
python -m scripts.scan_sensitive \
  --workspace ./workspace \
  --rules ./rules/default_rules.xlsx \
  --repo-name org_my-repo

# 指定多个规则文件（逗号分隔）
python -m scripts.scan_sensitive \
  --workspace ./workspace \
  --rules "./rules/rules1.xlsx,./rules/rules2.json" \
  --repo-name org_my-repo
```

输出至 `workspace/reports/org_my-repo_scan.xlsx`

### 规则文件目录结构

规则文件统一放置在 `rules/` 目录下，支持以下格式：

```
rules/
├── default_rules.xlsx    # 默认规则文件（Excel）
├── custom_rules.csv     # CSV 规则文件
└── extra_rules.json     # JSON 规则文件
```

**支持的文件格式**：

| 格式 | 扩展名 | 说明 |
|------|--------|------|
| Excel | `.xlsx` / `.xls` | 列定义同下方 XLSX 格式表 |
| CSV | `.csv` | 逗号分隔，列定义同 XLSX 格式表 |
| JSON | `.json` | 数组格式，每项包含 `rule_group`、`pattern`、`rule_type`、`description` 字段 |

**JSON 规则文件示例**：

```json
[
  {
    "rule_group": "密钥",
    "pattern": "(?i)(api_key)\\s*[:=]\\s*\\S+",
    "rule_type": "regex",
    "description": "检测 API Key 字段赋值"
  }
]
```

### 规则 XLSX 格式

| 列序号 | 列名 | 是否必填 | 说明 | 示例 |
|--------|------|----------|------|------|
| 1 | 规则组 | 否 | 规则分类 | `密钥` |
| 2 | 规则/模式 | 是 | 正则表达式或纯文本 | `(?i)(password)\s*[:=]\s*\S+` |
| 3 | 规则类型 | 否 | `regex`（正则匹配）或 `text`（纯文本匹配），默认 regex | `regex` |
| 4 | 检测描述 | 否 | 规则描述 | `检测密码字段赋值` |

**规则类型说明**：
- `regex`：使用 Python `re` 正则表达式匹配，适合模式检测
- `text`：纯文本匹配（大小写敏感），适合检测固定字符串如特定密钥前缀

**rule_id 自动生成**：格式为 `SEC-001`、`SEC-002`...，按行号递增

### 扫描范围

1. **代码文件**：`workspace/repos/` 下的所有文本文件，二进制文件自动跳过
2. **提交历史**：所有分支的提交信息、作者名称/邮箱和时间戳
3. **标签元数据**：所有标签的名称、信息和关联提交信息

### 匹配记录字段

| 字段 | 说明 |
|------|------|
| repo_name | 仓库名称 |
| source_type | code_file / commit_message / tag_metadata |
| file_path | 被扫描文件的本地完整路径 |
| line_start / line_end | 匹配起始/结束行号 |
| matched_content | 匹配的具体内容 |
| context_before / context_after | 前后3行上下文 |
| rule_id | 自动生成的规则ID（SEC-001等） |
| rule_group | 规则组 |
| rule_type | 规则类型（regex / text） |
| rule_description | 检测描述 |
| risk_level | 风险等级（默认 Medium） |
| category | 分类（同规则组） |
| scan_timestamp | 扫描时间戳 |

## 节点三：Sub-agent 分析确认

### 设计理念

- 分析仅基于扫描报告（xlsx）中的匹配代码片段及上下文，不依赖原始代码文件
- 由 Claude/sub-agent 直接分析，无需调用外部 LLM API
- 每个仓库独立完成分析，独立输出最终分析报告
- 无汇总合并步骤

### OpenCode 环境说明

> **⚠️ 以下内容仅在 OpenCode 环境下适用。非 OpenCode 环境请忽略，继续使用原有方式。**

在 OpenCode 环境中，节点三的分析由 `task(subagent_type="general")` 创建的 subagent **在其完整流程内自行完成**。主 Agent 无需为分析阶段单独创建 subagent，也无需在 subagent 之间传递中间数据。**分析是 subagent 生命周期的一部分，不得拆分为独立阶段或交由其他 subagent 处理。**

**OpenCode 环境下的分析执行方式**：

1. subagent 执行完步骤 4.3（提取 Finding）后，直接读取 findings.json
2. subagent 利用自身推理能力逐条分析每条 Finding
3. subagent 将所有分析结果汇总为 JSON 数组，保存至 `analysis_results.json`
4. subagent 执行步骤 4.5（写回分析结果并生成最终报告）
5. subagent 完成后向主 Agent 汇报结果摘要

**与默认方式的差异**：

| 方面 | 默认方式 | OpenCode 方式 |
|------|----------|---------------|
| 分析执行者 | 主 Agent 自身 | subagent（task 创建） |
| Finding 数据传递 | 主 Agent 读取 JSON | subagent 自行读取 |
| 分析结果写回 | 主 Agent 调用脚本 | subagent 自行调用脚本 |
| 并行分析 | 不支持（单 Agent 串行） | 支持（多个 subagent 并行分析不同仓库） |

### 辅助脚本

| 脚本 | 功能 |
|------|------|
| `extract_findings.py` | 从单仓库扫描报告提取 Finding 数据，输出 JSON 供 sub-agent 读取 |
| `write_analysis.py` | 将分析结果写回扫描报告，并生成最终分析报告 |

### 分析流程

1. **提取**：`extract_findings.py` 读取单仓库扫描报告，输出 Finding JSON
2. **分析**：Sub-agent 读取 Finding，仅基于扫描报告中的匹配代码片段及上下文判断是否为真实泄露
3. **写回**：`write_analysis.py` 将分析结果写入扫描报告，并生成最终分析报告

### 新增列

| 列名 | 说明 |
|------|------|
| is_sensitive | 是 / 否 / 未知 |
| confirmed_risk_level | 高 / 中 / 低（sub-agent 判定） |
| llm_reasoning | 分析依据 |
| recommendation | 处理建议 |
| analysis_timestamp | 分析时间戳 |

## 报告格式

### 扫描报告（`<repo_name>_scan.xlsx`）

| 工作表 | 内容 |
|--------|------|
| Findings | 所有匹配记录 |
| By Risk Level | 按风险等级分组统计 |
| By File Type | 按文件类型分组统计 |
| By Category | 按规则分类分组统计 |
| Summary | 统计汇总（含仓库名称标识） |

### 最终分析报告（`<repo_name>_analysis.xlsx`）

| 工作表 | 内容 |
|--------|------|
| Findings | 所有匹配记录及分析确认结果 |
| Confirmed Sensitive | 确认为敏感信息的记录（红色表头） |
| By Risk Level | 按确认风险等级分组统计 |
| By Category | 按规则分类分组统计 |
| Summary | 统计汇总（含仓库名称、确认统计、处理建议汇总） |

## 状态跟踪

每个节点写入状态文件：

- `workspace/scan_status.json` — 节点二
- `workspace/analysis_status.json` — 节点三

有效状态值：`not_started`（未开始）、`in_progress`（进行中）、`completed`（已完成）、`failed`（失败）。

> **注意**：节点一（仓库下载）使用 git clone 直接执行，不产生状态文件。subagent 应通过检查 `repos/<name>/` 目录是否存在来确认下载是否完成。

## 错误恢复

重试失败的操作：

```bash
# 重试失败的下载：删除失败的 mirrors/<name>.git 和 repos/<name>，重新执行 git clone
rm -rf ./workspace/mirrors/<repo_name>.git ./workspace/repos/<repo_name>
git clone --mirror <仓库URL> ./workspace/mirrors/<repo_name>.git
git clone <仓库URL> ./workspace/repos/<repo_name>

# 重试失败的扫描
python -m scripts.scan_sensitive --workspace ./workspace --rules ./rules --repo-name <repo_name>

# 重试失败的分析（重新提取未分析的 Finding）
python -m scripts.extract_findings --report ./workspace/reports/repo1_scan.xlsx --retry-failed --format analysis
```

## 参考文件

- `references/user_guide.md` — 详细用户文档
