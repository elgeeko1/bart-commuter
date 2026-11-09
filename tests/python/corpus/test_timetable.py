# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for reading service days from a static GTFS feed."""

from __future__ import annotations

import io
import zipfile

from bart_corpus.timetable import (
    ServiceSpan,
    gtfs_origin_s,
    read_static,
    service_span,
)
from corpus_support import gtfs_feed, ms

FRIDAY = "2026-11-06"
SATURDAY = "2026-11-07"


def test_span_runs_from_first_departure_to_last_arrival_past_midnight() -> None:
    assert service_span(gtfs_feed("05:00:00", "26:30:00"), FRIDAY) == ServiceSpan(
        ms("2026-11-06T05:00:00"), ms("2026-11-07T02:30:00")
    )


def test_a_service_runs_only_on_its_calendar_weekdays() -> None:
    assert (
        service_span(gtfs_feed("05:00:00", "25:00:00", days="1111100"), SATURDAY)
        is None
    )


def test_a_removal_exception_cancels_the_day() -> None:
    feed = gtfs_feed("05:00:00", "25:00:00", exceptions=[(FRIDAY, "2")])
    assert service_span(feed, FRIDAY) is None


def test_an_added_exception_runs_the_service_outside_its_weekdays() -> None:
    feed = gtfs_feed(
        "05:00:00", "25:00:00", days="1111100", exceptions=[(SATURDAY, "1")]
    )
    assert service_span(feed, SATURDAY) is not None


def test_times_count_from_noon_minus_twelve_hours_on_a_daylight_change() -> None:
    span = service_span(gtfs_feed("05:00:00", "25:00:00"), "2026-11-01")
    assert span is not None
    # counting from wall-clock midnight would give 04:00
    assert span.first_departure_ms == ms("2026-11-01T05:00:00")


def test_gtfs_origin_is_midnight_on_an_ordinary_day() -> None:
    midnight = ms("2026-11-06T00:00:00") // 1000
    assert gtfs_origin_s(FRIDAY) == midnight


def static_feed() -> bytes:
    files = {
        "stops.txt": "stop_id,parent_station\nA10-1,ORIG\nA10-2,ORIG\nDEST,\nXFER,\n",
        "routes.txt": (
            "route_id,route_short_name\n1,Yellow-S\n2,Orange-N\n3,Beige\n4,Blue-E\n"
        ),
        "trips.txt": "trip_id,route_id\nT1,1\nT2,2\n",
        # listed out of order, and the last stop runs past midnight
        "stop_times.txt": (
            "trip_id,stop_id,arrival_time,departure_time,stop_sequence\n"
            "T1,DEST,25:10:00,25:10:30,3\n"
            "T1,A10-1,24:50:00,24:50:20,1\n"
            "T1,XFER,25:00:00,25:00:30,2\n"
            "T2,A10-2,08:00:00,08:00:00,1\n"
        ),
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return out.getvalue()


def test_read_static_maps_stops_to_their_stations() -> None:
    assert read_static(static_feed()).stations == {
        "A10-1": "ORIG",
        "A10-2": "ORIG",
        "DEST": "DEST",
        "XFER": "XFER",
    }


def test_read_static_keeps_routes_with_a_north_or_south_direction() -> None:
    assert read_static(static_feed()).routes == {
        "1": ("YELLOW", "s"),
        "2": ("ORANGE", "n"),
    }


def test_read_static_orders_the_stations_of_a_trip_by_stop_sequence() -> None:
    static = read_static(static_feed())
    assert static.trip_routes == {"T1": "1", "T2": "2"}
    assert static.trip_stations == {
        "T1": ["ORIG", "XFER", "DEST"],
        "T2": ["ORIG"],
    }


def test_read_static_gives_arrival_and_departure_after_the_origin() -> None:
    times = read_static(static_feed()).trip_times
    assert times["T1"]["DEST"] == (25 * 3600 + 600, 25 * 3600 + 630)
    assert times["T1"]["ORIG"] == (24 * 3600 + 3000, 24 * 3600 + 3020)
    assert times["T2"] == {"ORIG": (8 * 3600, 8 * 3600)}
