"""Extract chat messages from zbox-lite LevelDB copies and forward cache.

LevelDB is Chromium IndexedDB on disk; we never open it with a native binding.
Instead we copy selected files (to avoid write locks) and string-scan for JSON
plus Chromium IDB UTF-16/binary fragments.

By default WAL `*.log` are preferred. For a known session, `*.ldb` files that
contain the session id or name (ASCII or UTF-16-LE) are also considered after a
cheap byte prefilter. Set `BAOHE_SCAN_LDB=1` to widen unscoped scans.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Set, Tuple

from baohe.discover import BaohePaths
from baohe.models import Message, Session

_SKIP_NAME_PARTS = ("LOCK",)
_SKIP_SUFFIXES = (".dbtmp",)

_DEFAULT_MAX_FILE_BYTES = 32 * 1024 * 1024
_MAX_JSON_SPAN = 512 * 1024
_MSG_ID_ANCHOR = '"msgId"'
_FRAGMENT_WINDOW = 6000
_FRAGMENT_TRAIL = 2500


def _env_scan_ldb() -> bool:
    return os.environ.get("BAOHE_SCAN_LDB", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def select_leveldb_files(
    leveldb_dir: Path,
    *,
    scan_ldb: Optional[bool] = None,
    max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES,
) -> List[Path]:
    """Choose files to scan: all `*.log` first; optionally `*.ldb` under size cap."""
    root = Path(leveldb_dir)
    if not root.is_dir():
        return []
    if scan_ldb is None:
        scan_ldb = _env_scan_ldb()

    logs: List[Path] = []
    ldbs: List[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        name = path.name
        if name in _SKIP_NAME_PARTS or name.endswith(_SKIP_SUFFIXES):
            continue
        lower = name.lower()
        if lower.endswith(".log"):
            logs.append(path)
        elif scan_ldb and lower.endswith(".ldb"):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size <= max_file_bytes:
                ldbs.append(path)
    return logs + ldbs


def copy_leveldb(src: Path, dest: Path) -> Path:
    """Copy LevelDB dir for read-only scanning. Ignore LOCK / *.dbtmp on fallback."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(src, dest, dirs_exist_ok=True)
        return dest
    except OSError:
        pass
    try:
        src = Path(src)
        for path in src.rglob("*"):
            if not path.is_file():
                continue
            name = path.name
            if name in _SKIP_NAME_PARTS or name.endswith(_SKIP_SUFFIXES):
                continue
            rel = path.relative_to(src)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        return dest
    except OSError as exc:
        raise OSError(
            f"无法拷贝 LevelDB（{src} → {dest}）：请关闭宝盒lite 后重试或检查权限。原始错误: {exc}"
        ) from exc


def copy_selected_leveldb_files(
    src: Path,
    dest: Path,
    files: Sequence[Path],
) -> Path:
    """Copy only `files` (under `src`) into `dest`, preserving relative paths."""
    src = Path(src)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for path in files:
        path = Path(path)
        try:
            rel = path.relative_to(src)
        except ValueError:
            rel = Path(path.name)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, target)
        except OSError as exc:
            raise OSError(
                f"无法拷贝 LevelDB 文件（{path} → {target}）：请关闭宝盒lite 后重试或检查权限。原始错误: {exc}"
            ) from exc
    return dest


def _extract_balanced_from(
    text: str,
    start: int,
    *,
    max_span: int = _MAX_JSON_SPAN,
) -> Optional[str]:
    """Extract one balanced `{...}` starting at `start`, or None if invalid/too long."""
    n = len(text)
    if start >= n or text[start] != "{":
        return None
    depth = 0
    in_str = False
    escape = False
    j = start
    limit = min(n, start + max_span)
    while j < limit:
        c = text[j]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start : j + 1]
        j += 1
    return None


def _extract_balanced_json_objects(
    text: str,
    *,
    max_span: int = _MAX_JSON_SPAN,
) -> List[str]:
    """Pull candidate JSON object substrings with a max span (avoids O(n^2) on binary)."""
    out: List[str] = []
    n = len(text)
    i = 0
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        blob = _extract_balanced_from(text, i, max_span=max_span)
        if blob is not None:
            out.append(blob)
            i += len(blob)
        else:
            i += 1
    return out


def _iter_anchor_starts(text: str, needles: Sequence[str]) -> List[int]:
    starts: List[int] = []
    for needle in needles:
        if not needle:
            continue
        start = 0
        while True:
            idx = text.find(needle, start)
            if idx < 0:
                break
            starts.append(idx)
            start = idx + max(1, len(needle))
    starts.sort()
    return starts


