# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for what replay concludes: whose fault each failure was and how the report tells it."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from bart_corpus import replay as rp
from bart_corpus.gtfs_rt import Stop, StopUpdate
from bart_corpus.train_runs import Journey
from corpus_support import MIN, STATIC, T0, feed_corpus, gtfs_rt_feed, stop_at

BOARD = "ORIG-DEST"
TICK = T0 * 1000
LATER_TICK = (T0 + MIN) * 1000

TIMED = replace(
    STATIC,
    # T1 reaches XFER at 5 minutes and T2 leaves it at 7, in seconds after the origin T0
    trip_times={
        "T1": {"ORIG": (0, 0), "XFER": (5 * MIN, 5 * MIN)},
        "T2": {"XFER": (7 * MIN, 7 * MIN), "DEST": (30 * MIN, 30 * MIN)},
        "D1": {"ORIG": (0, 0), "DEST": (20 * MIN, 20 * MIN)},
    },
)
TRIP = Journey(T0, T0 + 30 * MIN, "T1", "XFER", "T2")
DIRECT = Journey(T0, T0 + 20 * MIN, "D1", None, None)


def data(feed: dict[str, dict[str, Stop]] | None = None) -> rp.RefreshData:
    return rp.RefreshData(TIMED, T0, feed or {})


@pytest.mark.parametrize(
    ("journey", "expected"),
    [
        (DIRECT, True),
        (TRIP, True),
        (Journey(T0, T0 + 30 * MIN, "T1", "XFER", "T9"), False),
        (Journey(T0, T0 + 30 * MIN, "T9", "XFER", "T2"), False),
    ],
    ids=[
        "direct",
        "connects",
        "connecting-train-not-timetabled",
        "first-train-not-timetabled",
    ],
)
def test_in_timetable_requires_a_timetabled_connection(
    journey: Journey, expected: bool
) -> None:
    assert rp.in_timetable(data(), journey) is expected


def test_in_timetable_rejects_a_connection_timetabled_to_leave_first() -> None:
    static = replace(
        TIMED,
        trip_times=TIMED.trip_times
        | {"T2": {"XFER": (4 * MIN, 4 * MIN), "DEST": (30 * MIN, 30 * MIN)}},
    )
    assert rp.in_timetable(rp.RefreshData(static, T0, {}), TRIP) is False


@pytest.mark.parametrize(
    ("journey", "feed", "now", "expected"),
    [
        (DIRECT, None, T0 - MIN, T0 + 20 * MIN),
        (Journey(T0, T0, "T1", None, None), None, T0 - MIN, None),
        (Journey(T0, T0, "T9", None, None), None, T0 - MIN, None),
        (DIRECT, None, T0 + 31, None),
        (DIRECT, None, T0 + 30, T0 + 20 * MIN),
        (TRIP, None, T0 - MIN, T0 + 30 * MIN),
        (
            TRIP,
            {"T1": {"XFER": stop_at(T0 + 8 * MIN)}},
            T0 - MIN,
            None,
        ),
        (
            TRIP,
            {"T1": {"XFER": stop_at(T0 + 7 * MIN + 30)}},
            T0 - MIN,
            T0 + 30 * MIN,
        ),
        (
            Journey(T0, T0, "T1", "XFER", "T3"),
            {"T3": {"XFER": stop_at(T0 + 7 * MIN)}},
            T0 - MIN,
            None,
        ),
        (Journey(T0, T0, "T1", "ELSE", "T2"), None, T0 - MIN, None),
    ],
    ids=[
        "direct",
        "no-stop-at-destination",
        "unknown-train",
        "already-left",
        "left-within-recorded-timing",
        "transfer",
        "connection-impossible",
        "connection-within-recorded-timing",
        "no-time-at-destination",
        "no-time-at-transfer",
    ],
)
def test_expected_arrival_tells_when_the_data_had_a_rider_arrive(
    journey: Journey,
    feed: dict[str, dict[str, Stop]] | None,
    now: int,
    expected: int | None,
) -> None:
    assert rp.expected_arrival(data(feed), journey, BOARD, now) == expected


def test_refresh_data_keys_stops_by_station_and_skips_stops_without_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates = {
        "T1": [
            StopUpdate("S1", T0, T0),
            StopUpdate("OTHER", T0 + MIN, None),
            StopUpdate("S2", None, None),
        ]
    }
    monkeypatch.setattr(rp, "feed_updates", lambda _db, _tick: updates)
    got = rp.refresh_data(cast(sqlite3.Connection, None), TIMED, T0, TICK)
    assert got.feed == {
        "T1": {
            "ORIG": Stop(T0, T0),
            "OTHER": Stop(T0 + MIN, T0 + MIN),
        }
    }


