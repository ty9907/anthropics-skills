"""
仓库哨兵（Repo Sentinel）- 节点三辅助：提取扫描发现

从单仓库扫描报告（XLSX）中提取 Findings 工作表的数据，
输出为 JSON 格式，供 sub-agent 逐条/逐批读取和分析。

分析仅基于扫描报告中的匹配代码片段及上下文，不依赖原始代码文件。

使用方式：
    python -m scripts.extract_findings --report ./workspace/reports/repo1_scan.xlsx
    python -m scripts.extract_findings --report ./workspace/reports/repo1_scan.xlsx --batch-size 5
"""

import argparse
import json
import sys
from pathlib import Path

from utils import write_status


def extract_findings(report_path: str, batch_size: int = 0, retry_failed: bool = False) -> list[dict]:
    """
    从 XLSX 扫描报告中提取 Findings 数据

    参数：
        report_path: 单仓库扫描报告文件路径
        batch_size: 批次大小。0 表示不分批，输出全部 Finding
        retry_failed: 是否仅提取未分析或分析失败的记录

    返回：
        Finding 字典列表，每个字典包含所有列的值
    """
    from openpyxl import load_workbook

    report = Path(report_path)
    if not report.exists():
        print(f"错误：报告文件不存在: {report}")
        return []

    wb = load_workbook(str(report), read_only=True)

    if "Findings" not in wb.sheetnames:
        print("错误：报告中没有 Findings 工作表")
        wb.close()
        return []

    ws = wb["Findings"]
    headers = [cell.value for cell in ws[1]]

    findings = []
    for row in ws.iter_rows(min_row=2):
        row_dict = {}
        for i, cell in enumerate(row):
            if i < len(headers) and headers[i]:
                row_dict[headers[i]] = cell.value
        if not row_dict:
            continue

        if retry_failed:
            status = row_dict.get("is_sensitive")
            if status not in (None, "", "未知", "Unknown"):
                continue

        findings.append(row_dict)

    wb.close()
    return findings


def format_finding_for_analysis(finding: dict, index: int) -> str:
    """
    将单条 Finding 格式化为供 sub-agent 分析的结构化文本

    分析仅基于扫描报告中的匹配代码片段和上下文，不依赖原始代码文件。

    参数：
        finding: 单条扫描发现记录
        index: Finding 索引号

    返回：
        格式化的分析提示文本
    """
    parts = []
    parts.append("## 发现详情")
    parts.append(f"- 索引: {index}")
    parts.append(f"- 仓库: {finding.get('repo_name', '未知')}")
    parts.append(f"- 文件: {finding.get('file_path', '未知')}")
    parts.append(f"- 行号: {finding.get('line_start', '?')}-{finding.get('line_end', '?')}")
    parts.append(f"- 来源类型: {finding.get('source_type', '未知')}")
    parts.append(f"- 匹配规则: {finding.get('rule_id', '未知')} - {finding.get('rule_description', '无描述')}")
    parts.append(f"- 规则类型: {finding.get('rule_type', '未知')}")
    parts.append(f"- 规则组: {finding.get('rule_group', '未知')}")
    parts.append(f"- 规则定义的风险等级: {finding.get('risk_level', 'Medium')}")
    parts.append("")
    parts.append("## 匹配内容（来自扫描报告）")
    context_before = finding.get("context_before", "")
    context_after = finding.get("context_after", "")
    matched = finding.get("matched_content", "")
    if context_before:
        parts.append(str(context_before))
    parts.append(f">>> {matched} <<<")
    if context_after:
        parts.append(str(context_after))
    parts.append("")
    parts.append("## 分析任务")
    parts.append("请仅基于以上扫描报告中的匹配代码片段及上下文进行分析，不要访问原始代码文件。")
    parts.append("1. 这是否是真实的敏感信息泄露？（是/否）")
    parts.append("   - 考虑：这是测试固件、示例配置、文档还是真实凭证？")
    parts.append("   - 该值是占位符（如 \"YOUR_API_KEY_HERE\"）还是真实密钥？")
    parts.append("2. 如果是，实际风险等级是什么？（高/中/低）")
    parts.append("   - 高：活跃凭证、私钥、生产环境密钥")
    parts.append("   - 中：内部 URL、可能被复用的测试凭证、个人隐私信息")
    parts.append("   - 低：开发配置、非敏感元数据、公开信息")
    parts.append("3. 提供你的分析依据。")
    parts.append("4. 如果确认为敏感信息，提供处理建议。")
    parts.append("")
    parts.append('请严格按照以下 JSON 格式回复，不要包含其他内容：')
    parts.append('{"index": <索引>, "is_sensitive": "是/否", "confirmed_risk_level": "High/Medium/Low", "llm_reasoning": "分析依据", "recommendation": "处理建议"}')

    return "\n".join(parts)


def main(args: list[str] | None = None) -> None:
    """
    主入口：提取 Finding 并输出 JSON

    输出格式：
    - 不分批模式：输出完整 JSON 数组到 stdout 或文件
    - 分批模式：每批输出为独立 JSON 文件
    """
    parser = argparse.ArgumentParser(description="提取扫描发现数据")
    parser.add_argument("--report", required=True, help="单仓库扫描报告 XLSX 文件路径")
    parser.add_argument("--batch-size", type=int, default=0, help="每批 Finding 数量（0=不分批）")
    parser.add_argument("--output", help="输出 JSON 文件路径（不分批模式，默认 stdout）")
    parser.add_argument("--output-dir", help="分批输出目录（分批模式）")
    parser.add_argument("--retry-failed", action="store_true", help="仅提取未分析或分析失败的记录")
    parser.add_argument("--format", choices=["raw", "analysis"], default="raw",
                        help="输出格式：raw=原始数据，analysis=分析提示格式")

    parsed = parser.parse_args(args)
    report_path = Path(parsed.report)

    if not report_path.exists():
        print(f"错误：报告文件不存在: {report_path}", file=sys.stderr)
        sys.exit(1)

    findings = extract_findings(str(report_path), parsed.batch_size, parsed.retry_failed)

    if not findings:
        print("没有需要分析的发现记录。")
        if parsed.output:
            Path(parsed.output).write_text("[]", encoding="utf-8")
        else:
            print("[]")
        return

    print(f"共提取 {len(findings)} 条发现记录")

    if parsed.format == "analysis":
        output_data = []
        for i, f in enumerate(findings):
            output_data.append({
                "index": i,
                "finding": f,
                "analysis_prompt": format_finding_for_analysis(f, i),
            })
    else:
        output_data = findings

    if parsed.batch_size > 0 and parsed.output_dir:
        output_dir = Path(parsed.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        total_batches = (len(output_data) + parsed.batch_size - 1) // parsed.batch_size
        for batch_idx in range(total_batches):
            start = batch_idx * parsed.batch_size
            end = min(start + parsed.batch_size, len(output_data))
            batch_data = output_data[start:end]

            batch_file = output_dir / f"batch_{batch_idx + 1:03d}.json"
            batch_file.write_text(
                json.dumps(batch_data, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            print(f"  批次 {batch_idx + 1}/{total_batches}: {len(batch_data)} 条 -> {batch_file}")
    elif parsed.output:
        output_file = Path(parsed.output)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(
            json.dumps(output_data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"已输出到: {output_file}")
    else:
        print(json.dumps(output_data, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