def _object_start_before(text: str, anchor: int, *, lookback: int = 4096) -> Optional[int]:
    lo = max(0, anchor - lookback)
    i = anchor - 1
    while i >= lo:
        if text[i] == "{":
            return i
        i -= 1
    return None


def _extract_message_json_objects(
    text: str,
    *,
    session_id: Optional[str] = None,
    max_span: int = _MAX_JSON_SPAN,
) -> List[str]:
    needles = [_MSG_ID_ANCHOR]
    if session_id:
        needles.append(session_id)
    out: List[str] = []
    seen: Set[int] = set()
    for anchor in _iter_anchor_starts(text, needles):
        start = _object_start_before(text, anchor)
        if start is None or start in seen:
            continue
        blob = _extract_balanced_from(text, start, max_span=max_span)
        if blob is None:
            continue
        seen.add(start)
        out.append(blob)
    return out


def _is_message_like(obj: dict) -> bool:
    if "msgId" in obj:
        return True
    if "content" in obj and ("groupId" in obj or "conversationId" in obj):
        return True
    return False


def _session_needles(session: Session) -> List[bytes]:
    """Byte needles for cheap file prefilter (ASCII + UTF-16-LE)."""
    out: List[bytes] = []
    if session.id:
        out.append(session.id.encode("utf-8"))
        out.append(session.id.encode("utf-16-le"))
    if session.name:
        out.append(session.name.encode("utf-8"))
        out.append(session.name.encode("utf-16-le"))
    seen: Set[bytes] = set()
    uniq: List[bytes] = []
    for n in out:
        if n in seen:
            continue
        seen.add(n)
        uniq.append(n)
    return uniq


def _file_contains_any(data: bytes, needles: Sequence[bytes]) -> bool:
    return any(n in data for n in needles)


def _scan_file_for_blobs(
    path: Path,
    *,
    session_id: Optional[str] = None,
    raw: Optional[bytes] = None,
) -> List[str]:
    """Read one file, optional session prefilter, anchor-scan plain JSON."""
    blobs: List[str] = []
    try:
        data = raw if raw is not None else path.read_bytes()
    except OSError:
        return blobs
    if session_id:
        needles = [
            session_id.encode("utf-8"),
            session_id.encode("utf-16-le"),
        ]
        if not _file_contains_any(data, needles):
            return blobs
    text = data.decode("utf-8", errors="replace")
    for cand in _extract_message_json_objects(text, session_id=session_id):
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and _is_message_like(obj):
            blobs.append(cand)
    return blobs


def iter_raw_json_blobs(
    leveldb_dir: Path,
    *,
    session_id: Optional[str] = None,
    scan_ldb: Optional[bool] = None,
) -> List[str]:
    """Scan selected LevelDB files; return raw JSON object strings."""
    blobs: List[str] = []
    root = Path(leveldb_dir)
    if not root.is_dir():
        return blobs
    for path in select_leveldb_files(root, scan_ldb=scan_ldb):
        blobs.extend(_scan_file_for_blobs(path, session_id=session_id))
    return blobs


_TYPE_PLACEHOLDER = {
    "image": "[图片]",
    "file": "[文件]",
    "audio": "[语音]",
    "voice": "[语音]",
    "video": "[视频]",
}


def _body_from_content(content: Any) -> str:
    if content is None:
        return ""
    obj: Any = content
    if isinstance(content, str):
        s = content.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                return content
        else:
            return content
    if isinstance(obj, dict) and isinstance(obj.get("list"), list):
        parts: List[str] = []
        for item in obj["list"]:
            if not isinstance(item, dict):
                continue
            t = item.get("type")
            if t in _TYPE_PLACEHOLDER:
                parts.append(_TYPE_PLACEHOLDER[t])
            elif t == "text":
                parts.append(str(item.get("text") or ""))
            else:
                text = item.get("text")
                if text:
                    parts.append(str(text))
                elif t:
                    parts.append(f"[{t}]")
        return "".join(parts)
    if isinstance(content, str):
        return content
    return json.dumps(obj, ensure_ascii=False)


def _sender_name(obj: dict) -> str:
    extra = obj.get("extraParams") or {}
    if isinstance(extra, dict):
        name = extra.get("senderName")
        if name:
            return str(name)
    return ""


def _session_id_of(obj: dict) -> str:
    for key in ("groupId", "conversationId"):
        val = obj.get(key)
        if val is not None and str(val) != "":
            return str(val)
    return ""


def _matches_session(obj: dict, session: Session) -> bool:
    sid = session.id
    gid = str(obj.get("groupId") or "")
    cid = str(obj.get("conversationId") or "")
    return sid == gid or sid == cid


