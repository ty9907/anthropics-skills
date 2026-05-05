"""
仓库哨兵（Repo Sentinel）- 节点二：敏感信息扫描

单仓库模式：--repo-name 指定单个仓库，扫描完成后立即输出该仓库的完整扫描报告。
每个 subagent 独立负责一个仓库的完整流程（下载→扫描→分析），扫描报告即时生成。

规则文件格式适配：
- 规则组：规则分类（如"密钥"）
- 规则/模式列：正则表达式或纯文本
- 规则类型：regex（正则匹配）或 text（纯文本匹配）
- 检测描述：规则描述

扫描范围：
- 代码文件（文本文件）
- 提交历史记录
- 标签元数据
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import utils
from utils import (
    get_commit_log,
    get_tag_info,
    iso_now,
    is_text_file,
    write_status,
)


COLUMNS = [
    "repo_name",
    "source_type",
    "file_path",
    "line_start",
    "line_end",
    "matched_content",
    "context_before",
    "context_after",
    "rule_id",
    "rule_group",
    "rule_type",
    "rule_description",
    "risk_level",
    "category",
    "scan_timestamp",
]


def _is_header_row(values: list) -> bool:
    """
    判断一行是否为表头行

    综合检查多列的值来判断：
    - 如果第1列包含"规则组"等表头关键词，视为表头行
    - 如果第2列包含"规则"、"模式"等描述性关键词，视为表头行
    - 如果第4列包含"检测描述"等表头关键词，视为表头行
    """
    if not values or len(values) < 2:
        return False

    header_keywords = ["规则组", "规则/模式", "规则类型", "检测描述", "rule", "pattern", "type", "description"]

    for val in values:
        if val is None:
            continue
        val_str = str(val).strip().lower()
        for kw in header_keywords:
            if kw.lower() in val_str:
                return True
    return False


def load_rules(rules_path: str, counter_start: int = 0) -> tuple[list[dict], int]:
    """
    从 XLSX 文件加载扫描规则

    适配新的规则文件格式：
    - 第1列：规则组（分类）
    - 第2列：规则/模式（正则表达式或纯文本）
    - 第3列：规则类型（regex 或 text）
    - 第4列：检测描述

    自动判断第1行是否为表头行，自动生成 rule_id

    参数：
        rules_path: 规则文件路径
        counter_start: rule_id 计数器起始值（用于多文件合并时避免 ID 冲突）

    返回：
        (规则列表, 最终计数器值) 的元组
    """
    from openpyxl import load_workbook

    wb = load_workbook(rules_path, read_only=True)
    ws = wb.active
    all_rows = list(ws.iter_rows(min_row=1))
    wb.close()

    if not all_rows:
        return [], counter_start

    first_row_values = [cell.value for cell in all_rows[0]]
    if _is_header_row(first_row_values):
        data_rows = all_rows[1:]
    else:
        data_rows = all_rows

    rules = []
    rule_counter = counter_start
    for row in data_rows:
        values = [cell.value for cell in row]
        if not values or len(values) < 2 or not values[1]:
            continue

        rule_group = str(values[0]).strip() if values[0] else "general"
        pattern = str(values[1]).strip()
        rule_type = str(values[2]).strip().lower() if len(values) > 2 and values[2] else "regex"
        description = str(values[3]).strip() if len(values) > 3 and values[3] else ""

        if rule_type not in ("regex", "text"):
            rule_type = "regex"

        if rule_type == "regex":
            try:
                re.compile(pattern)
            except re.error as e:
                print(f"  警告: 规则正则表达式无效，已跳过 - {description}: {e}")
                continue

        rule_counter += 1
        rule_id = f"SEC-{rule_counter:03d}"

        rules.append({
            "rule_id": rule_id,
            "rule_group": rule_group,
            "pattern": pattern,
            "rule_type": rule_type,
            "description": description,
            "risk_level": "Medium",
            "category": rule_group,
        })

    return rules, rule_counter


SUPPORTED_RULE_EXTENSIONS = {".xlsx", ".xls", ".csv", ".json"}


def load_rules_from_dir(rules_dir: str) -> list[dict]:
    """
    从目录批量加载所有规则文件

    扫描指定目录下的所有支持格式的规则文件，按文件名排序后依次加载，
    合并为统一的规则列表。rule_id 跨文件递增，确保全局唯一。

    支持的文件格式：
    - .xlsx / .xls：Excel 规则文件（调用 load_rules）
    - .csv：CSV 规则文件（逗号分隔，格式同 XLSX 列定义）
    - .json：JSON 规则文件（数组格式，每项包含 rule_group/pattern/rule_type/description 字段）

    参数：
        rules_dir: 规则文件目录路径

    返回：
        合并后的规则列表
    """
    import csv as csv_module

    rules_path = Path(rules_dir)
    if not rules_path.is_dir():
        print(f"  警告: 规则目录不存在或不是目录: {rules_dir}")
        return []

    all_rules = []
    rule_counter = 0

    rule_files = sorted(
        [f for f in rules_path.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_RULE_EXTENSIONS],
        key=lambda f: f.name,
    )

    if not rule_files:
        print(f"  警告: 规则目录中没有找到规则文件: {rules_dir}")
        return []

    print(f"  发现 {len(rule_files)} 个规则文件")

    for rule_file in rule_files:
        ext = rule_file.suffix.lower()
        file_rules = []

        if ext in (".xlsx", ".xls"):
            try:
                file_rules, rule_counter = load_rules(str(rule_file), counter_start=rule_counter)
                print(f"    [{rule_file.name}] 加载 {len(file_rules)} 条规则（Excel）")
            except Exception as e:
                print(f"    [{rule_file.name}] 加载失败: {e}")

        elif ext == ".csv":
            try:
                with open(rule_file, "r", encoding="utf-8") as f:
                    reader = csv_module.reader(f)
                    rows = list(reader)

                if not rows:
                    continue

                if _is_header_row(rows[0]):
                    rows = rows[1:]

                for row in rows:
                    if not row or len(row) < 2 or not row[1].strip():
                        continue

                    rule_group = row[0].strip() if row[0] else "general"
                    pattern = row[1].strip()
                    rule_type = row[2].strip().lower() if len(row) > 2 and row[2].strip() else "regex"
                    description = row[3].strip() if len(row) > 3 and row[3].strip() else ""

                    if rule_type not in ("regex", "text"):
                        rule_type = "regex"

                    if rule_type == "regex":
                        try:
                            re.compile(pattern)
                        except re.error as e:
                            print(f"  警告: [{rule_file.name}] 规则正则表达式无效，已跳过 - {description}: {e}")
                            continue

                    rule_counter += 1
                    rule_id = f"SEC-{rule_counter:03d}"

                    file_rules.append({
                        "rule_id": rule_id,
                        "rule_group": rule_group,
                        "pattern": pattern,
                        "rule_type": rule_type,
                        "description": description,
                        "risk_level": "Medium",
                        "category": rule_group,
                    })

                print(f"    [{rule_file.name}] 加载 {len(file_rules)} 条规则（CSV）")
            except Exception as e:
                print(f"    [{rule_file.name}] 加载失败: {e}")

        elif ext == ".json":
            try:
                with open(rule_file, "r", encoding="utf-8") as f:
                    json_rules = json.load(f)

                if not isinstance(json_rules, list):
                    print(f"    [{rule_file.name}] JSON 根元素不是数组，已跳过")
                    continue

                for item in json_rules:
                    pattern = item.get("pattern", "")
                    if not pattern:
                        continue

                    rule_group = item.get("rule_group", item.get("group", "general"))
                    rule_type = item.get("rule_type", item.get("type", "regex")).lower()
                    description = item.get("description", item.get("desc", ""))

                    if rule_type not in ("regex", "text"):
                        rule_type = "regex"

                    if rule_type == "regex":
                        try:
                            re.compile(pattern)
                        except re.error as e:
                            print(f"  警告: [{rule_file.name}] 规则正则表达式无效，已跳过 - {description}: {e}")
                            continue

                    rule_counter += 1
                    rule_id = f"SEC-{rule_counter:03d}"

                    file_rules.append({
                        "rule_id": rule_id,
                        "rule_group": rule_group,
                        "pattern": pattern,
                        "rule_type": rule_type,
                        "description": description,
                        "risk_level": item.get("risk_level", "Medium"),
                        "category": item.get("category", rule_group),
                    })

                print(f"    [{rule_file.name}] 加载 {len(file_rules)} 条规则（JSON）")
            except Exception as e:
                print(f"    [{rule_file.name}] 加载失败: {e}")

        all_rules.extend(file_rules)

    return all_rules


def resolve_rules(rules_arg: str) -> list[dict]:
    """
    解析规则参数，自动判断是文件还是目录

    - 如果是目录路径：调用 load_rules_from_dir 批量加载
    - 如果是文件路径：调用 load_rules 加载单个文件
    - 支持逗号分隔的多文件路径

    参数：
        rules_arg: --rules 参数值（文件路径、目录路径或逗号分隔的多文件路径）

    返回：
        合并后的规则列表
    """
    rules_path = Path(rules_arg)

    if rules_path.is_dir():
        print(f"从规则目录加载: {rules_arg}")
        return load_rules_from_dir(rules_arg)

    if "," in rules_arg:
        all_rules = []
        rule_counter = 0
        for part in rules_arg.split(","):
            part = part.strip()
            if not part:
                continue
            path = Path(part)
            if path.is_dir():
                dir_rules = load_rules_from_dir(part)
                all_rules.extend(dir_rules)
                if dir_rules:
                    max_id = max(int(r["rule_id"].split("-")[1]) for r in dir_rules)
                    rule_counter = max(rule_counter, max_id)
            elif path.is_file():
                file_rules, rule_counter = load_rules(part, counter_start=rule_counter)
                all_rules.extend(file_rules)
            else:
                print(f"  警告: 规则路径不存在: {part}")
        return all_rules

    if rules_path.is_file():
        rules, _ = load_rules(rules_arg)
        return rules

    print(f"错误：规则路径不存在: {rules_arg}")
    return []


def _match_text(pattern: str, text: str) -> list[str]:
    """
    纯文本匹配：在 text 中查找所有 pattern 的出现

    参数：
        pattern: 要查找的纯文本
        text: 被搜索的文本

    返回：
        所有匹配到的文本片段列表
    """
    matches = []
    start = 0
    while True:
        idx = text.find(pattern, start)
        if idx == -1:
            break
        matches.append(pattern)
        start = idx + 1
    return matches


def scan_file(filepath: Path, rules: list[dict], repo_name: str) -> list[dict]:
    """
    扫描单个文本文件中的敏感信息

    逐行检查文件内容，根据规则类型使用正则表达式或纯文本匹配。
    对匹配到的内容，提取上下文（前后3行）并记录详细位置信息。
    """
    findings = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (OSError, PermissionError):
        return findings

    for rule in rules:
        if rule["rule_type"] == "regex":
            try:
                compiled = re.compile(rule["pattern"])
            except re.error:
                continue

            for i, line in enumerate(lines):
                match = compiled.search(line)
                if match:
                    matched_content = match.group(0)
                    before_start = max(0, i - 3)
                    after_end = min(len(lines), i + 4)
                    context_before = "".join(lines[before_start:i])
                    context_after = "".join(lines[i + 1:after_end])

                    findings.append({
                        "repo_name": repo_name,
                        "source_type": "code_file",
                        "file_path": str(filepath),
                        "line_start": i + 1,
                        "line_end": i + 1,
                        "matched_content": matched_content,
                        "context_before": context_before.strip(),
                        "context_after": context_after.strip(),
                        "rule_id": rule["rule_id"],
                        "rule_group": rule["rule_group"],
                        "rule_type": rule["rule_type"],
                        "rule_description": rule["description"],
                        "risk_level": rule["risk_level"],
                        "category": rule["category"],
                        "scan_timestamp": iso_now(),
                    })
        elif rule["rule_type"] == "text":
            for i, line in enumerate(lines):
                matches = _match_text(rule["pattern"], line)
                for matched_content in matches:
                    before_start = max(0, i - 3)
                    after_end = min(len(lines), i + 4)
                    context_before = "".join(lines[before_start:i])
                    context_after = "".join(lines[i + 1:after_end])

                    findings.append({
                        "repo_name": repo_name,
                        "source_type": "code_file",
                        "file_path": str(filepath),
                        "line_start": i + 1,
                        "line_end": i + 1,
                        "matched_content": matched_content,
                        "context_before": context_before.strip(),
                        "context_after": context_after.strip(),
                        "rule_id": rule["rule_id"],
                        "rule_group": rule["rule_group"],
                        "rule_type": rule["rule_type"],
                        "rule_description": rule["description"],
                        "risk_level": rule["risk_level"],
                        "category": rule["category"],
                        "scan_timestamp": iso_now(),
                    })

    return findings


def scan_commits(mirror_path: Path, rules: list[dict], repo_name: str) -> list[dict]:
    """
    扫描提交历史中的敏感信息

    将每条提交的信息、作者名、作者邮箱拼接后，
    根据规则类型使用正则表达式或纯文本匹配。
    """
    findings = []
    commits = get_commit_log(mirror_path)
    for commit in commits:
        text = f"{commit['message']} {commit['author_name']} {commit['author_email']}"
        for rule in rules:
            matched = False
            matched_content = ""

            if rule["rule_type"] == "regex":
                try:
                    compiled = re.compile(rule["pattern"])
                except re.error:
                    continue
                match = compiled.search(text)
                if match:
                    matched = True
                    matched_content = match.group(0)
            elif rule["rule_type"] == "text":
                matches = _match_text(rule["pattern"], text)
                if matches:
                    matched = True
                    matched_content = matches[0]

            if matched:
                findings.append({
                    "repo_name": repo_name,
                    "source_type": "commit_message",
                    "file_path": f"commit:{commit['hash']}",
                    "line_start": 1,
                    "line_end": 1,
                    "matched_content": matched_content,
                    "context_before": f"Author: {commit['author_name']} <{commit['author_email']}>",
                    "context_after": f"Date: {commit['timestamp']}",
                    "rule_id": rule["rule_id"],
                    "rule_group": rule["rule_group"],
                    "rule_type": rule["rule_type"],
                    "rule_description": rule["description"],
                    "risk_level": rule["risk_level"],
                    "category": rule["category"],
                    "scan_timestamp": iso_now(),
                })
    return findings


def scan_tags(mirror_path: Path, rules: list[dict], repo_name: str) -> list[dict]:
    """
    扫描标签元数据中的敏感信息

    将标签名、标签信息、标签者名称/邮箱拼接后，
    根据规则类型使用正则表达式或纯文本匹配。
    """
    findings = []
    tags = get_tag_info(mirror_path)
    for tag in tags:
        text = f"{tag['name']} {tag.get('message', '')} {tag.get('tagger_name', '')} {tag.get('tagger_email', '')}"
        for rule in rules:
            matched = False
            matched_content = ""

            if rule["rule_type"] == "regex":
                try:
                    compiled = re.compile(rule["pattern"])
                except re.error:
                    continue
                match = compiled.search(text)
                if match:
                    matched = True
                    matched_content = match.group(0)
            elif rule["rule_type"] == "text":
                matches = _match_text(rule["pattern"], text)
                if matches:
                    matched = True
                    matched_content = matches[0]

            if matched:
                findings.append({
                    "repo_name": repo_name,
                    "source_type": "tag_metadata",
                    "file_path": f"tag:{tag['name']}",
                    "line_start": 1,
                    "line_end": 1,
                    "matched_content": matched_content,
                    "context_before": f"Tag: {tag['name']} Type: {tag.get('object_type', '')}",
                    "context_after": f"Tagger: {tag.get('tagger_name', '')} Date: {tag.get('tagger_date', '')}",
                    "rule_id": rule["rule_id"],
                    "rule_group": rule["rule_group"],
                    "rule_type": rule["rule_type"],
                    "rule_description": rule["description"],
                    "risk_level": rule["risk_level"],
                    "category": rule["category"],
                    "scan_timestamp": iso_now(),
                })
    return findings


def write_report(findings: list[dict], output_path: str, repo_name: str = "") -> None:
    """
    生成 XLSX 格式的扫描报告

    每个仓库扫描完成后立即调用此函数生成独立报告。
    报告包含以下工作表：
    - Findings：所有匹配记录
    - By Risk Level：按风险等级分组
    - By File Type：按文件类型分组
    - By Category：按规则分类分组
    - Summary：统计汇总（含仓库名称标识）

    参数：
        findings: 扫描发现列表
        output_path: 输出文件路径
        repo_name: 仓库名称（用于 Summary 标识）
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()

    ws_findings = wb.active
    ws_findings.title = "Findings"
    ws_findings.append(COLUMNS)
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font_white = Font(bold=True, color="FFFFFF")
    for cell in ws_findings[1]:
        cell.font = header_font_white
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for f in findings:
        ws_findings.append([f.get(col, "") for col in COLUMNS])

    for col_idx in range(1, len(COLUMNS) + 1):
        col_letter = ws_findings.cell(row=1, column=col_idx).column_letter
        ws_findings.column_dimensions[col_letter].width = 20

    risk_fill = {
        "High": PatternFill(start_color="FF4444", end_color="FF4444", fill_type="solid"),
        "Medium": PatternFill(start_color="FFA500", end_color="FFA500", fill_type="solid"),
        "Low": PatternFill(start_color="44AA44", end_color="44AA44", fill_type="solid"),
    }
    risk_col_idx = COLUMNS.index("risk_level") + 1
    for row in ws_findings.iter_rows(min_row=2, min_col=risk_col_idx, max_col=risk_col_idx):
        for cell in row:
            if cell.value in risk_fill:
                cell.fill = risk_fill[cell.value]
                cell.font = Font(color="FFFFFF", bold=True)

    by_risk = defaultdict(list)
    by_filetype = defaultdict(list)
    by_category = defaultdict(list)
    for f in findings:
        by_risk[f.get("risk_level", "Unknown")].append(f)
        ext = Path(f.get("file_path", "")).suffix.lower() or "unknown"
        by_filetype[ext].append(f)
        by_category[f.get("category", "unknown")].append(f)

    def _write_pivot(ws, title, groups, key_name):
        ws.append([key_name, "Count", "High", "Medium", "Low"])
        for cell in ws[1]:
            cell.font = header_font_white
            cell.fill = header_fill
        for key, items in sorted(groups.items()):
            high = sum(1 for i in items if i.get("risk_level") == "High")
            medium = sum(1 for i in items if i.get("risk_level") == "Medium")
            low = sum(1 for i in items if i.get("risk_level") == "Low")
            ws.append([key, len(items), high, medium, low])

    ws_risk = wb.create_sheet("By Risk Level")
    _write_pivot(ws_risk, "By Risk Level", by_risk, "Risk Level")

    ws_filetype = wb.create_sheet("By File Type")
    _write_pivot(ws_filetype, "By File Type", by_filetype, "File Type")

    ws_category = wb.create_sheet("By Category")
    _write_pivot(ws_category, "By Category", by_category, "Category")

    ws_summary = wb.create_sheet("Summary")
    ws_summary.append(["Metric", "Value"])
    ws_summary.append(["Repository", repo_name])
    ws_summary.append(["Total Findings", len(findings)])
    ws_summary.append(["High Risk", len(by_risk.get("High", []))])
    ws_summary.append(["Medium Risk", len(by_risk.get("Medium", []))])
    ws_summary.append(["Low Risk", len(by_risk.get("Low", []))])
    ws_summary.append(["Unique Files", len(set(f.get("file_path") for f in findings))])
    ws_summary.append(["Unique Rules Matched", len(set(f.get("rule_id") for f in findings))])
    ws_summary.append(["Scan Completed", iso_now()])
    for cell in ws_summary[1]:
        cell.font = header_font_white
        cell.fill = header_fill

    wb.save(output_path)


