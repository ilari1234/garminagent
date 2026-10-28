"""Test doubles for the Garmin client: a fake API server, a fake clock and fake files.
All data here is synthetic."""

import base64
import io
import json
import re
import zipfile
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from garminagent.garmin import GarminClient
from garminagent.garmin.token import TOKEN_PATH

FIXTURES = Path(__file__).parent.parent / "fixtures" / "garmin"
START_TIME = 1_800_000_000.0  # fixed Unix time for deterministic token expiry
TEST_API_KEY = "test-api-key-not-real"
FAKE_JWT_SIGNATURE = "fake-signature"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_jwt(exp: float | None) -> str:
    def b64(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    claims: dict[str, Any] = {"sub": "fake-user"} if exp is None else {"sub": "fake-user", "exp": int(exp)}
    return f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims)}.{FAKE_JWT_SIGNATURE}"


def make_fit(payload: bytes = b"\x00" * 32) -> bytes:
    """Minimal bytes with a valid 14-byte FIT header signature."""
    header = bytes([14, 0x20, 0, 0]) + len(payload).to_bytes(4, "little") + b".FIT" + b"\x00\x00"
    return header + payload


def make_zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class FakeTime:
    """Monotonic and wall clock in one; sleeping advances time instantly."""

    def __init__(self, start: float = START_TIME) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Seen:
    method: str
    path: str
    params: dict[str, str]
    authorization: str


type Reply = httpx.Response | Exception
type Handler = Callable[[httpx.Request], httpx.Response]


@dataclass
class FakeGarmin:
    """Queued replies per (method, path); the last reply for a route repeats. Handlers matched
    by a path pattern can compute replies from the request (e.g. query parameters). The token
    endpoint answers with a fresh plain-text JWT unless told otherwise."""

    clock: FakeTime
    replies: dict[tuple[str, str], deque[Reply]] = field(default_factory=dict[tuple[str, str], deque[Reply]])
    seen: list[Seen] = field(default_factory=list[Seen])
    routes: list[tuple[str, re.Pattern[str], Handler]] = field(
        default_factory=list[tuple[str, re.Pattern[str], Handler]]
    )

    def route(self, method: str, pattern: str, handler: Handler) -> None:
        self.routes.append((method, re.compile(pattern), handler))

    def reply(self, method: str, path: str, *replies: Reply) -> None:
        self.replies[(method, path)] = deque(replies)

    def json(self, path: str, body: Any, status: int = 200) -> None:
        self.reply("GET", path, httpx.Response(status, json=body))

    def requests_to(self, path: str) -> list[Seen]:
        return [s for s in self.seen if s.path == path]

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.seen.append(Seen(request.method, path, dict(request.url.params), request.headers.get("Authorization", "")))
        for method, pattern, route_handler in self.routes:
            if method == request.method and pattern.fullmatch(path):
                return route_handler(request)
        queue = self.replies.get((request.method, path))
        if queue is None:
            if request.method == "POST" and path == TOKEN_PATH:
                token = make_jwt(self.clock.time() + 24 * 3600)
                return httpx.Response(200, text=token, headers={"content-type": "text/plain"})
            return httpx.Response(404, json={})
        reply = queue.popleft() if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    def client(self, **kwargs: Any) -> GarminClient:
        return GarminClient(
            SecretStr(TEST_API_KEY),
            transport=httpx.MockTransport(self.handler),
            clock=self.clock.time,
            wall_clock=self.clock.time,
            sleep=self.clock.sleep,
            **kwargs,
        )