def _obj_to_message(obj: dict, session: Session, source: str) -> Optional[Message]:
    if not _matches_session(obj, session):
        return None
    msg_id = str(obj.get("msgId") or "")
    sender_id = str(obj.get("senderId") or "")
    body = _body_from_content(obj.get("content"))
    send_time = obj.get("sendTime")
    if send_time is not None:
        try:
            send_time = int(send_time)
        except (TypeError, ValueError):
            send_time = None
    chat_type = obj.get("chatType")
    if chat_type is not None:
        try:
            chat_type = int(chat_type)
        except (TypeError, ValueError):
            chat_type = session.chat_type
    else:
        chat_type = session.chat_type
    return Message(
        msg_id=msg_id,
        sender=_sender_name(obj),
        sender_id=sender_id,
        body=body,
        send_time=send_time,
        chat_type=chat_type,
        session_id=session.id,
        session_name=session.name,
        source=source,
    )


def parse_messages_from_blobs(blobs: Iterable[str], session: Session) -> List[Message]:
    out: List[Message] = []
    for blob in blobs:
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        msg = _obj_to_message(obj, session, "indexeddb")
        if msg is not None:
            out.append(msg)
    return out


def _decode_views(data: bytes) -> List[str]:
    """UTF-8 replace view plus UTF-16-LE both alignments (Chromium IDB)."""
    views = [data.decode("utf-8", errors="replace")]
    pad = data + (bytes([0]) if len(data) % 2 else b"")
    for off in (0, 1):
        chunk = pad[off:]
        if len(chunk) % 2:
            chunk = chunk[:-1]
        if not chunk:
            continue
        views.append(chunk.decode("utf-16-le", errors="replace"))
    return views


_CJK_RUN = re.compile(
    r"[\u4e00-\u9fff@][\u4e00-\u9fffA-Za-z0-9@，。：；、？！“”‘’（）()\s\n:./_\-]{8,500}"
)
_SUBTITLE_RUN = re.compile(
    "subTitle[" + chr(0) + r"-\x20]{0,24}([\u4e00-\u9fff@][^" + chr(0) + r"]{8,500})"
)
_SENDER_AT = re.compile(r"^([\u4e00-\u9fffA-Za-z0-9_]{2,20}):(@?.+)$")
_NOISE_FRAGMENT = re.compile(
    r"^(群|故障|内部|title|content|msgId|groupId|channel|pcId)"
)


