# 仓库哨兵（Repo Sentinel）- 用户操作指南

## 环境配置

### 前置条件

- Python 3.10 或更高版本
- Git 2.30 或更高版本
- pip 包管理器

### 安装依赖

```bash
cd <技能目录>
pip install -r scripts/requirements.txt
```

### 验证安装

```bash
python -c "from scripts.utils import iso_now; print(iso_now())"
git --version
```

## 架构说明

### Subagent 并行模式

每个仓库由一个独立的 subagent 完成从下载到分析的全部流程，各 subagent 完全独立、互不依赖。

```
Subagent 1: [下载 repo1] → [扫描 repo1] → repo1_scan.xlsx → [分析 repo1] → repo1_analysis.xlsx
Subagent 2: [下载 repo2] → [扫描 repo2] → repo2_scan.xlsx → [分析 repo2] → repo2_analysis.xlsx
Subagent 3: [下载 repo3] → [扫描 repo3] → repo3_scan.xlsx → [分析 repo3] → repo3_analysis.xlsx
```

**关键特性**：
- 每个 subagent 独立完成一个仓库的完整流程（下载→扫描→分析）
- 扫描完成后立即生成该仓库的扫描报告，无需等待其他仓库
- 分析仅基于扫描报告中的匹配代码片段及上下文，不依赖原始代码文件
- 每个仓库独立输出最终分析报告，无汇总合并步骤
- 无需外部 API Key，分析由 Agent 自身推理能力完成

## 使用步骤

### 第一步：准备输入文件

#### 规则 XLSX 文件（扫描规则）

创建一个包含以下列的 XLSX 文件：

| 列序号 | 列名 | 是否必填 | 说明 | 示例 |
|--------|------|----------|------|------|
| 1 | 规则组 | 否 | 规则分类 | `密钥` |
| 2 | 规则/模式 | 是 | 正则表达式或纯文本 | `(?i)(password)\s*[:=]\s*\S+` |
| 3 | 规则类型 | 否 | `regex` 或 `text`，默认 regex | `regex` |
| 4 | 检测描述 | 否 | 规则描述 | `检测密码字段赋值` |

**规则类型说明**：
- `regex`：使用 Python `re` 正则表达式匹配，适合模式检测（如密码字段、API密钥格式）
- `text`：纯文本匹配（大小写敏感），适合检测固定字符串（如特定密钥前缀、已知泄露值）

**rule_id 自动生成**：格式为 `SEC-001`、`SEC-002`...，按行号递增

示例规则：

| 规则组 | 规则/模式 | 规则类型 | 检测描述 |
|--------|-----------|----------|----------|
| 密钥 | `(?i)(password|passwd|pwd)\s*[:=]\s*\S+` | regex | 检测常见密码字段赋值 |
| 密钥 | `\b(?:access_)?token\s*[:=]\s*(\S+|".*?"|'.*?')` | regex | 检测 Token 赋值 |
| 密钥 | `-----BEGIN (?:RSA|EC|OPENSSH|DSA) PRIVATE KEY-----` | regex | 检测私钥文件头 |
| 密钥 | `\b(ghp|gho|ghu|ghs)_[a-zA-Z0-9]{36}\b` | regex | 检测 GitHub 个人访问令牌 |
| 密钥 | `\bAKIA[0-9A-Z]{16}\b` | regex | 检测 AWS 访问密钥 |
| 密钥 | `eyJ[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+` | regex | 检测 JWT |
| 密钥 | `\b(?:mysql|postgresql|mongodb|redis|sqlserver)://[^:]+:([^@]+)@` | regex | 检测数据库连接字符串中的密码 |
| 凭证 | `AKIAIOSFODNN7EXAMPLE` | text | 检测已知的 AWS 示例密钥 |

### 第二步：为每个仓库运行完整流程

每个仓库由独立的 subagent 执行以下完整流程：

#### 2.1 下载仓库

```bash
python -m scripts.download_repos \
  --repo-url "https://github.com/org/repo1" \
  --token "ghp_xxx" \
  --output-dir ./workspace \
  --force
```

**仓库名称提取规则**：从 URL 路径部分提取，如 `https://github.com/org/my-repo` → `org_my-repo`

**参数说明**：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| --repo-url | - | 单个仓库 URL |
| --token | - | GitHub 令牌（公开仓库可省略） |
| --output-dir | - | 输出工作目录 |
| --timeout | 1800 | 每个仓库的超时时间（秒） |
| --retry-count | 2 | 每个仓库的重试次数 |
| --force | false | 强制覆盖已有目录 |

#### 2.2 扫描仓库（立即生成扫描报告）

```bash
python -m scripts.scan_sensitive \
  --workspace ./workspace \
  --rules ./rules \
  --repo-name org_repo1
```

扫描完成后立即输出 `workspace/reports/org_repo1_scan.xlsx`，无需等待其他仓库。

