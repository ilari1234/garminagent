"""Blob storage for raw files (activity zips and FIT files).

`BlobStore` is the seam for AWS: `LocalBlobStore` writes under a directory now; an S3
implementation can replace it without touching callers.

Keys are built by our own code from IDs and content hashes; they are still validated so a
bug can never write outside the store. Files are private to the owner (0600, directories 0700)
on POSIX systems; Windows ignores these modes and relies on the user profile's ACLs.
"""

import os
import re
import secrets
import shutil
from pathlib import Path
from typing import Protocol

# Segments start with a letter or digit, so "..", "." and hidden names are impossible.
KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}(/[A-Za-z0-9][A-Za-z0-9._-]{0,127}){0,15}")
FILE_MODE = 0o600
DIR_MODE = 0o700


class BlobStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete_prefix(self, prefix: str) -> int: ...


def validate_key(key: str) -> str:
    if not KEY_PATTERN.fullmatch(key):
        raise ValueError(f"invalid blob key: {key!r}")
    return key


class LocalBlobStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def put(self, key: str, data: bytes) -> None:
        """Write atomically (temp file + rename); rewriting a key replaces it."""
        path = self._path(key)
        self._make_dirs(path.parent)
        temp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), FILE_MODE)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete_prefix(self, prefix: str) -> int:
        """Delete every blob under `prefix` (a key or key directory); returns the file count."""
        target = self._path(prefix.rstrip("/"))
        if target.is_file():
            target.unlink()
            return 1
        if not target.is_dir():
            return 0
        count = sum(1 for p in target.rglob("*") if p.is_file())
        shutil.rmtree(target)
        return count

    def _path(self, key: str) -> Path:
        path = (self._root / validate_key(key)).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError(f"blob key escapes the store: {key!r}")
        return path

    def _make_dirs(self, directory: Path) -> None:
        missing: list[Path] = []
        current = directory
        while not current.exists():
            missing.append(current)
            current = current.parent
        for path in reversed(missing):
            path.mkdir(mode=DIR_MODE, exist_ok=True)
            os.chmod(path, DIR_MODE)  # mkdir's mode is reduced by the umask
