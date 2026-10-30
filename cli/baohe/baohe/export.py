from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple

from baohe.models import Message, Session

DEFAULT_EXPORT_ROOT = Path.home() / "Documents" / "grok-exports" / "baohe"


def _slugify(name: str) -> str:
    """Keep CJK and alphanumerics; replace other runs with a single dash."""
    parts: List[str] = []
    buf: List[str] = []
    for ch in name.strip():
        if ch.isalnum() or ("\u4e00" <= ch <= "\u9fff"):
            buf.append(ch)
        else:
            if buf:
                parts.append("".join(buf))
                buf = []
            if parts and parts[-1] != "-":
                parts.append("-")
    if buf:
        parts.append("".join(buf))
    slug = "".join(parts).strip("-")
    # collapse repeated dashes
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or "session"


def default_out_dir(session_name: str, root: Optional[Path] = None) -> Path:
    base = (root if root is not None else DEFAULT_EXPORT_ROOT).expanduser().resolve()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return base / f"{_slugify(session_name)}-{stamp}"


def _format_time(send_time: Optional[int]) -> str:
    if send_time is None:
        return "unknown"
    try:
        # milliseconds
        dt = datetime.fromtimestamp(send_time / 1000.0, tz=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    except (OSError, OverflowError, ValueError):
        return str(send_time)


def _render_report(
    session: Session, messages: List[Message], coverage_note: str
) -> str:
    lines: List[str] = [
        f"# {session.name}",
        "",
        "## 数据来源与覆盖",
        "",
        "| 项 | 结果 |",
        "| --- | --- |",
        f"| 会话 ID | {session.id} |",
        f"| 消息条数 | {len(messages)} |",
        f"| 覆盖说明 | {coverage_note} |",
        "",
        "## 关键结论",
        "",
        "（待填写）",
        "",
        "## 消息时间线",
        "",
    ]
    for m in messages:
        ts = _format_time(m.send_time)
        sender = m.sender or m.sender_id or "unknown"
        body = (m.body or "").replace("\n", " ")
        lines.append(f"- [{ts}] **{sender}**: {body}")
    if not messages:
        lines.append("- （无消息）")
    lines.append("")
    return "\n".join(lines)


def write_export(
    out_dir: Path,
    session: Session,
    messages: List[Message],
    *,
    coverage_note: str,
) -> Tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "messages.json"
    md_path = out_dir / "report.md"
    payload: dict[str, Any] = {
        "session": session.to_dict(),
        "coverageNote": coverage_note,
        "messages": [m.to_dict() for m in messages],
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(
        _render_report(session, messages, coverage_note),
        encoding="utf-8",
    )
    return json_path, md_path


def write_recent_index(index_path: Path, rows: List[dict]) -> None:
    index_path = Path(index_path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 最近会话导出索引",
        "",
        "| 会话 | ID | 消息数 | 目录 |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        name = row.get("name", "")
        sid = row.get("id", "")
        count = row.get("count", 0)
        d = row.get("dir", "")
        lines.append(f"| {name} | {sid} | {count} | {d} |")
    lines.append("")
    index_path.write_text("\n".join(lines), encoding="utf-8")
