import json
import logging

import pytest

from garminagent.log import configure_logging


def test_json_format_emits_one_object_per_line(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "json")
    logging.getLogger("garminagent.test").info("hello %s", "world")
    entry = json.loads(capsys.readouterr().err.strip())
    assert entry["level"] == "INFO"
    assert entry["logger"] == "garminagent.test"
    assert entry["message"] == "hello world"


def test_http_library_loggers_stay_quiet_at_debug(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("DEBUG", "text")
    logging.getLogger("httpx").info("HTTP Request: GET https://example.test/x")
    logging.getLogger("httpcore.http11").debug("receive_response_headers.complete")
    logging.getLogger("httpx").warning("kept")
    out = capsys.readouterr().err
    assert "HTTP Request" not in out
    assert "receive_response_headers" not in out
    assert "kept" in out


def test_text_format(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("WARNING", "text")
    logging.getLogger("garminagent.test").info("hidden")
    logging.getLogger("garminagent.test").warning("shown")
    out = capsys.readouterr().err
    assert "shown" in out
    assert "hidden" not in out
