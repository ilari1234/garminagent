import pytest

from garminagent.runner import RATE_LIMIT_PAUSE_S, next_delay_s, run_schedule

HOUR = 3600.0
INTERVAL, CATCH_UP = 6 * HOUR, 15 * 60.0


@pytest.mark.parametrize(
    ("stopped", "failures", "expected"),
    [
        (None, 0, INTERVAL),  # complete: regular interval
        ("already_running", 0, INTERVAL),
        ("auth_failed", 1, INTERVAL),  # needs a human; don't hammer
        ("budget", 0, CATCH_UP),  # more to do: soon
        ("rate_limited", 0, INTERVAL),  # never sooner than the interval
        ("garmin_error", 1, 2 * CATCH_UP),  # exponential backoff...
        ("garmin_error", 3, 8 * CATCH_UP),
        ("database_error", 20, INTERVAL),  # ...capped at the interval
    ],
)
def test_next_delay(stopped: str | None, failures: int, expected: float) -> None:
    assert next_delay_s(stopped, failures, INTERVAL, CATCH_UP) == expected


def test_rate_limit_pause_has_a_floor_for_short_intervals() -> None:
    assert next_delay_s("rate_limited", 0, 1 * HOUR, CATCH_UP) == RATE_LIMIT_PAUSE_S


def test_schedule_runs_waits_and_stops_on_request() -> None:
    outcomes = iter(["budget", "budget", None, None])
    waits: list[float] = []

    def wait(delay: float) -> bool:
        waits.append(delay)
        return len(waits) == 3  # stop requested during the third wait

    runs = run_schedule(lambda: next(outcomes), wait, interval_s=INTERVAL, catch_up_s=CATCH_UP)

    assert runs == 3
    assert CATCH_UP <= waits[0] <= CATCH_UP + 300  # jitter is small and only adds time
    assert CATCH_UP <= waits[1] <= CATCH_UP + 300
    assert INTERVAL <= waits[2] <= INTERVAL + 300


def test_failures_back_off_and_reset_after_success() -> None:
    outcomes = iter(["garmin_error", "garmin_error", None, "garmin_error", None])
    waits: list[float] = []

    def wait(delay: float) -> bool:
        waits.append(delay)
        return False

    run_schedule(lambda: next(outcomes), wait, interval_s=INTERVAL, catch_up_s=CATCH_UP, max_runs=5)

    first, second, _, after_success = waits
    assert 2 * CATCH_UP <= first < 3 * CATCH_UP
    assert 4 * CATCH_UP <= second < 5 * CATCH_UP
    assert 2 * CATCH_UP <= after_success < 3 * CATCH_UP  # the success reset the backoff
