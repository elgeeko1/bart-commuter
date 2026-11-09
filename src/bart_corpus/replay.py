# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Replay a corpus file through the widget script and check its trips against the trains that ran.

For each refresh in a corpus file, runs the script on the responses
the widget would have seen, for each commute whose trip plans were recorded both ways. Then compares
the trips each board lists with the trains that ran, as later refreshes of GTFS-RT report them: the
last prediction of a stop before the train leaves it serves as its actual time.

Checks:
- errors: exceptions, harness errors, warnings, and trip plans that fail to load
- shown trips: the arrival a rider taking each trip reaches, against the best arrival of any trip
  leaving no earlier, direct or with one transfer
- missed trips: trips that ran and that no later trip beat, which a board's list omits
- transfers: planned transfers the widget dropped although a rider made the connection, and kept
  although a rider missed it

The report leads with what riders experienced, each trip and connection once, as of the last refresh
before its first train left. Each failure then names its fault: ours when the data the widget had at
that refresh, GTFS-RT where it lists a train and the timetable otherwise, showed the better outcome, with
the widget rule that chose otherwise as the why; a schedule exception when it did not, or when the trip
exists only because trains ran off the timetable; BART data wrong when every source was wrong.

Only trips that ran on lines in GTFS-RT, and that arrive before the corpus file's last refresh, can be
checked. Trips needing two transfers are not considered.

Run with bart-corpus replay.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import statistics
import subprocess
import tempfile
from collections import Counter, defaultdict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field
from itertools import batched
from math import ceil
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

from bart_corpus.core import (
    ENGINE,
    ROOT,
    CorpusError,
    parse_time,
    service_day,
    time_of_day,
)
from bart_corpus.fixtures import load_refresh
from bart_corpus.gtfs_rt import Stop
from bart_corpus.timetable import StaticGtfs, gtfs_origin_s
from bart_corpus.train_runs import (
    TIMING_S,
    Journey,
    Run,
    actual_runs,
    best_arrival,
    connection,
    connection_outcome,
    feed_updates,
    frontier,
    journeys,
    load_recording,
    match_run,
)

DRIVER = "tools/replay/widget.js"
# a shown trip arriving later than the best trip by more than this was the wrong choice
REGRET_S = 60
# a board showing no trains hides any trip leaving within this long
EMPTY_LIST_S = 3600
# a shown train is the train that ran from this long before to this long after its shown departure;
# where a line starts, a waiting train can leave minutes before its live departure
MATCH_EARLY_S = 300
MATCH_LATE_S = 900
# planned legs match trains leaving up to this long early or late, as the widget matches them
LEG_EARLY_S = 60
LEG_LATE_S = 600
# owner of a rider-facing failure
OUR_FAULT = "our fault"
SCHEDULE_EXCEPTION = "schedule exception"
SOURCE_WRONG = "BART data wrong"
FAULTS = (OUR_FAULT, SOURCE_WRONG, SCHEDULE_EXCEPTION)
OFF_TIMETABLE = "only possible because trains ran off schedule"
CHANGED_LATER = "trains changed after the widget's last update"
# a better trip that was already listed below the one a rider took
ON_THE_BOARD = "on the board, later in the list"
# why the widget did not offer a trip, by what it made of the trip
STATUS_WHY = {
    "not in the trip plan": "BART's trip planner did not offer it",
    "direct": "a direct train was left off the board",
    "kept, then beaten by an estimate": "hidden: estimated to arrive after a listed trip",
    ON_THE_BOARD: "listed lower down: estimated to arrive no sooner than the trip above it",
    "no trains": "the board said no trains",
}
# minutes in a warning vary between refreshes
NUMBER = re.compile(r"\d+")

# the trips of a board, each with the train that ran it and when a rider taking it arrived
Realized = list[tuple[dict[str, Any], Run | None, int | None]]


@dataclass(frozen=True)
class Moment:
    """One refresh of one commute as replayed."""

    label: str
    tick: int
    now_ms: int

    @property
    def now_s(self) -> int:
        return self.now_ms // 1000


@dataclass(frozen=True)
class BoardTrips:
    """The trips that ran for one board and arrived early enough to check."""

    options: list[Journey]
    # sorted by departure; only a trip leaving no earlier can beat a trip, so a start time just cuts the list
    fastest: list[Journey]

    def fastest_from(self, now_s: int) -> list[Journey]:
        return [j for j in self.fastest if j.departs >= now_s]


TripKey = tuple[str, str, str | None]
ConnectionKey = tuple[str, int]


class Entry(TypedDict):
    """A trip or board that may have failed a rider; attribution adds the fault and why."""

    at: str
    board: str
    fault: NotRequired[str]
    why: NotRequired[str]


class FastestTripShown(TypedDict):
    shown: Literal[True]
    cause: None


class HiddenTrip(Entry):
    """A fastest trip that a board kept from a rider."""

    shown: Literal[False]
    cause: str
    tick: int
    now: int
    journey: Journey
    described: str
    alternative: int | None
    lost_s: int | None


class ShownTrip(Entry):
    beaten: bool
    transfer: bool
    tick: int
    now: int
    described: str
    better: Journey | None
    better_described: str | None
    lost_s: int | None
    estimate: int
    status: NotRequired[str]