`--rules` 参数支持以下三种输入方式：
- **规则目录路径**（推荐）：指向 `rules/` 目录，自动加载目录下所有规则文件（`.xlsx`、`.xls`、`.csv`、`.json`）
- **单个规则文件路径**：指向单个规则文件
- **逗号分隔的多文件路径**：如 `rules1.xlsx,rules2.json`

**参数说明**：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| --workspace | - | 工作目录 |
| --rules | - | 扫描规则文件路径或规则目录路径（支持 .xlsx/.xls/.csv/.json 格式） |
| --repo-name | - | 仓库名称 |
| --output | 自动 | 输出报告路径（默认: workspace/reports/\<repo_name\>_scan.xlsx） |

#### 2.3 提取 Finding 数据

```bash
python -m scripts.extract_findings \
  --report ./workspace/reports/org_repo1_scan.xlsx \
  --format analysis \
  --output ./workspace/reports/org_repo1_findings.json
```

**参数说明**：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| --report | - | 单仓库扫描报告 XLSX 文件路径 |
| --batch-size | 0 | 每批 Finding 数量（0=不分批） |
| --output | stdout | 输出 JSON 文件路径 |
| --output-dir | - | 分批输出目录（分批模式） |
| --retry-failed | false | 仅提取未分析或分析失败的记录 |
| --format | raw | 输出格式：raw=原始数据，analysis=含分析提示 |

#### 2.4 Sub-agent 分析每条 Finding

每个 sub-agent 读取 Finding 数据，**仅基于扫描报告中的匹配代码片段及上下文进行分析**，不访问原始代码文件。

**单条 Finding 分析指令**（sub-agent 收到如下格式的数据）：

```
## 发现详情
- 索引: 0
- 仓库: org_repo1
- 文件: /path/to/file.py
- 行号: 10-10
- 来源类型: code_file
- 匹配规则: SEC-001 - 检测密码字段赋值
- 规则类型: regex
- 规则组: 密钥
- 规则定义的风险等级: Medium

## 匹配内容（来自扫描报告）
config = {}
>>> password=secret123 <<<
db.connect()

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

#### 2.5 写回分析结果并生成最终分析报告

```bash
# 批量写回（推荐：所有 sub-agent 完成后汇总写入）
python -m scripts.write_analysis \
  --report ./workspace/reports/org_repo1_scan.xlsx \
  --results-file ./workspace/reports/org_repo1_analysis_results.json \
  --workspace ./workspace
```

执行后自动生成最终分析报告：`workspace/reports/org_repo1_analysis.xlsx`

**参数说明**：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| --report | - | 单仓库扫描报告 XLSX 文件路径 |
| --index | - | 单条模式：Finding 索引（从0开始） |
| --result | - | 单条模式：分析结果 JSON 字符串 |
| --results-file | - | 批量模式：分析结果 JSON 文件路径 |
| --workspace | - | 工作目录（用于写状态文件和生成最终报告） |

### 第三步：查看结果

每个仓库独立生成两份报告：

#### 扫描报告（`<repo_name>_scan.xlsx`）

| 工作表 | 内容 |
|--------|------|
| Findings | 所有匹配记录 |
| By Risk Level | 按风险等级分组统计 |
| By File Type | 按文件类型分组统计 |
| By Category | 按规则分类分组统计 |
| Summary | 统计汇总（含仓库名称标识） |

#### 最终分析报告（`<repo_name>_analysis.xlsx`）

| 工作表 | 内容 |
|--------|------|
| Findings | 所有匹配记录及分析确认结果 |
| Confirmed Sensitive | 确认为敏感信息的记录（红色表头） |
| By Risk Level | 按确认风险等级分组统计 |
| By Category | 按规则分类分组统计 |
| Summary | 统计汇总（含仓库名称、确认统计、处理建议汇总） |

**Findings 工作表新增列（分析后）**：

| 列名 | 说明 |
|------|------|
| is_sensitive | 是 / 否 / 未知 |
| confirmed_risk_level | 高 / 中 / 低（sub-agent 判定） |
| llm_reasoning | 分析依据 |
| recommendation | 处理建议 |
| analysis_timestamp | 分析时间戳 |

## 完整示例

### 单仓库完整流程

```bash
# 1. 下载仓库
python -m scripts.download_repos \
  --repo-url "https://github.com/org/my-repo" \
  --output-dir ./workspace \
  --force

# 2. 扫描仓库（立即生成扫描报告）
python -m scripts.scan_sensitive \
  --workspace ./workspace \
  --rules ./rules \
  --repo-name org_my-repo

# 3. 提取 Finding 数据
python -m scripts.extract_findings \
  --report ./workspace/reports/org_my-repo_scan.xlsx \
  --format analysis \
  --output ./workspace/reports/org_my-repo_findings.json

# 4. Sub-agent 分析 Finding（由 Claude 直接完成，无需脚本）

# 5. 写回分析结果并生成最终分析报告
python -m scripts.write_analysis \
  --report ./workspace/reports/org_my-repo_scan.xlsx \
  --results-file ./workspace/reports/org_my-repo_analysis_results.json \
  --workspace ./workspace
