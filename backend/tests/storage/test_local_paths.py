import importlib
import sqlite3

from app.storage import local_paths


def test_ensure_snapshot_dir_creates_configured_directory(
    monkeypatch,
    tmp_path,
):
    snapshot_dir = tmp_path / "storage" / "snapshots"
    monkeypatch.setattr(local_paths, "SNAPSHOT_DIR", snapshot_dir)

    result = local_paths.ensure_snapshot_dir()

    assert result == snapshot_dir
    assert snapshot_dir.is_dir()


def test_main_import_does_not_open_sqlite(monkeypatch):
    def fail_if_opened(*_args, **_kwargs):
        raise AssertionError("FastAPI startup must not open SQLite.")

    monkeypatch.setattr(sqlite3, "connect", fail_if_opened)

    import app.main

    importlib.reload(app.main)