@pytest.mark.parametrize(
    ("status", "told", "now", "fault"),
    [
        (
            "no trains",
            None,
            T0 - MIN,
            (rp.OUR_FAULT, "the board said no trains"),
        ),
        (
            "hidden: first train not running",
            T0 + 40 * MIN,
            T0 - MIN,
            (rp.OUR_FAULT, "hidden: first train not running"),
        ),
        (
            "direct",
            T0 + 40 * MIN,
            T0 + MIN,
            (rp.SCHEDULE_EXCEPTION, rp.CHANGED_LATER),
        ),
    ],
    ids=[
        "board-told-of-no-trip",
        "status-without-a-rider-wording-is-reported-as-is",
        "first-train-had-already-left",
    ],
)
def test_attribute_trip_names_the_fault_from_what_the_board_said(
    status: str, told: int | None, now: int, fault: tuple[str, str]
) -> None:
    assert rp.attribute_trip(data(), TRIP, BOARD, status, told, now) == fault


@pytest.mark.parametrize(
    ("shown_late_by", "gone"),
    [(rp.REGRET_S, False), (rp.REGRET_S + 1, True)],
    ids=["at-the-limit", "past-the-limit"],
)
def test_a_train_is_gone_only_when_shown_over_the_limit_after_it_left(
    shown_late_by: int, gone: bool
) -> None:
    d = cast(rp.Departure, {"shown": T0 + shown_late_by, "left": T0})
    assert rp.train_gone(d) is gone


def hidden(**changes: Any) -> rp.HiddenTrip:
    fields: dict[str, Any] = {
        "shown": False,
        "cause": "not in the trip plan",
        "at": "17:00:00 ORIG DEST",
        "board": BOARD,
        "tick": TICK,
        "now": T0 - MIN,
        "journey": TRIP,
        "described": "17:00:00 YELLOW via XFER ORANGE arriving 17:30:00",
        "alternative": T0 + 40 * MIN,
        "lost_s": 10 * MIN,
    } | changes
    return cast(rp.HiddenTrip, fields)


def shown_trip(beaten: bool, **changes: Any) -> rp.ShownTrip:
    fields: dict[str, Any] = {
        "beaten": beaten,
        "transfer": False,
        "at": "17:01:00 ORIG DEST",
        "board": BOARD,
        "tick": TICK,
        "now": T0 - MIN,
        "described": "direct 17:10:00 RED",
        "better": TRIP if beaten else None,
        "better_described": "17:00:00 YELLOW via XFER ORANGE arriving 17:30:00"
        if beaten
        else None,
        "lost_s": 10 * MIN if beaten else None,
        "estimate": T0 + 40 * MIN,
    } | changes
    return cast(rp.ShownTrip, fields)


def connection(shown: bool, kept: bool, made: bool, **changes: Any) -> rp.Connection:
    fields: dict[str, Any] = {
        "shown": shown,
        "kept": kept,
        "made": made,
        "at": "17:02:00 ORIG DEST",
        "board": BOARD,
        "planned": "17:00:00 YELLOW to XFER, 17:07:00 ORANGE",
        "tick": TICK,
        "now": T0 - MIN,
        "origin": "ORIG",
        "first_trip": "T1",
        "connecting_trip": "T2",
        "station": "XFER",
        "first_live": True,
        "connecting_live": True,
        "drop_reason": None,
        "margin_s": 2 * MIN,
    } | changes
    return cast(rp.Connection, fields)


def departure(shown_late_by: int, **changes: Any) -> rp.Departure:
    fields: dict[str, Any] = {
        "at": "17:03:00 ORIG DEST",
        "board": BOARD,
        "tick": TICK,
        "trip": "T1",
        "line": "YELLOW",
        "origin": "ORIG",
        "shown": T0 + MIN + shown_late_by,
        "left": T0 + MIN,
    } | changes
    return cast(rp.Departure, fields)


def empty_board(tick: int, journey: Journey) -> rp.EmptyBoard:
    return {
        "at": "17:04:00 ORIG DEST",
        "board": BOARD,
        "tick": tick,
        "now": T0 - MIN,
        "journey": journey,
        "described": "17:02:00 ? arriving 17:20:00",
    }


ONLY_IN_FEED = Journey(T0 + 2 * MIN, T0 + 20 * MIN, "T8", None, None)