class Connection(Entry):
    shown: bool
    kept: bool
    made: bool
    planned: str
    tick: int
    now: int
    origin: str
    first_trip: str
    connecting_trip: str
    station: str
    first_live: bool
    connecting_live: bool
    drop_reason: str | None
    margin_s: int


class Departure(Entry):
    tick: int
    trip: str
    line: str | None
    origin: str
    shown: int
    left: int


class EmptyBoard(Entry):
    tick: int
    now: int
    journey: Journey
    described: str


class Experience(TypedDict):
    """What riders experienced of one kind, with its failures counted by fault and why."""

    rider: str
    of: str
    total: int
    failed: int
    faults: dict[str, dict[str, int]]


class FailureRow(TypedDict):
    item: str
    at: str
    board: str
    what: str
    fault: str
    why: str
    lost_s: int | None


Suboptimal = TypedDict(
    "Suboptimal",
    {
        "at": str,
        "board": str,
        "shown": str,
        "rode": str,
        "estimated arrival": str | None,
        "better": str,
        "lost_s": int,
    },
)


class MissedTrip(TypedDict):
    at: str
    board: str
    missed: str
    lost_s: int | None
    planned: str


class TransferDetail(TypedDict):
    at: str
    board: str
    planned: str
    ran: str
    margin_s: int
    first_train: str | None
    connecting_train: str | None
    reason: NotRequired[str]
    arrived: NotRequired[str | None]
    lost_s: NotRequired[int | None]
    refreshes: NotRequired[int]


class Report(TypedDict):
    rider: dict[str, Experience]
    failures: list[FailureRow]
    errors: Counter[str]
    warnings: Counter[str]
    shown: Counter[str]
    transfers: Counter[str]
    suboptimal: list[Suboptimal]
    missed: list[MissedTrip]
    false_drops: list[TransferDetail]
    false_keeps: list[TransferDetail]
    records: list[dict[str, Any]]


@dataclass
class Findings:
    records: int = 0
    boards: int = 0
    errors: Counter[str] = field(default_factory=Counter)
    warnings: Counter[str] = field(default_factory=Counter)
    shown: Counter[str] = field(default_factory=Counter)
    suboptimal: list[Suboptimal] = field(default_factory=list)
    missed: list[MissedTrip] = field(default_factory=list)
    transfers: Counter[str] = field(default_factory=Counter)
    false_drops: dict[ConnectionKey, TransferDetail] = field(default_factory=dict)
    false_keeps: dict[ConnectionKey, TransferDetail] = field(default_factory=dict)
    arrival_error_s: dict[str, list[int]] = field(
        default_factory=lambda: defaultdict(list)
    )
    # each trip and planned connection as of the last refresh before its first train left
    best_trips: dict[TripKey, FastestTripShown | HiddenTrip] = field(
        default_factory=dict
    )
    shown_trips: dict[TripKey, ShownTrip] = field(default_factory=dict)
    connections: dict[ConnectionKey, Connection] = field(default_factory=dict)
    # each train on a board as of the last refresh before it left, by board and trip
    departures: dict[tuple[str, str], Departure] = field(default_factory=dict)
    views: Counter[str] = field(default_factory=Counter)
    # boards that said no trains, or had no data, one per board and refresh
    empty_boards: list[EmptyBoard] = field(default_factory=list)
    no_data_boards: list[Entry] = field(default_factory=list)


def drop_reason(t: dict[str, Any], now_ms: int) -> str:
    """Names the rule that dropped a planned transfer, as the widget applies them."""
    first, connecting, legs = t["firstTrain"], t["connectingTrain"], t["legs"]
    if first == "not running":
        return "first train not running"
    if connecting == "not running":
        return "connecting train not running"
    if first is None and legs[0]["departs"] < now_ms:
        return "timetabled to leave before now, with no live first train"
    return "connecting train projected to leave before the first train arrives"


def evaluate(
    record: dict[str, Any],
    runs: dict[str, Run],
    known_until: int,
    trips: dict[str, BoardTrips],
    out: Findings,
) -> None:
    out.records += 1
    if record["thrown"]:
        out.errors[f"thrown: {record['thrown']}"] += 1
    for e in record["harnessErrors"]:
        out.errors[f"harness: {e}"] += 1
    for w in record["warnings"]:
        out.warnings[NUMBER.sub("N", w)] += 1
    if record["boards"] is None:
        out.errors["no capture"] += 1
        return
    moment = Moment(
        f"{time_of_day(record['now'] // 1000)} {record['commute']}",
        record["tick"],
        record["now"],
    )
    for board in record["boards"]:
        evaluate_board(board, moment, runs, known_until, trips[board["board"]], out)


def trips_by_board(
    records: Sequence[dict[str, Any]], runs: dict[str, Run], known_until: int
) -> dict[str, BoardTrips]:
    """Returns the checkable trips of each replayed board, which do not vary between refreshes."""
    trips: dict[str, BoardTrips] = {}
    for board_id in {b["board"] for r in records for b in r["boards"] or []}:
        origin, destination = board_id.split("-")
        options = [
            j for j in journeys(runs, origin, destination) if j.arrives <= known_until
        ]
        trips[board_id] = BoardTrips(options, frontier(options, 0))
    return trips


def leaving_soon(trips: BoardTrips, now_s: int) -> list[Journey]:
    """Returns the trips no later trip beats that leave within EMPTY_LIST_S."""
    return [j for j in trips.fastest_from(now_s) if j.departs <= now_s + EMPTY_LIST_S]


