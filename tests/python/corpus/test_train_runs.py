# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for the trains that ran and the trips they made possible; they make no requests."""

from __future__ import annotations

import io
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path

import pytest

from bart_corpus import core, store, train_runs
from corpus_support import (
    MIN,
    RUNS,
    STATIC,
    T0,
    feed_corpus,
    field,
    gtfs_rt_feed,
    poll,
    stop_at,
)


def test_actual_runs_keep_stops_the_train_left_or_where_its_trip_ended(
    tmp_path: Path,
) -> None:
    path = feed_corpus(
        tmp_path,
        [
            (
                T0,
                gtfs_rt_feed(
                    {
                        "T1": [("S1", T0 + 30), ("S2", T0 + 200)],
                        "T2": [("S2", T0 + 400), ("S3", T0 + 900)],
                    }
                ),
            ),
            # T1 left ORIG, then its trip ended at XFER; T2 has not reached DEST
            (
                T0 + MIN,
                gtfs_rt_feed(
                    {
                        "T1": [("S2", T0 + 210)],
                        "T2": [("S2", T0 + 400), ("S3", T0 + 900)],
                    }
                ),
            ),
            (
                T0 + 3 * MIN,
                gtfs_rt_feed(
                    {
                        "T1": [("S2", T0 + 210)],
                        "T2": [("S2", T0 + 400), ("S3", T0 + 900)],
                    }
                ),
            ),
            (T0 + 4 * MIN, gtfs_rt_feed({"T2": [("S2", T0 + 420), ("S3", T0 + 900)]})),
            (T0 + 8 * MIN, gtfs_rt_feed({"T2": [("S3", T0 + 910)]})),
        ],
    )
    with closing(sqlite3.connect(path)) as db:
        runs, known_until = train_runs.actual_runs(db, STATIC, store.refresh_ticks(db))
    assert known_until == T0 + 8 * MIN - train_runs.FINAL_S
    assert runs["T1"].stops == {
        "ORIG": stop_at(T0 + 30),
        "XFER": stop_at(T0 + 210),
    }
    assert runs["T2"].stops == {"XFER": stop_at(T0 + 420)}
    assert (runs["T2"].line, runs["T2"].dir, runs["T2"].stations) == (
        "ORANGE",
        "s",
        ["XFER", "DEST"],
    )


def test_actual_runs_take_a_stop_the_feed_drops_as_left_before_then(
    tmp_path: Path,
) -> None:
    # the last prediction of T1 at ORIG stays 2 minutes ahead, but the next refresh lists T1 without ORIG
    path = feed_corpus(
        tmp_path,
        [
            (T0, gtfs_rt_feed({"T1": [("S1", T0 + 2 * MIN), ("S2", T0 + 5 * MIN)]})),
            (T0 + MIN, gtfs_rt_feed({"T1": [("S2", T0 + 5 * MIN)]})),
            (T0 + 9 * MIN, gtfs_rt_feed({})),
        ],
    )
    with closing(sqlite3.connect(path)) as db:
        runs, _ = train_runs.actual_runs(db, STATIC, store.refresh_ticks(db))
    assert runs["T1"].stops["ORIG"] == stop_at(T0 + MIN - 1)


def test_journeys_include_connections_a_rider_can_make() -> None:
    found = {
        (j.first, j.via, j.second, j.arrives - T0)
        for j in train_runs.journeys(RUNS, "ORIG", "DEST")
    }
    assert found == {
        ("D1", None, None, 40 * MIN),
        ("D1", "XFER", "C1", 35 * MIN),
        ("F1", "XFER", "C1", 35 * MIN),
        ("D2", None, None, 35 * MIN),
    }


def test_frontier_keeps_trips_no_later_trip_beats() -> None:
    kept = train_runs.frontier(train_runs.journeys(RUNS, "ORIG", "DEST"), T0)
    # D2 leaves last of those arriving at 35, so it beats D1 and F1
    assert [(j.first, j.via) for j in kept] == [("D2", None)]


@pytest.mark.parametrize(
    ("margin_s", "outcome"),
    [
        (0, "held"),
        (-1, "tight"),
        (-train_runs.TIMING_S, "tight"),
        (-train_runs.TIMING_S - 1, "missed"),
    ],
)
def test_connection_outcome_allows_for_the_recorded_times(
    margin_s: int, outcome: str
) -> None:
    assert train_runs.connection_outcome(margin_s) == outcome