def feed_of_one_refresh(tmp_path: Path) -> Path:
    """A corpus whose feed carries train T8 at the first refresh and nothing at the second."""
    return feed_corpus(
        tmp_path,
        [
            (
                T0,
                gtfs_rt_feed(
                    {
                        "T8": [
                            ("S1", T0 + 2 * MIN),
                            ("S2", T0 + 5 * MIN),
                            ("S3", T0 + 20 * MIN),
                        ]
                    }
                ),
            ),
            (T0 + MIN, gtfs_rt_feed({})),
        ],
    )


def named(entry: rp.Entry) -> tuple[str | None, str | None]:
    return entry.get("fault"), entry.get("why")


def test_attribute_names_a_fault_for_each_failure_only(tmp_path: Path) -> None:
    path = feed_of_one_refresh(tmp_path)
    out = rp.Findings()
    out.best_trips[BOARD, "T1", "XFER"] = hidden()
    out.best_trips[BOARD, "OK", None] = {"shown": True, "cause": None}
    out.shown_trips[BOARD, "T1", None] = shown_trip(True, status="direct")
    out.shown_trips[BOARD, "T2", None] = shown_trip(False)
    out.connections[BOARD, 0] = connection(
        False, False, True, drop_reason="first train not running"
    )
    out.departures[BOARD, "T1"] = departure(5 * MIN)
    out.departures[BOARD, "T2"] = departure(0, trip="T2")
    out.empty_boards.extend(
        [empty_board(TICK, ONLY_IN_FEED), empty_board(LATER_TICK, ONLY_IN_FEED)]
    )
    out.no_data_boards.append({"at": "17:05:00 ORIG DEST", "board": BOARD})
    with closing(sqlite3.connect(path)) as db:
        rp.attribute(db, TIMED, out, T0)
    trip = out.best_trips[BOARD, "T1", "XFER"]
    assert trip["shown"] is False
    assert named(trip) == (rp.OUR_FAULT, "BART's trip planner did not offer it")
    assert "fault" not in out.best_trips[BOARD, "OK", None]
    assert named(out.shown_trips[BOARD, "T1", None]) == (
        rp.OUR_FAULT,
        "a direct train was left off the board",
    )
    assert "fault" not in out.shown_trips[BOARD, "T2", None]
    assert named(out.connections[BOARD, 0]) == (
        rp.OUR_FAULT,
        "hidden: first train not running",
    )
    assert named(out.departures[BOARD, "T1"]) == (
        rp.OUR_FAULT,
        "time shown was later than the timetable, at the start of its line",
    )
    assert "fault" not in out.departures[BOARD, "T2"]
    # the same empty board is judged by what each refresh's data showed
    assert [named(e) for e in out.empty_boards] == [
        (rp.OUR_FAULT, "the widget's data had a trip within the hour"),
        (rp.SCHEDULE_EXCEPTION, rp.CHANGED_LATER),
    ]
    assert named(out.no_data_boards[0]) == (rp.SOURCE_WRONG, "no BART data loaded")


def test_attribute_weighs_each_failure_against_what_the_board_told_at_that_refresh(
    tmp_path: Path,
) -> None:
    path = feed_of_one_refresh(tmp_path)
    out = rp.Findings()
    # the board told of an arrival no later than the trip's, so it did not fail the rider
    out.best_trips[BOARD, "as-soon", None] = hidden(alternative=T0 + 30 * MIN)
    out.best_trips[BOARD, "later", None] = hidden(alternative=T0 + 40 * MIN)
    # a trip only the first refresh's feed carried
    out.best_trips[BOARD, "first", None] = hidden(journey=ONLY_IN_FEED)
    out.best_trips[BOARD, "second", None] = hidden(
        journey=ONLY_IN_FEED, tick=LATER_TICK
    )
    out.shown_trips[BOARD, "as-soon", None] = shown_trip(
        True, status="direct", estimate=T0 + 30 * MIN
    )
    out.shown_trips[BOARD, "later", None] = shown_trip(
        True, status="direct", estimate=T0 + 40 * MIN
    )
    out.shown_trips[BOARD, "first", None] = shown_trip(
        True, status="direct", better=ONLY_IN_FEED
    )
    out.shown_trips[BOARD, "second", None] = shown_trip(
        True, status="direct", better=ONLY_IN_FEED, tick=LATER_TICK
    )
    hidden_t8 = {
        "drop_reason": "first train not running",
        "first_trip": "T8",
        "station": "XFER",
    }
    out.connections[BOARD, 0] = connection(False, False, True, **hidden_t8)
    out.connections[BOARD, 1] = connection(
        False, False, True, tick=LATER_TICK, **hidden_t8
    )
    left = {"trip": "T8", "shown": T0 + 9 * MIN, "left": T0 + 2 * MIN}
    out.departures[BOARD, "first"] = departure(0, **left)
    out.departures[BOARD, "second"] = departure(0, tick=LATER_TICK, **left)
    with closing(sqlite3.connect(path)) as db:
        rp.attribute(db, TIMED, out, T0)
    planner = (rp.OUR_FAULT, "BART's trip planner did not offer it")
    changed = (rp.SCHEDULE_EXCEPTION, rp.CHANGED_LATER)
    assert [named(b) for b in out.best_trips.values() if b["shown"] is False] == [
        changed,
        planner,
        planner,
        changed,
    ]
    direct = (rp.OUR_FAULT, "a direct train was left off the board")
    assert [named(t) for t in out.shown_trips.values()] == [
        changed,
        direct,
        direct,
        changed,
    ]
    assert [named(c) for c in out.connections.values()] == [
        (rp.OUR_FAULT, "hidden: first train not running"),
        changed,
    ]
    assert [named(d) for d in out.departures.values()] == [
        (rp.OUR_FAULT, "time shown was later than BART's live feed, along its line"),
        (rp.SOURCE_WRONG, "all BART data gave a later time, along its line"),
    ]


