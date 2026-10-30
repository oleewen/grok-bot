from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


DEFAULT_DATA_DIR = Path.home() / "Library" / "Application Support" / "zbox-lite"


@dataclass(frozen=True)
class BaohePaths:
    data_dir: Path
    local_cache: Path
    local_cache2: Optional[Path]
    leveldb_dir: Path
    uid: str


def resolve_paths(data_dir: Optional[Path] = None) -> BaohePaths:
    root = Path(
        data_dir
        if data_dir is not None
        else os.environ.get("BAOHE_DATA_DIR", DEFAULT_DATA_DIR)
    ).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(
            f"找不到宝盒数据目录: {root}。请确认已安装宝盒lite，或用 --data-dir / BAOHE_DATA_DIR 指定。"
        )
    cache = root / "localCache.json"
    if not cache.is_file():
        raise FileNotFoundError(
            f"缺少 localCache.json: {cache}。可能不是有效的 zbox-lite 数据根。"
        )
    cache2 = root / "localCache2.json"
    cache2_path = cache2 if cache2.is_file() else None
    parts = root / "Partitions"
    candidates = sorted(parts.glob("baohe_*")) if parts.is_dir() else []
    if not candidates:
        raise FileNotFoundError(
            f"在 {parts} 下找不到 baohe_* 分区。宝盒可能未登录过。"
        )
    part = candidates[0]
    uid = part.name.split("_", 1)[1]
    leveldb = part / "IndexedDB" / "file__0.indexeddb.leveldb"
    if not leveldb.is_dir():
        raise FileNotFoundError(
            f"缺少 IndexedDB LevelDB 目录: {leveldb}。可尝试关闭宝盒后重试。"
        )
    return BaohePaths(
        data_dir=root,
        local_cache=cache,
        local_cache2=cache2_path,
        leveldb_dir=leveldb,
        uid=uid,
    )