def scan_single_repo(workspace: str, rules: list[dict], repo_name: str, output_path: str) -> list[dict]:
    """
    扫描单个仓库并立即生成扫描报告

    参数：
        workspace: 工作目录
        rules: 扫描规则列表
        repo_name: 仓库名称
        output_path: 输出报告路径

    返回：
        扫描发现列表
    """
    repos_dir = Path(workspace) / "repos"
    mirrors_dir = Path(workspace) / "mirrors"
    repo_dir = repos_dir / repo_name
    mirror_path = mirrors_dir / f"{repo_name}.git"

    if not repo_dir.exists():
        print(f"错误：指定仓库不存在: {repo_dir}")
        return []

    all_findings: list[dict] = []

    print(f"  正在扫描仓库: {repo_name}")

    code_files = []
    for filepath in repo_dir.rglob("*"):
        if filepath.is_file() and is_text_file(filepath):
            code_files.append(filepath)

    print(f"    待扫描文件: {len(code_files)} 个")

    for filepath in code_files:
        try:
            file_findings = scan_file(filepath, rules, repo_name)
            all_findings.extend(file_findings)
        except Exception as e:
            print(f"    扫描文件出错 {filepath}: {e}")

    print(f"    代码文件: {len(all_findings)} 条发现")

    if mirror_path.exists():
        try:
            commit_findings = scan_commits(mirror_path, rules, repo_name)
            all_findings.extend(commit_findings)
            print(f"    提交历史: {len(commit_findings)} 条发现")
        except Exception as e:
            print(f"    扫描提交历史出错: {e}")

        try:
            tag_findings = scan_tags(mirror_path, rules, repo_name)
            all_findings.extend(tag_findings)
            print(f"    标签元数据: {len(tag_findings)} 条发现")
        except Exception as e:
            print(f"    扫描标签出错: {e}")

    write_report(all_findings, output_path, repo_name)
    print(f"    扫描报告已生成: {output_path}")

    return all_findings


