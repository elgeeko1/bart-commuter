# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""What a static GTFS feed says about trips, stations and service days."""

from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

from bart_corpus.core import BART_TZ

WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
SERVICE_ADDED = "1"
SERVICE_REMOVED = "2"
HALF_DAY_S = 12 * 3600


@dataclass(frozen=True)
class ServiceSpan:
    """The first departure and last arrival of a service day, in epoch ms."""

    first_departure_ms: int
    last_arrival_ms: int


def read_gtfs_table(archive: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    with archive.open(name) as f:
        return list(csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")))


def gtfs_origin_s(day: str) -> int:
    """Returns the epoch seconds GTFS times count from on an ISO service day.

    GTFS counts from noon minus 12 hours, which differs from midnight on daylight time changes.
    """
    noon = datetime.fromisoformat(day).replace(hour=12, tzinfo=BART_TZ)
    return round(noon.timestamp()) - HALF_DAY_S


def gtfs_seconds(text: str) -> int:
    """Returns seconds after the origin of a GTFS service day; hours run past 24 after midnight."""
    hours, minutes, seconds = text.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds)


def _active_services(
    calendar: Iterable[dict[str, str]],
    exceptions: Iterable[dict[str, str]],
    day: date,
) -> set[str]:
    compact = day.strftime("%Y%m%d")
    active = {
        r["service_id"]
        for r in calendar
        if r["start_date"] <= compact <= r["end_date"]
        and r[WEEKDAYS[day.weekday()]] == "1"
    }
    for r in exceptions:
        if r["date"] == compact and r["exception_type"] == SERVICE_ADDED:
            active.add(r["service_id"])
        elif r["date"] == compact and r["exception_type"] == SERVICE_REMOVED:
            active.discard(r["service_id"])
    return active


def service_span(feed: bytes, day: str) -> ServiceSpan | None:
    """Returns when the trains of a service day run, None when the feed has none that day."""
    with zipfile.ZipFile(io.BytesIO(feed)) as z:
        active = _active_services(
            read_gtfs_table(z, "calendar.txt"),
            read_gtfs_table(z, "calendar_dates.txt"),
            datetime.fromisoformat(day).date(),
        )
        trips = {
            r["trip_id"]
            for r in read_gtfs_table(z, "trips.txt")
            if r["service_id"] in active
        }
        stops = [
            r for r in read_gtfs_table(z, "stop_times.txt") if r["trip_id"] in trips
        ]
    if not stops:
        return None
    origin_s = gtfs_origin_s(day)
    first_s = min(gtfs_seconds(r["departure_time"]) for r in stops)
    last_s = max(gtfs_seconds(r["arrival_time"]) for r in stops)
    return ServiceSpan((origin_s + first_s) * 1000, (origin_s + last_s) * 1000)


@dataclass(frozen=True)
class StaticGtfs:
    """Static GTFS: stop stations, route lines and directions, and the route and stations of each trip.

    trip_times gives the timetable arrival and departure of each trip at its stations, in seconds after
    gtfs_origin_s of its service day.
    """

    stations: dict[str, str]
    routes: dict[str, tuple[str, str]]
    trip_routes: dict[str, str]
    trip_stations: dict[str, list[str]]
    trip_times: dict[str, dict[str, tuple[int, int]]]


def read_static(zipped: bytes) -> StaticGtfs:
    with zipfile.ZipFile(io.BytesIO(zipped)) as z:
        stations = {
            r["stop_id"]: r["parent_station"] or r["stop_id"]
            for r in read_gtfs_table(z, "stops.txt")
        }
        routes: dict[str, tuple[str, str]] = {}
        for r in read_gtfs_table(z, "routes.txt"):
            # short names read like "Yellow-S"
            color, _, direction = r["route_short_name"].partition("-")
            if direction in ("N", "S"):
                routes[r["route_id"]] = (color.upper(), direction.lower())
        trip_routes = {
            r["trip_id"]: r["route_id"] for r in read_gtfs_table(z, "trips.txt")
        }
        stop_times: dict[str, list[tuple[int, str]]] = defaultdict(list)
        trip_times: dict[str, dict[str, tuple[int, int]]] = defaultdict(dict)
        for r in read_gtfs_table(z, "stop_times.txt"):
            station = stations[r["stop_id"]]
            stop_times[r["trip_id"]].append((int(r["stop_sequence"]), station))
            trip_times[r["trip_id"]][station] = (
                gtfs_seconds(r["arrival_time"]),
                gtfs_seconds(r["departure_time"]),
            )
        trip_stations = {
            t: [station for _, station in sorted(stops)]
            for t, stops in stop_times.items()
        }
    return StaticGtfs(stations, routes, trip_routes, trip_stations, dict(trip_times))