def hidden_trip(
    cause: str,
    moment: Moment,
    board_id: str,
    j: Journey,
    runs: dict[str, Run],
    alternative: int | None,
    lost_s: int | None,
) -> HiddenTrip:
    """Returns the record of a fastest trip that a board kept from a rider."""
    return {
        "shown": False,
        "cause": cause,
        "at": moment.label,
        "board": board_id,
        "tick": moment.tick,
        "now": moment.now_s,
        "journey": j,
        "described": j.describe(runs),
        "alternative": alternative,
        "lost_s": lost_s,
    }


def train_kind(train: dict[str, Any]) -> str:
    return "transfer" if train["transfer"] else "direct"


def shown_train(train: dict[str, Any]) -> str:
    """Describes a train as a board listed it."""
    transfer = train["transfer"]
    via = f" via {transfer['station']}" if transfer else ""
    departs = time_of_day(train["departs"] // 1000)
    return f"{train_kind(train)} {departs} {train['line']}{via}"


def ride(
    train: dict[str, Any],
    origin: str,
    destination: str,
    runs: dict[str, Run],
    lines: set[str | None],
    known_until: int,
    out: Findings,
) -> tuple[Run | None, int | None]:
    """Returns the train that ran a listed trip and when a rider taking it arrived, counting what could be checked."""
    transfer = train["transfer"]
    kind = train_kind(train)
    to = transfer["station"] if transfer else destination
    departs_s = train["departs"] // 1000
    if departs_s + MATCH_LATE_S > known_until:
        out.shown[f"{kind}: not yet run"] += 1
        return None, None
    if train["line"] is not None and train["line"] not in lines:
        out.shown[f"{kind}: line not in GTFS-RT"] += 1
        return None, None
    first = match_run(
        runs,
        train["line"],
        origin,
        to,
        departs_s,
        MATCH_EARLY_S,
        MATCH_LATE_S,
        nearest=True,
    )
    if first is None:
        out.shown[f"{kind}: no train that ran matches"] += 1
        return None, None
    if transfer:
        reaches = first.reaches(to)
        second = (
            connection(runs, to, reaches - TIMING_S, destination, first.trip_id)
            if reaches
            else None
        )
        arrives = second.reaches(destination) if second else None
    else:
        arrives = first.reaches(destination)
    if arrives is None or arrives > known_until:
        out.shown[f"{kind}: arrival not yet known"] += 1
        return first, None
    out.shown[f"{kind}: checked"] += 1
    return first, arrives


def evaluate_board(
    board: dict[str, Any],
    moment: Moment,
    runs: dict[str, Run],
    known_until: int,
    trips: BoardTrips,
    out: Findings,
) -> None:
    out.boards += 1
    board_id = board["board"]
    origin, destination = board_id.split("-")
    now_s = moment.now_s
    if board["planError"]:
        out.errors[f"trip plan: {board['planError']}"] += 1
    options = trips.options
    out.views["total"] += 1
    trains = board["trains"]
    if trains is None:
        out.shown["no data"] += 1
        out.views["no data"] += 1
        out.no_data_boards.append({"at": moment.label, "board": board_id})
        for j in leaving_soon(trips, now_s):
            out.best_trips[board_id, j.first, j.via] = hidden_trip(
                "no data", moment, board_id, j, runs, None, None
            )
        return
    if not trains:
        soon = leaving_soon(trips, now_s)
        if soon:
            out.views["no trains while a trip left within an hour"] += 1
            out.empty_boards.append(
                {
                    "at": moment.label,
                    "board": board_id,
                    "tick": moment.tick,
                    "now": now_s,
                    "journey": soon[0],
                    "described": soon[0].describe(runs),
                }
            )
    lines = {r.line for r in runs.values()}
    realized: Realized = []
    entered: list[TripKey] = []
    for train in trains:
        first, arrives = ride(train, origin, destination, runs, lines, known_until, out)
        realized.append((train, first, arrives))
        if first is None:
            continue
        departs = first.leaves(origin) or 0
        if now_s < departs:
            out.departures[board_id, first.trip_id] = {
                "at": moment.label,
                "board": board_id,
                "tick": moment.tick,
                "trip": first.trip_id,
                "line": train["line"],
                "origin": origin,
                "shown": train["departs"] // 1000,
                "left": departs,
            }
        if arrives is None:
            continue
        transfer = train["transfer"]
        # a board without transfers estimates no arrivals
        if train["arrives"] is not None:
            error = arrives - train["arrives"] // 1000
            out.arrival_error_s[
                train_kind(train) + (" starred" if train["scheduled"] else "")
            ].append(error)
        best = best_arrival(options, departs)
        if now_s < departs:
            key = (board_id, first.trip_id, transfer["station"] if transfer else None)
            out.shown_trips[key] = {
                "beaten": bool(best and arrives - best.arrives > REGRET_S),
                "transfer": transfer is not None,
                "at": moment.label,
                "board": board_id,
                "tick": moment.tick,
                "now": now_s,
                "described": shown_train(train),
                "better": best,
                "better_described": best.describe(runs) if best else None,
                "lost_s": arrives - best.arrives if best else None,
                "estimate": train["arrives"] // 1000 if train["arrives"] else arrives,
            }
            entered.append(key)
        if best and arrives - best.arrives > REGRET_S:
            out.suboptimal.append(
                {
                    "at": moment.label,
                    "board": board_id,
                    "shown": shown_train(train) + (" *" if train["scheduled"] else ""),
                    "rode": f"left {time_of_day(departs)}, arrived {time_of_day(arrives)}",
                    "estimated arrival": time_of_day(train["arrives"] // 1000)
                    if train["arrives"]
                    else None,
                    "better": best.describe(runs),
                    "lost_s": arrives - best.arrives,
                }
            )
    matched = matched_trips(realized)
    for key in entered:
        entry = out.shown_trips[key]
        if entry["beaten"]:
            better = entry["better"]
            assert better is not None
            entry["status"] = (
                ON_THE_BOARD
                if (better.first, better.via) in matched
                else planned_status(board, better, moment.now_ms)
            )
    check_missed(board, realized, trips, moment, runs, out)
    check_transfers(board, realized, runs, known_until, moment, out)


def check_missed(
    board: dict[str, Any],
    realized: Realized,
    trips: BoardTrips,
    moment: Moment,
    runs: dict[str, Run],
    out: Findings,
) -> None:
    """Records trips no later trip beat that a list omits, up to its last trip, or within EMPTY_LIST_S when it shows none."""
    board_id = board["board"]
    origin = board_id.split("-")[0]
    now_s = moment.now_s
    matched = matched_trips(realized)
    shown_departures = [
        d for _, r, _ in realized if r and (d := r.leaves(origin)) is not None
    ]
    last = max(shown_departures) if shown_departures else now_s + EMPTY_LIST_S
    if not board["trains"] or shown_departures:
        for j in trips.fastest_from(now_s):
            if j.departs > last:
                break
            key = (board_id, j.first, j.via)
            if (j.first, j.via) in matched:
                out.best_trips[key] = {"shown": True, "cause": None}
                continue
            # a rider leaving then takes the next shown trip
            alternative = shown_alternative(realized, origin, j.departs)
            lost = alternative - j.arrives if alternative is not None else None
            if lost is not None and lost <= REGRET_S:
                out.best_trips[key] = {"shown": True, "cause": None}
                continue
            status = (
                planned_status(board, j, now_s * 1000)
                if board["trains"]
                else "no trains"
            )
            # what the board told the rider about the next trip it shows
            told = [
                t["arrives"] // 1000 if t["arrives"] else a
                for t, r, a in realized
                if r and a and (r.leaves(origin) or 0) >= j.departs
            ]
            out.best_trips[key] = hidden_trip(
                status, moment, board_id, j, runs, min(told) if told else None, lost
            )
            out.missed.append(
                {
                    "at": moment.label,
                    "board": board_id,
                    "missed": j.describe(runs),
                    "lost_s": lost,
                    "planned": status,
                }
            )


def matched_trips(realized: Realized) -> set[tuple[str, str | None]]:
    """Returns the first train and transfer station of each listed trip that ran."""
    return {
        (r.trip_id, (t["transfer"] or {}).get("station")) for t, r, _ in realized if r
    }


def planned_status(board: dict[str, Any], j: Journey, now_ms: int) -> str:
    """Tells what the widget made, at now_ms, of the planned trip nearest a trip that ran."""
    if j.via is None:
        return "direct"
    near = [
        t
        for t in board["transfers"]
        if abs(t["legs"][0]["departs"] // 1000 - j.departs) <= LEG_LATE_S
    ]
    if not near:
        return "not in the trip plan"
    t = min(near, key=lambda t: abs(t["legs"][0]["departs"] // 1000 - j.departs))
    return (
        "kept, then beaten by an estimate"
        if t["train"]
        else f"hidden: {drop_reason(t, now_ms)}"
    )


def shown_alternative(
    realized: Realized,
    origin: str,
    departs: int,
) -> int | None:
    """Returns the soonest a rider leaving at a time arrives on a trip the board shows."""
    later = [
        a for _, r, a in realized if r and a and (r.leaves(origin) or 0) >= departs
    ]
    return min(later) if later else None


def check_transfers(
    board: dict[str, Any],
    realized: Realized,
    runs: dict[str, Run],
    known_until: int,
    moment: Moment,
    out: Findings,
) -> None:
    """Checks each planned transfer the widget kept or dropped against the connection as it ran."""
    now_ms = moment.now_ms
    origin, destination = board["board"].split("-")
    shown_transfers = {
        (train["departs"], train["transfer"]["station"])
        for train in board["trains"] or []
        if train["transfer"]
    }
    for t in board["transfers"]:
        first_leg, second_leg = t["legs"]
        at = first_leg["to"]
        first = match_run(
            runs,
            first_leg["line"],
            origin,
            at,
            first_leg["departs"] // 1000,
            LEG_EARLY_S,
            LEG_LATE_S,
            nearest=False,
        )
        second = match_run(
            runs,
            second_leg["line"],
            at,
            destination,
            second_leg["departs"] // 1000,
            LEG_EARLY_S,
            LEG_LATE_S,
            nearest=False,
        )
        verdict = "kept" if t["train"] else "dropped"
        kind = "grand meet" if t["grandMeet"] else "transfer"
        if second_leg["arrives"] // 1000 + MATCH_LATE_S > known_until:
            out.transfers[f"{kind} {verdict}: not yet run"] += 1
            continue
        if {first_leg["line"], second_leg["line"]} - {r.line for r in runs.values()}:
            out.transfers[f"{kind} {verdict}: line not in GTFS-RT"] += 1
            continue
        if first is None or second is None:
            out.transfers[f"{kind} {verdict}: no train that ran matches"] += 1
            continue
        reaches, leaves = first.reaches(at), second.leaves(at)
        if reaches is None or leaves is None:
            out.transfers[f"{kind} {verdict}: not yet run"] += 1
            continue
        margin = leaves - reaches
        outcome = connection_outcome(margin)
        made = outcome != "missed"
        out.transfers[f"{kind} {verdict}: connection {outcome}"] += 1
        key = (board["board"], first_leg["departs"])
        detail: TransferDetail = {
            "at": moment.label,
            "board": board["board"],
            "planned": f"{time_of_day(first_leg['departs'] // 1000)} {first_leg['line']} to {at}, "
            f"{time_of_day(second_leg['departs'] // 1000)} {second_leg['line']}",
            "ran": f"first reached {at} {time_of_day(reaches)}, connection left {time_of_day(leaves)}",
            "margin_s": margin,
            "first_train": live_train(t["firstTrain"]),
            "connecting_train": live_train(t["connectingTrain"]),
        }
        if not t["train"] and made:
            detail["reason"] = drop_reason(t, now_ms)
            arrives = second.reaches(destination)
            alternative = shown_alternative(realized, origin, first.leaves(origin) or 0)
            detail["arrived"] = time_of_day(arrives) if arrives else None
            # how much sooner the dropped trip arrived than the best shown trip leaving no earlier
            detail["lost_s"] = (
                alternative - arrives if arrives and alternative else None
            )
            entry = out.false_drops.setdefault(key, detail)
            entry["refreshes"] = entry.get("refreshes", 0) + 1
        if t["train"] and outcome == "missed":
            entry = out.false_keeps.setdefault(key, detail)
            entry["refreshes"] = entry.get("refreshes", 0) + 1
        if now_ms // 1000 < (first.leaves(origin) or 0):
            out.connections[key] = {
                # a board may show a first train at its timetable departure
                "shown": t["train"] is not None
                and bool(
                    {(t["train"]["departs"], at), (first_leg["departs"], at)}
                    & shown_transfers
                ),
                "kept": t["train"] is not None,
                "made": made,
                "at": moment.label,
                "board": board["board"],
                "planned": detail["planned"],
                "tick": moment.tick,
                "now": now_ms // 1000,
                "origin": origin,
                "first_trip": first.trip_id,
                "connecting_trip": second.trip_id,
                "station": at,
                "first_live": isinstance(t["firstTrain"], dict),
                "connecting_live": isinstance(t["connectingTrain"], dict),
                "drop_reason": None if t["train"] else drop_reason(t, now_ms),
                "margin_s": margin,
            }


def live_train(train: dict[str, Any] | str | None) -> str | None:
    """Describes the live train of a leg as the widget matched it."""
    return time_of_day(train["departs"] // 1000) if isinstance(train, dict) else train


@dataclass(frozen=True)
class RefreshData:
    """What the data of one refresh showed of trains: GTFS-RT where it lists a stop, otherwise the timetable."""

    static: StaticGtfs
    # gtfs_origin_s of the service day
    origin_s: int
    feed: dict[str, dict[str, Stop]]

    def timetable(self, trip_id: str, station: str) -> Stop | None:
        times = self.static.trip_times.get(trip_id, {}).get(station)
        return (
            Stop(self.origin_s + times[0], self.origin_s + times[1]) if times else None
        )

    def live(self, trip_id: str, station: str) -> Stop | None:
        return self.feed.get(trip_id, {}).get(station)

    def stop(self, trip_id: str, station: str) -> Stop | None:
        return self.live(trip_id, station) or self.timetable(trip_id, station)


def refresh_data(
    db: sqlite3.Connection, static: StaticGtfs, origin_s: int, tick: int
) -> RefreshData:
    feed: dict[str, dict[str, Stop]] = {}
    for trip_id, stops in feed_updates(db, tick).items():
        for stop in stops:
            times = stop.times()
            if times is not None:
                station = static.stations.get(stop.stop_id, stop.stop_id)
                feed.setdefault(trip_id, {})[station] = times
    return RefreshData(static, origin_s, feed)


def in_timetable(data: RefreshData, j: Journey) -> bool:
    """Whether the timetable makes a trip, so that it does not depend on trains running off it."""
    if j.via is None or j.second is None:
        return True
    reaches, leaves = data.timetable(j.first, j.via), data.timetable(j.second, j.via)
    return (
        reaches is not None and leaves is not None and leaves.departs >= reaches.arrives
    )


def expected_arrival(
    data: RefreshData, j: Journey, board: str, now_s: int
) -> int | None:
    """Returns when the data of a refresh had a rider taking a trip arrive, or None when it did not show the
    trip catchable."""
    origin, destination = board.split("-")
    first = data.stop(j.first, origin)
    if first is None or first.departs < now_s - TIMING_S:
        return None
    if j.via is None or j.second is None:
        end = data.stop(j.first, destination)
        return end.arrives if end else None
    reaches, leaves = data.stop(j.first, j.via), data.stop(j.second, j.via)
    end = data.stop(j.second, destination)
    if (
        reaches is None
        or leaves is None
        or end is None
        or leaves.departs - reaches.arrives < -TIMING_S
    ):
        return None
    return end.arrives


def expected_margin(data: RefreshData, c: Connection) -> int | None:
    """Returns how long after the expected arrival of the first train the connecting train was expected to
    leave, by a refresh's data."""
    first = data.stop(c["first_trip"], c["station"])
    connecting = data.stop(c["connecting_trip"], c["station"])
    return connecting.departs - first.arrives if first and connecting else None


def attribute_trip(
    data: RefreshData, j: Journey, board: str, status: str, told: int | None, now_s: int
) -> tuple[str, str]:
    """Names the fault for a trip a rider did not take: ours when the data of the refresh showed the trip, a
    timetable-supported one, arriving over REGRET_S sooner than what the board told the rider."""
    if status == "no data":
        return SOURCE_WRONG, "no BART data loaded"
    if not in_timetable(data, j):
        return SCHEDULE_EXCEPTION, OFF_TIMETABLE
    arrives = expected_arrival(data, j, board, now_s)
    if arrives is not None and (told is None or arrives + REGRET_S < told):
        return OUR_FAULT, STATUS_WHY.get(status, status)
    return SCHEDULE_EXCEPTION, CHANGED_LATER


def attribute_missed_connection(data: RefreshData, c: Connection) -> tuple[str, str]:
    """Names the fault for a shown connection a rider missed: ours when the data of the refresh showed the miss."""
    margin = expected_margin(data, c)
    if margin is None or margin >= -TIMING_S:
        return SCHEDULE_EXCEPTION, CHANGED_LATER
    if not c["first_live"] and not c["connecting_live"]:
        return OUR_FAULT, "listed with no live time for either train"
    if not c["connecting_live"]:
        return OUR_FAULT, "listed with no live time for the connecting train"
    if not c["first_live"]:
        return OUR_FAULT, "listed with no live time for the first train"
    return OUR_FAULT, "listed: looked possible after rounding to whole minutes"


def attribute_hidden_connection(data: RefreshData, c: Connection) -> tuple[str, str]:
    """Names the fault for a connection a rider would have made that the widget dropped: ours when the
    refresh's data showed the first train still to leave and the connection holding."""
    margin = expected_margin(data, c)
    first = data.stop(c["first_trip"], c["origin"])
    if (
        margin is not None
        and margin >= -TIMING_S
        and first is not None
        and first.departs >= c["now"] - TIMING_S
    ):
        return OUR_FAULT, f"hidden: {c['drop_reason']}"
    return SCHEDULE_EXCEPTION, CHANGED_LATER


def attribute_departure(data: RefreshData, d: Departure) -> tuple[str, str]:
    """Names the fault for a train shown leaving over REGRET_S after it left: ours when other data the
    widget had gave a time that would have caught it."""
    trip_stations = data.static.trip_stations.get(d["trip"], [])
    where = (
        "at the start of its line"
        if trip_stations[:1] == [d["origin"]]
        else "along its line"
    )
    limit = d["left"] + REGRET_S
    timetable = data.timetable(d["trip"], d["origin"])
    if timetable is not None and timetable.departs <= limit:
        return OUR_FAULT, f"time shown was later than the timetable, {where}"
    live = data.live(d["trip"], d["origin"])
    if live is not None and live.departs <= limit:
        return OUR_FAULT, f"time shown was later than BART's live feed, {where}"
    return SOURCE_WRONG, f"all BART data gave a later time, {where}"


def connection_missed(c: Connection) -> bool:
    return c["shown"] and not c["made"]


def connection_hidden(c: Connection) -> bool:
    return c["made"] and not c["kept"]


def train_gone(d: Departure) -> bool:
    return d["shown"] - d["left"] > REGRET_S


def attribute(
    db: sqlite3.Connection, static: StaticGtfs, out: Findings, origin_s: int
) -> None:
    """Names the fault for each rider-facing failure, by the data of the last refresh before it mattered."""
    cache: dict[int, RefreshData] = {}

    def at(tick: int) -> RefreshData:
        if tick not in cache:
            cache[tick] = refresh_data(db, static, origin_s, tick)
        return cache[tick]

    for b in out.best_trips.values():
        if b["shown"] is False:
            b["fault"], b["why"] = attribute_trip(
                at(b["tick"]),
                b["journey"],
                b["board"],
                b["cause"],
                b["alternative"],
                b["now"],
            )
    for t in out.shown_trips.values():
        if t["beaten"]:
            better = t["better"]
            assert better is not None and "status" in t
            t["fault"], t["why"] = attribute_trip(
                at(t["tick"]),
                better,
                t["board"],
                t["status"],
                t["estimate"],
                t["now"],
            )
    for c in out.connections.values():
        if connection_missed(c):
            c["fault"], c["why"] = attribute_missed_connection(at(c["tick"]), c)
        if connection_hidden(c):
            c["fault"], c["why"] = attribute_hidden_connection(at(c["tick"]), c)
    for d in out.departures.values():
        if train_gone(d):
            d["fault"], d["why"] = attribute_departure(at(d["tick"]), d)
    for e in out.empty_boards:
        listed = expected_arrival(at(e["tick"]), e["journey"], e["board"], e["now"])
        e["fault"], e["why"] = (
            (OUR_FAULT, "the widget's data had a trip within the hour")
            if listed is not None
            else (SCHEDULE_EXCEPTION, CHANGED_LATER)
        )
    for n in out.no_data_boards:
        n["fault"], n["why"] = SOURCE_WRONG, "no BART data loaded"


def attribution(f: Entry) -> tuple[str, str]:
    assert "fault" in f and "why" in f
    return f["fault"], f["why"]


def experience(rider: str, of: str, total: int, failed: Sequence[Entry]) -> Experience:
    faults: dict[str, Counter[str]] = defaultdict(Counter)
    for f in failed:
        fault, why = attribution(f)
        faults[fault][why] += 1
    return {
        "rider": rider,
        "of": of,
        "total": total,
        "failed": len(failed),
        "faults": {fault: dict(faults[fault]) for fault in FAULTS if fault in faults},
    }


def rider_view(out: Findings) -> dict[str, Experience]:
    """Reports each rider experience, each trip and connection once as of the last refresh before it left."""
    best = out.best_trips.values()
    shown_trips = out.shown_trips.values()
    connections = out.connections.values()
    on_board = [c for c in connections if c["shown"]]
    made = [c for c in connections if c["made"]]
    views = out.views["total"]
    return {
        "faster_trip_hidden": experience(
            "Rider wasn't shown the fastest trip",
            "fastest trips",
            len(best),
            [b for b in best if b["shown"] is False],
        ),
        "later_arrival": experience(
            "Rider took a listed trip, but one leaving at the same time or later got there over 1 minute sooner",
            "listed trips",
            len(shown_trips),
            [t for t in shown_trips if t["beaten"]],
        ),
        "connection_missed": experience(
            "Rider missed a listed connection",
            "listed connections",
            len(on_board),
            [c for c in connections if connection_missed(c)],
        ),
        "connection_hidden": experience(
            "Rider wasn't shown a connection they could have made",
            "connections riders could make",
            len(made),
            [c for c in connections if connection_hidden(c)],
        ),
        "train_gone": experience(
            "Rider arrived at the time shown and the train had already left",
            "listed trains",
            len(out.departures),
            [d for d in out.departures.values() if train_gone(d)],
        ),
        "no_trains": experience(
            "Board said no trains, but a trip left within the hour",
            "board views",
            views,
            out.empty_boards,
        ),
        "no_data": experience(
            "Board had no data", "board views", views, out.no_data_boards
        ),
    }


def rider_failures(out: Findings) -> list[FailureRow]:
    """Lists every rider-facing failure once, with its fault and why."""
    rows: list[FailureRow] = []

    def add(item: str, at: str, what: str, f: Entry, lost_s: int | None = None) -> None:
        fault, why = attribution(f)
        rows.append(
            {
                "item": item,
                "at": at,
                "board": f["board"],
                "what": what,
                "fault": fault,
                "why": why,
                "lost_s": lost_s,
            }
        )

    for b in out.best_trips.values():
        if b["shown"] is False:
            add("faster_trip_hidden", b["at"], b["described"], b, b["lost_s"])
    for t in out.shown_trips.values():
        if t["beaten"]:
            add(
                "later_arrival",
                t["at"],
                f"{t['described']}; better {t['better_described']}",
                t,
                t["lost_s"],
            )
    for c in out.connections.values():
        if connection_missed(c):
            add(
                "connection_missed",
                c["at"],
                f"{c['planned']}, margin {c['margin_s']} s",
                c,
            )
        if connection_hidden(c):
            add(
                "connection_hidden",
                c["at"],
                f"{c['planned']}, margin {c['margin_s']} s",
                c,
            )
    for d in out.departures.values():
        if train_gone(d):
            add(
                "train_gone",
                d["at"],
                f"{d['line']} shown {time_of_day(d['shown'])}, left {time_of_day(d['left'])}",
                d,
            )
    for e in out.empty_boards:
        add("no_trains", e["at"], e["described"], e)
    for n in out.no_data_boards:
        add("no_data", n["at"], "", n)
    return rows


def share(n: int, of: int) -> str:
    return f"{n} of {of} ({n / of:.1%})" if of else f"{n} of 0"


def summarize_rider_view(view: dict[str, Experience]) -> list[str]:
    lines = [
        "rider experience, each trip and connection as of the last refresh before its first train left:"
    ]
    for item in view.values():
        lines.append(
            f"  {item['rider']}: {share(item['failed'], item['total'])} {item['of']}"
        )
        for fault, whys in item["faults"].items():
            lines.append(f"    {fault}: {share(sum(whys.values()), item['total'])}")
            lines.extend(
                f"      {why}: {share(n, item['total'])}"
                for why, n in sorted(whys.items(), key=lambda w: -w[1])
            )
    return lines


def drop_first_trips(response: str, count: int) -> str:
    """Drops the first count trips of a trip plan."""
    plan = json.loads(response)
    # BART reports a planner failure as a 200 without a schedule
    if "schedule" not in plan["root"]:
        return response
    request = plan["root"]["schedule"]["request"]
    trips = request["trip"] if isinstance(request["trip"], list) else [request["trip"]]
    request["trip"] = trips[count:]
    return json.dumps(plan)


def write_refreshes(
    db: sqlite3.Connection,
    ticks: Sequence[int],
    path: Path,
    drop_first_trips_count: int,
) -> None:
    """Writes each refresh as a JSON line for the driver, each trip plan without its first
    drop_first_trips_count trips."""
    with path.open("w", encoding="utf-8") as f:
        for tick in ticks:
            loaded = load_refresh(db, tick, ("etd", "depart", "tripupdate"))
            etd, depart = {}, {}
            for name, data in loaded.files.items():
                if name.startswith("etd-"):
                    etd[name[4:-5]] = data.decode()
                elif name.startswith("depart-"):
                    depart[name[7:-5]] = data.decode()
            if drop_first_trips_count:
                depart = {
                    pair: drop_first_trips(text, drop_first_trips_count)
                    for pair, text in depart.items()
                }
            feed = loaded.files.get("tripupdate.pb")
            pairs = {tuple(p.split("-")) for p in depart}
            commutes = sorted(
                {" ".join(sorted(p)) for p in pairs if (p[1], p[0]) in pairs}
            )
            line = {
                "tick": tick,
                "now": loaded.snapshot["now"],
                "commutes": commutes,
                "etd": etd,
                "depart": depart,
                "feed": feed.hex() if feed is not None else None,
            }
            f.write(json.dumps(line) + "\n")


def run_driver(path: Path, script: Path) -> list[dict[str, Any]]:
    done = subprocess.run(
        ["bun", DRIVER, str(path), str(script.resolve())],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise CorpusError(f"driver failed: {done.stderr or done.stdout}")
    records = [
        json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")
    ]
    fatal = [r["fatal"] for r in records if "fatal" in r]
    if fatal:
        raise CorpusError(f"driver failed: {fatal[0]}")
    return records


def replay_refreshes(
    db: sqlite3.Connection,
    ticks: Sequence[int],
    script: Path,
    drop_first_trips_count: int,
) -> list[dict[str, Any]]:
    """Runs the script on each refresh, one driver per CPU, and returns the records in refresh order."""
    workers = os.cpu_count() or 1
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for n, shard in enumerate(batched(ticks, ceil(len(ticks) / workers) or 1)):
            paths.append(Path(tmp) / f"refreshes-{n}.jsonl")
            write_refreshes(db, shard, paths[-1], drop_first_trips_count)
        with ThreadPoolExecutor(workers) as pool:
            shards = pool.map(lambda path: run_driver(path, script), paths)
            return [record for shard in shards for record in shard]


def summarize(out: Findings) -> str:
    lines = [f"{out.records} runs, {out.boards} boards", ""]
    lines += summarize_rider_view(rider_view(out))

    def section(title: str, counter: Counter[str]) -> None:
        lines.append(f"\n{title}:" + ("" if counter else " none"))
        lines.extend(f"  {n:5d}  {k}" for k, n in sorted(counter.items()))

    section("errors", out.errors)
    section("warnings", out.warnings)
    section("shown trips", out.shown)
    lines.append("\narrival estimate error, actual minus estimated, seconds:")
    for kind, errors in sorted(out.arrival_error_s.items()):
        q = statistics.quantiles(errors, n=10) if len(errors) > 1 else errors * 9
        lines.append(
            f"  {kind}: n={len(errors)} median={statistics.median(errors):.0f} p10={q[0]:.0f} p90={q[-1]:.0f} max={max(errors)}"
        )
    section("planned transfers by refresh", out.transfers)
    failures = rider_failures(out)
    lines.append(f"\nrider-facing failures: {len(failures)}")
    lines.extend("  " + json.dumps(f) for f in failures)
    return "\n".join(lines)


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("corpus_file", type=Path)
    parser.add_argument("--report", type=Path, help="also write the findings as JSON")
    parser.add_argument(
        "--since", type=parse_time, help="first refresh to replay, ISO 8601, BART time"
    )
    parser.add_argument(
        "--until", type=parse_time, help="last refresh to replay, ISO 8601, BART time"
    )
    parser.add_argument(
        "--script",
        type=Path,
        default=ENGINE,
        help="widget script to run, by default bart-commuter.js",
    )
    parser.add_argument(
        "--drop-first-trips",
        type=int,
        default=0,
        help="trips to drop from the start of each recorded trip plan, for a script that requests that "
        "many fewer trips timetabled before now than the recording did, the planner's b parameter",
    )


def run(args: argparse.Namespace) -> None:
    if not args.corpus_file.exists():
        raise CorpusError(f"no such corpus file: {args.corpus_file}")
    if not args.script.exists():
        raise CorpusError(f"no such script: {args.script}")
    with closing(sqlite3.connect(f"file:{args.corpus_file}?mode=ro", uri=True)) as db:
        ticks, static = load_recording(db, args.corpus_file)
        runs, known_until = actual_runs(db, static, ticks)
        replayed = [
            t
            for t in ticks
            if (args.since is None or t >= args.since)
            and (args.until is None or t <= args.until)
        ]
        records = replay_refreshes(db, replayed, args.script, args.drop_first_trips)
        trips = trips_by_board(records, runs, known_until)
        out = Findings()
        for record in records:
            evaluate(record, runs, known_until, trips, out)
        attribute(db, static, out, gtfs_origin_s(service_day(ticks[0])))
    print(
        f"replayed {len(replayed)} refreshes; trains known until {time_of_day(known_until)}"
    )
    print(summarize(out))
    if args.report:
        report: Report = {
            "rider": rider_view(out),
            "failures": rider_failures(out),
            "errors": out.errors,
            "warnings": out.warnings,
            "shown": out.shown,
            "transfers": out.transfers,
            "suboptimal": out.suboptimal,
            "missed": out.missed,
            "false_drops": list(out.false_drops.values()),
            "false_keeps": list(out.false_keeps.values()),
            "records": records,
        }
        args.report.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
