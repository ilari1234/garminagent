"""S3 blob store (ADR 0008, stage B3b).

Only `make_blob_store` imports this module, and only when `BLOB_BACKEND=s3`, so local runs never
load boto3. Same contract as `LocalBlobStore`: keys pass `validate_key`, a missing key raises
`FileNotFoundError` (sync marks the file `invalid` instead of failing), and `delete_prefix`
removes a key or everything under `prefix/`.

Encryption at rest is the bucket's job (SSE-KMS default encryption, B3c), so `put` sends no
encryption headers; boto3 uses HTTPS, which the bucket policy requires. The task role needs
`s3:ListBucket` as well as get, put and delete: without it S3 answers 403 instead of 404 for a
missing key, and `delete_prefix` can't list.

Errors carry the operation, the key and the HTTP status (or the botocore error class) only.
S3's error bodies and headers stay out, and the botocore exception is not chained, so a
traceback can't show them either.
"""

from collections.abc import Iterator
from itertools import batched
from typing import TYPE_CHECKING, NoReturn

from botocore.exceptions import BotoCoreError, ClientError

from garminagent.storage.blobs import BlobStoreError, validate_key

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

# DeleteObjects accepts at most 1000 keys per request.
DELETE_BATCH = 1000


def make_s3_blob_store(bucket: str) -> S3BlobStore:
    """A store on `bucket`, with region and credentials from the default AWS chain (on ECS, the
    task role; locally, the owner's SSO profile). No keys are ever configured here."""
    import boto3  # deferred, so local runs never load boto3
    from botocore.config import Config

    config = Config(retries={"mode": "standard", "max_attempts": 5}, connect_timeout=5, read_timeout=60)
    # The stubs type every service's overload; only S3's is installed, so the others are unknown.
    client = boto3.client("s3", config=config)  # pyright: ignore[reportUnknownMemberType]
    return S3BlobStore(client, bucket)


class S3BlobStore:
    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def put(self, key: str, data: bytes) -> None:
        """One PutObject: S3 replaces a whole object atomically, so readers never see half a blob."""
        key = validate_key(key)
        try:
            self._client.put_object(Bucket=self._bucket, Key=key, Body=data)
        except (ClientError, BotoCoreError) as exc:
            _fail("put", key, exc)

    def get(self, key: str) -> bytes:
        key = validate_key(key)
        try:
            body = self._client.get_object(Bucket=self._bucket, Key=key)["Body"]
            try:
                return body.read()
            finally:
                body.close()
        except ClientError as exc:
            # Only NoSuchKey: a missing bucket is also a 404, and must not pass for a missing blob.
            if exc.response.get("Error", {}).get("Code") == "NoSuchKey":
                raise FileNotFoundError(f"blob not found: {key}") from None
            _fail("get", key, exc)
        except BotoCoreError as exc:
            _fail("get", key, exc)

    def exists(self, key: str) -> bool:
        key = validate_key(key)
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _status(exc) == 404:
                return False
            _fail("exists", key, exc)
        except BotoCoreError as exc:
            _fail("exists", key, exc)
        return True

    def delete_prefix(self, prefix: str) -> int:
        """Delete the key `prefix` and every key under `prefix/`; returns how many were deleted.
        Lists first, then deletes in batches. Safe to re-run after a failure midway."""
        prefix = validate_key(prefix.rstrip("/"))
        keys = [key for key in self._list(prefix) if key == prefix or key.startswith(f"{prefix}/")]
        for batch in batched(keys, DELETE_BATCH, strict=False):
            try:
                response = self._client.delete_objects(
                    Bucket=self._bucket, Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True}
                )
            except (ClientError, BotoCoreError) as exc:
                _fail("delete_prefix", prefix, exc)
            if failed := response.get("Errors"):
                raise BlobStoreError(f"s3 delete_prefix failed for key {prefix}: {len(failed)} objects not deleted")
        return len(keys)

    def _list(self, prefix: str) -> Iterator[str]:
        token: str | None = None
        while True:
            try:
                if token is None:
                    page = self._client.list_objects_v2(Bucket=self._bucket, Prefix=prefix)
                else:
                    page = self._client.list_objects_v2(Bucket=self._bucket, Prefix=prefix, ContinuationToken=token)
            except (ClientError, BotoCoreError) as exc:
                _fail("delete_prefix", prefix, exc)
            for item in page.get("Contents", []):
                if "Key" in item:
                    yield item["Key"]
            if not page.get("IsTruncated"):
                return
            token = page.get("NextContinuationToken")
            if not token:  # would list the first page again, forever
                raise BlobStoreError(f"s3 delete_prefix failed for key {prefix}: truncated listing without a token")


def _status(exc: ClientError) -> int | None:
    return exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")


def _fail(operation: str, key: str, exc: ClientError | BotoCoreError) -> NoReturn:
    detail = f"HTTP {_status(exc)}" if isinstance(exc, ClientError) else exc.__class__.__name__
    raise BlobStoreError(f"s3 {operation} failed for key {key}: {detail}") from None
