import os
import stat
import sys
from pathlib import Path

import pytest

from garminagent.storage.blobs import LocalBlobStore, validate_key


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
