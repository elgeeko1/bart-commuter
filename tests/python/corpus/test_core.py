# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for service days and BART time."""

from __future__ import annotations

from datetime import datetime

import pytest

from bart_corpus import core
from corpus_support import ms


@pytest.mark.parametrize(
    ("time", "day"),
    [
        ("2026-11-07T02:59:59", "2026-11-06"),
        ("2026-11-07T03:00:00", "2026-11-07"),
        # the hour repeated when daylight time ends
        ("2026-11-01T01:30:00-08:00", "2026-10-31"),
    ],
)
def test_service_day_changes_at_3am(time: str, day: str) -> None:
    assert core.service_day(ms(time)) == day


def test_service_day_bounds_follow_daylight_time() -> None:
    start_ms, end_ms = core.day_bounds_ms("2026-10-31")
    assert end_ms - start_ms == 25 * 3_600_000
    assert core.service_day(start_ms) == "2026-10-31"
    assert core.service_day(end_ms) == "2026-11-01"


def test_parse_time_reads_naive_times_as_bart_time() -> None:
    expected = datetime.fromisoformat("2026-07-01T08:00:00-07:00").timestamp() * 1000
    assert ms("2026-07-01T08:00:00") == expected
