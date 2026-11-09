# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for scoring replayed boards and attributing faults; they make no requests and run no script."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from bart_corpus import gtfs_rt, train_runs
from bart_corpus import replay as rp
from corpus_support import (
    MIN,
    RUNS,
    STATIC,
    T0,
    feed_corpus,
    gtfs_rt_feed,
    stop_at,
    train_run,
)


def transfer_board(
    first_min: int, second_min: int, kept: bool, first_train: str | None
) -> dict[str, Any]:
    legs = [
        {
            "line": "YELLOW",
            "from": "ORIG",
            "to": "XFER",
            "departs": (T0 + first_min * MIN) * 1000,
            "arrives": (T0 + first_min * MIN + 4 * MIN) * 1000,
        },
        {
            "line": "ORANGE",
            "from": "XFER",
            "to": "DEST",
            "departs": (T0 + second_min * MIN) * 1000,
            "arrives": (T0 + second_min * MIN + 27 * MIN) * 1000,
        },
    ]
    train = {"departs": legs[0]["departs"], "scheduled": False} if kept else None
    transfer = {
        "legs": legs,
        "firstTrain": first_train,
        "connectingTrain": None,
        "train": train,
        "grandMeet": False,
    }
    shown = (
        [{"departs": train["departs"], "transfer": {"station": "XFER"}}]
        if train
        else []
    )
    return {"board": "ORIG-DEST", "trains": shown, "transfers": [transfer]}


MOMENT = rp.Moment("17:00", T0 * 1000, T0 * 1000)


def test_check_transfers_finds_a_dropped_connection_that_held() -> None:
    out = rp.Findings()
    rp.check_transfers(
        transfer_board(2, 8, False, "not running"),
        [],
        RUNS,
        T0 + 60 * MIN,
        MOMENT,
        out,
    )
    assert out.transfers == {"transfer dropped: connection held": 1}
    (detail,) = out.false_drops.values()
    assert (detail.get("reason"), detail["margin_s"], detail.get("refreshes")) == (
        "first train not running",
        2 * MIN,
        1,
    )
    assert not out.false_keeps


def test_check_transfers_counts_a_connection_within_recorded_timing_as_made() -> None:
    # the connection left 20 seconds before the recorded arrival of F2
    runs = {
        "F2": RUNS["F2"],
        "C3": train_run(
            "C3", "ORANGE", {"XFER": T0 + 24 * MIN - 20, "DEST": T0 + 50 * MIN}
        ),
    }
    out = rp.Findings()
    rp.check_transfers(
        transfer_board(20, 24, False, None),
        [],
        runs,
        T0 + 90 * MIN,
        MOMENT,
        out,
    )
    assert out.transfers == {"transfer dropped: connection tight": 1}
    (detail,) = out.false_drops.values()
    assert (detail.get("reason"), detail["margin_s"]) == (
        "connecting train projected to leave before the first train arrives",
        -20,
    )


def test_check_transfers_finds_a_kept_connection_that_was_missed() -> None:
    out = rp.Findings()
    rp.check_transfers(
        transfer_board(20, 24, True, None),
        [],
        RUNS,
        T0 + 90 * MIN,
        MOMENT,
        out,
    )
    assert out.transfers == {"transfer kept: connection missed": 1}
    (detail,) = out.false_keeps.values()
    assert detail["margin_s"] == -45
    assert not out.false_drops


def test_planned_status_names_the_rule_that_dropped_a_trip() -> None:
    board = transfer_board(2, 8, False, "not running")
    trip = train_runs.Journey(T0 + 2 * MIN, T0 + 35 * MIN, "F1", "XFER", "C1")
    assert (
        rp.planned_status(board, trip, T0 * 1000) == "hidden: first train not running"
    )
    # a trip timetabled to leave before now needs a live first train
    late = transfer_board(2, 8, False, None)
    assert (
        rp.planned_status(late, trip, (T0 + 3 * MIN) * 1000)
        == "hidden: timetabled to leave before now, with no live first train"
    )


def test_drop_first_trips_leaves_a_planner_failure_unchanged() -> None:
    failure = json.dumps({"root": {"message": {"error": "An exception has occurred"}}})
    assert rp.drop_first_trips(failure, 1) == failure


def test_drop_first_trips_drops_the_earliest_trips() -> None:
    plan = {"root": {"schedule": {"request": {"trip": [{"n": n} for n in range(5)]}}}}
    dropped = json.loads(rp.drop_first_trips(json.dumps(plan), 1))
    assert dropped["root"]["schedule"]["request"]["trip"] == [
        {"n": n} for n in range(1, 5)
    ]


