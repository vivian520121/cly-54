#!/usr/bin/env python3
"""
文件去重归档工具 - 基于MD5哈希的重复文件检测与管理
仅依赖: rich, hashlib, pathlib
"""

import sys
import hashlib
import json
import shutil
import argparse
from pathlib import Path
from datetime import datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    from rich.console import Console
    from rich.table import Table
    from rich.progress import Progress, BarColumn, TextColumn
    from rich.panel import Panel
    from rich.text import Text
    from rich.tree import Tree
    from rich import print as rprint
except ImportError:
    print("请先安装 rich 库: pip install rich")
    sys.exit(1)


console = Console()

CHUNK_SIZE = 1024 * 1024 * 8  # 8MB 分片
BACKUP_DIR_NAME = ".dedup_backup"
MANIFEST_NAME = "dedup_manifest.json"


def compute_md5(filepath: Path) -> str:
    """计算文件MD5哈希，大文件分片读取防止内存溢出"""
    md5 = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                md5.update(chunk)
        return md5.hexdigest()
    except (IOError, PermissionError) as e:
        console.print(f"[yellow]无法读取文件 {filepath}: {e}[/yellow]")
        return ""


def format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def get_creation_time(filepath: Path) -> datetime:
    """获取文件创建时间"""
    stat = filepath.stat()
    return datetime.fromtimestamp(stat.st_ctime)


def filter_by_extension(files: list, whitelist: list = None, blacklist: list = None) -> list:
    """按扩展名过滤文件，支持白名单和黑名单"""
    if not whitelist and not blacklist:
        return files

    result = []
    for f in files:
        ext = f.suffix.lower().lstrip(".")
        if whitelist:
            if ext in [e.lower().lstrip(".") for e in whitelist]:
                result.append(f)
        elif blacklist:
            if ext not in [e.lower().lstrip(".") for e in blacklist]:
                result.append(f)
        else:
            result.append(f)
    return result