def main(args: list[str] | None = None) -> None:
    """
    节点二主入口：敏感信息扫描

    单仓库模式（推荐）：--repo-name 指定单个仓库，扫描完成后立即生成该仓库的独立报告。
    每个仓库的扫描报告独立输出，无需等待其他仓库。
    """
    parser = argparse.ArgumentParser(description="节点二：敏感信息扫描")
    parser.add_argument("--workspace", required=True, help="工作目录")
    parser.add_argument("--rules", required=True, help="扫描规则文件路径或规则目录路径（支持 .xlsx/.xls/.csv/.json 格式，目录下所有规则文件将批量加载）")
    parser.add_argument("--repo-name", required=True, help="仓库名称")
    parser.add_argument("--output", default="", help="输出报告路径（默认: workspace/reports/<repo_name>_scan.xlsx）")

    parsed = parser.parse_args(args)
    workspace = Path(parsed.workspace)
    reports_dir = workspace / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    status_path = workspace / "scan_status.json"

    print("正在加载扫描规则...")
    rules = resolve_rules(parsed.rules)
    print(f"已加载 {len(rules)} 条规则")

    if not rules:
        print("错误：没有加载到有效规则")
        write_status(status_path, "failed", errors=["No valid rules"])
        sys.exit(1)

    repo_name = parsed.repo_name

    if parsed.output:
        output_path = parsed.output
    else:
        output_path = str(reports_dir / f"{repo_name}_scan.xlsx")

    write_status(status_path, "in_progress", repo=repo_name)

    findings = scan_single_repo(str(workspace), rules, repo_name, output_path)

    if findings:
        write_status(status_path, "completed", repo=repo_name, findings_count=len(findings))
    else:
        write_status(status_path, "completed", repo=repo_name, findings_count=0)

    print(f"\n仓库 {repo_name} 扫描完成: {len(findings)} 条发现")
    print(f"扫描报告: {output_path}")


if __name__ == "__main__":
    main()
