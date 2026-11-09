# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Decodes the GTFS-RT trip updates feed.

Fields read: FeedMessage.entity (2), FeedEntity.trip_update (3), TripUpdate.trip (1) and
stop_time_update (2), TripDescriptor.trip_id (1), StopTimeUpdate.stop_id (4), arrival (2) and
departure (3), StopTimeEvent.time (2).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from bart_corpus.core import CorpusError

DAY_S = 86400


def varint(data: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[i]
        i += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, i


def fields(data: bytes) -> Iterator[tuple[int, int | bytes]]:
    i = 0
    while i < len(data):
        tag, i = varint(data, i)
        number, wire = tag >> 3, tag & 7
        value: int | bytes
        if wire == 0:
            value, i = varint(data, i)
        elif wire == 2:
            length, i = varint(data, i)
            value = data[i : i + length]
            i += length
        elif wire == 1:
            value = data[i : i + 8]
            i += 8
        elif wire == 5:
            value = data[i : i + 4]
            i += 4
        else:
            raise CorpusError(f"unsupported protobuf wire type {wire}")
        yield number, value


def message(data: int | bytes) -> list[tuple[int, int | bytes]]:
    if not isinstance(data, bytes):
        raise CorpusError("expected a protobuf message")
    return list(fields(data))


def event_time(data: int | bytes) -> int | None:
    for number, value in message(data):
        if number == 2 and isinstance(value, int):
            return value
    return None


@dataclass
class Stop:
    """A train arrival and departure at a station, epoch seconds."""

    arrives: int
    departs: int


@dataclass
class StopUpdate:
    stop_id: str
    arrives: int | None
    departs: int | None

    def times(self) -> Stop | None:
        """Returns the stop times, each standing in for the other when absent, None with neither."""
        arrives = self.arrives if self.arrives is not None else self.departs
        departs = self.departs if self.departs is not None else self.arrives
        return None if arrives is None or departs is None else Stop(arrives, departs)


def trip_updates(feed: bytes) -> dict[str, list[StopUpdate]]:
    """Returns the stop updates of each trip, by trip id."""
    trips: dict[str, list[StopUpdate]] = {}
    for number, entity in fields(feed):
        if number != 2:
            continue
        for e_number, update in message(entity):
            if e_number != 3:
                continue
            trip_id = ""
            stops: list[StopUpdate] = []
            for u_number, value in message(update):
                if u_number == 1:
                    for d_number, d_value in message(value):
                        if d_number == 1 and isinstance(d_value, bytes):
                            trip_id = d_value.decode()
                elif u_number == 2:
                    stop = StopUpdate("", None, None)
                    for s_number, s_value in message(value):
                        if s_number == 4 and isinstance(s_value, bytes):
                            stop.stop_id = s_value.decode()
                        elif s_number == 2:
                            stop.arrives = event_time(s_value)
                        elif s_number == 3:
                            stop.departs = event_time(s_value)
                    # BART dates a departure just after midnight on the previous day when its arrival is
                    # before midnight
                    if (
                        stop.arrives is not None
                        and stop.departs is not None
                        and stop.departs < stop.arrives - DAY_S // 2
                    ):
                        stop.departs += DAY_S
                    stops.append(stop)
            trips[trip_id] = stops
    return trips