def static_zip() -> bytes:
    files = {
        "stops.txt": "stop_id,parent_station\nS1,ORIG\nS2,XFER\n",
        "routes.txt": "route_id,route_short_name\n1,Yellow-S\n",
        "trips.txt": "trip_id,route_id\nT1,1\n",
        "stop_times.txt": (
            "trip_id,stop_id,arrival_time,departure_time,stop_sequence\n"
            "T1,S1,17:00:00,17:00:00,1\nT1,S2,17:05:00,17:05:00,2\n"
        ),
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return out.getvalue()


def test_load_recording_returns_the_refreshes_and_the_timetable(
    tmp_path: Path,
) -> None:
    tick = T0 * 1000
    writer = store.Store(tmp_path)
    writer.write(
        [
            poll("gtfs-static", tick, static_zip()),
            poll("tripupdate", tick),
            poll("tripupdate", tick + 60_000),
        ]
    )
    writer.close()
    path = next(tmp_path.glob("*.sqlite"))
    with closing(sqlite3.connect(path)) as db:
        ticks, static = train_runs.load_recording(db, path)
    assert ticks == [tick, tick + 60_000]
    assert static.trip_stations == {"T1": ["ORIG", "XFER"]}


def test_load_recording_requires_a_refresh(tmp_path: Path) -> None:
    path = tmp_path / "2026-11-06.sqlite"
    with closing(store.open_db(path)) as db, pytest.raises(core.CorpusError):
        train_runs.load_recording(db, path)


@pytest.mark.parametrize(
    "static_status", [None, 500], ids=["never-fetched", "fetch-failed"]
)
def test_load_recording_requires_a_timetable(
    tmp_path: Path, static_status: int | None
) -> None:
    tick = T0 * 1000
    writer = store.Store(tmp_path)
    writer.write([poll("tripupdate", tick)])
    if static_status is not None:
        writer.write([poll("gtfs-static", tick, status=static_status)])
    writer.close()
    path = next(tmp_path.glob("*.sqlite"))
    with closing(sqlite3.connect(path)) as db, pytest.raises(core.CorpusError):
        train_runs.load_recording(db, path)


def test_actual_runs_leave_out_a_stop_without_times(tmp_path: Path) -> None:
    update = (
        field(1, field(1, b"T1"))
        + field(2, field(4, b"S1"))
        + field(
            2,
            field(4, b"S2") + field(2, field(2, T0 + 10)) + field(3, field(2, T0 + 10)),
        )
    )
    feed = field(2, field(1, b"T1") + field(3, update))
    path = feed_corpus(tmp_path, [(T0, feed), (T0 + MIN, gtfs_rt_feed({}))])
    with closing(sqlite3.connect(path)) as db:
        runs, _ = train_runs.actual_runs(db, STATIC, store.refresh_ticks(db))
    assert runs["T1"].stops == {"XFER": stop_at(T0 + 10)}


def test_journey_describes_a_transfer_with_both_lines() -> None:
    journey = train_runs.Journey(T0 + 2 * MIN, T0 + 35 * MIN, "F1", "XFER", "C1")
    assert {"17:02:00", "YELLOW", "XFER", "ORANGE", "17:35:00"} <= set(
        journey.describe(RUNS).split()
    )


def test_journey_describes_a_direct_trip_without_a_transfer() -> None:
    journey = train_runs.Journey(T0, T0 + 40 * MIN, "D1", None, None)
    words = journey.describe(RUNS).split()
    assert {"17:00:00", "RED", "17:40:00"} <= set(words)
    assert "XFER" not in words


def test_journey_marks_a_train_of_unknown_line() -> None:
    runs = {"X": train_runs.Run("X", None, None, ["ORIG"])}
    journey = train_runs.Journey(T0, T0 + MIN, "X", None, None)
    assert "?" in journey.describe(runs).split()


def test_journeys_do_not_transfer_at_a_station_the_train_has_no_time_for() -> None:
    through = train_runs.Run(
        "A",
        "RED",
        "s",
        ["ORIG", "XFER", "DEST"],
        {"ORIG": stop_at(T0), "DEST": stop_at(T0 + 40 * MIN)},
    )
    faster = train_runs.Run(
        "C",
        "ORANGE",
        "s",
        ["XFER", "DEST"],
        {"XFER": stop_at(T0 + 10 * MIN), "DEST": stop_at(T0 + 20 * MIN)},
    )
    found = train_runs.journeys({"A": through, "C": faster}, "ORIG", "DEST")
    assert [(j.first, j.via) for j in found] == [("A", None)]


EARLY_DIRECT = train_runs.Journey(T0, T0 + 40, "D1", None, None)
TRANSFER = train_runs.Journey(T0 + 5, T0 + 35, "F1", "XFER", "C1")
FIRST_TIED = train_runs.Journey(T0 + 2, T0 + 35, "D2", None, None)
LATER_TIED = train_runs.Journey(T0 + 4, T0 + 35, "D3", None, None)
ALL_JOURNEYS = [EARLY_DIRECT, TRANSFER, FIRST_TIED, LATER_TIED]


@pytest.mark.parametrize(
    ("departs", "best"),
    [
        # a direct train beats a transfer arriving together; the later of two direct trains wins
        (T0, LATER_TIED),
        # only the transfer leaves this late
        (T0 + 5, TRANSFER),
        (T0 + 6, None),
    ],
    ids=["prefers-direct-then-latest", "only-later-trips", "nothing-left"],
)
def test_best_arrival_picks_the_earliest_arrival_leaving_in_time(
    departs: int, best: train_runs.Journey | None
) -> None:
    assert train_runs.best_arrival(ALL_JOURNEYS, departs) is best


@pytest.mark.parametrize(
    ("line", "to", "nearest", "trip"),
    [
        (None, "DEST", True, "D2"),
        (None, "DEST", False, "D1"),
        ("YELLOW", "XFER", True, "F1"),
        ("BLUE", "DEST", True, None),
    ],
    ids=["nearest-time", "first-to-leave", "by-line", "no-train-of-the-line"],
)
def test_match_run_finds_the_train_in_the_window(
    line: str | None, to: str, nearest: bool, trip: str | None
) -> None:
    matched = train_runs.match_run(
        RUNS, line, "ORIG", to, T0 + 3 * MIN, 5 * MIN, 5 * MIN, nearest
    )
    assert (matched.trip_id if matched else None) == trip


def test_match_run_ignores_a_train_outside_the_window() -> None:
    assert (
        train_runs.match_run(RUNS, "RED", "ORIG", "DEST", T0 + 3 * MIN, 0, 30, True)
        is None
    )