```

### 多仓库并行流程（Subagent 编排）

为每个仓库启动独立的 subagent，各 subagent 执行上述完整流程：

```
Subagent 1: repo1 → 下载 → 扫描 → repo1_scan.xlsx → 分析 → repo1_analysis.xlsx
Subagent 2: repo2 → 下载 → 扫描 → repo2_scan.xlsx → 分析 → repo2_analysis.xlsx
Subagent 3: repo3 → 下载 → 扫描 → repo3_scan.xlsx → 分析 → repo3_analysis.xlsx
```

每个 subagent 完成后立即输出该仓库的分析报告，无需等待其他仓库。

### 使用流水线入口（单仓库快捷方式）

```bash
python -m scripts.utils pipeline \
  --repo-url "https://github.com/org/my-repo" \
  --rules ./rules \
  --output-dir ./workspace \
  --force
```

此命令自动执行下载→扫描→提取 Finding，节点三的分析由 Agent 直接完成。

## 输出目录结构

```
workspace/
├── mirrors/
│   ├── org_repo1.git/          # 裸镜像（所有分支/标签/历史）
│   └── org_repo2.git/
├── repos/
│   ├── org_repo1/              # 工作副本
│   └── org_repo2/
├── reports/
│   ├── org_repo1_scan.xlsx     # 仓库1扫描报告
│   ├── org_repo1_findings.json # 仓库1 Finding 数据
│   ├── org_repo1_analysis.xlsx # 仓库1最终分析报告
│   ├── org_repo2_scan.xlsx     # 仓库2扫描报告
│   ├── org_repo2_findings.json # 仓库2 Finding 数据
│   └── org_repo2_analysis.xlsx # 仓库2最终分析报告
├── download_status.json        # 下载状态
├── scan_status.json            # 扫描状态
└── analysis_status.json        # 分析状态
```

## 错误恢复

### 重试失败的下载

```bash
python -m scripts.download_repos \
  --repo-url "https://github.com/org/repo" \
  --output-dir ./workspace \
  --retry-failed
```

### 重试失败的分析

```bash
python -m scripts.extract_findings \
  --report ./workspace/reports/org_repo1_scan.xlsx \
  --retry-failed \
  --format analysis \
  --output ./workspace/reports/org_repo1_findings_retry.json
```

### 强制重新下载

```bash
python -m scripts.download_repos \
  --repo-url "https://github.com/org/repo" \
  --output-dir ./workspace \
  --force
```

## 常见问题解答

**问：如何获取 GitHub 个人访问令牌？**
答：前往 GitHub Settings > Developer settings > Personal access tokens > Generate new token。选择 `repo` 权限范围即可访问私有仓库。

**问：下载超时了怎么办？**
答：该仓库会被标记为失败并记录错误详情。你可以使用 `--retry-failed` 重试，或增大 `--timeout` 值。

**问：可以只扫描单个仓库吗？**
答：可以，使用 `--repo-name` 参数指定仓库名称：
```bash
python -m scripts.scan_sensitive --workspace ./workspace --rules ./rules --repo-name org_repo
```

**问：二进制文件如何处理？**
答：二进制文件会被自动检测并跳过，仅扫描文本文件。

**问：regex 和 text 两种规则类型有什么区别？**
答：`regex` 使用 Python 正则表达式匹配，适合检测模式（如 `password=xxx`）；`text` 使用纯文本查找（大小写敏感），适合检测已知固定字符串（如特定的泄露密钥值）。

**问：节点三为什么不用脚本调用外部 LLM API？**
答：因为技能运行在 LLM Agent（Claude）上下文中，Agent 自身具备推理能力，直接分析 Finding 更高效，且无需管理外部 API Key。

**问：分析过程是否依赖原始代码文件？**
答：不依赖。分析仅基于扫描报告（xlsx）中的匹配代码片段及上下文，sub-agent 无需访问原始代码文件。

**问：每个仓库的报告是独立的吗？**
答：是的。每个仓库独立生成扫描报告和最终分析报告，无需等待其他仓库处理完成，也没有汇总合并步骤。

**问：sub-agent 分析 Finding 时需要什么格式的输入？**
答：使用 `extract_findings.py --format analysis` 提取的数据已包含结构化的分析提示，sub-agent 直接读取并分析即可。

**问：如何重试分析失败的 Finding？**
答：使用 `--retry-failed` 参数重新提取未分析的记录：
```bash
python -m scripts.extract_findings --report ./workspace/reports/org_repo1_scan.xlsx --retry-failed --format analysis
```

**问：如何添加自定义扫描规则？**
答：在规则 XLSX 文件中添加新行，填入规则组、规则/模式、规则类型和检测描述即可。`regex` 类型支持 Python `re` 语法，`text` 类型直接进行纯文本匹配。

**问：最终分析报告包含哪些内容？**
答：最终分析报告包含：所有匹配记录及分析确认结果（Findings）、确认为敏感信息的记录（Confirmed Sensitive）、按确认风险等级分组统计（By Risk Level）、按规则分类分组统计（By Category）、统计汇总含仓库名称和处理建议（Summary）。
