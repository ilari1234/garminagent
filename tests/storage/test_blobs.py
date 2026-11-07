"""The blob store contract (docs/contracts/ingestion.md), run against every backend, plus each
backend's own guarantees. S3 runs against an in-memory fake client: no network, no AWS."""

import ast
import os
import stat
import subprocess
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from botocore.exceptions import ClientError
from pydantic import ValidationError

import garminagent
from garminagent.config import Settings
from garminagent.storage.blobs import BlobStore, BlobStoreError, LocalBlobStore, make_blob_store, validate_key
from garminagent.storage.s3 import S3BlobStore
from tests.storage.fakes import BODY_CANARY, HEADER_CANARY, FakeS3, client_error, connection_error

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

BUCKET = "garminagent-test-blobs"


def s3_store(fake: FakeS3) -> S3BlobStore:
    return S3BlobStore(cast("S3Client", fake), BUCKET)


@pytest.fixture(params=["local", "s3"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> BlobStore:
    if request.param == "local":
        return LocalBlobStore(tmp_path / "blobs")
    return s3_store(FakeS3(BUCKET))


# --- the contract, for every backend ---


def test_put_get_roundtrip(store: BlobStore) -> None:
    store.put("1/activities/42/original-abc.zip", b"data")
    assert store.get("1/activities/42/original-abc.zip") == b"data"
    assert store.exists("1/activities/42/original-abc.zip")
    assert not store.exists("1/activities/42/missing.zip")


def test_put_replaces(store: BlobStore) -> None:
    store.put("1/a.bin", b"first")
    store.put("1/a.bin", b"second")
    assert store.get("1/a.bin") == b"second"


def test_get_of_a_missing_key_raises_file_not_found(store: BlobStore) -> None:
    """sync.py relies on this to mark a lost FIT file `invalid` instead of failing the run."""
    with pytest.raises(FileNotFoundError):
        store.get("1/activities/42/missing.fit")


UNSAFE_KEYS = [
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
]


@pytest.mark.parametrize("key", UNSAFE_KEYS)
def test_rejects_unsafe_keys_in_every_operation(store: BlobStore, key: str) -> None:
    operations: list[Callable[[], object]] = [
        lambda: store.put(key, b"x"),
        lambda: store.get(key),
        lambda: store.exists(key),
        lambda: store.delete_prefix(key),
    ]
    for operation in operations:
        with pytest.raises(ValueError, match="blob key"):
            operation()
    with pytest.raises(ValueError):
        validate_key(key)


def test_delete_prefix_counts_and_removes(store: BlobStore) -> None:
    store.put("1/activities/1/a.fit", b"a")
    store.put("1/activities/2/b.fit", b"b")
    store.put("2/activities/3/c.fit", b"c")
    assert store.delete_prefix("1") == 2
    assert not store.exists("1/activities/1/a.fit")
    assert not store.exists("1/activities/2/b.fit")
    assert store.exists("2/activities/3/c.fit")
    assert store.delete_prefix("1") == 0  # idempotent
    assert store.delete_prefix("2/activities/3/c.fit") == 1  # a single key
    assert not store.exists("2/activities/3/c.fit")


def test_delete_prefix_stops_at_segment_boundaries(store: BlobStore) -> None:
    """Athlete 1's purge must not touch athlete 12's files, though "12" starts with "1"."""
    store.put("1/activities/5/a.fit", b"a")
    store.put("12/activities/6/b.fit", b"b")
    store.put("1/activities/50/c.fit", b"c")
    assert store.delete_prefix("1/activities/5/") == 1
    assert store.exists("1/activities/50/c.fit")
    assert store.delete_prefix("1") == 1
    assert store.exists("12/activities/6/b.fit")


# --- local backend ---


def test_local_put_is_atomic_and_leaves_no_temp_files(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path)
    store.put("1/a.bin", b"first")
    store.put("1/a.bin", b"second")
    assert [p.name for p in (tmp_path / "1").iterdir()] == ["a.bin"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_local_files_and_directories_are_private(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path / "blobs")
    store.put("1/activities/42/x.fit", b"data")
    file_mode = stat.S_IMODE(os.stat(tmp_path / "blobs/1/activities/42/x.fit").st_mode)
    dir_mode = stat.S_IMODE(os.stat(tmp_path / "blobs/1/activities").st_mode)
    assert file_mode == 0o600
    assert dir_mode == 0o700


# --- S3 backend ---


def test_s3_uses_one_request_per_operation() -> None:
    fake = FakeS3(BUCKET)
    store = s3_store(fake)
    store.put("1/a.fit", b"a")
    store.exists("1/a.fit")
    store.get("1/a.fit")
    assert fake.operations() == ["PutObject", "HeadObject", "GetObject"]
    assert fake.calls[0][1] == {"Key": "1/a.fit", "Body": b"a"}  # no extra headers: the bucket encrypts


def test_s3_rejected_keys_never_reach_s3() -> None:
    fake = FakeS3(BUCKET)
    with pytest.raises(ValueError):
        s3_store(fake).put("../escape", b"x")
    assert fake.calls == []


def test_s3_delete_prefix_pages_and_deletes_in_batches_of_1000() -> None:
    fake = FakeS3(BUCKET, page_size=700)
    store = s3_store(fake)
    for i in range(2500):
        fake.objects[f"1/activities/{i}/fit-{i}.fit"] = b"x"
    fake.objects["2/activities/1/fit-1.fit"] = b"y"
    assert store.delete_prefix("1") == 2500
    assert list(fake.objects) == ["2/activities/1/fit-1.fit"]
    batches = [len(kwargs["Delete"]["Objects"]) for name, kwargs in fake.calls if name == "DeleteObjects"]
    assert batches == [1000, 1000, 500]
    assert fake.operations().count("ListObjectsV2") == 4  # 2500 keys, 700 a page


def test_s3_missing_bucket_is_an_error_not_a_missing_blob() -> None:
    fake = FakeS3("another-bucket")
    with pytest.raises(BlobStoreError):
        s3_store(fake).get("1/a.fit")


def run_operation(store: S3BlobStore, operation: str) -> object:
    key = "1/a.fit"
    match operation:
        case "put":
            return store.put(key, b"a")
        case "get":
            return store.get(key)
        case "exists":
            return store.exists(key)
        case _:
            return store.delete_prefix(key)


@pytest.mark.parametrize(
    ("operation", "s3_operation"),
    [
        ("put", "PutObject"),
        ("get", "GetObject"),
        ("exists", "HeadObject"),
        ("delete_prefix", "ListObjectsV2"),
        ("delete_prefix", "DeleteObjects"),
    ],
)
@pytest.mark.parametrize("error", ["client", "connection"])
def test_s3_errors_carry_operation_key_and_status_only(operation: str, s3_operation: str, error: str) -> None:
    fake = FakeS3(BUCKET)
    fake.objects["1/a.fit"] = b"a"
    denied = client_error(s3_operation, 403, "AccessDenied")
    fake.fail[s3_operation] = denied if error == "client" else connection_error()
    with pytest.raises(BlobStoreError) as info:
        run_operation(s3_store(fake), operation)
    message = str(info.value)
    assert f"s3 {operation} failed" in message
    assert "1/a.fit" in message
    assert ("HTTP 403" if error == "client" else "EndpointConnectionError") in message
    shown = "".join(traceback.format_exception(info.value))
    assert BODY_CANARY not in shown
    assert HEADER_CANARY not in shown
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__
    assert isinstance(info.value, OSError)  # handled like the local store's own failures


def test_s3_delete_prefix_fails_when_s3_keeps_objects() -> None:
    fake = FakeS3(BUCKET)
    fake.objects.update({"1/a.fit": b"a", "1/b.fit": b"b"})
    fake.undeletable = {"1/b.fit"}
    with pytest.raises(BlobStoreError, match="1 objects not deleted") as info:
        s3_store(fake).delete_prefix("1")
    assert BODY_CANARY not in str(info.value)
    fake.undeletable = set()
    assert s3_store(fake).delete_prefix("1") == 1  # a re-run removes the rest


def test_s3_truncated_listing_without_a_token_fails_instead_of_looping() -> None:
    fake = FakeS3(BUCKET, page_size=1, truncate_without_token=True)
    fake.objects.update({"1/a.fit": b"a", "1/b.fit": b"b"})
    with pytest.raises(BlobStoreError, match="without a token"):
        s3_store(fake).delete_prefix("1")


def test_fake_raises_what_botocore_raises() -> None:
    """The fake's errors must look like botocore's, or the store's tests prove nothing."""
    error = client_error("GetObject", 404, "NoSuchKey")
    assert isinstance(error, ClientError)
    assert error.response.get("Error", {}).get("Code") == "NoSuchKey"
    assert error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404


# --- settings and factory ---


def test_factory_returns_a_working_local_store_under_the_data_dir(tmp_path: Path) -> None:
    store = make_blob_store(Settings(data_dir=tmp_path / "data"))
    assert isinstance(store, LocalBlobStore)
    store.put("1/activities/42/fit-abc.fit", b"data")
    assert (tmp_path / "data" / "blobs" / "1" / "activities" / "42" / "fit-abc.fit").read_bytes() == b"data"
    assert store.get("1/activities/42/fit-abc.fit") == b"data"


def test_factory_returns_an_s3_store_without_touching_aws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # No profile, credentials or config from the developer's machine; creating a client makes
    # no request (credentials are resolved on the first call, which never happens here).
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "missing-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "missing-credentials"))
    monkeypatch.setenv("AWS_REGION", "eu-north-1")
    store = make_blob_store(Settings(blob_backend="s3", blob_s3_bucket=BUCKET))
    assert isinstance(store, S3BlobStore)


def test_s3_backend_needs_a_valid_bucket() -> None:
    with pytest.raises(ValidationError, match="BLOB_S3_BUCKET"):
        Settings(blob_backend="s3")
    for bad in ("Upper-Case", "a", "bucket_with_underscore", "-leading-hyphen", "x" * 64):
        with pytest.raises(ValidationError, match="BLOB_S3_BUCKET"):
            Settings(blob_backend="s3", blob_s3_bucket=bad)


def test_blob_settings_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLOB_BACKEND", "s3")
    monkeypatch.setenv("BLOB_S3_BUCKET", BUCKET)
    settings = Settings()
    assert (settings.blob_backend, settings.blob_s3_bucket) == ("s3", BUCKET)
    assert settings.redacted()["blob_backend"] == "s3"
    monkeypatch.setenv("BLOB_BACKEND", "gcs")
    with pytest.raises(ValidationError):
        Settings()


def test_local_runs_never_import_boto3() -> None:
    """boto3 and botocore load only when BLOB_BACKEND=s3 (ADR 0008, B3b)."""
    code = (
        "import sys\n"
        "import garminagent.cli\n"
        "from garminagent.config import Settings\n"
        "from garminagent.storage.blobs import make_blob_store\n"
        "make_blob_store(Settings())\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in {'boto3', 'botocore', 's3transfer'}))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)  # noqa: S603
    assert result.stdout.strip() == "[]"


def test_app_code_builds_blob_stores_only_through_the_factory() -> None:
    """A direct `LocalBlobStore(...)` or `S3BlobStore(...)` in app code would bypass the backend
    choice (ADR 0008, B3)."""
    package = Path(garminagent.__file__).parent
    factories = {"make_blob_store", "make_s3_blob_store"}
    direct: list[str] = []
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        allowed: set[ast.AST] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in factories:
                allowed.update(ast.walk(node))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or node in allowed:
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name in {"LocalBlobStore", "S3BlobStore"}:
                direct.append(f"{path.relative_to(package)}:{node.lineno}")
    assert direct == []
