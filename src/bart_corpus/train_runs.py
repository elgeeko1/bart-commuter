# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""The trains that ran, as later refreshes of GTFS-RT report them, and the trips they made possible."""

from __future__ import annotations

import bisect
import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from bart_corpus.core import HTTP_OK, CorpusError, time_of_day
from bart_corpus.fixtures import load_refresh
from bart_corpus.gtfs_rt import Stop, StopUpdate, trip_updates
from bart_corpus.store import read_body, refresh_ticks
from bart_corpus.timetable import StaticGtfs, read_static

# recorded times are this coarse, so a rider made a connection whose train left up to this long before
# the recorded arrival of the first train
TIMING_S = 30
# the last prediction of a stop is its actual time when the train left it, or when the trip ended within this
# long of the prediction
FINAL_S = 90


@dataclass
class Run:
    """A trip as it ran: its line and direction, its stations in order, and the actual times known."""

    trip_id: str
    line: str | None
    dir: str | None
    stations: list[str]
    stops: dict[str, Stop] = field(default_factory=dict)

    def leaves(self, station: str) -> int | None:
        stop = self.stops.get(station)
        return stop.departs if stop else None

    def reaches(self, station: str) -> int | None:
        stop = self.stops.get(station)
        return stop.arrives if stop else None

    def serves(self, origin: str, destination: str) -> bool:
        if origin not in self.stations or destination not in self.stations:
            return False
        return self.stations.index(origin) < self.stations.index(destination)


def feed_updates(db: sqlite3.Connection, tick: int) -> dict[str, list[StopUpdate]]:
    """Returns the trip updates of the feed recorded at a tick, none when that request failed."""
    row = db.execute(
        "SELECT sha256 FROM poll WHERE source = 'tripupdate' AND tick = ? AND status = ? AND error IS NULL",
        (tick, HTTP_OK),
    ).fetchone()
    return {} if row is None else trip_updates(read_body(db, row[0]))


def load_recording(db: sqlite3.Connection, path: Path) -> tuple[list[int], StaticGtfs]:
    """Returns the refresh ticks of a corpus file and the static GTFS feed as of its last refresh."""
    ticks = refresh_ticks(db)
    if not ticks:
        raise CorpusError(f"no refresh in {path}")
    feed = load_refresh(db, ticks[-1], ("gtfs-static",)).files.get("google_transit.zip")
    if feed is None:
        raise CorpusError(f"no static GTFS in {path}")
    return ticks, read_static(feed)


def actual_runs(
    db: sqlite3.Connection, static: StaticGtfs, ticks: Sequence[int]
) -> tuple[dict[str, Run], int]:
    """Returns the trips that ran, with their final stops only, and the time up to which they are known.

    A stop is final once a later refresh lists its trip without it, or when its trip leaves the feed
    within FINAL_S of the stop. A final stop's time is its last prediction, or just before the first
    refresh that lists its trip without it when that is earlier, since the feed can stop updating a train
    still waiting where its line starts. A trip's stations come from the timetable, or for a trip it
    lacks, from the feed.
    """
    last: dict[tuple[str, str], tuple[int, StopUpdate]] = {}
    seen: dict[str, int] = {}
    # refreshes of each trip, in order
    trip_ticks: dict[str, list[int]] = defaultdict(list)
    listed: dict[str, dict[str, int]] = defaultdict(dict)
    for tick in ticks:
        for trip_id, stops in feed_updates(db, tick).items():
            seen[trip_id] = tick
            trip_ticks[trip_id].append(tick)
            for stop in stops:
                station = static.stations.get(stop.stop_id, stop.stop_id)
                last[trip_id, station] = (tick, stop)
                listed[trip_id][station] = stop.arrives or stop.departs or 0
    end = ticks[-1]
    runs: dict[str, Run] = {}
    for (trip_id, station), (tick, stop) in last.items():
        times = stop.times()
        if times is None:
            continue
        arrives, departs = times.arrives, times.departs
        passed = tick < seen[trip_id]
        ended = seen[trip_id] < end and departs * 1000 <= seen[trip_id] + FINAL_S * 1000
        if not (passed or ended):
            continue
        if passed:
            ticks_of_trip = trip_ticks[trip_id]
            gone = ticks_of_trip[bisect.bisect_right(ticks_of_trip, tick)] // 1000
            departs = min(departs, gone - 1)
            arrives = min(arrives, departs)
        if trip_id not in runs:
            route = static.routes.get(static.trip_routes.get(trip_id, ""))
            stations = static.trip_stations.get(trip_id) or sorted(
                listed[trip_id], key=listed[trip_id].__getitem__
            )
            runs[trip_id] = Run(
                trip_id,
                route[0] if route else None,
                route[1] if route else None,
                stations,
            )
        runs[trip_id].stops[station] = Stop(arrives, departs)
    # an event before the last refresh by more than a poll is final for every trip in the feed
    known_until = end // 1000 - FINAL_S
    return runs, known_until


