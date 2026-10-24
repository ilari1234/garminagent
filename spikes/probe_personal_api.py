# /// script
# requires-python = ">=3.15"
# dependencies = ["httpx>=0.28", "python-dotenv>=1.1"]
# ///
"""M0 spike: probe every Garmin Connect Personal API endpoint with a real key.

Usage:
    uv run spikes/probe_personal_api.py [--date YYYY-MM-DD] [--probe-limits]

Reads GARMIN_PERSONAL_API_KEY from the environment or a .env file.

Writes to data/spike/<timestamp>/ (gitignored):
    raw/<name>.json|.bin   full responses (personal data, stays local)
    report.json            per-call status, latency, size, interesting headers and
                           payload *structure* (keys/types only, no values)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

BASE_URL = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
HEADER_HINTS = ("rate", "limit", "retry", "quota", "x-", "content-type", "content-disposition")
REQUEST_DELAY_S = 1.0
DATA_KEY = re.compile(r"^[\d-]+$")


@dataclass
class Call:
    name: str
    path: str
    params: dict[str, Any] = field(default_factory=dict)
    binary: bool = False


def structure(value: Any, depth: int = 0, max_depth: int = 4) -> Any:
    """Describe the shape of a JSON value without exposing its contents."""
    if depth >= max_depth:
        return type(value).__name__
    if isinstance(value, dict):
        # Keys that look like dates or IDs are data, not field names: collapse them.
        data_keys = [k for k in value if DATA_KEY.match(str(k))]
        shape = {k: structure(v, depth + 1, max_depth) for k, v in value.items() if k not in data_keys}
        if data_keys:
            shape[f"<{len(data_keys)} date/id keys>"] = structure(value[data_keys[0]], depth + 1, max_depth)
        return shape
    if isinstance(value, list):
        return [len(value), structure(value[0], depth + 1, max_depth)] if value else [0]
    return None if value is None else type(value).__name__


def file_format(content: bytes) -> str:
    if content[:2] == b"PK":
        return "zip"
    if len(content) >= 12 and content[8:12] == b".FIT":
        return "fit"
    if content.lstrip()[:5] in (b"<?xml", b"<gpx ", b"<Trai"):
        return "xml (gpx/tcx)"
    return "unknown"


def safe_headers(resp: httpx.Response) -> dict[str, str]:
    """Interesting headers; values of anything auth-related are withheld."""
    return {
        k: ("<withheld>" if any(s in k.lower() for s in ("token", "auth", "cookie")) else v)
        for k, v in resp.headers.items()
        if k.lower().startswith(HEADER_HINTS) or "token" in k.lower()
    }


def get_token(client: httpx.Client, api_key: str) -> tuple[str, dict[str, Any]]:
    """Exchange the API key for an access token. The response format is undocumented,
    so accept JSON or a bare token string; never print the body."""
    resp = client.post(TOKEN_PATH, headers={"Authorization": f"ApiKey {api_key}"})
    text = resp.text.strip()
    meta: dict[str, Any] = {
        "status": resp.status_code,
        "content_type": resp.headers.get("content-type"),
        "body_length": len(text),
        "redirects": [str(r.url) for r in resp.history],
        "final_url": str(resp.url),
        "headers": safe_headers(resp),
    }
    if resp.status_code != 200:
        sys.exit(f"Token request failed: HTTP {resp.status_code} {text[:300]}")

    token: str | None = None
    try:
        body = json.loads(text)
    except ValueError:
        body = None
    if isinstance(body, dict):
        meta["format"] = "json object"
        meta["structure"] = structure(body)
        # Only numbers/booleans (e.g. expires_in); any string could be a credential.
        meta["numeric_fields"] = {k: v for k, v in body.items() if isinstance(v, (int, float, bool))}
        token = next((body[k] for k in ("access_token", "accessToken", "token") if k in body), None)
    elif isinstance(body, str):
        meta["format"] = "json string"
        token = body
    elif text and not any(c.isspace() for c in text) and not text.startswith("<"):
        meta["format"] = "plain text"
        token = text

    if not token:
        meta["format"] = meta.get("format") or ("html" if text.startswith("<") else "empty" if not text else "other")
        sys.exit(f"No access token recognized; response metadata: {json.dumps(meta, indent=2)}")
    meta["token_length"] = len(token)
    meta["looks_like_jwt"] = token.count(".") == 2
    return token, meta


def build_calls(d: date) -> list[Call]:
    week_ago = d - timedelta(days=6)
    month_ago = d - timedelta(days=27)
    year_ago = d - timedelta(days=364)
    ds, ws, ms, ys = d.isoformat(), week_ago.isoformat(), month_ago.isoformat(), year_ago.isoformat()
    return [
        Call("userprofile_settings", "/userprofile-service/userprofile/settings"),
        Call("heart_rate_zones", "/biometric-service/heartRateZones/v2"),
        Call("activities_recent", "/activitylist-service/activities/search/activities", {"start": 0, "limit": 20}),
        Call(
            "activities_oldest",
            "/activitylist-service/activities/search/activities",
            {"start": 0, "limit": 1, "sortOrder": "asc"},
        ),
        Call("fitness_age", f"/fitnessage-service/fitnessage/v2/{ds}"),
        Call("weight_goals", f"/goal-service/goal/user/effective/weightgoal/{ms}/{ds}"),
        Call("hrv_day", f"/hrv-service/hrv/{ds}"),
        Call("hrv_range", f"/hrv-service/hrv/daily/{ms}/{ds}"),
        Call("nutrition_day", f"/nutrition-service/food/logs/{ds}"),
        Call("menstrual_summary", f"/periodichealth-service/menstrualcycle/summary/{ds}"),
        Call("menstrual_dayview", f"/periodichealth-service/menstrualcycle/dayview/{ds}"),
        Call("sleep_day", "/sleep-service/sleep/dailySleep", {"date": ds}),
        Call("sleep_range", "/sleep-service/sleep/dailySleepSummariesByDate", {"startDate": ms, "endDate": ds}),
        Call("hydration_range", f"/usersummary-service/stats/hydration/daily/{ms}/{ds}"),
        Call("steps_range", f"/usersummary-service/stats/steps/daily/{ms}/{ds}"),
        Call("stress_range", f"/usersummary-service/stats/stress/daily/{ms}/{ds}"),
        Call("user_summary_day", "/usersummary-service/usersummary/daily", {"calendarDate": ds}),
        Call("daily_summaries_year", "/usersummary-service/v2/dailysummaries", {"startDate": ys, "endDate": ds}),
        Call("weight_all", f"/weight-service/weight/all/{ms}/{ds}"),
        Call("weight_range", f"/weight-service/weight/range/{ms}/{ds}"),
        Call(
            "body_battery_range",
            "/wellness-service/wellness/bodyBattery/reports/daily",
            {"startDate": ws, "endDate": ds},
        ),
        Call("spo2_day", f"/wellness-service/wellness/daily/spo2acclimation/{ds}"),
        Call("daily_heart_rate", "/wellness-service/wellness/dailyHeartRate", {"date": ds}),
        Call("wellness_activity_range", f"/wellnessactivity-service/activity/summary/range/{ws}/{ds}"),
    ]


def limit_calls(d: date) -> list[Call]:
    """Calls just over documented range limits, plus an old range to gauge history depth."""
    ds = d.isoformat()
    old = d - timedelta(days=365 * 5)
    return [
        Call(
            "limit_sleep_32d",
            "/sleep-service/sleep/dailySleepSummariesByDate",
            {"startDate": (d - timedelta(days=31)).isoformat(), "endDate": ds},
        ),
        Call(
            "limit_stress_29d", f"/usersummary-service/stats/stress/daily/{(d - timedelta(days=28)).isoformat()}/{ds}"
        ),
        Call(
            "limit_daily_summaries_366d",
            "/usersummary-service/v2/dailysummaries",
            {"startDate": (d - timedelta(days=365)).isoformat(), "endDate": ds},
        ),
        Call(
            "history_daily_summaries_5y_ago",
            "/usersummary-service/v2/dailysummaries",
            {"startDate": (old - timedelta(days=30)).isoformat(), "endDate": old.isoformat()},
        ),
    ]


def run_call(client: httpx.Client, call: Call, raw_dir: Path) -> tuple[dict[str, Any], Any]:
    started = time.perf_counter()
    resp = client.get(call.path, params=call.params)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    entry: dict[str, Any] = {
        "path": call.path,
        "params": call.params,
        "status": resp.status_code,
        "elapsed_ms": elapsed_ms,
        "bytes": len(resp.content),
        "headers": safe_headers(resp),
    }
    body: Any = None
    if call.binary and resp.status_code == 200:
        entry["file_format"] = file_format(resp.content)
        (raw_dir / f"{call.name}.bin").write_bytes(resp.content)
    elif resp.content:
        try:
            body = resp.json()
            entry["structure"] = structure(body)
            (raw_dir / f"{call.name}.json").write_text(json.dumps(body, indent=2), encoding="utf-8")
        except ValueError:
            entry["content_type"] = resp.headers.get("content-type")
            (raw_dir / f"{call.name}.txt").write_text(resp.text, encoding="utf-8")
    if resp.status_code >= 400:
        entry["error_preview"] = resp.text[:300]
    return entry, body


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=date.today() - timedelta(days=1),
        help="target calendar date (default: yesterday)",
    )
    parser.add_argument(
        "--probe-limits", action="store_true", help="also call just over documented range limits and 5 years back"
    )
    args = parser.parse_args()

    load_dotenv()
    api_key = os.environ.get("GARMIN_PERSONAL_API_KEY")
    if not api_key:
        sys.exit("Set GARMIN_PERSONAL_API_KEY in the environment or in .env")

    out_dir = Path("data/spike") / datetime.now().strftime("%Y%m%dT%H%M%S")
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True)

    report: dict[str, Any] = {"target_date": args.date.isoformat(), "calls": {}}
    with httpx.Client(base_url=BASE_URL, timeout=60, follow_redirects=True) as client:
        token, report["token"] = get_token(client, api_key)
        client.headers["Authorization"] = f"Bearer {token}"

        calls = build_calls(args.date) + (limit_calls(args.date) if args.probe_limits else [])
        recent_activities: Any = None
        for call in calls:
            entry, body = run_call(client, call, raw_dir)
            report["calls"][call.name] = entry
            print(f"{entry['status']:>3}  {entry['elapsed_ms']:>5} ms  {entry['bytes']:>9} B  {call.name}")
            if call.name == "activities_recent":
                recent_activities = body
            if call.name == "activities_oldest" and isinstance(body, list) and body:
                report["oldest_activity_start"] = body[0].get("startTimeLocal")
            time.sleep(REQUEST_DELAY_S)

        # Per-activity endpoints, using the most recent activity.
        if isinstance(recent_activities, list) and recent_activities:
            activity_id = recent_activities[0].get("activityId")
            for call in (
                Call("activity_detail", f"/activity-service/activity/{activity_id}"),
                Call("activity_workouts", f"/activity-service/activity/{activity_id}/workouts"),
                Call("activity_file", f"/download-service/files/activity/{activity_id}", binary=True),
            ):
                entry, _ = run_call(client, call, raw_dir)
                report["calls"][call.name] = entry
                print(f"{entry['status']:>3}  {entry['elapsed_ms']:>5} ms  {entry['bytes']:>9} B  {call.name}")
                time.sleep(REQUEST_DELAY_S)

    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport: {out_dir / 'report.json'}")


if __name__ == "__main__":
    main()