TIMED = replace(
    STATIC,
    # T1 reaches XFER at 5 minutes and T2 leaves XFER at 7, in seconds after the origin T0
    trip_times={
        "T1": {"ORIG": (0, 0), "XFER": (5 * MIN, 5 * MIN)},
        "T2": {"XFER": (7 * MIN, 7 * MIN), "DEST": (30 * MIN, 30 * MIN)},
    },
)
TRIP = train_runs.Journey(T0, T0 + 30 * MIN, "T1", "XFER", "T2")


def refresh_data(
    feed_stops: dict[str, dict[str, gtfs_rt.Stop]] | None = None,
) -> rp.RefreshData:
    return rp.RefreshData(TIMED, T0, feed_stops or {})


def connection(**changes: Any) -> rp.Connection:
    fields: dict[str, Any] = {
        "first_trip": "T1",
        "connecting_trip": "T2",
        "station": "XFER",
        "origin": "ORIG",
        "now": T0 - MIN,
        "first_live": True,
        "connecting_live": True,
        "drop_reason": "connecting train projected to leave before the first train arrives",
    } | changes
    return cast(rp.Connection, fields)


def test_refresh_data_prefers_the_feed_and_falls_back_to_the_timetable() -> None:
    live = stop_at(T0 + 6 * MIN)
    d = refresh_data({"T1": {"XFER": live}})
    assert d.stop("T1", "XFER") == live
    assert d.stop("T2", "XFER") == stop_at(T0 + 7 * MIN)
    assert d.stop("T9", "XFER") is None


@pytest.mark.parametrize(
    ("first_live", "connecting_live", "why"),
    [
        (True, True, "listed: looked possible after rounding to whole minutes"),
        (True, False, "listed with no live time for the connecting train"),
        (False, True, "listed with no live time for the first train"),
        (False, False, "listed with no live time for either train"),
    ],
)
def test_a_missed_connection_is_our_fault_when_the_data_showed_the_miss(
    first_live: bool, connecting_live: bool, why: str
) -> None:
    feed_stops = {"T1": {"XFER": stop_at(T0 + 9 * MIN)}}
    c = connection(first_live=first_live, connecting_live=connecting_live)
    assert rp.attribute_missed_connection(refresh_data(feed_stops), c) == (
        rp.OUR_FAULT,
        why,
    )


def test_a_missed_connection_is_an_exception_when_the_data_showed_it_holding() -> None:
    assert rp.attribute_missed_connection(refresh_data(), connection()) == (
        rp.SCHEDULE_EXCEPTION,
        rp.CHANGED_LATER,
    )


def test_a_hidden_connection_is_our_fault_when_the_data_showed_it_holding() -> None:
    assert rp.attribute_hidden_connection(refresh_data(), connection()) == (
        rp.OUR_FAULT,
        "hidden: connecting train projected to leave before the first train arrives",
    )


def test_a_hidden_connection_is_an_exception_when_the_data_showed_a_miss() -> None:
    feed_stops = {"T1": {"XFER": stop_at(T0 + 9 * MIN)}}
    assert rp.attribute_hidden_connection(refresh_data(feed_stops), connection()) == (
        rp.SCHEDULE_EXCEPTION,
        rp.CHANGED_LATER,
    )


def test_a_hidden_connection_is_an_exception_when_the_first_train_had_left() -> None:
    gone = connection(now=T0 + 2 * MIN)
    assert (
        rp.attribute_hidden_connection(refresh_data(), gone)[0] == rp.SCHEDULE_EXCEPTION
    )


@pytest.mark.parametrize(
    ("status", "told", "fault"),
    [
        (
            "not in the trip plan",
            T0 + 40 * MIN,
            (rp.OUR_FAULT, "BART's trip planner did not offer it"),
        ),
        (
            "not in the trip plan",
            T0 + 30 * MIN,
            (rp.SCHEDULE_EXCEPTION, rp.CHANGED_LATER),
        ),
        ("no data", None, (rp.SOURCE_WRONG, "no BART data loaded")),
    ],
    ids=["data-showed-it-sooner", "board-told-of-as-soon-a-trip", "no-data"],
)
def test_a_hidden_trip_is_attributed_from_what_the_refresh_showed(
    status: str, told: int | None, fault: tuple[str, str]
) -> None:
    assert (
        rp.attribute_trip(refresh_data(), TRIP, "ORIG-DEST", status, told, T0 - MIN)
        == fault
    )


