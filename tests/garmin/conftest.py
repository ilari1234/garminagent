import pytest

from tests.garmin.fakes import FakeGarmin, FakeTime


@pytest.fixture
def clock() -> FakeTime:
    return FakeTime()


@pytest.fixture
def garmin(clock: FakeTime) -> FakeGarmin:
    return FakeGarmin(clock)