FAULT = {"fault": rp.OUR_FAULT, "why": "the data showed it"}


def failing_findings() -> rp.Findings:
    out = rp.Findings(records=2, boards=3)
    out.views["total"] = 3
    out.best_trips[BOARD, "H", None] = hidden(**FAULT)
    out.best_trips[BOARD, "OK", None] = {"shown": True, "cause": None}
    out.shown_trips[BOARD, "B", None] = shown_trip(True, status="direct", **FAULT)
    out.shown_trips[BOARD, "OK", None] = shown_trip(False)
    out.connections[BOARD, 0] = connection(True, True, False, **FAULT)
    out.connections[BOARD, 1] = connection(
        False, False, True, drop_reason="first train not running", **FAULT
    )
    out.connections[BOARD, 2] = connection(True, True, True)
    out.departures[BOARD, "G"] = departure(5 * MIN, **FAULT)
    out.departures[BOARD, "OK"] = departure(0)
    out.empty_boards.append(cast(rp.EmptyBoard, empty_board(TICK, TRIP) | FAULT))
    out.no_data_boards.append(
        {
            "at": "17:05:00 ORIG DEST",
            "board": BOARD,
            "fault": rp.SOURCE_WRONG,
            "why": "no BART data loaded",
        }
    )
    return out


def test_rider_view_counts_each_experience_against_its_opportunities() -> None:
    view = rp.rider_view(failing_findings())
    assert {k: (v["failed"], v["total"]) for k, v in view.items()} == {
        "faster_trip_hidden": (1, 2),
        "later_arrival": (1, 2),
        "connection_missed": (1, 2),
        "connection_hidden": (1, 2),
        "train_gone": (1, 2),
        "no_trains": (1, 3),
        "no_data": (1, 3),
    }
    assert view["no_data"]["faults"] == {rp.SOURCE_WRONG: {"no BART data loaded": 1}}
    assert view["faster_trip_hidden"]["faults"] == {
        rp.OUR_FAULT: {"the data showed it": 1}
    }


def test_rider_view_lists_faults_in_the_reporting_order() -> None:
    out = rp.Findings()
    out.no_data_boards.extend(
        [
            {"at": "a", "board": BOARD, "fault": rp.SCHEDULE_EXCEPTION, "why": "w"},
            {"at": "b", "board": BOARD, "fault": rp.SOURCE_WRONG, "why": "w"},
            {"at": "c", "board": BOARD, "fault": rp.OUR_FAULT, "why": "w"},
        ]
    )
    assert list(rp.rider_view(out)["no_data"]["faults"]) == list(rp.FAULTS)


