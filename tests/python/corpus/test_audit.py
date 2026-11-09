# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for auditing recordings against the queries of the current recorder."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from bart_corpus import archive, core, recorder, store
from bart_corpus import audit as au
from corpus_support import gtfs_feed, ms, poll

DAY = "2026-11-06"
PAIR = "WOAK-EMBR"
EXPECTED = au.ExpectedQueries(
    queries=frozenset({("tripupdate", ""), ("etd", "ALL"), ("depart", PAIR)}),
    trips_before=1,
    trips_planned=4,
)
START = ms("2026-11-06T03:00:00")
END = ms("2026-11-07T02:59:00")
AFTER_THE_DAY = ms("2026-11-07T12:00:00")
FEED = gtfs_feed("05:00:00", "26:30:00")
LAST_ARRIVAL = ms("2026-11-07T02:30:00")


def planner_body(before: int, after: int) -> bytes:
    return json.dumps(
        {"root": {"schedule": {"before": str(before), "after": str(after)}}}
    ).encode()


def record(
    corpus: Path,
    ticks: list[int],
    *,
    before: int = 1,
    daily: bool = True,
    pair: str = PAIR,
    feed: bytes = FEED,
    planned: bytes | None = None,
) -> None:
    writer = store.Store(corpus)
    for tick in ticks:
        writer.write(
            [
                poll("tripupdate", tick),
                poll("etd", tick, subject="ALL"),
                poll("depart", tick, planned or planner_body(before, 4), subject=pair),
            ]
        )
    if daily:
        writer.write(
            [
                poll(s, ticks[0], feed if s == "gtfs-static" else b"x", subject="X")
                for s in au.PER_DAY_SOURCES
            ]
        )
    writer.close()


def kinds(result: au.DayAudit) -> set[au.ProblemKind]:
    return {p.kind for p in result.problems}


def whole_day(step_s: int = 240) -> list[int]:
    return [*range(START, END, step_s * 1000), END]


def audit_one(corpus: Path, now: int = AFTER_THE_DAY) -> au.DayAudit:
    (result,) = au.audit(corpus, EXPECTED, now)
    return result


def test_a_full_day_of_the_current_queries_is_complete(tmp_path: Path) -> None:
    record(tmp_path, whole_day())
    result = audit_one(tmp_path)
    assert result.complete
    assert au.prunable([result]) == []


def test_a_recording_closed_after_the_last_arrival_is_complete(tmp_path: Path) -> None:
    wake = ms("2026-11-06T03:30:00")
    record(tmp_path, [*range(wake, LAST_ARRIVAL, 240_000), LAST_ARRIVAL])
    assert audit_one(tmp_path).complete


def test_an_archived_day_is_audited_and_is_not_in_progress(tmp_path: Path) -> None:
    record(tmp_path, whole_day())
    archive.close_out(tmp_path, "2026-11-07")
    result = audit_one(tmp_path, now=END)
    assert result.complete
    assert result.path == archive.archive_file(tmp_path / archive.ARCHIVE_DIR, DAY)


def test_a_day_still_being_recorded_is_in_progress_and_kept(tmp_path: Path) -> None:
    record(tmp_path, [START, START + 60_000])
    result = audit_one(tmp_path, now=START + 120_000)
    assert kinds(result) == {au.ProblemKind.IN_PROGRESS}
    assert au.prunable([result]) == []


def test_a_recording_that_starts_late_is_prunable(tmp_path: Path) -> None:
    record(tmp_path, [t for t in whole_day() if t >= ms("2026-11-06T04:00:00")])
    result = audit_one(tmp_path)
    assert kinds(result) == {au.ProblemKind.STARTS_LATE}
    assert au.prunable([result]) == [result]


def test_a_recording_that_ends_early_is_incomplete(tmp_path: Path) -> None:
    record(tmp_path, [t for t in whole_day() if t < LAST_ARRIVAL - 3_600_000])
    assert kinds(audit_one(tmp_path)) == {au.ProblemKind.ENDS_EARLY}


