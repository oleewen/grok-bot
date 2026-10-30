from pathlib import Path
import json

from baohe.export import default_out_dir, write_export, write_recent_index
from baohe.models import Message, Session


def test_write_export_creates_json_and_md(tmp_path):
    session = Session(id="2100", name="日志采集治理", chat_type=1, group=True)
    msgs = [
        Message("m1", "高明", "u1", "结论一条", 1728800000000, 1, "2100", "日志采集治理", "indexeddb")
    ]
    out = tmp_path / "one"
    jp, mp = write_export(
        out,
        session,
        msgs,
        coverage_note="本地缓存尽力而为，非全量历史。",
    )
    assert jp.is_file() and mp.is_file()
    text = mp.read_text(encoding="utf-8")
    assert "本地缓存" in text
    assert "关键结论" in text
    assert "结论一条" in text
    assert "日志采集治理" in text
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data["session"]["id"] == "2100"
    assert data["coverageNote"] == "本地缓存尽力而为，非全量历史。"
    assert data["messages"][0]["body"] == "结论一条"


def test_write_recent_index(tmp_path):
    p = tmp_path / "index.md"
    write_recent_index(
        p,
        [{"name": "日志采集治理", "id": "2100", "count": 3, "dir": "rizhi-xxx"}],
    )
    assert "日志采集治理" in p.read_text(encoding="utf-8")


def test_default_out_dir_slug(tmp_path):
    p = default_out_dir("日志采集治理!", root=tmp_path)
    assert p.parent == tmp_path.resolve()
    assert "日志采集治理" in p.name
    assert "!" not in p.name
