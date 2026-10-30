"""Baohe CLI: list / export / recent (extract-only)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from baohe.discover import resolve_paths
from baohe.export import default_out_dir, write_export, write_recent_index
from baohe.messages import collect_messages
from baohe.sessions import discover_sessions, find_session

COVERAGE_NOTE = "本地缓存尽力而为，非全量历史。"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="baohe",
        description="从宝盒lite 本地缓存抽取单聊/群聊消息（只读，非全量）。",
    )
    p.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="zbox-lite 数据根（默认 ~/Library/Application Support/zbox-lite）",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="打印调试信息到 stderr")
    sub = p.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", help="列出本机缓存中的会话")
    list_p.add_argument("--days", type=int, default=None, help="只列出最近 N 天内有消息的会话")

    export_p = sub.add_parser("export", help="按名称或 ID 导出单个会话")
    export_p.add_argument("query", help="会话名称或 ID")
    export_p.add_argument("--days", type=int, default=None, help="只保留最近 N 天消息")
    export_p.add_argument(
        "-o",
        "--out",
        type=Path,
        default=None,
        help="输出目录（默认 ~/Documents/grok-exports/baohe/<slug>-<时间戳>/）",
    )

    recent_p = sub.add_parser("recent", help="批量导出最近会话简报")
    recent_p.add_argument("--days", type=int, default=None, help="只保留最近 N 天消息")
    recent_p.add_argument("--limit", type=int, default=20, help="最多导出会话数（默认 20）")
    recent_p.add_argument(
        "-o",
        "--out",
        type=Path,
        default=None,
        help="批量输出根目录（默认 recent-batch-<时间戳>）",
    )
    return p


def _cmd_list(args: argparse.Namespace) -> int:
    paths = resolve_paths(args.data_dir)
    sessions = discover_sessions(paths)
    if args.days is not None:
        sessions = [
            s for s in sessions if collect_messages(paths, s, days=args.days)
        ]
    for s in sessions:
        kind = "group" if s.group else "dm"
        print(f"{s.id}\t{s.name}\t{kind}")
    if args.verbose:
        src = paths.local_cache2 or paths.local_cache
        print(
            f"# {len(sessions)} sessions (cache+cache2+leveldb mine) data={paths.data_dir}",
            file=sys.stderr,
        )
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    paths = resolve_paths(args.data_dir)
    sessions = discover_sessions(paths)
    session = find_session(sessions, args.query)
    if session is None:
        print(f"找不到会话: {args.query!r}（已查 localCache/localCache2 search_history + LevelDB 挖掘）", file=sys.stderr)
        return 2
    messages = collect_messages(paths, session, days=args.days)
    if not messages:
        days_note = f"最近 {args.days} 天" if args.days is not None else "全部可解析缓存"
        print(
            f"会话 {session.name}（{session.id}）零消息。"
            f"已扫 LevelDB + forward 缓存（范围: {days_note}）。"
            f"本地缓存未必含全量历史。",
            file=sys.stderr,
        )
        return 2
    out_dir = args.out if args.out is not None else default_out_dir(session.name)
    jp, mp = write_export(out_dir, session, messages, coverage_note=COVERAGE_NOTE)
    print(str(jp))
    print(str(mp))
    if args.verbose:
        print(f"# {len(messages)} messages → {out_dir}", file=sys.stderr)
    return 0


def _cmd_recent(args: argparse.Namespace) -> int:
    paths = resolve_paths(args.data_dir)
    sessions = discover_sessions(paths)
    batch_root = (
        args.out
        if args.out is not None
        else default_out_dir("recent-batch")
    )
    batch_root = Path(batch_root)
    batch_root.mkdir(parents=True, exist_ok=True)

    rows: List[dict] = []
    exported = 0
    limit = max(0, int(args.limit))
    for session in sessions:
        if exported >= limit:
            break
        messages = collect_messages(paths, session, days=args.days)
        if not messages:
            if args.verbose:
                print(f"# skip empty: {session.name}", file=sys.stderr)
            continue
        sub = batch_root / f"{exported:02d}-{session.id}"
        write_export(sub, session, messages, coverage_note=COVERAGE_NOTE)
        rows.append(
            {
                "name": session.name,
                "id": session.id,
                "count": len(messages),
                "dir": str(sub.relative_to(batch_root)),
            }
        )
        exported += 1

    write_recent_index(batch_root / "index.md", rows)
    if exported == 0:
        days_note = f"最近 {args.days} 天" if args.days is not None else "全部可解析缓存"
        print(
            f"recent 零命中：已扫 {len(sessions)} 个会话（cache+cache2+leveldb），"
            f"范围 {days_note}，无会话产出导出。"
            f"本地缓存未必含全量历史。",
            file=sys.stderr,
        )
        return 2
    print(str(batch_root / "index.md"))
    if args.verbose:
        print(f"# exported {exported} sessions → {batch_root}", file=sys.stderr)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "list":
            return _cmd_list(args)
        if args.command == "export":
            return _cmd_export(args)
        if args.command == "recent":
            return _cmd_recent(args)
        parser.error(f"unknown command: {args.command}")
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
