import ast
import os
import stat
import sys
from pathlib import Path

import pytest

import garminagent
from garminagent.config import Settings
from garminagent.storage.blobs import LocalBlobStore, make_blob_store, validate_key


def test_put_get_roundtrip(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path / "blobs")
    store.put("1/activities/42/original-abc.zip", b"data")
    assert store.get("1/activities/42/original-abc.zip") == b"data"
    assert store.exists("1/activities/42/original-abc.zip")
    assert not store.exists("1/activities/42/missing.zip")


def test_put_replaces_atomically_and_leaves_no_temp_files(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path)
    store.put("1/a.bin", b"first")
    store.put("1/a.bin", b"second")
    assert store.get("1/a.bin") == b"second"
    assert [p.name for p in (tmp_path / "1").iterdir()] == ["a.bin"]


@pytest.mark.parametrize(
    "key",
    [
        "",
        "../escape",
        "1/../../escape",
        "/absolute",
        "1//double",
        "1/.hidden",
        "1\\windows",
        "C:/drive",
        "1/name with space",
        "1/" + "a" * 200,
    ],
)
def test_rejects_unsafe_keys(tmp_path: Path, key: str) -> None:
    store = LocalBlobStore(tmp_path)
    with pytest.raises(ValueError, match="blob key"):
        store.put(key, b"x")
    with pytest.raises(ValueError):
        validate_key(key)


def test_delete_prefix_counts_and_removes(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path)
    store.put("1/activities/1/a.fit", b"a")
    store.put("1/activities/2/b.fit", b"b")
    store.put("2/activities/3/c.fit", b"c")
    assert store.delete_prefix("1") == 2
    assert not (tmp_path / "1").exists()
    assert store.exists("2/activities/3/c.fit")
    assert store.delete_prefix("1") == 0  # idempotent
    assert store.delete_prefix("2/activities/3/c.fit") == 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_files_and_directories_are_private(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path / "blobs")
    store.put("1/activities/42/x.fit", b"data")
    file_mode = stat.S_IMODE(os.stat(tmp_path / "blobs/1/activities/42/x.fit").st_mode)
    dir_mode = stat.S_IMODE(os.stat(tmp_path / "blobs/1/activities").st_mode)
    assert file_mode == 0o600
    assert dir_mode == 0o700


def test_factory_returns_a_working_local_store_under_the_data_dir(tmp_path: Path) -> None:
    store = make_blob_store(Settings(data_dir=tmp_path / "data"))
    assert isinstance(store, LocalBlobStore)
    store.put("1/activities/42/fit-abc.fit", b"data")
    assert (tmp_path / "data" / "blobs" / "1" / "activities" / "42" / "fit-abc.fit").read_bytes() == b"data"
    assert store.get("1/activities/42/fit-abc.fit") == b"data"


def test_app_code_builds_blob_stores_only_through_the_factory() -> None:
    """A direct `LocalBlobStore(...)` in app code would bypass the backend choice (ADR 0008, B3)."""
    package = Path(garminagent.__file__).parent
    direct: list[str] = []
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        allowed: set[ast.AST] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "make_blob_store":
                allowed.update(ast.walk(node))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or node in allowed:
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name == "LocalBlobStore":
                direct.append(f"{path.relative_to(package)}:{node.lineno}")
    assert direct == []
