# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Times, paths and errors shared across the corpus tools."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "bart-commuter.js"
BART_TZ = ZoneInfo("America/Los_Angeles")
# the BART service day changes at 3 AM wall-clock time
SERVICE_DAY_START = timedelta(hours=3)
HTTP_OK = 200


class CorpusError(Exception):
    pass


def service_day(epoch_ms: int) -> str:
    """Returns the ISO date of the BART service day in progress at a time."""
    wall = datetime.fromtimestamp(epoch_ms / 1000, BART_TZ)
    return (wall - SERVICE_DAY_START).date().isoformat()


def day_bounds_ms(day: str) -> tuple[int, int]:
    """Returns the epoch ms a service day starts and the next one starts."""
    midnight = datetime.fromisoformat(day).replace(tzinfo=BART_TZ)
    start = midnight + SERVICE_DAY_START
    end = start + timedelta(days=1)
    return round(start.timestamp() * 1000), round(end.timestamp() * 1000)


def next_service_day(day: str) -> str:
    return (datetime.fromisoformat(day).date() + timedelta(days=1)).isoformat()


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def clock(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, BART_TZ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def time_of_day(epoch_s: int) -> str:
    return datetime.fromtimestamp(epoch_s, BART_TZ).strftime("%H:%M:%S")


def parse_time(text: str) -> int:
    """Returns epoch ms of an ISO 8601 time, read as BART time without an offset."""
    t = datetime.fromisoformat(text)
    if t.tzinfo is None:
        t = t.replace(tzinfo=BART_TZ)
    return round(t.timestamp() * 1000)
