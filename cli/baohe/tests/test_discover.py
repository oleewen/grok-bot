from pathlib import Path

import pytest

from baohe.discover import resolve_paths


FIXTURE = Path(__file__).parent / "fixtures" / "fake_zbox"


def test_resolve_paths_from_explicit_dir():
    p = resolve_paths(FIXTURE)
    assert p.data_dir == FIXTURE.resolve()
    assert p.local_cache.name == "localCache.json"
    assert p.local_cache2 is not None
    assert p.local_cache2.name == "localCache2.json"
    assert p.leveldb_dir.name == "file__0.indexeddb.leveldb"
    assert p.uid == "6458944"


def test_resolve_paths_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError) as ei:
        resolve_paths(tmp_path / "nope")
    assert "zbox-lite" in str(ei.value).lower() or "找不到" in str(ei.value)


def test_resolve_paths_without_cache2(tmp_path):
    # Minimal valid tree without localCache2
    cache = tmp_path / "localCache.json"
    cache.write_text('{"search_history":{"key":"search_history","type":"data","value":[]}}', encoding="utf-8")
    part = tmp_path / "Partitions" / "baohe_1"
    ldb = part / "IndexedDB" / "file__0.indexeddb.leveldb"
    ldb.mkdir(parents=True)
    (ldb / "x.log").write_bytes(b"")
    p = resolve_paths(tmp_path)
    assert p.local_cache2 is None