def scan_files(root_dir: Path, whitelist: list = None, blacklist: list = None) -> list:
    """递归扫描目录下所有文件"""
    all_files = []
    backup_dir = root_dir / BACKUP_DIR_NAME

    with Progress(
        TextColumn("[bold cyan]●[/bold cyan]"),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("扫描目录中...", total=None)

        for path in root_dir.rglob("*"):
            if path.is_file():
                if backup_dir in path.parents or path.parent == backup_dir:
                    continue
                all_files.append(path)

        progress.update(task, description=f"已发现 {len(all_files)} 个文件")

    filtered = filter_by_extension(all_files, whitelist, blacklist)
    if whitelist or blacklist:
        console.print(f"[dim]过滤后剩余 {len(filtered)} 个文件[/dim]")

    return filtered


def group_by_hash(files: list) -> dict:
    """按MD5哈希分组，返回 {hash: [filepath, ...]}"""
    hash_groups = {}

    with Progress(
        TextColumn("[bold cyan]●[/bold cyan]"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task = progress.add_task("计算文件哈希...", total=len(files))

        for filepath in files:
            try:
                file_hash = compute_md5(filepath)
                if file_hash:
                    if file_hash not in hash_groups:
                        hash_groups[file_hash] = []
                    hash_groups[file_hash].append(filepath)
            except Exception as e:
                console.print(f"[yellow]处理 {filepath} 时出错: {e}[/yellow]")
            progress.advance(task)

    dup_groups = {k: v for k, v in hash_groups.items() if len(v) > 1}
    return dup_groups


def print_dup_groups(dup_groups: dict):
    """彩色展示重复文件列表"""
    if not dup_groups:
        console.print(Panel("[green]未发现重复文件！[/green]", title="扫描结果", border_style="green"))
        return

    total_dup_count = sum(len(v) - 1 for v in dup_groups.values())
    total_wasted = 0

    table = Table(
        title=f"重复文件列表 (共 {len(dup_groups)} 组, {total_dup_count} 个重复文件)",
        show_lines=True,
        border_style="blue",
    )
    table.add_column("序号", style="cyan", justify="center", width=6)
    table.add_column("文件路径", style="white")
    table.add_column("大小", style="yellow", justify="right", width=12)
    table.add_column("创建时间", style="green", width=20)

    for idx, (file_hash, files) in enumerate(sorted(dup_groups.items(), key=lambda x: -x[1][0].stat().st_size), 1):
        file_size = files[0].stat().st_size
        total_wasted += file_size * (len(files) - 1)

        for i, fpath in enumerate(files):
            ctime = get_creation_time(fpath).strftime("%Y-%m-%d %H:%M:%S")
            if i == 0:
                label = f"[bold magenta]#{idx} ({file_hash[:8]}...)[/bold magenta]"
                path_display = f"[green]✓ {fpath}[/green]"
            else:
                label = ""
                path_display = f"[red]× {fpath}[/red]"

            table.add_row(
                label,
                path_display,
                format_size(file_size),
                ctime,
            )

    console.print(table)
    console.print(f"\n[bold yellow]可释放空间: {format_size(total_wasted)}[/bold yellow]")


def print_stats(dup_groups: dict, all_files: list):
    """输出统计报表"""
    total_files = len(all_files)
    dup_file_count = sum(len(v) for v in dup_groups.values())
    unique_file_count = len(dup_groups)
    dup_only_count = dup_file_count - unique_file_count

    total_size = sum(f.stat().st_size for f in all_files)
    wasted_size = sum(
        (len(files) - 1) * files[0].stat().st_size
        for files in dup_groups.values()
    )

    size_groups = {}
    for files in dup_groups.values():
        size = files[0].stat().st_size
        size_range = (size // (1024 * 1024)) * 1024 * 1024
        size_groups[size_range] = size_groups.get(size_range, 0) + len(files) - 1

    table = Table(title="统计报表", border_style="cyan")
    table.add_column("指标", style="bold")
    table.add_column("数值", style="yellow")

    table.add_row("总文件数", str(total_files))
    table.add_row("重复文件组", str(unique_file_count))
    table.add_row("重复文件总数", str(dup_file_count))
    table.add_row("可删除重复数", str(dup_only_count))
    table.add_row("总大小", format_size(total_size))
    table.add_row("可释放空间", format_size(wasted_size))
    table.add_row("空间占比", f"{wasted_size / total_size * 100:.2f}%" if total_size > 0 else "0%")

    console.print(table)


def dedup_files(root_dir: Path, dup_groups: dict, dry_run: bool = False) -> dict:
    """
    去重：将重复文件移动到备份目录，保留原始目录结构
    每组保留第一个文件（按创建时间排序，保留最早的）
    返回移动清单
    """
    backup_dir = root_dir / BACKUP_DIR_NAME
    moved_files = []  # [(src, dst_rel), ...]

    if not dry_run:
        backup_dir.mkdir(exist_ok=True)

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        total = sum(len(v) - 1 for v in dup_groups.values())
        task = progress.add_task("移动重复文件..." if not dry_run else "模拟移动...", total=total)

        for file_hash, files in dup_groups.items():
            sorted_files = sorted(files, key=lambda f: get_creation_time(f))
            keep_file = sorted_files[0]
            dup_files = sorted_files[1:]

            for dup_file in dup_files:
                rel_path = dup_file.relative_to(root_dir)
                dst_path = backup_dir / rel_path

                if not dry_run:
                    dst_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dup_file), str(dst_path))

                moved_files.append((str(dup_file), str(rel_path)))
                progress.advance(task)

    manifest = {
        "timestamp": datetime.now().isoformat(),
        "root_dir": str(root_dir.absolute()),
        "backup_dir": str(backup_dir.relative_to(root_dir)),
        "total_moved": len(moved_files),
        "files": [
            {"original": src, "relative": dst_rel}
            for src, dst_rel in moved_files
        ],
    }

    if not dry_run:
        manifest_path = backup_dir / MANIFEST_NAME
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

    return manifest


def restore_files(root_dir: Path) -> int:
    """从备份目录还原所有移动的文件"""
    backup_dir = root_dir / BACKUP_DIR_NAME
    manifest_path = backup_dir / MANIFEST_NAME

    if not manifest_path.exists():
        console.print("[red]未找到去重清单文件，无法还原[/red]")
        return 0

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    restored_count = 0
    failed_count = 0

    files = manifest.get("files", [])

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task = progress.add_task("还原文件中...", total=len(files))

        for item in files:
            rel_path = item["relative"]
            src_path = backup_dir / rel_path
            dst_path = root_dir / rel_path

            try:
                if src_path.exists():
                    dst_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src_path), str(dst_path))
                    restored_count += 1
                else:
                    console.print(f"[yellow]警告: 备份文件不存在 {src_path}[/yellow]")
                    failed_count += 1
            except Exception as e:
                console.print(f"[red]还原失败 {src_path}: {e}[/red]")
                failed_count += 1

            progress.advance(task)

    if restored_count > 0 and failed_count == 0:
        manifest_path.unlink(missing_ok=True)
        _cleanup_empty_dirs(backup_dir)

    return restored_count


def _cleanup_empty_dirs(directory: Path):
    """递归清理空目录"""
    if not directory.is_dir():
        return
    for child in list(directory.iterdir()):
        if child.is_dir():
            _cleanup_empty_dirs(child)
    try:
        if not any(directory.iterdir()):
            directory.rmdir()
    except OSError:
        pass


