from pathlib import Path

from baohe.discover import resolve_paths
from baohe.messages import _filter_days, collect_messages, load_forward_messages, parse_messages_from_blobs
from baohe.models import Message, Session
from baohe.sessions import find_session, load_sessions

FIXTURE = Path(__file__).parent / "fixtures" / "fake_zbox"


def test_collect_messages_filters_by_session():
    paths = resolve_paths(FIXTURE)
    sessions = load_sessions(paths.local_cache)
    session = find_session(sessions, "日志采集治理")
    assert session is not None
    msgs = collect_messages(paths, session, days=None)
    assert any("治理目标" in m.body for m in msgs)
    assert all(m.session_id == session.id for m in msgs)
    # forward fixture should merge in
    assert any(m.source == "forward" and "转发缓存" in m.body for m in msgs)
    # wrong session filtered
    assert not any("别的群" in m.body or "不应出现" in m.body for m in msgs)
    # image placeholder
    assert any("[图片]" in m.body for m in msgs)
    # sorted by send_time ascending
    times = [m.send_time for m in msgs if m.send_time is not None]
    assert times == sorted(times)


def test_collect_messages_days_filter(monkeypatch):
    paths = resolve_paths(FIXTURE)
    session = Session(id="2100049657645613109", name="日志采集治理", chat_type=1, group=True)
    # sendTime 在 fixture 内；days=0 应滤掉很旧的（若实现用 now-days）
    msgs = collect_messages(paths, session, days=0)
    # days=0 → 仅「今天」；fixture 时间戳固定为 2024 附近，应为空或极少
    assert isinstance(msgs, list)
    assert msgs == []


def test_load_forward_messages_only_matching_session():
    paths = resolve_paths(FIXTURE)
    session = Session(id="2100049657645613109", name="日志采集治理", chat_type=1, group=True)
    msgs = load_forward_messages(paths.local_cache, session)
    assert len(msgs) == 1
    assert msgs[0].msg_id == "fwd1"
    assert msgs[0].source == "forward"
    assert msgs[0].sender == "转发人"


def test_session_prefilter_skips_ldb_without_id(tmp_path, monkeypatch):
    """Huge/garbage .ldb without session id must not be brace-parsed; .log still yields msg."""
    import baohe.messages as messages

    sid = "2100049657645613109"
    root = tmp_path / "leveldb"
    root.mkdir()
    obj = {
        "msgId": "from-log",
        "senderId": "u1",
        "extraParams": {"senderName": "A"},
        "content": '{"list":[{"type":"text","text":"来自log"}]}',
        "groupId": sid,
        "chatType": 1,
        "sendTime": 1700000000000,
    }
    import json as _json
    (root / "000001.log").write_bytes(b"NOISE" + _json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    garbage = ("{" * 5000 + "x" * 2000 + "}" * 5000).encode("utf-8")
    (root / "000002.ldb").write_bytes(garbage)

    scan_calls = []
    real_scan = messages._scan_file_for_blobs

    def spy_scan(path, *, session_id=None, raw=None):
        scan_calls.append(Path(path).name)
        return real_scan(path, session_id=session_id, raw=raw)

    monkeypatch.setattr(messages, "_scan_file_for_blobs", spy_scan)
    monkeypatch.delenv("BAOHE_SCAN_LDB", raising=False)

    selected = messages.select_leveldb_files(root, scan_ldb=False)
    assert [p.name for p in selected] == ["000001.log"]

    blobs = messages.iter_raw_json_blobs(root, session_id=sid, scan_ldb=False)
    session = Session(id=sid, name="日志采集治理", chat_type=1, group=True)
    msgs = parse_messages_from_blobs(blobs, session)
    assert any(m.msg_id == "from-log" and "来自log" in m.body for m in msgs)
    # Default path only considers .log
    assert scan_calls == ["000001.log"]

    scan_calls.clear()
    # With scan_ldb: both files are candidates, but .ldb without session id
    # returns early inside _scan_file_for_blobs (no JSON work needed beyond needle).
    brace_calls = []
    real_bal = messages._extract_balanced_from

    def spy_bal(text, start, *, max_span=messages._MAX_JSON_SPAN):
        brace_calls.append((start, len(text)))
        return real_bal(text, start, max_span=max_span)

    monkeypatch.setattr(messages, "_extract_balanced_from", spy_bal)
    blobs2 = messages.iter_raw_json_blobs(root, session_id=sid, scan_ldb=True)
    assert any("from-log" in b for b in blobs2)
    assert "000001.log" in scan_calls
    assert "000002.ldb" in scan_calls  # selected, but prefilter exits before balanced extract
    # No balanced extract should run against the huge garbage .ldb text
    assert all(span < 10_000 for _, span in brace_calls)
    assert len(brace_calls) >= 1


def test_select_leveldb_files_respects_env(tmp_path, monkeypatch):
    import baohe.messages as messages

    root = tmp_path / "leveldb"
    root.mkdir()
    (root / "a.log").write_bytes(b"x")
    (root / "b.ldb").write_bytes(b"y")
    monkeypatch.delenv("BAOHE_SCAN_LDB", raising=False)
    assert [p.name for p in messages.select_leveldb_files(root)] == ["a.log"]
    monkeypatch.setenv("BAOHE_SCAN_LDB", "1")
    names = [p.name for p in messages.select_leveldb_files(root)]
    assert names == ["a.log", "b.ldb"]

def test_filter_days_drops_none_send_time_when_days_set():
    """When days is set, messages without send_time must be excluded."""
    with_time = Message(
        msg_id="t1",
        sender="A",
        sender_id="u1",
        body="有时间戳",
        send_time=1_700_000_000_000,
        chat_type=1,
        session_id="s1",
        session_name="测试",
        source="indexeddb",
    )
    no_time = Message(
        msg_id="frag-0",
        sender="B",
        sender_id="",
        body="无时间戳片段",
        send_time=None,
        chat_type=1,
        session_id="s1",
        session_name="测试",
        source="indexeddb-fragment",
    )
    msgs = [with_time, no_time]
    kept_no_days = _filter_days(msgs, None)
    assert len(kept_no_days) == 2
    assert any(m.send_time is None for m in kept_no_days)

    kept_with_days = _filter_days(msgs, days=36500)  # generous window keeps with_time
    assert all(m.send_time is not None for m in kept_with_days)
    assert any(m.msg_id == "t1" for m in kept_with_days)
    assert not any(m.send_time is None for m in kept_with_days)
    assert not any(m.msg_id == "frag-0" for m in kept_with_days)
