from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .discover import BaohePaths
from .models import Session

# Prefer *.log; also allow ldb under this size for session mining (not message dump).
_DEFAULT_MINE_LDB_CAP = 48 * 1024 * 1024

_ID_RE = re.compile(
    r"(?:groupId|conversationId|contact_id|sessionId)\s*[\"\']?\s*[:=]\s*[\"\']?(\d{10,22})"
)
_NAME_KEYS = r"(?:groupName|contact_name|title|name|conversationName|sessionName)"
# Ban quotes/NUL/controls in names; digits and CJK allowed.
_NAME_CHARS = "[^\x00-\x1f\"\']{2,120}"
_NAME_AFTER_ID = re.compile(
    r"(?:groupId|conversationId|contact_id|sessionId)\s*[\"\']?\s*[:=]\s*[\"\']?(\d{10,22})"
    r".{0,500}?"
    + _NAME_KEYS
    + r"\s*[\"\']?\s*[:=]\s*[\"\']("
    + _NAME_CHARS
    + r")",
    re.DOTALL,
)
_ID_AFTER_NAME = re.compile(
    _NAME_KEYS
    + r"\s*[\"\']?\s*[:=]\s*[\"\']("
    + _NAME_CHARS
    + r").{0,500}?"
    r"(?:groupId|conversationId|contact_id|sessionId)\s*[\"\']?\s*[:=]\s*[\"\']?(\d{10,22})",
    re.DOTALL,
)

_CHAT_TYPE_NEAR = re.compile(
    r"(?:chatType|chat_type|type)\s*[\"']?\s*[:=]\s*[\"']?(\d)",
)