def _clean_fragment(text: str) -> str:
    nul = chr(0)
    s = text.replace(nul, " ")
    s = "".join(ch if (ord(ch) >= 32 or ch in "\n\t") else " " for ch in s)
    s = re.sub(r"[ \t\r\f]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = s.strip()
    m2 = re.search(r"[\u4e00-\u9fff@]", s)
    if m2 and m2.start() > 0:
        s = s[m2.start() :].strip()
    return s


def _looks_like_chat(body: str) -> bool:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", body))
    if cjk < 6:
        return False
    if _NOISE_FRAGMENT.match(body):
        return False
    if cjk < 8 and not any(x in body for x in ("@", "：", ":", "http")):
        return False
    if body.count("\ufffd") > 3:
        return False
    return True


def _split_sender_body(text: str) -> Tuple[str, str]:
    m = _SENDER_AT.match(text)
    if m:
        return m.group(1), m.group(2).strip()
    return "", text


def extract_fragment_messages(data: bytes, session: Session) -> List[Message]:
    """Best-effort Chromium IDB fragment messages near session id/name."""
    needles = _session_needles(session)
    if not needles:
        return []
    bodies: List[Tuple[str, str]] = []
    seen: Set[str] = set()

    for needle in needles:
        start = 0
        while True:
            i = data.find(needle, start)
            if i < 0:
                break
            win = data[max(0, i - _FRAGMENT_WINDOW) : i + _FRAGMENT_TRAIL]
            for text in _decode_views(win):
                for m in _SUBTITLE_RUN.finditer(text):
                    frag = _clean_fragment(m.group(1))
                    frag = re.split(r'(?:"[a-zA-Z]{3,}|pcId|msgType|groupId)', frag, 1)[0]
                    frag = _clean_fragment(frag)
                    if not _looks_like_chat(frag):
                        continue
                    sender, body = _split_sender_body(frag)
                    if body in seen:
                        continue
                    seen.add(body)
                    bodies.append((sender, body))
                for m in _CJK_RUN.finditer(text):
                    frag = _clean_fragment(m.group(0))
                    if not _looks_like_chat(frag):
                        continue
                    if not (
                        "@" in frag
                        or ":" in frag[:24]
                        or "：" in frag[:24]
                        or "大佬" in frag
                        or "http" in frag
                    ):
                        continue
                    sender, body = _split_sender_body(frag)
                    if body in seen:
                        continue
                    seen.add(body)
                    bodies.append((sender, body))
            start = i + max(2, len(needle))

    out: List[Message] = []
    for idx, (sender, body) in enumerate(bodies):
        out.append(
            Message(
                msg_id=f"frag-{idx}-{hash(body) & 0xFFFFFFFF:08x}",
                sender=sender,
                sender_id="",
                body=body,
                send_time=None,
                chat_type=session.chat_type,
                session_id=session.id,
                session_name=session.name,
                source="indexeddb-fragment",
            )
        )
    return out


def _decode_forward_value(value: Any) -> Optional[dict]:
    if isinstance(value, dict) and "messages" in value:
        return value
    if not isinstance(value, str):
        return None
    raw = value
    if "base64," in raw:
        raw = raw.split("base64,", 1)[1]
    try:
        decoded = base64.b64decode(raw)
        obj = json.loads(decoded.decode("utf-8"))
    except Exception:
        try:
            obj = json.loads(value)
        except Exception:
            return None
    return obj if isinstance(obj, dict) else None


def load_forward_messages(local_cache: Path, session: Session) -> List[Message]:
    data = json.loads(Path(local_cache).read_text(encoding="utf-8"))
    out: List[Message] = []
    for key, entry in data.items():
        if not str(key).startswith("forwardMsgId_"):
            continue
        value = entry.get("value") if isinstance(entry, dict) else entry
        payload = _decode_forward_value(value)
        if not payload:
            continue
        messages = payload.get("messages")
        if not isinstance(messages, list):
            continue
        for item in messages:
            if not isinstance(item, dict):
                continue
            msg = _obj_to_message(item, session, "forward")
            if msg is not None:
                out.append(msg)
    return out


def _dedupe_key(m: Message) -> str:
    if m.msg_id and not str(m.msg_id).startswith("frag-"):
        return f"id:{m.msg_id}"
    return f"h:{hash((m.sender, m.body, m.send_time))}"


def _filter_days(messages: List[Message], days: Optional[int]) -> List[Message]:
    if days is None:
        return messages
    now_ms = int(time.time() * 1000)
    cutoff = now_ms - int(days) * 86_400_000
    kept: List[Message] = []
    for m in messages:
        if m.send_time is None:
            continue
        if m.send_time >= cutoff:
            kept.append(m)
    return kept


def _prefilter_files_for_session(
    src: Path,
    files: Sequence[Path],
    session: Session,
) -> List[Path]:
    """Keep files whose bytes contain session id/name (ASCII or UTF-16-LE)."""
    needles = _session_needles(session)
    kept: List[Path] = []
    for path in files:
        try:
            data = Path(path).read_bytes()
        except OSError:
            kept.append(Path(path))
            continue
        if _file_contains_any(data, needles):
            kept.append(Path(path))
    return kept


def collect_messages(
    paths: BaohePaths,
    session: Session,
    *,
    days: Optional[int] = None,
) -> List[Message]:
    """Copy selected LevelDB files, scan blobs + IDB fragments, merge forwards."""
    with tempfile.TemporaryDirectory(prefix="baohe-leveldb-") as tmp:
        dest = Path(tmp) / "leveldb"
        candidates = select_leveldb_files(paths.leveldb_dir, scan_ldb=False)
        ldb_pool = select_leveldb_files(paths.leveldb_dir, scan_ldb=True)
        ldb_only = [p for p in ldb_pool if p.name.lower().endswith(".ldb")]
        to_copy = _prefilter_files_for_session(
            paths.leveldb_dir, list(candidates) + ldb_only, session
        )
        from_db: List[Message] = []
        if to_copy:
            copy_selected_leveldb_files(paths.leveldb_dir, dest, to_copy)
            blobs = iter_raw_json_blobs(dest, session_id=session.id, scan_ldb=True)
            from_db.extend(parse_messages_from_blobs(blobs, session))
            for path in sorted(dest.rglob("*")):
                if not path.is_file():
                    continue
                lower = path.name.lower()
                if not (lower.endswith(".log") or lower.endswith(".ldb")):
                    continue
                try:
                    raw = path.read_bytes()
                except OSError:
                    continue
                from_db.extend(extract_fragment_messages(raw, session))
    from_fwd = load_forward_messages(paths.local_cache, session)
    merged = list(from_db) + list(from_fwd)
    seen: Set[str] = set()
    unique: List[Message] = []
    for m in merged:
        key = _dedupe_key(m)
        if key in seen:
            continue
        seen.add(key)
        unique.append(m)
    unique = _filter_days(unique, days)
    unique.sort(key=lambda m: (m.send_time is None, m.send_time or 0, m.msg_id))
    return unique
