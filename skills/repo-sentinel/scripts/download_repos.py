"""
仓库哨兵（Repo Sentinel）- 节点一：仓库下载

单仓库模式：每次调用只下载一个仓库，由 Claude 编排多个 subagent 并行执行。
每个 subagent 独立负责一个仓库的完整流程（下载→扫描→分析）。

功能：
- 从 GitHub 仓库地址下载代码至本地指定目录
- 支持公开仓库（无需认证）和私有仓库（通过 GitHub PAT 认证）
- 使用 git clone --mirror 完整下载所有分支、标签和提交历史
- 实时下载进度显示、超时处理、失败重试机制
- 支持 --force 参数覆盖已有目录
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from utils import (
    get_all_branches,
    get_all_tags,
    get_commit_count,
    iso_now,
    run_git,
    write_status,
)


def _create_askpass_script(token: str) -> str:
    """
    创建临时 GIT_ASKPASS 脚本用于私有仓库认证

    参数：
        token: GitHub Personal Access Token

    返回：
        临时脚本的文件路径
    """
    script_dir = tempfile.mkdtemp(prefix="repo_sentinel_")
    script_path = os.path.join(script_dir, "askpass.sh")
    with open(script_path, "w") as f:
        f.write(f"#!/bin/sh\necho '{token}'\n")
    os.chmod(script_path, 0o700)
    return script_path


def _cleanup_askpass(script_path: str) -> None:
    """清理临时 GIT_ASKPASS 脚本及其目录，防止 token 泄露"""
    try:
        script_dir = os.path.dirname(script_path)
        os.remove(script_path)
        os.rmdir(script_dir)
    except OSError:
        pass


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


def clone_single_repo(repo_url: str, token: str | None, output_dir: str,
                      timeout: int = 1800, retry_count: int = 2, force: bool = False) -> dict:
    """
    克隆单个仓库（含重试机制）

    流程：
    1. 使用 git clone --mirror 创建裸镜像（包含所有分支、标签、提交历史）
    2. 从镜像创建工作副本
    3. 收集仓库元数据（分支列表、标签列表、提交数）

    参数：
        repo_url: 仓库 URL
        token: GitHub Personal Access Token（可选）
        output_dir: 输出目录
        timeout: 单次克隆超时时间（秒）
        retry_count: 最大重试次数
        force: 是否强制覆盖已有目录

    返回：
        包含克隆结果和元数据的字典
    """
    repo_name = extract_repo_name(repo_url)
    mirror_dir = Path(output_dir) / "mirrors" / f"{repo_name}.git"
    working_dir = Path(output_dir) / "repos" / repo_name

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

    if mirror_dir.exists() or working_dir.exists():
        if force:
            print(f"  [{repo_name}] 检测到已有目录，--force 模式下将覆盖...")
            if mirror_dir.exists():
                shutil.rmtree(mirror_dir, ignore_errors=True)
            if working_dir.exists():
                shutil.rmtree(working_dir, ignore_errors=True)
        else:
            if mirror_dir.exists() and working_dir.exists():
                print(f"  [{repo_name}] 检测到已有目录，跳过下载（使用 --force 强制覆盖）")
                result_info["status"] = "completed"
                result_info["skipped"] = True
                try:
                    branches = get_all_branches(mirror_dir)
                    tags = get_all_tags(mirror_dir)
                    commit_count = get_commit_count(mirror_dir)
                    result_info["branches"] = branches
                    result_info["tags"] = tags
                    result_info["commit_count"] = commit_count
                except Exception:
                    pass
                return result_info
            else:
                print(f"  [{repo_name}] 检测到不完整目录，清理后重新下载...")
                if mirror_dir.exists():
                    shutil.rmtree(mirror_dir, ignore_errors=True)
                if working_dir.exists():
                    shutil.rmtree(working_dir, ignore_errors=True)

    askpass_script = None
    env = os.environ.copy()
    if token:
        askpass_script = _create_askpass_script(token)
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
                ["clone", str(mirror_dir), str(working_dir)],
                timeout=timeout,
            )
            if work_result.returncode != 0:
                raise RuntimeError(f"工作副本创建失败: {work_result.stderr.strip()}")

            branches = get_all_branches(mirror_dir)
            tags = get_all_tags(mirror_dir)
            commit_count = get_commit_count(mirror_dir)

            for branch in branches:
                if branch not in ("main", "master", "HEAD"):
                    try:
                        run_git(["checkout", branch], cwd=working_dir, timeout=60)
                    except (subprocess.TimeoutExpired, RuntimeError):
                        pass

            try:
                run_git(["checkout", branches[0] if branches else "HEAD"], cwd=working_dir, timeout=60)
            except (subprocess.TimeoutExpired, RuntimeError):
                pass

            result_info["branches"] = branches
            result_info["tags"] = tags
            result_info["commit_count"] = commit_count
            result_info["status"] = "completed"
            result_info["duration_seconds"] = round(time.time() - start_time, 2)
            print(f"  [{repo_name}] 完成（{commit_count} 次提交，{len(branches)} 个分支，{len(tags)} 个标签）")

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
        _cleanup_askpass(askpass_script)

    return result_info


def main(args: list[str] | None = None) -> None:
    """
    节点一主入口：下载 GitHub 仓库

    支持两种模式：
    - 单仓库模式：--repo-url 指定单个仓库（subagent 并行时每个 subagent 调用一次）
    - 批量模式：--repos 指定 JSON 文件，串行下载多个仓库
    """
    parser = argparse.ArgumentParser(description="节点一：仓库下载")
    parser.add_argument("--repo-url", help="单个仓库 URL（subagent 模式）")
    parser.add_argument("--token", help="GitHub Token")
    parser.add_argument("--repos", help="repos.json 文件路径（批量模式）")
    parser.add_argument("--repo-urls", nargs="+", help="多个仓库 URL（批量模式）")
    parser.add_argument("--output-dir", required=True, help="输出工作目录")
    parser.add_argument("--timeout", type=int, default=1800, help="每个仓库的超时时间（秒）")
    parser.add_argument("--retry-count", type=int, default=2, help="每个仓库的重试次数")
    parser.add_argument("--retry-failed", action="store_true", help="仅重试之前失败的仓库")
    parser.add_argument("--force", action="store_true", help="强制覆盖已有目录")

    parsed = parser.parse_args(args)
    output_dir = Path(parsed.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    status_path = output_dir / "download_status.json"

    if parsed.repo_url:
        result = clone_single_repo(
            parsed.repo_url, parsed.token, str(output_dir),
            parsed.timeout, parsed.retry_count, parsed.force,
        )

        existing = {"repos": []}
        if status_path.exists():
            try:
                existing = json.loads(status_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        existing["repos"] = [r for r in existing.get("repos", []) if r["name"] != result["name"]]
        existing["repos"].append(result)

        all_completed = all(r["status"] == "completed" for r in existing["repos"])
        any_failed = any(r["status"] == "failed" for r in existing["repos"])
        final_status = "completed" if all_completed else ("failed" if any_failed else "in_progress")

        write_status(status_path, final_status, **existing)
        print(f"仓库 {result['name']} 下载状态: {result['status']}")
        if result["status"] == "failed":
            print(f"错误: {result.get('error', '未知错误')}")
        return

    repos: list[dict] = []
    if parsed.repos:
        with open(parsed.repos, "r", encoding="utf-8") as f:
            repos = json.load(f)
    elif parsed.repo_urls:
        for url in parsed.repo_urls:
            repos.append({"url": url, "token": parsed.token})
    else:
        print("错误：请提供 --repo-url（单仓库模式）或 --repos/--repo-urls（批量模式）")
        sys.exit(1)

    if parsed.retry_failed:
        existing = {}
        if status_path.exists():
            try:
                existing = json.loads(status_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        failed_names = set()
        for r in existing.get("repos", []):
            if r.get("status") == "failed":
                failed_names.add(r["name"])
        if failed_names:
            repos = [r for r in repos if extract_repo_name(r["url"]) in failed_names]
            print(f"正在重试 {len(repos)} 个失败的仓库: {failed_names}")
        else:
            print("没有失败的仓库需要重试。")
            return

    print(f"正在下载 {len(repos)} 个仓库...")
    print("=" * 60)

    results = []
    for repo in repos:
        result = clone_single_repo(
            repo["url"],
            repo.get("token") or parsed.token,
            str(output_dir),
            parsed.timeout,
            parsed.retry_count,
            parsed.force,
        )
        results.append(result)

    status_data = {
        "repos": results,
        "started_at": iso_now(),
        "completed_at": iso_now(),
    }
    completed = sum(1 for r in results if r["status"] == "completed")
    failed = sum(1 for r in results if r["status"] == "failed")
    final_status = "completed" if failed == 0 else "failed" if completed == 0 else "partial"
    write_status(status_path, final_status, **status_data)

    print("=" * 60)
    print(f"下载完成：{completed} 个成功，{failed} 个失败，共 {len(repos)} 个仓库")
    if failed > 0:
        print("失败的仓库：")
        for r in results:
            if r["status"] == "failed":
                print(f"  - {r['name']}: {r.get('error', '未知错误')}")
    print(f"状态已保存至: {status_path}")


if __name__ == "__main__":
    main()
