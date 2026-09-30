import json
from pathlib import Path

import pytest

from clipper import cli
from clipper.config import Settings


@pytest.mark.parametrize("argv", [["x", "--min", "nan"], ["x", "--max", "inf"], ["x", "-n", "0"], ["x", "-n", "-1"]])
def test_invalid_numbers_fail_before_any_work(argv):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 2


def test_missing_source_fails_before_preflight(monkeypatch, tmp_path):
    from clipper import runtime
    monkeypatch.setattr(runtime, "preflight", lambda *a, **kw: pytest.fail("preflight reached"))
    with pytest.raises(SystemExit) as exc:
        cli.main([str(tmp_path / "missing.mp4")])
    assert exc.value.code == 2


def test_bad_range_fails_before_download(monkeypatch):
    from clipper import download
    monkeypatch.setattr(download, "fetch", lambda *a, **kw: pytest.fail("download reached"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["https://youtu.be/test", "--min", "60", "--max", "15"])
    assert exc.value.code == 2


def test_json_has_only_one_object_and_nonzero_failure(monkeypatch, capsys):
    def fake_execute(args, parser):
        print("download progress")
        return {"status": "partial", "clips": ["clip_00.mp4"]}, 1
    monkeypatch.setattr(cli, "execute", fake_execute)
    assert cli.main(["video.mp4", "--json"]) == 1
    result = capsys.readouterr()
    assert json.loads(result.out)["status"] == "partial"
    assert "download progress" in result.err


def test_runtime_error_is_actionable_json(monkeypatch, capsys):
    monkeypatch.setattr(cli, "execute", lambda *a: (_ for _ in ()).throw(RuntimeError("missing key")))
    assert cli.main(["x", "--json"]) == 1
    result = capsys.readouterr()
    assert json.loads(result.out)["status"] == "failed"
    assert "Traceback" not in result.err


def test_cache_identity_includes_selection_and_source(tmp_path):
    a, b = tmp_path / "one.mp4", tmp_path / "two.mp4"
    a.write_bytes(b"one")
    b.write_bytes(b"two")
    s = Settings()
    before = cli.cache_key(a, s)
    assert before != cli.cache_key(b, s)
    s.max_secs = 20
    assert before != cli.cache_key(a, s)
    before = cli.cache_key(a, s)
    a.write_bytes(b"changed footage")
    assert before != cli.cache_key(a, s)


def test_manifest_url_removes_tokens():
    assert cli._public_url("https://youtube.com/watch?v=abc&token=secret#fragment") == "https://youtube.com/watch?v=abc"
