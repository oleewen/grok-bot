from pathlib import Path

from baohe.discover import resolve_paths
from baohe.sessions import (
    discover_sessions,
    find_session,
    load_sessions,
    load_sessions_from_cache_file,
    merge_sessions,
    mine_sessions_from_leveldb,
)

FIXTURE = Path(__file__).parent / "fixtures" / "fake_zbox"
CACHE = FIXTURE / "localCache.json"
CACHE2 = FIXTURE / "localCache2.json"


def test_load_sessions_from_search_history():
    sessions = load_sessions(CACHE)
    assert len(sessions) == 3
    names = {s.name for s in sessions}
    assert "日志采集治理" in names
    g = next(s for s in sessions if s.id == "2100049657645613109")
    assert g.group is True


def test_load_cache2_extra_session():
    sessions = load_sessions_from_cache_file(CACHE2)
    names = {s.name for s in sessions}
    assert "盘古&财务中心对接" in names


def test_merge_sessions_dedupe_prefers_longer_name():
    from baohe.models import Session

    a = Session(id="1", name="短", chat_type=1, group=True)
    b = Session(id="1", name="更长的名字", chat_type=1, group=True)
    c = Session(id="2", name="另一", chat_type=0, group=False)
    merged = merge_sessions([a, c], [b])
    assert len(merged) == 2
    assert merged[0].id == "1"
    assert merged[0].name == "更长的名字"
    assert merged[1].id == "2"


def test_mine_sessions_from_fixture_leveldb():
    paths = resolve_paths(FIXTURE)
    mined = mine_sessions_from_leveldb(paths.leveldb_dir)
    ids = {s.id for s in mined}
    names = {s.name for s in mined}
    assert "2116010121734451271" in ids
    assert any("问题-20261030" in n for n in names)
    assert "2116010121734451999" in ids
    assert "测试挖掘群名ABC" in names


def test_discover_sessions_merges_cache_and_mine():
    paths = resolve_paths(FIXTURE)
    sessions = discover_sessions(paths)
    names = {s.name for s in sessions}
    # from cache1
    assert "日志采集治理" in names
    assert "增值计费系统" in names
    # from cache2 only
    assert "盘古&财务中心对接" in names
    # from leveldb mine
    assert any("问题-20261030" in n for n in names)
    # dedupe cache1/cache2 same id
    assert sum(1 for s in sessions if s.id == "2100049657645613109") == 1


def test_find_session_by_name_substring():
    sessions = load_sessions(CACHE)
    s = find_session(sessions, "日志采集")
    assert s is not None
    assert s.id == "2100049657645613109"


def test_find_session_by_id():
    sessions = load_sessions(CACHE)
    s = find_session(sessions, "1991687173296697422")
    assert s.name == "增值计费系统"


def test_find_session_miss():
    assert find_session(load_sessions(CACHE), "不存在的群") is None
