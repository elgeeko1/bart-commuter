# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Measure how long connecting trains wait at timed transfers, from corpus files.

At each of BART's timed transfers, a meet is a train arriving and a train of another line
leaving in the same direction within MEET_S after it in the timetable, where changing trains takes a
rider somewhere the first train does not go. For every meet that ran, as
GTFS-RT reports it, compares how late the arriving train was with how late the connecting train left,
and whether a rider could make the connection. The table groups meets by how late the arriving train
was past the connection's timetable departure, the wait a connection must make for a rider to make it.

Run with bart-corpus meets.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sqlite3
import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path

from bart_corpus.core import ENGINE, CorpusError, service_day
from bart_corpus.timetable import StaticGtfs, gtfs_origin_s
from bart_corpus.train_runs import Run, actual_runs, connection_outcome, load_recording

# the longest timetable gap between an arriving train and its connection that counts as a meet
MEET_S = 4 * 60
# how late the arriving train was past the timetable departure of the connection, in minutes
BUCKETS = (0, 1, 2, 3, 4, 5)
BUCKET_LABELS = (
    "on time",
    *(f"{low}-{high} min" for low, high in itertools.pairwise(BUCKETS)),
    f"over {BUCKETS[-1]} min",
)


@dataclass(frozen=True)
class Meet:
    """A meet as it ran; times are epoch seconds and lateness is actual minus timetable."""

    station: str
    dir: str
    arriving: str
    arriving_line: str
    connecting: str
    connecting_line: str
    timetable_gap_s: int
    arrived: int
    arrived_late_s: int
    left: int
    left_late_s: int

    @property
    def margin_s(self) -> int:
        return self.left - self.arrived

    @property
    def needed_wait_s(self) -> int:
        """How long past its timetable departure the connection had to wait for the arriving train."""
        return self.arrived_late_s - self.timetable_gap_s


def useful(arriving: Run, connecting: Run, station: str) -> bool:
    """Whether changing trains at a station serves a trip neither train makes alone, without doubling back."""
    i, j = arriving.stations.index(station), connecting.stations.index(station)
    came, went = set(arriving.stations[:i]), set(arriving.stations[i + 1 :])
    joined, goes = set(connecting.stations[:j]), set(connecting.stations[j + 1 :])
    return bool(goes - went) and bool(came - joined) and not goes & came


def meets(
    runs: dict[str, Run],
    static: StaticGtfs,
    origin: int,
    transfers: Sequence[tuple[str, str]],
) -> list[Meet]:
    """Returns the meets at the timed transfers whose arrival and departure both ran."""
    found = []
    for station, direction in transfers:
        here = [
            r
            for r in runs.values()
            if r.dir == direction and station in r.stations and r.line
        ]
        arriving = [
            r
            for r in here
            if r.stations[0] != station and r.reaches(station) is not None
        ]
        leaving = [
            r
            for r in here
            if r.stations[-1] != station and r.leaves(station) is not None
        ]
        for a in arriving:
            a_times = static.trip_times.get(a.trip_id, {}).get(station)
            if a_times is None:
                continue
            for c in leaving:
                c_times = static.trip_times.get(c.trip_id, {}).get(station)
                if (
                    c.line == a.line
                    or c_times is None
                    or not 0 <= c_times[1] - a_times[0] <= MEET_S
                    or not useful(a, c, station)
                ):
                    continue
                arrived, left = a.reaches(station), c.leaves(station)
                assert arrived is not None and left is not None
                found.append(
                    Meet(
                        station,
                        direction,
                        a.trip_id,
                        a.line or "",
                        c.trip_id,
                        c.line or "",
                        c_times[1] - a_times[0],
                        arrived,
                        arrived - (origin + a_times[0]),
                        left,
                        left - (origin + c_times[1]),
                    )
                )
    return found


def bucket(needed_s: int) -> str:
    minutes = needed_s / 60
    for limit, label in zip(BUCKETS, BUCKET_LABELS, strict=False):
        if minutes <= limit:
            return label
    return BUCKET_LABELS[-1]


