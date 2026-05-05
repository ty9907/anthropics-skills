"""
仓库哨兵（Repo Sentinel）- 共享工具模块

提供各节点共用的工具函数，包括：
- 状态文件读写（write_status / read_status）
- Git 命令执行（run_git）
- 仓库信息查询（分支、标签、提交历史）
- 文件类型检测
- 单仓库完整流水线编排入口
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def iso_now() -> str:
    """返回当前 UTC 时间的 ISO 8601 格式字符串"""
    return datetime.now(timezone.utc).isoformat()


def write_status(status_path: Path, status: str, **kwargs: Any) -> None:
    """
    写入节点执行状态文件

    如果状态文件已存在，则合并更新（保留已有字段，覆盖同名字段）。
    状态值：not_started / in_progress / completed / failed

    参数：
        status_path: 状态文件路径
        status: 当前状态字符串
        **kwargs: 额外需要记录的字段
    """
    data = {
        "status": status,
        "updated_at": iso_now(),
        **kwargs,
    }
    existing = {}
    if status_path.exists():
        try:
            existing = json.loads(status_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    existing.update(data)
    status_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def read_status(status_path: Path) -> dict[str, Any]:
    """
    读取节点执行状态文件

    如果文件不存在或解析失败，返回默认状态 {"status": "not_started"}
    """
    if not status_path.exists():
        return {"status": "not_started"}
    try:
        return json.loads(status_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "not_started"}


def run_git(args: list[str], cwd: Path | None = None, timeout: int = 1800,
            env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """
    执行 Git 命令

    参数：
        args: Git 子命令及参数列表（不含 "git" 本身）
        cwd: 工作目录
        timeout: 超时时间（秒），默认 1800（30 分钟）
        env: 环境变量（用于传递 GIT_ASKPASS 等）

    返回：
        subprocess.CompletedProcess 对象
    """
    cmd = ["git"] + args
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    return result


def is_text_file(filepath: Path, block_size: int = 1024) -> bool:
    """
    判断文件是否为文本文件

    通过读取文件前 block_size 字节，检查是否包含空字节（\\x00）。
    包含空字节的文件被视为二进制文件。

    参数：
        filepath: 文件路径
        block_size: 读取的字节数，默认 1024

    返回：
        True 表示文本文件，False 表示二进制文件
    """
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(block_size)
        if b"\x00" in chunk:
            return False
        return True
    except (OSError, PermissionError):
        return False


def get_all_branches(mirror_path: Path) -> list[str]:
    """
    获取裸镜像仓库的所有远程分支名称

    参数：
        mirror_path: 裸镜像仓库路径（.git 目录）

    返回：
        分支名称列表（已去除 "origin/" 前缀）
    """
    result = run_git(["branch", "-r"], cwd=mirror_path)
    if result.returncode != 0:
        return []
    branches = []
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if "->" in line:
            continue
        if line.startswith("origin/"):
            branches.append(line[len("origin/"):])
    return branches


def get_all_tags(mirror_path: Path) -> list[str]:
    """
    获取裸镜像仓库的所有标签名称

    参数：
        mirror_path: 裸镜像仓库路径

    返回：
        标签名称列表
    """
    result = run_git(["tag"], cwd=mirror_path)
    if result.returncode != 0:
        return []
    return [t.strip() for t in result.stdout.strip().split("\n") if t.strip()]


def get_commit_count(mirror_path: Path) -> int:
    """
    获取裸镜像仓库的总提交数

    参数：
        mirror_path: 裸镜像仓库路径

    返回：
        提交总数，失败时返回 0
    """
    result = run_git(["rev-list", "--count", "--all"], cwd=mirror_path)
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def get_commit_log(mirror_path: Path) -> list[dict[str, str]]:
    """
    获取裸镜像仓库的所有提交记录

    使用自定义格式获取：哈希、作者名、作者邮箱、时间戳、提交信息

    参数：
        mirror_path: 裸镜像仓库路径

    返回：
        提交记录字典列表
    """
    fmt = "%H||%an||%ae||%at||%s"
    result = run_git(["log", "--all", f"--format={fmt}"], cwd=mirror_path)
    if result.returncode != 0:
        return []
    commits = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("||", 4)
        if len(parts) == 5:
            commits.append({
                "hash": parts[0],
                "author_name": parts[1],
                "author_email": parts[2],
                "timestamp": parts[3],
                "message": parts[4],
            })
    return commits


def get_tag_info(mirror_path: Path) -> list[dict[str, str]]:
    """
    获取裸镜像仓库的所有标签详细信息

    包括：标签名、对象类型、关联提交哈希、标签者名称/邮箱/日期、标签信息

    参数：
        mirror_path: 裸镜像仓库路径

    返回：
        标签信息字典列表
    """
    result = run_git(["tag", "-l", "--format=%(refname:short)||%(objecttype)||%(*objectname)||%(taggername)||%(taggeremail)||%(taggerdate:iso)||%(subject)"], cwd=mirror_path)
    if result.returncode != 0:
        return []
    tags = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("||", 6)
        if len(parts) >= 1 and parts[0]:
            tags.append({
                "name": parts[0],
                "object_type": parts[1] if len(parts) > 1 else "",
                "commit_hash": parts[2] if len(parts) > 2 else "",
                "tagger_name": parts[3] if len(parts) > 3 else "",
                "tagger_email": parts[4] if len(parts) > 4 else "",
                "tagger_date": parts[5] if len(parts) > 5 else "",
                "message": parts[6] if len(parts) > 6 else "",
            })
    return tags


def extract_repo_name(repo_url: str) -> str:
    """
    从仓库 URL 中提取仓库名称

    规则：从 URL 路径部分提取，如 https://github.com/org/my-repo → org_my-repo

    参数：
        repo_url: 仓库 URL

    返回：
        仓库名称字符串
    """
    repo_name = urlparse(repo_url).path.strip("/").replace("/", "_")
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]
    return repo_name


def clone_repo(repo_url: str, workspace: str, token: str = "",
               timeout: int = 600, retry_count: int = 2, force: bool = False) -> dict:
    """
    使用 git clone 命令克隆单个仓库

    流程：
    1. 使用 git clone --mirror 创建裸镜像（包含所有分支、标签、提交历史）
    2. 从远程创建工作副本
    3. 收集仓库元数据（分支列表、标签列表、提交数）

    参数：
        repo_url: 仓库 URL
        workspace: 工作目录
        token: GitHub Personal Access Token（可选）
        timeout: 单次克隆超时时间（秒）
        retry_count: 最大重试次数
        force: 是否强制覆盖已有目录

    返回：
        包含克隆结果和元数据的字典
    """
    repo_name = extract_repo_name(repo_url)
    mirror_dir = Path(workspace) / "mirrors" / f"{repo_name}.git"
    working_dir = Path(workspace) / "repos" / repo_name

    result_info = {
        "name": repo_name,
        "url": repo_url,
        "status": "not_started",
        "mirror_path": str(mirror_dir),
        "working_path": str(working_dir),
        "branches": [],
        "tags": [],
        "commit_count": 0,
        "duration_seconds": 0,
        "error": None,
    }

    if not force and mirror_dir.exists() and working_dir.exists():
        print(f"  [{repo_name}] 检测到已有目录，跳过下载（使用 --force 强制覆盖）")
        result_info["status"] = "completed"
        result_info["skipped"] = True
        try:
            result_info["branches"] = get_all_branches(mirror_dir)
            result_info["tags"] = get_all_tags(mirror_dir)
            result_info["commit_count"] = get_commit_count(mirror_dir)
        except Exception:
            pass
        return result_info

    if force:
        if mirror_dir.exists():
            shutil.rmtree(mirror_dir, ignore_errors=True)
        if working_dir.exists():
            shutil.rmtree(working_dir, ignore_errors=True)

    askpass_script = None
    env = os.environ.copy()
    if token:
        script_dir = tempfile.mkdtemp(prefix="repo_sentinel_")
        askpass_script = os.path.join(script_dir, "askpass.sh")
        with open(askpass_script, "w") as f:
            f.write(f"#!/bin/sh\necho '{token}'\n")
        os.chmod(askpass_script, 0o700)
        env["GIT_ASKPASS"] = askpass_script
        env["GIT_TERMINAL_PROMPT"] = "0"

    start_time = time.time()

    for attempt in range(1, retry_count + 1):
        try:
            result_info["status"] = "in_progress"

            if mirror_dir.exists():
                shutil.rmtree(mirror_dir, ignore_errors=True)
            if working_dir.exists():
                shutil.rmtree(working_dir, ignore_errors=True)

            mirror_dir.parent.mkdir(parents=True, exist_ok=True)
            working_dir.parent.mkdir(parents=True, exist_ok=True)

            print(f"  [{repo_name}] 正在克隆镜像（第 {attempt}/{retry_count} 次尝试）...")
            clone_result = run_git(
                ["clone", "--mirror", "--progress", repo_url, str(mirror_dir)],
                timeout=timeout,
                env=env,
            )
            if clone_result.returncode != 0:
                raise RuntimeError(f"镜像克隆失败: {clone_result.stderr.strip()}")

            print(f"  [{repo_name}] 正在创建工作副本...")
            work_result = run_git(
                ["clone", repo_url, str(working_dir)],
                timeout=timeout,
            )
            if work_result.returncode != 0:
                raise RuntimeError(f"工作副本创建失败: {work_result.stderr.strip()}")

            result_info["branches"] = get_all_branches(mirror_dir)
            result_info["tags"] = get_all_tags(mirror_dir)
            result_info["commit_count"] = get_commit_count(mirror_dir)
            result_info["status"] = "completed"
            result_info["duration_seconds"] = round(time.time() - start_time, 2)
            print(f"  [{repo_name}] 完成（{result_info['commit_count']} 次提交，"
                  f"{len(result_info['branches'])} 个分支，{len(result_info['tags'])} 个标签）")
            break

        except subprocess.TimeoutExpired:
            result_info["error"] = f"超时（{timeout}秒），第 {attempt}/{retry_count} 次尝试"
            print(f"  [{repo_name}] 第 {attempt} 次尝试超时")
        except Exception as e:
            result_info["error"] = f"{type(e).__name__}: {e}，第 {attempt}/{retry_count} 次尝试"
            print(f"  [{repo_name}] 第 {attempt} 次尝试出错: {e}")
            if attempt < retry_count:
                wait = 2 ** attempt
                print(f"  [{repo_name}] {wait} 秒后重试...")
                time.sleep(wait)
    else:
        result_info["status"] = "failed"
        result_info["duration_seconds"] = round(time.time() - start_time, 2)

    if askpass_script:
        try:
            script_dir = os.path.dirname(askpass_script)
            os.remove(askpass_script)
            os.rmdir(script_dir)
        except OSError:
            pass

    return result_info


def pipeline_main(args: list[str] | None = None) -> None:
    """
    单仓库完整流水线入口（下载→扫描→提取 Finding）

    每个仓库独立执行完整流程，无需等待其他仓库。
    下载步骤使用 git clone 命令，无需 download_repos.py 脚本。
    节点三的分析由 Agent 直接完成，此入口仅执行节点一、二并提取 Finding。

    参数：
        args: 命令行参数列表
    """
    import argparse

    parser = argparse.ArgumentParser(description="运行单仓库完整流水线（下载→扫描→提取Finding）")
    parser.add_argument("--repo-url", required=True, help="仓库 URL")
    parser.add_argument("--token", default="", help="GitHub Token")
    parser.add_argument("--rules", required=True, help="扫描规则文件路径或规则目录路径（支持 .xlsx/.xls/.csv/.json 格式）")
    parser.add_argument("--output-dir", required=True, help="输出工作目录")
    parser.add_argument("--download-timeout", type=int, default=600, help="下载超时时间（秒）")
    parser.add_argument("--force", action="store_true", help="强制覆盖已有目录")

    parsed = parser.parse_args(args)
    workspace = Path(parsed.output_dir)
    workspace.mkdir(parents=True, exist_ok=True)

    repo_name = extract_repo_name(parsed.repo_url)

    print("=" * 60)
    print(f"仓库哨兵（REPO SENTINEL）- 单仓库流水线")
    print(f"仓库: {repo_name}")
    print("=" * 60)

    print("\n[1/3] 正在下载仓库...")
    result = clone_repo(
        parsed.repo_url, str(workspace), parsed.token,
        parsed.download_timeout, 2, parsed.force,
    )
    if result["status"] == "failed":
        print(f"下载失败: {result.get('error', '未知错误')}")
        return

    print("\n[2/3] 正在扫描敏感信息...")
    from scripts.scan_sensitive import main as scan_main
    scan_main([
        "--workspace", str(workspace),
        "--rules", parsed.rules,
        "--repo-name", repo_name,
    ])

    scan_report = str(workspace / "reports" / f"{repo_name}_scan.xlsx")
    findings_output = str(workspace / "reports" / f"{repo_name}_findings.json")

    print("\n[3/3] 正在提取 Finding 数据...")
    from scripts.extract_findings import main as extract_main
    extract_main([
        "--report", scan_report,
        "--format", "analysis",
        "--output", findings_output,
    ])

    print("\n" + "=" * 60)
    print(f"仓库 {repo_name} 流水线执行完成")
    print(f"扫描报告: {scan_report}")
    print(f"Finding 数据: {findings_output}")
    print("节点三（分析确认）请由 Claude/sub-agent 基于 Finding 数据直接执行")
    print("=" * 60)


if __name__ == "__main__":
    pipeline_main()