def _name_of(item: dict) -> str:
    for k in ("name", "groupName", "title", "conversationName", "nickname"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _id_of(item: dict) -> Optional[str]:
    for k in ("id", "groupId", "conversationId", "sessionId", "targetId"):
        v = item.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def _chat_type_of(item: dict) -> int:
    for k in ("chatType", "type", "conversationType"):
        v = item.get(k)
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.isdigit():
            return int(v)
    return 1


def _is_noise_name(name: str) -> bool:
    n = name.strip()
    if len(n) < 2:
        return True
    low = n.lower()
    if low in {
        "undefined",
        "null",
        "true",
        "false",
        "object",
        "string",
        "number",
        "grouptype",
        "chattype",
        "contact_id",
        "contact_name",
        "groupid",
        "groupname",
        "sessionid",
        "stick_on_top",
        "unread_msg_count",
        "typename",
        "defaultsub",
    }:
        return True
    # discard pure hex / uuid-looking short tokens without CJK
    if re.fullmatch(r"[0-9a-fA-F-]{8,}", n) and not re.search(r"[\u4e00-\u9fff]", n):
        return True
    # mostly replacement / mojibake
    if n.count("\ufffd") >= max(1, len(n) // 4):
        return True
    return False


def _session_from_item(item: dict) -> Optional[Session]:
    if not isinstance(item, dict):
        return None
    sid = _id_of(item)
    name = _name_of(item)
    if not sid or not name or _is_noise_name(name):
        return None
    ct = _chat_type_of(item)
    return Session(id=sid, name=name, chat_type=ct, group=(ct == 1))


def _sessions_from_search_history_value(value) -> List[Session]:
    out: List[Session] = []
    if not isinstance(value, list):
        return out
    for item in value:
        s = _session_from_item(item)
        if s:
            out.append(s)
    return out


def load_sessions_from_cache_file(cache_path: Path) -> List[Session]:
    """Parse one localCache*.json search_history into Session list."""
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    block = data.get("search_history")
    if not isinstance(block, dict):
        return []
    return _sessions_from_search_history_value(block.get("value"))


def load_sessions(cache_path: Path) -> List[Session]:
    """Backward-compatible: sessions from a single cache file."""
    return load_sessions_from_cache_file(cache_path)


def merge_sessions(*groups: Sequence[Session]) -> List[Session]:
    """Dedupe by id; prefer longer / more informative name; keep first-seen order of first source."""
    by_id: Dict[str, Session] = {}
    order: List[str] = []
    for group in groups:
        for s in group:
            if not s.id:
                continue
            prev = by_id.get(s.id)
            if prev is None:
                by_id[s.id] = s
                order.append(s.id)
                continue
            # Prefer longer name; if tie, prefer one with CJK / group flag already set
            if len(s.name) > len(prev.name) or (
                len(s.name) == len(prev.name)
                and ("-" in s.name or "群" in s.name)
                and not ("-" in prev.name or "群" in prev.name)
            ):
                by_id[s.id] = Session(
                    id=s.id,
                    name=s.name,
                    chat_type=s.chat_type if s.chat_type is not None else prev.chat_type,
                    group=s.group or prev.group,
                )
            elif prev.name and s.name and prev.name != s.name and len(s.name) == len(prev.name):
                pass
    return [by_id[i] for i in order]


def find_session(sessions: Sequence[Session], query: str) -> Optional[Session]:
    q = query.strip()
    if not q:
        return None
    for s in sessions:
        if s.id == q:
            return s
    exact = [s for s in sessions if s.name == q]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ValueError(f"多个会话同名 {q!r}: {[s.id for s in exact]}")
    partial = [s for s in sessions if q in s.name]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        raise ValueError(
            f"多个会话匹配 {q!r}: " + ", ".join(f"{s.name}({s.id})" for s in partial[:8])
        )
    return None


def _decode_texts(data: bytes) -> List[str]:
    texts = [data.decode("utf-8", errors="replace")]
    # LevelDB blobs mix encodings; try both UTF-16 alignments.
    for offset in (0, 1):
        chunk = data[offset:]
        if len(chunk) < 4:
            continue
        if len(chunk) % 2:
            chunk = chunk[:-1]
        texts.append(chunk.decode("utf-16-le", errors="replace"))
    return texts


def _chat_type_from_window(window: str, default: int = 1) -> int:
    m = _CHAT_TYPE_NEAR.search(window)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return default


def _extract_sessions_from_text(text: str) -> Dict[str, Session]:
    found: Dict[str, Session] = {}

    def add(sid: str, name: str, window: str) -> None:
        name = name.strip().strip("\\").strip()
        if not sid or _is_noise_name(name):
            return
        # Heuristic: long digit-only id + multi-char name; groups often chatType 1
        ct = _chat_type_from_window(window, default=1)
        # Names with many members markers / 问题- prefix → group
        if "问题-" in name or "对接" in name or "群" in name or "、" in name:
            ct = 1
        s = Session(id=sid, name=name, chat_type=ct, group=(ct == 1))
        prev = found.get(sid)
        if prev is None or len(name) > len(prev.name):
            found[sid] = s

    for m in _NAME_AFTER_ID.finditer(text):
        sid, name = m.group(1), m.group(2)
        add(sid, name, m.group(0))
    for m in _ID_AFTER_NAME.finditer(text):
        name, sid = m.group(1), m.group(2)
        add(sid, name, m.group(0))
    return found


def _iter_mine_files(leveldb_dir: Path) -> Iterable[Path]:
    files = [p for p in leveldb_dir.iterdir() if p.is_file()]
    scan_all = os.environ.get("BAOHE_SCAN_LDB", "").lower() in ("1", "true", "yes")
    try:
        ldb_budget = int(os.environ.get("BAOHE_MINE_LDB_FILES", "60"))
    except ValueError:
        ldb_budget = 60
    try:
        small_cap = int(os.environ.get("BAOHE_MINE_SMALL_LDB", str(512 * 1024)))
    except ValueError:
        small_cap = 512 * 1024
    logs: List[Path] = []
    ldbs: List[Path] = []
    for p in files:
        name = p.name
        if name.endswith(".log"):
            logs.append(p)
        elif name.endswith(".ldb"):
            ldbs.append(p)
    logs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    ldbs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for p in logs:
        yield p
    if scan_all:
        for p in ldbs:
            yield p
        return
    yielded = 0
    for p in ldbs:
        try:
            sz = p.stat().st_size
        except OSError:
            continue
        if yielded < ldb_budget or sz <= small_cap:
            yield p
            yielded += 1


_ID_KEYS_BIN = (b"contact_id", b"groupId", b"conversationId")
_TITLE_MARK_U16 = "问题-".encode("utf-16-le")
_LEFT_BRACKET_U16 = "【".encode("utf-16-le")
_STAR_U16 = "★".encode("utf-16-le")
_NAME_HINT_BIN = (b"contact_name", b"groupName", b"conversationName")
_UTF16_NAME_OK = set(range(0x30, 0x3A)) | set(range(0x41, 0x5B)) | set(range(0x61, 0x7B))
_UTF16_NAME_OK |= {
    0x2D,
    0x26,
    0x5F,
    0x2C,
    0x2E,
    0x3A,
    0xFF0C,
    0x3001,
    0x3002,
    0xFF08,
    0xFF09,
    0x3010,
    0x3011,
    0x2605,
    0xFF1A,
}
_MSG_LIKE = (
    "。",
    "？",
    "?",
    "！",
    "!",
    "吗",
    "吧",
    "呢",
    "啦",
    "嗯",
    "我们",
    "你们",
    "他们",
    "一个",
    "一下",
    "一会",
    "一般",
    "什么",
    "怎么",
    "如果",
    "就是",
    "然后",
    "可以",
    "已经",
    "不要",
)


def _utf16_cjk_runs(data: bytes) -> List[Tuple[int, str]]:
    """Contiguous UTF-16-LE runs that may be chat titles."""
    out: List[Tuple[int, str]] = []
    n = len(data)
    for align in (0, 1):
        i = align
        while i + 1 < n:
            code = data[i] | (data[i + 1] << 8)
            if 0x4E00 <= code <= 0x9FFF:
                j = i
                chars: List[str] = []
                while j + 1 < n:
                    c = data[j] | (data[j + 1] << 8)
                    if 0x4E00 <= c <= 0x9FFF or c in _UTF16_NAME_OK:
                        chars.append(chr(c))
                        j += 2
                    else:
                        break
                if len(chars) >= 2:
                    s = "".join(chars)
                    if sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff") >= 2:
                        out.append((i, s))
                i = max(j, i + 2)
            else:
                i += 2
    return out


def _looks_like_session_title(name: str) -> bool:
    n = name.strip()
    if not (2 <= len(n) <= 80):
        return False
    if _is_noise_name(n):
        return False
    if any(tok in n for tok in _MSG_LIKE):
        return False
    if n.startswith(("一、", "二、", "三、", "四、", "五、")):
        return False
    cjk = sum(1 for ch in n if "\u4e00" <= ch <= "\u9fff")
    if cjk < 2:
        return False
    if re.search(r"问题-\d{6,8}", n):
        return True
    if "&" in n or "【" in n or "】" in n or "★" in n:
        return True
    if re.search(r"\d{8}", n) and "-" in n:
        return True
    if len(n) <= 24:
        return True
    return False


def _is_strong_title(name: str) -> bool:
    n = name.strip()
    if not _looks_like_session_title(n):
        return False
    if re.search(r"问题-\d{6,8}", n):
        return True
    if "&" in n or "【" in n or "】" in n or "★" in n:
        return True
    if re.search(r"\d{8}", n) and "-" in n:
        return True
    return False


def _pick_best_name(names: Sequence[str]) -> Optional[str]:
    cleaned = [n.strip() for n in names if _looks_like_session_title(n)]
    if not cleaned:
        return None
    cleaned.sort(
        key=lambda s: (
            _is_strong_title(s),
            "盘古" in s,
            "&" in s or "【" in s,
            len(s),
        ),
        reverse=True,
    )
    return cleaned[0]


def _score_id_near_title(data: bytes, id_abs: int, id_len: int, title_abs: int) -> Tuple[int, int]:
    """Higher is better: length-prefix bonus, then closer before title."""
    bonus = 0
    if id_abs >= 2 and data[id_abs - 2] == 0x22 and data[id_abs - 1] == id_len:
        bonus = 1000
    elif id_abs >= 1 and data[id_abs - 1] == id_len:
        bonus = 500
    dist = title_abs - id_abs
    return (bonus, -abs(dist) if dist < 0 else -dist)


def _mine_binary_sessions(data: bytes) -> Dict[str, Session]:
    """Local-only: strong UTF-16 titles ↔ nearby digit ids; plus id-key pairs."""
    found: Dict[str, Session] = {}

    # Path A: title-first around strong UTF-16 markers (handles length-prefixed ids).
    mark_hits: List[int] = []
    for mark in (_TITLE_MARK_U16, _LEFT_BRACKET_U16, _STAR_U16):
        start = 0
        while True:
            i = data.find(mark, start)
            if i < 0:
                break
            mark_hits.append(i)
            start = i + len(mark)
    seen_offsets = set()
    for hit in mark_hits:
        lo = max(0, hit - 80)
        hi = min(len(data), hit + 240)
        for offset, name in _utf16_cjk_runs(data[lo:hi]):
            abs_off = lo + offset
            if abs_off in seen_offsets or not _is_strong_title(name):
                continue
            seen_offsets.add(abs_off)
            wlo = max(0, abs_off - 700)
            whi = min(len(data), abs_off + 500)
            window = data[wlo:whi]
            scored: List[Tuple[Tuple[int, int], str]] = []
            for m in re.finditer(rb"(\d{10,22})", window):
                sid = m.group(1).decode("ascii")
                abs_pos = wlo + m.start()
                scored.append(
                    (_score_id_near_title(data, abs_pos, len(sid), abs_off), sid)
                )
            if not scored:
                continue
            scored.sort(reverse=True)
            sid = scored[0][1]
            prev = found.get(sid)
            if prev is None or len(name) > len(prev.name):
                found[sid] = Session(id=sid, name=name, chat_type=1, group=True)

    # Path B (optional): DM/weak titles via id keys — off by default (noisy/slow).
    if os.environ.get("BAOHE_MINE_DM", "").lower() not in ("1", "true", "yes"):
        return found
    if len(data) > 8 * 1024 * 1024:
        return found
    anchors: List[Tuple[int, str]] = []
    for key in _ID_KEYS_BIN:
        start = 0
        while True:
            i = data.find(key, start)
            if i < 0:
                break
            m = re.search(rb"(\d{10,22})", data[i : i + 120])
            if m:
                anchors.append((i + m.start(), m.group(1).decode("ascii")))
            start = i + len(key)
    for pos, sid in anchors:
        if sid in found:
            continue
        lo = max(0, pos - 500)
        hi = min(len(data), pos + 800)
        region = data[lo:hi]
        if not any(h in region for h in _NAME_HINT_BIN):
            continue
        runs = _utf16_cjk_runs(region)
        name = _pick_best_name([s for _, s in runs])
        if not name:
            continue
        ct = 0 if (len(name) <= 4 and not _is_strong_title(name)) else 1
        found[sid] = Session(id=sid, name=name, chat_type=ct, group=(ct == 1))
    return found



def mine_sessions_from_leveldb(leveldb_dir: Path) -> List[Session]:
    """Best-effort: extract session id+name pairs from IndexedDB LevelDB bytes (local only)."""
    by_id: Dict[str, Session] = {}
    for path in _iter_mine_files(leveldb_dir):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        # Cheap prefilter: id keys, groupName, or UTF-16 title markers
        has_title_mark = _TITLE_MARK_U16 in data or _LEFT_BRACKET_U16 in data or _STAR_U16 in data
        has_id_key = (
            b"groupId" in data
            or b"contact_id" in data
            or b"conversationId" in data
            or b"groupName" in data
            or b"g\x00r\x00o\x00u\x00p\x00I\x00d" in data
            or b"c\x00o\x00n\x00t\x00a\x00c\x00t\x00_\x00i\x00d" in data
        )
        if not has_title_mark and not has_id_key:
            continue
        # JSON-ish extract only on smaller files / logs (full unicode decode is expensive)
        if path.name.endswith(".log") or len(data) <= 2 * 1024 * 1024:
            for text in _decode_texts(data):
                for sid, sess in _extract_sessions_from_text(text).items():
                    prev = by_id.get(sid)
                    if prev is None or len(sess.name) > len(prev.name):
                        by_id[sid] = sess
        for sid, sess in _mine_binary_sessions(data).items():
            prev = by_id.get(sid)
            if prev is None or len(sess.name) > len(prev.name):
                by_id[sid] = sess
    # Stable order: name then id
    return sorted(by_id.values(), key=lambda s: (s.name, s.id))


def discover_sessions(paths: BaohePaths) -> List[Session]:
    """Merge search_history from localCache + localCache2 and LevelDB-mined id+name pairs."""
    cache_lists: List[List[Session]] = []
    cache_lists.append(load_sessions_from_cache_file(paths.local_cache))
    if paths.local_cache2 is not None:
        try:
            cache_lists.append(load_sessions_from_cache_file(paths.local_cache2))
        except (OSError, json.JSONDecodeError, UnicodeError):
            pass
    mined: List[Session] = []
    try:
        mined = mine_sessions_from_leveldb(paths.leveldb_dir)
    except OSError:
        mined = []
    # Prefer cache order first (recent search), then mined extras
    return merge_sessions(*cache_lists, mined)


def list_sessions_payload(sessions: Sequence[Session]) -> List[dict]:
    return [s.to_dict() for s in sessions]