def cmd_scan(args):
    """scan 子命令：扫描并展示重复文件"""
    root_dir = Path(args.dir).resolve()

    if not root_dir.exists():
        console.print(f"[red]目录不存在: {root_dir}[/red]")
        sys.exit(1)

    console.print(Panel(f"[bold blue]扫描目录:[/bold blue] {root_dir}", title="文件去重扫描", border_style="blue"))

    whitelist = args.include.split(",") if args.include else None
    blacklist = args.exclude.split(",") if args.exclude else None

    if whitelist:
        console.print(f"[green]白名单:[/green] {whitelist}")
    if blacklist:
        console.print(f"[red]黑名单:[/red] {blacklist}")

    files = scan_files(root_dir, whitelist, blacklist)
    dup_groups = group_by_hash(files)

    print_dup_groups(dup_groups)
    print_stats(dup_groups, files)


def cmd_dedup(args):
    """dedup 子命令：执行去重操作"""
    root_dir = Path(args.dir).resolve()

    if not root_dir.exists():
        console.print(f"[red]目录不存在: {root_dir}[/red]")
        sys.exit(1)

    console.print(Panel(f"[bold blue]去重目录:[/bold blue] {root_dir}", title="文件去重", border_style="magenta"))

    whitelist = args.include.split(",") if args.include else None
    blacklist = args.exclude.split(",") if args.exclude else None

    files = scan_files(root_dir, whitelist, blacklist)
    dup_groups = group_by_hash(files)

    if not dup_groups:
        console.print("[green]没有发现重复文件，无需去重[/green]")
        return

    print_dup_groups(dup_groups)
    print_stats(dup_groups, files)

    if not args.yes:
        console.print()
        try:
            answer = console.input("[bold yellow]确认执行去重操作？(y/N): [/bold yellow]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]已取消[/yellow]")
            return

        if answer.lower() not in ("y", "yes"):
            console.print("[yellow]已取消[/yellow]")
            return

    dry_run = args.dry_run
    manifest = dedup_files(root_dir, dup_groups, dry_run=dry_run)

    action = "模拟移动" if dry_run else "已移动"
    console.print(f"\n[bold green]{action} {manifest['total_moved']} 个重复文件[/bold green]")

    if not dry_run:
        backup_dir = root_dir / BACKUP_DIR_NAME
        console.print(f"[dim]备份目录: {backup_dir}[/dim]")
        console.print(f"[dim]清单文件: {backup_dir / MANIFEST_NAME}[/dim]")
        console.print(f"[dim]使用 'file_dedup.py restore -d {root_dir}' 还原所有文件[/dim]")


def cmd_restore(args):
    """restore 子命令：还原移动的文件"""
    root_dir = Path(args.dir).resolve()

    if not root_dir.exists():
        console.print(f"[red]目录不存在: {root_dir}[/red]")
        sys.exit(1)

    console.print(Panel(f"[bold blue]还原目录:[/bold blue] {root_dir}", title="文件还原", border_style="green"))

    if not args.yes:
        try:
            answer = console.input("[bold yellow]确认还原所有备份文件？(y/N): [/bold yellow]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]已取消[/yellow]")
            return

        if answer.lower() not in ("y", "yes"):
            console.print("[yellow]已取消[/yellow]")
            return

    restored = restore_files(root_dir)
    console.print(f"[bold green]成功还原 {restored} 个文件[/bold green]")


def main():
    parser = argparse.ArgumentParser(
        description="文件去重归档工具 - 基于MD5哈希的重复文件检测与管理",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s scan -d ./images
  %(prog)s dedup -d ./images -i png,jpg,svg
  %(prog)s dedup -d ./music -e m3u --dry-run
  %(prog)s restore -d ./images
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="可用子命令")

    scan_parser = subparsers.add_parser("scan", help="扫描目录，展示重复文件")
    scan_parser.add_argument("-d", "--dir", required=True, help="要扫描的目录")
    scan_parser.add_argument("-i", "--include", help="白名单后缀，逗号分隔，如: png,jpg,svg")
    scan_parser.add_argument("-e", "--exclude", help="黑名单后缀，逗号分隔，如: tmp,log")

    dedup_parser = subparsers.add_parser("dedup", help="执行去重，移动重复文件到备份目录")
    dedup_parser.add_argument("-d", "--dir", required=True, help="要去重的目录")
    dedup_parser.add_argument("-i", "--include", help="白名单后缀，逗号分隔")
    dedup_parser.add_argument("-e", "--exclude", help="黑名单后缀，逗号分隔")
    dedup_parser.add_argument("-y", "--yes", action="store_true", help="跳过确认，直接执行")
    dedup_parser.add_argument("--dry-run", action="store_true", help="模拟运行，不实际移动文件")

    restore_parser = subparsers.add_parser("restore", help="从备份目录还原所有文件")
    restore_parser.add_argument("-d", "--dir", required=True, help="原始目录")
    restore_parser.add_argument("-y", "--yes", action="store_true", help="跳过确认，直接还原")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        if args.command == "scan":
            cmd_scan(args)
        elif args.command == "dedup":
            cmd_dedup(args)
        elif args.command == "restore":
            cmd_restore(args)
    except KeyboardInterrupt:
        console.print("\n[yellow]操作已中断[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
