# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for decoding GTFS-RT trip updates; they make no requests."""

from __future__ import annotations

import pytest

from bart_corpus import core, gtfs_rt
from corpus_support import T0, field, gtfs_rt_feed


def test_trip_updates_reads_stop_times() -> None:
    decoded = gtfs_rt.trip_updates(
        gtfs_rt_feed({"T1": [("A10-1", T0), ("A20-1", T0 + 120)]})
    )
    assert decoded == {
        "T1": [
            gtfs_rt.StopUpdate("A10-1", T0, T0),
            gtfs_rt.StopUpdate("A20-1", T0 + 120, T0 + 120),
        ]
    }


def test_trip_updates_moves_a_departure_dated_a_day_before_its_arrival() -> None:
    arrives = field(2, T0)
    departs = field(2, T0 + 18 - gtfs_rt.DAY_S)
    update = field(1, field(1, b"T1")) + field(
        2, field(4, b"A10-1") + field(2, arrives) + field(3, departs)
    )
    data = field(1, field(1, b"2.0")) + field(2, field(1, b"T1") + field(3, update))
    assert gtfs_rt.trip_updates(data) == {
        "T1": [gtfs_rt.StopUpdate("A10-1", T0, T0 + 18)]
    }


def test_fields_step_over_fixed_width_values() -> None:
    fixed64 = bytes([1 << 3 | 1]) + b"12345678"
    fixed32 = bytes([2 << 3 | 5]) + b"abcd"
    assert list(gtfs_rt.fields(fixed64 + fixed32 + field(3, 7))) == [
        (1, b"12345678"),
        (2, b"abcd"),
        (3, 7),
    ]


def test_an_unsupported_wire_type_is_an_error() -> None:
    with pytest.raises(core.CorpusError):
        list(gtfs_rt.fields(bytes([1 << 3 | 3])))


def test_a_number_where_a_message_belongs_is_an_error() -> None:
    with pytest.raises(core.CorpusError):
        gtfs_rt.trip_updates(field(2, 5))


def test_an_event_without_a_time_is_read_as_absent() -> None:
    delay_only = field(1, 30)
    update = field(1, field(1, b"T1")) + field(
        2, field(4, b"A10-1") + field(2, delay_only) + field(3, field(2, T0))
    )
    (stop,) = gtfs_rt.trip_updates(field(2, field(1, b"T1") + field(3, update)))["T1"]
    assert stop == gtfs_rt.StopUpdate("A10-1", None, T0)
    assert stop.times() == gtfs_rt.Stop(T0, T0)
