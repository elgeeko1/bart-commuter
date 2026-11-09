# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for the polling schedule."""

from __future__ import annotations

from bart_corpus import recorder
from corpus_support import ms, poll


def test_periodic_job_runs_on_wall_clock_multiples() -> None:
    job = recorder.Job("refresh", 30, lambda tick: [])
    assert job.next_due % 30 == 0
    job.started(1000.5)
    assert job.next_due == 1020
    assert not job.due(1019.9)
    assert job.due(1020)
    assert job.tick(1020.3) == 1_020_000


def test_periodic_job_after_a_stall_runs_in_the_current_slot() -> None:
    job = recorder.Job("refresh", 30, lambda tick: [])
    job.started(1000.5)
    assert job.due(5000.2)
    assert job.tick(5000.2) == 4_980_000
    job.started(5000.2)
    assert job.next_due == 5010


def test_daily_job_waits_while_running_retries_failures_and_reruns_next_day() -> None:
    job = recorder.Job("daily", None, lambda tick: [])
    t = ms("2026-11-06T12:00:00") / 1000
    assert job.due(t)
    job.started(t)
    assert not job.due(t + 1)
    assert job.wake_at(t + 1) == float("inf")

    job.finished([poll("stns", round(t * 1000), status=503)], t + 1)
    assert not job.due(t + recorder.DAILY_RETRY_S - 1)
    assert job.due(t + recorder.DAILY_RETRY_S + 1)

    job.started(t + recorder.DAILY_RETRY_S + 1)
    job.finished([poll("stns", round(t * 1000))], t + recorder.DAILY_RETRY_S + 2)
    assert job.done_day == "2026-11-06"
    assert not job.due(ms("2026-11-07T02:59:00") / 1000)
    assert job.due(ms("2026-11-07T03:00:00") / 1000)