def test_rider_failures_lists_every_failure_once_with_its_fault() -> None:
    rows = rp.rider_failures(failing_findings())
    assert [(r["item"], r["at"], r["what"], r["lost_s"]) for r in rows] == [
        (
            "faster_trip_hidden",
            "17:00:00 ORIG DEST",
            "17:00:00 YELLOW via XFER ORANGE arriving 17:30:00",
            10 * MIN,
        ),
        (
            "later_arrival",
            "17:01:00 ORIG DEST",
            "direct 17:10:00 RED; better 17:00:00 YELLOW via XFER ORANGE arriving 17:30:00",
            10 * MIN,
        ),
        (
            "connection_missed",
            "17:02:00 ORIG DEST",
            "17:00:00 YELLOW to XFER, 17:07:00 ORANGE, margin 120 s",
            None,
        ),
        (
            "connection_hidden",
            "17:02:00 ORIG DEST",
            "17:00:00 YELLOW to XFER, 17:07:00 ORANGE, margin 120 s",
            None,
        ),
        (
            "train_gone",
            "17:03:00 ORIG DEST",
            "YELLOW shown 17:06:00, left 17:01:00",
            None,
        ),
        (
            "no_trains",
            "17:04:00 ORIG DEST",
            "17:02:00 ? arriving 17:20:00",
            None,
        ),
        ("no_data", "17:05:00 ORIG DEST", "", None),
    ]
    assert {r["board"] for r in rows} == {BOARD}
    assert [(r["fault"], r["why"]) for r in rows[-2:]] == [
        (rp.OUR_FAULT, "the data showed it"),
        (rp.SOURCE_WRONG, "no BART data loaded"),
    ]


@pytest.mark.parametrize(
    ("n", "of", "text"),
    [(1, 8, "1 of 8 (12.5%)"), (0, 0, "0 of 0")],
    ids=["share", "no-opportunities"],
)
def test_share_shows_a_percentage_only_of_a_real_total(
    n: int, of: int, text: str
) -> None:
    assert rp.share(n, of) == text


def test_summarize_rider_view_nests_faults_and_whys_most_common_first() -> None:
    view: dict[str, rp.Experience] = {
        "connection_missed": {
            "rider": "Rider missed a listed connection",
            "of": "listed connections",
            "total": 20,
            "failed": 5,
            "faults": {rp.OUR_FAULT: {"a": 1, "b": 4}},
        },
    }
    assert rp.summarize_rider_view(view) == [
        "rider experience, each trip and connection as of the last refresh before its first train left:",
        "  Rider missed a listed connection: 5 of 20 (25.0%) listed connections",
        "    our fault: 5 of 20 (25.0%)",
        "      b: 4 of 20 (20.0%)",
        "      a: 1 of 20 (5.0%)",
    ]


def test_summarize_of_nothing_reports_every_section_as_empty() -> None:
    assert rp.summarize(rp.Findings()).splitlines() == [
        "0 runs, 0 boards",
        "",
        "rider experience, each trip and connection as of the last refresh before its first train left:",
        "  Rider wasn't shown the fastest trip: 0 of 0 fastest trips",
        "  Rider took a listed trip, but one leaving at the same time or later got there over 1 minute sooner: 0 of 0 listed trips",
        "  Rider missed a listed connection: 0 of 0 listed connections",
        "  Rider wasn't shown a connection they could have made: 0 of 0 connections riders could make",
        "  Rider arrived at the time shown and the train had already left: 0 of 0 listed trains",
        "  Board said no trains, but a trip left within the hour: 0 of 0 board views",
        "  Board had no data: 0 of 0 board views",
        "",
        "errors: none",
        "",
        "warnings: none",
        "",
        "shown trips: none",
        "",
        "arrival estimate error, actual minus estimated, seconds:",
        "",
        "planned transfers by refresh: none",
        "",
        "rider-facing failures: 0",
    ]


def test_summarize_reports_counts_estimates_and_every_failure() -> None:
    out = failing_findings()
    out.errors["thrown: boom"] = 2
    out.warnings["stale data"] = 1
    out.shown["direct: checked"] = 4
    out.transfers["transfer kept: connection held"] = 3
    out.arrival_error_s["direct"].extend([0, 10, 20])
    out.arrival_error_s["transfer"].append(12)
    text = rp.summarize(out)
    lines = text.splitlines()
    assert lines[0] == "2 runs, 3 boards"
    for line in (
        "      2  thrown: boom",
        "      1  stale data",
        "      4  direct: checked",
        "      3  transfer kept: connection held",
        # three samples extrapolate past their range, one sample has no spread
        "  direct: n=3 median=10 p10=-6 p90=26 max=20",
        "  transfer: n=1 median=12 p10=12 p90=12 max=12",
        "rider-facing failures: 7",
    ):
        assert line in lines
    failures = lines[lines.index("rider-facing failures: 7") + 1 :]
    rows = [json.loads(line) for line in failures]
    assert rows == rp.rider_failures(out)