@dataclass(frozen=True)
class Journey:
    """A trip that ran from an origin to a destination, with at most one transfer."""

    departs: int
    arrives: int
    first: str
    via: str | None
    second: str | None

    def describe(self, runs: dict[str, Run]) -> str:
        first = runs[self.first]
        text = f"{time_of_day(self.departs)} {first.line or '?'}"
        if self.via and self.second:
            text += f" via {self.via} {runs[self.second].line or '?'}"
        return f"{text} arriving {time_of_day(self.arrives)}"


def connection(
    runs: dict[str, Run], at: str, ready: int, destination: str, exclude: str
) -> Run | None:
    """Returns the first train from a station to a destination leaving once a rider is ready.

    Trains do not overtake, so the first to leave arrives first.
    """
    best: Run | None = None
    for run in runs.values():
        leaves = run.leaves(at)
        if (
            run.trip_id == exclude
            or leaves is None
            or leaves < ready
            or not run.serves(at, destination)
        ):
            continue
        if best is None or leaves < (best.leaves(at) or 0):
            best = run
    return best


def journeys(runs: dict[str, Run], origin: str, destination: str) -> list[Journey]:
    """Returns every direct trip, and every trip with one transfer that beats the arrival of its own first train.

    Trains meeting at several stations make one trip, through the first of them.
    """
    found: list[Journey] = []
    pairs: set[tuple[str, str]] = set()
    for run in runs.values():
        departs = run.leaves(origin)
        if departs is None:
            continue
        direct = run.reaches(destination) if run.serves(origin, destination) else None
        if direct is not None:
            found.append(Journey(departs, direct, run.trip_id, None, None))
        for at in run.stations[run.stations.index(origin) + 1 :]:
            if at == destination:
                break
            reaches = run.reaches(at)
            if reaches is None:
                continue
            second = connection(runs, at, reaches - TIMING_S, destination, run.trip_id)
            arrives = second.reaches(destination) if second else None
            if (
                second is None
                or arrives is None
                or (run.trip_id, second.trip_id) in pairs
            ):
                continue
            if direct is None or arrives < direct:
                pairs.add((run.trip_id, second.trip_id))
                found.append(Journey(departs, arrives, run.trip_id, at, second.trip_id))
    return found


def best_arrival(options: Sequence[Journey], departs: int) -> Journey | None:
    later = [j for j in options if j.departs >= departs]
    return (
        min(later, key=lambda j: (j.arrives, j.via is not None, -j.departs))
        if later
        else None
    )


def frontier(options: Sequence[Journey], since: int) -> list[Journey]:
    """Returns the trips leaving at or after a time that no trip leaving no earlier beats."""
    later = [j for j in options if j.departs >= since]
    kept = []
    for j in later:
        beaten = any(
            k is not j
            and k.departs >= j.departs
            and (
                k.arrives < j.arrives
                or (
                    k.arrives == j.arrives
                    and (k.departs > j.departs or (k.via is None and j.via is not None))
                )
            )
            for k in later
        )
        if not beaten:
            kept.append(j)
    return sorted(kept, key=lambda j: j.departs)


def match_run(
    runs: dict[str, Run],
    line: str | None,
    origin: str,
    to: str,
    departs_s: int,
    early_s: int,
    late_s: int,
    nearest: bool,
) -> Run | None:
    """Returns the train of a line from an origin to a station leaving within a window of a time."""
    candidates = [
        r
        for r in runs.values()
        if (line is None or r.line == line)
        and r.serves(origin, to)
        and departs_s - early_s <= (r.leaves(origin) or 0) <= departs_s + late_s
    ]
    if not candidates:
        return None
    if nearest:
        return min(candidates, key=lambda r: abs((r.leaves(origin) or 0) - departs_s))
    return min(candidates, key=lambda r: r.leaves(origin) or 0)


def connection_outcome(margin_s: int) -> str:
    """Names a connection by its margin, the departure of the connecting train less the arrival of the first train.

    held when the connecting train left no earlier than the arrival, tight when it left up to TIMING_S
    before, which the recorded times cannot tell from held, and otherwise missed.
    """
    if margin_s >= 0:
        return "held"
    return "tight" if margin_s >= -TIMING_S else "missed"
