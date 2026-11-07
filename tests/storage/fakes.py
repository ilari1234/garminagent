"""An in-memory stand-in for the boto3 S3 client, covering the calls `S3BlobStore` makes.

Shaped like the real client where the store depends on it: errors are botocore `ClientError`s
with the status and code S3 sends (`NoSuchKey` for GetObject, a bare `404` for HeadObject, which
has no body), listings page with continuation tokens, and DeleteObjects reports per-key errors.
Failures can be injected per operation; their messages and headers carry canaries so tests can
show the store never passes them on.
"""

import io
import secrets
from dataclasses import dataclass, field
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError
from botocore.response import StreamingBody

# Stand-ins for what S3 error responses may contain: a body message and a header value. Built
# at runtime, so the repo holds no credential-shaped literal.
BODY_CANARY = f"body-{secrets.token_hex(8)}"
HEADER_CANARY = f"header-{secrets.token_hex(8)}"


def client_error(operation: str, status: int, code: str) -> ClientError:
    response: Any = {
        "Error": {"Code": code, "Message": f"S3 says {BODY_CANARY}"},
        "ResponseMetadata": {"HTTPStatusCode": status, "HTTPHeaders": {"x-amz-id-2": HEADER_CANARY}},
    }
    return ClientError(response, operation)


@dataclass
class FakeS3:
    bucket: str
    page_size: int = 1000
    objects: dict[str, bytes] = field(default_factory=dict[str, bytes])
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list[tuple[str, dict[str, Any]]])
    # Operation name -> the error its next call raises (a ClientError or BotoCoreError).
    fail: dict[str, Exception] = field(default_factory=dict[str, Exception])
    # Keys DeleteObjects reports as failed instead of deleting.
    undeletable: set[str] = field(default_factory=set[str])
    truncate_without_token: bool = False

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> dict[str, Any]:  # boto3's argument names
        self._call("PutObject", Bucket, Key=Key, Body=Body)
        self.objects[Key] = bytes(Body)
        return {}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self._call("GetObject", Bucket, Key=Key)
        if Key not in self.objects:
            raise client_error("GetObject", 404, "NoSuchKey")
        data = self.objects[Key]
        return {"Body": StreamingBody(io.BytesIO(data), len(data))}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self._call("HeadObject", Bucket, Key=Key)
        if Key not in self.objects:
            raise client_error("HeadObject", 404, "404")
        return {"ContentLength": len(self.objects[Key])}

    def list_objects_v2(self, *, Bucket: str, Prefix: str, ContinuationToken: str | None = None) -> dict[str, Any]:
        self._call("ListObjectsV2", Bucket, Prefix=Prefix, ContinuationToken=ContinuationToken)
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        if ContinuationToken is not None:
            keys = [k for k in keys if k > ContinuationToken]
        page, rest = keys[: self.page_size], keys[self.page_size :]
        response: dict[str, Any] = {"IsTruncated": bool(rest), "KeyCount": len(page)}
        if page:
            response["Contents"] = [{"Key": k, "Size": len(self.objects[k])} for k in page]
        if rest and not self.truncate_without_token:
            response["NextContinuationToken"] = page[-1]
        return response

    def delete_objects(self, *, Bucket: str, Delete: dict[str, Any]) -> dict[str, Any]:
        self._call("DeleteObjects", Bucket, Delete=Delete)
        if len(Delete["Objects"]) > 1000:
            raise client_error("DeleteObjects", 400, "MalformedXML")
        errors: list[dict[str, str]] = []
        for item in Delete["Objects"]:
            if item["Key"] in self.undeletable:
                errors.append({"Key": item["Key"], "Code": "AccessDenied", "Message": BODY_CANARY})
            else:
                self.objects.pop(item["Key"], None)  # S3 reports a missing key as deleted
        return {"Errors": errors} if errors else {}

    def operations(self) -> list[str]:
        return [name for name, _ in self.calls]

    def _call(self, operation: str, bucket: str, **kwargs: Any) -> None:
        self.calls.append((operation, kwargs))
        if bucket != self.bucket:
            raise client_error(operation, 404, "NoSuchBucket")
        if (error := self.fail.pop(operation, None)) is not None:
            raise error


def connection_error() -> BotoCoreError:
    """A network-level failure, as botocore raises it (its message names the endpoint)."""
    return EndpointConnectionError(endpoint_url=f"https://{HEADER_CANARY}.example.test")