@dataclass(frozen=True)
class MeetRow:
    """Meets at one station and direction that needed a wait in one bucket."""

    station: str
    direction: str
    bucket: str
    meets: int
    held: int
    tight: int
    missed: int
    median_left_late_s: float
    max_left_late_s: int


def tabulate(found: Sequence[Meet]) -> list[MeetRow]:
    """Groups meets by station and direction, then by the wait the connection needed."""
    groups: dict[tuple[str, str], list[Meet]] = {}
    for m in found:
        groups.setdefault((m.station, m.dir), []).append(m)
    rows = []
    for (station, direction), group in sorted(groups.items()):
        by_bucket: dict[str, list[Meet]] = {}
        for m in group:
            by_bucket.setdefault(bucket(m.needed_wait_s), []).append(m)
        for name in BUCKET_LABELS:
            ms = by_bucket.get(name)
            if not ms:
                continue
            outcomes = Counter(connection_outcome(m.margin_s) for m in ms)
            late = [m.left_late_s for m in ms]
            rows.append(
                MeetRow(
                    station,
                    direction,
                    name,
                    len(ms),
                    outcomes["held"],
                    outcomes["tight"],
                    outcomes["missed"],
                    statistics.median(late),
                    max(late),
                )
            )
    return rows


def summarize(found: Sequence[Meet]) -> str:
    lines = [f"{len(found)} meets"]
    station: tuple[str, str] | None = None
    for row in tabulate(found):
        if (row.station, row.direction) != station:
            station = (row.station, row.direction)
            count = sum(1 for m in found if (m.station, m.dir) == station)
            lines.append(f"\n{row.station} {row.direction}: {count} meets")
            lines.append(
                "  needed wait    meets  held  tight  missed  connection left late, median and max s"
            )
        lines.append(
            f"  {row.bucket:<12} {row.meets:6d} {row.held:5d} {row.tight:6d}"
            f" {row.missed:7d}  {row.median_left_late_s:5.0f} {row.max_left_late_s:5d}"
        )
    return "\n".join(lines)


def merge(found: Iterable[Meet]) -> list[Meet]:
    """Returns each meet once, in order of arrival, as a snapshot and the file of its day can hold the same meet.

    Trip ids recur every service day, so a meet is its trips on its day.
    """
    unique = {
        (service_day(m.arrived * 1000), m.station, m.arriving, m.connecting): m
        for m in found
    }
    return sorted(unique.values(), key=lambda m: m.arrived)


def timed_transfers(source: str) -> tuple[tuple[str, str], ...]:
    """Reads the BART timed transfers, as (station, direction of travel), from the widget script source."""
    match = re.search(
        r"^const TIMED_TRANSFERS = \[(.*?)^\];$", source, re.MULTILINE | re.DOTALL
    )
    if match is None:
        raise CorpusError("TIMED_TRANSFERS not found in the widget script")
    return tuple(re.findall(r'\{ station: "(\w+)", dir: "([ns])" \}', match.group(1)))


def read_meets(path: Path, transfers: Sequence[tuple[str, str]]) -> list[Meet]:
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        ticks, static = load_recording(db, path)
        runs, _ = actual_runs(db, static, ticks)
    return meets(runs, static, gtfs_origin_s(service_day(ticks[0])), transfers)


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("corpus_files", type=Path, nargs="+")
    parser.add_argument("--report", type=Path, help="also write each meet as JSON")


def run(args: argparse.Namespace) -> None:
    timed = timed_transfers(ENGINE.read_text(encoding="utf-8"))
    found: list[Meet] = []
    for path in args.corpus_files:
        if not path.exists():
            raise CorpusError(f"no such corpus file: {path}")
        found += read_meets(path, timed)
    ordered = merge(found)
    print(summarize(ordered))
    if args.report:
        rows = [
            asdict(m) | {"margin_s": m.margin_s, "needed_wait_s": m.needed_wait_s}
            for m in ordered
        ]
        args.report.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