def test_a_long_gap_is_incomplete(tmp_path: Path) -> None:
    gap = (START + 7_200_000, START + 7_200_000 + (au.MAX_GAP_S + 240) * 1000)
    record(tmp_path, [t for t in whole_day() if t <= gap[0] or t >= gap[1]])
    assert kinds(audit_one(tmp_path)) == {au.ProblemKind.GAP}


def test_a_missing_query_is_incomplete(tmp_path: Path) -> None:
    record(tmp_path, whole_day(), pair="ROCK-POWL")
    result = audit_one(tmp_path)
    assert kinds(result) == {au.ProblemKind.MISSING_QUERY}
    assert [p.detail for p in result.problems] == [f"depart {PAIR}"]


def test_a_missing_daily_source_is_incomplete(tmp_path: Path) -> None:
    record(tmp_path, whole_day(), daily=False)
    assert kinds(audit_one(tmp_path)) == {au.ProblemKind.MISSING_QUERY}


def test_trip_plans_requested_with_other_counts_are_a_mismatch(tmp_path: Path) -> None:
    record(tmp_path, whole_day(), before=0)
    assert kinds(audit_one(tmp_path)) == {au.ProblemKind.PLANNER_MISMATCH}


def test_expected_queries_cover_each_pair_and_its_stations() -> None:
    settings = recorder.WidgetSettings(
        api_key="K", refresh_s=60, request_timeout_s=15, trips_planned=4, trips_before=1
    )
    queries = au.expected_queries(settings, [PAIR]).queries
    assert {
        ("depart", PAIR),
        ("etd", "ALL"),
        ("etd", "WOAK"),
        ("etd", "EMBR"),
    } <= queries


@pytest.mark.parametrize(
    ("response", "counts"),
    [
        (planner_body(1, 4), (1, 4)),
        (json.dumps({"root": {"schedule": {"date": "x"}}}).encode(), None),
    ],
)
def test_planner_counts_read_the_response(
    response: bytes, counts: tuple[int, int] | None
) -> None:
    assert au.planner_counts(response) == counts


def zip_without_the_calendar() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("readme.txt", "")
    return out.getvalue()


@pytest.mark.parametrize(
    "feed",
    [b"not a zip", zip_without_the_calendar(), gtfs_feed("05:00", "26:30:00")],
    ids=["not-a-zip", "missing-table", "malformed-time"],
)
def test_a_timetable_that_cannot_be_read_leaves_the_span_unjudged(
    tmp_path: Path, feed: bytes
) -> None:
    noon = ms("2026-11-06T12:00:00")
    record(tmp_path, [noon + i * 240_000 for i in range(3)], feed=feed)
    assert audit_one(tmp_path).complete


def test_a_recording_without_refreshes_is_empty_and_prunable(tmp_path: Path) -> None:
    writer = store.Store(tmp_path)
    writer.write([poll("etd", START, subject="ALL")])
    writer.close()
    result = audit_one(tmp_path)
    assert kinds(result) == {au.ProblemKind.EMPTY}
    assert au.prunable([result]) == [result]


@pytest.mark.parametrize(
    "planned",
    [
        b"not json",
        b'{"root": {}}',
        b"[]",
        b'{"root": {"schedule": {"before": "x", "after": "4"}}}',
    ],
    ids=["not-json", "no-schedule", "not-an-object", "count-not-a-number"],
)
def test_a_trip_plan_that_cannot_be_read_is_no_mismatch(
    tmp_path: Path, planned: bytes
) -> None:
    record(tmp_path, whole_day(), planned=planned)
    assert audit_one(tmp_path).complete


def test_auditing_a_corpus_without_recordings_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(core.CorpusError):
        au.audit(tmp_path, EXPECTED, AFTER_THE_DAY)
