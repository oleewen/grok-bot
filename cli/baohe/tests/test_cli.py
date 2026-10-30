from pathlib import Path

from baohe.cli import main

FIXTURE = str(Path(__file__).parent / "fixtures" / "fake_zbox")


def test_list_prints_session(capsys):
    code = main(["--data-dir", FIXTURE, "list"])
    assert code == 0
    out = capsys.readouterr().out
    assert "日志采集治理" in out
    assert "group" in out or "dm" in out


def test_export_writes_files(tmp_path):
    code = main(
        [
            "--data-dir",
            FIXTURE,
            "export",
            "日志采集治理",
            "-o",
            str(tmp_path / "out"),
        ]
    )
    assert code == 0
    assert any(tmp_path.rglob("messages.json"))
    assert any(tmp_path.rglob("report.md"))
    md = next(tmp_path.rglob("report.md")).read_text(encoding="utf-8")
    assert "本地缓存" in md
    assert "非全量" in md


def test_export_missing_session():
    code = main(["--data-dir", FIXTURE, "export", "绝对不存在xyz"])
    assert code == 2


def test_path_error_returns_1():
    code = main(["--data-dir", "/no/such/baohe/data", "list"])
    assert code == 1


def test_recent_writes_index(tmp_path, capsys):
    out = tmp_path / "batch"
    code = main(
        [
            "--data-dir",
            FIXTURE,
            "recent",
            "--limit",
            "5",
            "-o",
            str(out),
        ]
    )
    assert code == 0
    assert (out / "index.md").is_file()
    text = (out / "index.md").read_text(encoding="utf-8")
    assert "日志采集治理" in text


def test_list_days_filters_empty(capsys):
    """days=0 excludes all fixture messages (timestamps are historical)."""
    code = main(["--data-dir", FIXTURE, "list", "--days", "0"])
    assert code == 0
    out = capsys.readouterr().out.strip()
    assert out == ""
    assert "日志采集治理" not in out


def test_list_days_keeps_session_with_messages(capsys):
    """Wide days window still lists sessions that have cached messages."""
    code = main(["--data-dir", FIXTURE, "list", "--days", "36500"])
    assert code == 0
    out = capsys.readouterr().out
    assert "日志采集治理" in out


def test_recent_zero_messages_exits_2(tmp_path, capsys):
    """Extreme days filter → no exports → exit 2, not success with empty index."""
    out = tmp_path / "batch"
    code = main(
        [
            "--data-dir",
            FIXTURE,
            "recent",
            "--days",
            "0",
            "--limit",
            "5",
            "-o",
            str(out),
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "零命中" in err or "零" in err
