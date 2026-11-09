# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Builders shared by the corpus tests."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Sequence
from pathlib import Path

from bart_corpus import core, gtfs_rt, store, timetable, train_runs


def ms(text: str) -> int:
    return int(core.parse_time(text))


def etd_all(*abbrs: str) -> bytes:
    stations = [
        {"name": abbr, "abbr": abbr, "etd": [{"abbreviation": "X", "estimate": []}]}
        for abbr in abbrs
    ]
    root = {
        "date": "11/06/2026",
        "station": stations,
        "message": "Direction not supported for ALL ETD messages.",
    }
    return json.dumps({"?xml": {"@version": "1.0"}, "root": root}).encode()


def plan(*trips: list[tuple[str, str]]) -> bytes:
    """A trip planner response; each trip is a list of legs, (origin, destination)."""
    legs = [[{"@origin": o, "@destination": d} for o, d in trip] for trip in trips]
    request = {"trip": [{"leg": leg} for leg in legs]}
    return json.dumps({"root": {"schedule": {"request": request}}}).encode()


def gtfs_feed(
    first_departure: str,
    last_arrival: str,
    days: str = "1111111",
    exceptions: Sequence[tuple[str, str]] = (),
) -> bytes:
    """A static GTFS feed of one trip; days flags Monday to Sunday, exceptions are (ISO date, type)."""
    weekdays = "monday,tuesday,wednesday,thursday,friday,saturday,sunday"
    dates = "".join(f"S,{d.replace('-', '')},{kind}\n" for d, kind in exceptions)
    files = {
        "calendar.txt": f"service_id,{weekdays},start_date,end_date\nS,{','.join(days)},20260101,20271231\n",
        "calendar_dates.txt": f"service_id,date,exception_type\n{dates}",
        "trips.txt": "trip_id,service_id\nT,S\n",
        "stop_times.txt": (
            "trip_id,arrival_time,departure_time\n"
            f"T,{first_departure},{first_departure}\nT,{last_arrival},{last_arrival}\n"
        ),
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return out.getvalue()


def poll(
    source: str,
    tick: int,
    body: bytes | None = b"x",
    subject: str = "",
    status: int | None = 200,
    error: str | None = None,
) -> store.Poll:
    return store.Poll(source, subject, tick, tick + 5, 5, status, error, body)


MIN = 60


T0 = int(core.parse_time("2026-11-06T17:00:00")) // 1000


def stop_at(t: int) -> gtfs_rt.Stop:
    return gtfs_rt.Stop(t, t)


def varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def field(number: int, value: int | bytes) -> bytes:
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    return varint(number << 3 | 2) + varint(len(value)) + value


def gtfs_rt_feed(trips: dict[str, list[tuple[str, int]]]) -> bytes:
    """A GTFS-RT feed of trips, each a list of (stop id, time) for both arrival and departure."""
    entities = b""
    for trip_id, stops in trips.items():
        update = field(1, field(1, trip_id.encode()))
        for stop_id, t in stops:
            event = field(2, t)
            update += field(
                2, field(4, stop_id.encode()) + field(2, event) + field(3, event)
            )
        entities += field(2, field(1, trip_id.encode()) + field(3, update))
    return field(1, field(1, b"2.0")) + entities


STATIC = timetable.StaticGtfs(
    stations={"S1": "ORIG", "S2": "XFER", "S3": "DEST"},
    routes={"1": ("YELLOW", "s"), "2": ("ORANGE", "s")},
    trip_routes={"T1": "1", "T2": "2"},
    trip_stations={"T1": ["ORIG", "XFER"], "T2": ["XFER", "DEST"]},
    trip_times={},
)


def feed_corpus(tmp_path: Path, feeds: list[tuple[int, bytes]]) -> Path:
    corpus_store = store.Store(tmp_path)
    for tick_s, data in feeds:
        corpus_store.write(
            [
                store.Poll(
                    "tripupdate", "", tick_s * 1000, tick_s * 1000, 5, 200, None, data
                )
            ]
        )
    corpus_store.close()
    return next(tmp_path.glob("*.sqlite"))


def train_run(trip_id: str, line: str, stops: dict[str, int]) -> train_runs.Run:
    return train_runs.Run(
        trip_id,
        line,
        "s",
        list(stops),
        {s: stop_at(t) for s, t in stops.items()},
    )


RUNS = {
    r.trip_id: r
    for r in [
        # direct, slow
        train_run(
            "D1", "RED", {"ORIG": T0, "XFER": T0 + 5 * MIN, "DEST": T0 + 40 * MIN}
        ),
        # first leg, connecting with C1 after 2 minutes, arriving at 35
        train_run("F1", "YELLOW", {"ORIG": T0 + 2 * MIN, "XFER": T0 + 6 * MIN}),
        train_run("C1", "ORANGE", {"XFER": T0 + 8 * MIN, "DEST": T0 + 35 * MIN}),
        # a later direct train arriving as soon as the transfer
        train_run(
            "D2",
            "RED",
            {"ORIG": T0 + 4 * MIN, "XFER": T0 + 9 * MIN, "DEST": T0 + 35 * MIN},
        ),
        # a connection leaving 45 seconds before F2 arrives
        train_run("F2", "YELLOW", {"ORIG": T0 + 20 * MIN, "XFER": T0 + 24 * MIN}),
        train_run("C2", "ORANGE", {"XFER": T0 + 24 * MIN - 45, "DEST": T0 + 50 * MIN}),
    ]
}