def test_a_trip_the_timetable_does_not_make_is_an_exception_even_when_live_showed_it() -> (
    None
):
    # the timetable has T2 leave XFER before T1 arrives, but the feed had T2 late enough to connect
    static = replace(
        TIMED,
        trip_times=TIMED.trip_times
        | {"T2": {"XFER": (4 * MIN, 4 * MIN), "DEST": (30 * MIN, 30 * MIN)}},
    )
    late = rp.RefreshData(static, T0, {"T2": {"XFER": stop_at(T0 + 6 * MIN)}})
    fault = rp.attribute_trip(
        late, TRIP, "ORIG-DEST", "not in the trip plan", T0 + 50 * MIN, T0 - MIN
    )
    assert fault == (rp.SCHEDULE_EXCEPTION, rp.OFF_TIMETABLE)


@pytest.mark.parametrize(
    ("departure", "feed_stops", "fault"),
    [
        (
            {"trip": "T1", "origin": "ORIG", "shown": T0 + 5 * MIN, "left": T0 + MIN},
            None,
            (
                rp.OUR_FAULT,
                "time shown was later than the timetable, at the start of its line",
            ),
        ),
        (
            {
                "trip": "T2",
                "origin": "XFER",
                "shown": T0 + 12 * MIN,
                "left": T0 + 5 * MIN,
            },
            {"T2": {"XFER": stop_at(T0 + 5 * MIN)}},
            (
                rp.OUR_FAULT,
                "time shown was later than BART's live feed, at the start of its line",
            ),
        ),
        (
            {
                "trip": "T9",
                "origin": "XFER",
                "shown": T0 + 12 * MIN,
                "left": T0 + 5 * MIN,
            },
            None,
            (rp.SOURCE_WRONG, "all BART data gave a later time, along its line"),
        ),
    ],
    ids=[
        "timetable-had-it-earlier",
        "gtfs-rt-had-it-earlier",
        "every-source-had-it-later",
    ],
)
def test_a_train_shown_late_is_attributed_to_the_source_with_the_earlier_time(
    departure: dict[str, Any],
    feed_stops: dict[str, dict[str, gtfs_rt.Stop]] | None,
    fault: tuple[str, str],
) -> None:
    assert (
        rp.attribute_departure(refresh_data(feed_stops), cast(rp.Departure, departure))
        == fault
    )


def test_attribute_judges_each_failure_by_its_refresh(tmp_path: Path) -> None:
    # at the last refresh T1 was predicted at XFER 2 minutes after T2 left
    path = feed_corpus(
        tmp_path,
        [
            (
                T0,
                gtfs_rt_feed(
                    {"T1": [("S2", T0 + 5 * MIN)], "T2": [("S2", T0 + 3 * MIN)]}
                ),
            )
        ],
    )
    out = rp.Findings()
    out.connections["ORIG-DEST", 0] = connection(
        shown=True, kept=True, made=False, tick=T0 * 1000
    )
    out.connections["ORIG-DEST", 1] = connection(
        shown=False, kept=True, made=True, tick=T0 * 1000
    )
    with closing(sqlite3.connect(path)) as db:
        rp.attribute(db, TIMED, out, T0)
    first, second = out.connections.values()
    assert (first.get("fault"), first.get("why")) == (
        rp.OUR_FAULT,
        "listed: looked possible after rounding to whole minutes",
    )
    assert "fault" not in second


def test_rider_view_groups_each_experience_by_fault_and_why() -> None:
    out = rp.Findings()
    out.connections["ORIG-DEST", 0] = connection(
        shown=True, kept=True, made=False, fault=rp.OUR_FAULT, why="w1"
    )
    out.connections["ORIG-DEST", 1] = connection(
        shown=True,
        kept=True,
        made=False,
        fault=rp.SCHEDULE_EXCEPTION,
        why=rp.CHANGED_LATER,
    )
    out.connections["ORIG-DEST", 2] = connection(shown=True, kept=True, made=True)
    item = rp.rider_view(out)["connection_missed"]
    assert (item["total"], item["failed"]) == (3, 2)
    assert item["faults"] == {
        rp.OUR_FAULT: {"w1": 1},
        rp.SCHEDULE_EXCEPTION: {rp.CHANGED_LATER: 1},
    }
