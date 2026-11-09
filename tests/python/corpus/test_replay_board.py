# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for scoring replayed boards against the trains that ran; they make no requests and run no script."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from bart_corpus import replay as rp
from bart_corpus.gtfs_rt import Stop
from bart_corpus.train_runs import Journey, Run, frontier, journeys
from corpus_support import MIN, T0, stop_at, train_run

BOARD = "ORIG-DEST"
LABEL = "17:00:00 ORIG DEST"


def minute(n: float) -> int:
    return int(T0 + n * MIN)


def at_ms(n: float) -> int:
    return minute(n) * 1000


KNOWN_UNTIL = minute(120)

FIRST = train_run("FIRST", "YELLOW", {"ORIG": minute(5), "XFER": minute(10)})
LINK = train_run("LINK", "ORANGE", {"XFER": minute(12), "DEST": minute(30)})
SLOW = train_run("SLOW", "RED", {"ORIG": minute(10), "DEST": minute(40)})
LAST = train_run("LAST", "RED", {"ORIG": minute(20), "DEST": minute(50)})
# leaves after SLOW and arrives before it
EXPRESS = train_run("EXPRESS", "RED", {"ORIG": minute(12), "DEST": minute(25)})

# the transfer leaves first and arrives first, and each later train arrives later
NETWORK = {r.trip_id: r for r in (FIRST, LINK, SLOW, LAST)}
RACE = {r.trip_id: r for r in (SLOW, EXPRESS)}

TRANSFER_TRIP = Journey(minute(5), minute(30), "FIRST", "XFER", "LINK")
SLOW_TRIP = Journey(minute(10), minute(40), "SLOW", None, None)
LAST_TRIP = Journey(minute(20), minute(50), "LAST", None, None)


def board_trips(runs: dict[str, Run], known_until: int = KNOWN_UNTIL) -> rp.BoardTrips:
    options = [j for j in journeys(runs, "ORIG", "DEST") if j.arrives <= known_until]
    return rp.BoardTrips(options, frontier(options, 0))


def moment(n: float = 0) -> rp.Moment:
    return rp.Moment(LABEL, at_ms(n), at_ms(n))


def listed(
    departs: float,
    arrives: float | None = None,
    line: str | None = "RED",
    station: str | None = None,
    scheduled: bool = False,
) -> dict[str, Any]:
    return {
        "departs": at_ms(departs),
        "arrives": None if arrives is None else at_ms(arrives),
        "line": line,
        "transfer": {"station": station} if station else None,
        "scheduled": scheduled,
    }


def board(
    trains: list[dict[str, Any]] | None,
    transfers: Sequence[dict[str, Any]] = (),
    plan_error: str | None = None,
) -> dict[str, Any]:
    return {
        "board": BOARD,
        "trains": trains,
        "transfers": list(transfers),
        "planError": plan_error,
    }


def planned(
    first_min: float = 5,
    second_min: float = 12,
    arrives_min: float = 30,
    kept_at: float | None = None,
    first_train: dict[str, Any] | str | None = None,
    connecting_train: dict[str, Any] | str | None = None,
    grand_meet: bool = False,
    first_line: str = "YELLOW",
    second_line: str = "ORANGE",
) -> dict[str, Any]:
    """A trip plan's transfer; kept_at is when the widget kept it to leave, None when it dropped it."""
    legs = [
        {
            "line": first_line,
            "from": "ORIG",
            "to": "XFER",
            "departs": at_ms(first_min),
            "arrives": at_ms(first_min + 4),
        },
        {
            "line": second_line,
            "from": "XFER",
            "to": "DEST",
            "departs": at_ms(second_min),
            "arrives": at_ms(arrives_min),
        },
    ]
    return {
        "legs": legs,
        "firstTrain": first_train,
        "connectingTrain": connecting_train,
        "train": None
        if kept_at is None
        else {"departs": at_ms(kept_at), "scheduled": False},
        "grandMeet": grand_meet,
    }


def score(
    b: dict[str, Any],
    runs: dict[str, Run],
    now: float = 0,
    known_until: int = KNOWN_UNTIL,
) -> rp.Findings:
    out = rp.Findings()
    rp.evaluate_board(b, moment(now), runs, known_until, board_trips(runs), out)
    return out


def test_trips_by_board_keeps_the_trips_arriving_early_enough_to_check() -> None:
    records: list[dict[str, Any]] = [
        {"boards": None},
        {"boards": [{"board": BOARD}, {"board": "DEST-ORIG"}]},
    ]
    trips = rp.trips_by_board(records, NETWORK, minute(45))
    assert set(trips) == {BOARD, "DEST-ORIG"}
    assert [j.first for j in trips[BOARD].options] == ["FIRST", "SLOW"]
    assert [j.first for j in trips[BOARD].fastest] == ["FIRST", "SLOW"]
    assert trips["DEST-ORIG"].options == []


def test_a_fastest_trip_excludes_one_a_later_train_beats() -> None:
    assert [j.first for j in board_trips(RACE).fastest] == ["EXPRESS"]


def test_fastest_from_starts_at_the_given_time() -> None:
    trips = board_trips(NETWORK)
    assert [j.first for j in trips.fastest_from(minute(6))] == ["SLOW", "LAST"]


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (-41, ["FIRST", "SLOW"]),
        (-40, ["FIRST", "SLOW", "LAST"]),
        (6, ["SLOW", "LAST"]),
    ],
    ids=["last-trip-past-the-hour", "last-trip-exactly-an-hour-out", "first-gone"],
)
def test_leaving_soon_covers_the_next_hour(now: int, expected: list[str]) -> None:
    trips = board_trips(NETWORK)
    assert [j.first for j in rp.leaving_soon(trips, minute(now))] == expected


def test_evaluate_counts_failures_and_merges_warnings_that_differ_by_minutes() -> None:
    out = rp.Findings()
    record = {
        "thrown": "boom",
        "harnessErrors": ["no such request"],
        "warnings": ["no estimate in 5 min", "no estimate in 12 min"],
        "boards": None,
    }
    rp.evaluate(record, NETWORK, KNOWN_UNTIL, {}, out)
    assert out.records == 1
    assert out.boards == 0
    assert out.errors == {
        "thrown: boom": 1,
        "harness: no such request": 1,
        "no capture": 1,
    }
    assert out.warnings == {"no estimate in N min": 2}


def test_evaluate_scores_each_board_labelled_by_refresh_and_commute() -> None:
    out = rp.Findings()
    record = {
        "thrown": None,
        "harnessErrors": [],
        "warnings": [],
        "boards": [board(None)],
        "now": T0 * 1000,
        "tick": T0 * 1000,
        "commute": "ORIG DEST",
    }
    rp.evaluate(record, NETWORK, KNOWN_UNTIL, {BOARD: board_trips(NETWORK)}, out)
    assert not out.errors
    assert out.boards == 1
    assert out.no_data_boards == [{"at": LABEL, "board": BOARD}]


@pytest.mark.parametrize(
    ("train", "known_until", "expected", "counted"),
    [
        (listed(106), minute(120), (None, None), "direct: not yet run"),
        (
            listed(10, line="BLUE"),
            minute(120),
            (None, None),
            "direct: line not in GTFS-RT",
        ),
        (listed(60), minute(120), (None, None), "direct: no train that ran matches"),
        (listed(10), minute(120), (SLOW, minute(40)), "direct: checked"),
        # with no line, the train leaving nearest the shown time
        (listed(11, line=None), minute(120), (SLOW, minute(40)), "direct: checked"),
        (
            listed(5, line="YELLOW", station="XFER"),
            minute(120),
            (FIRST, minute(30)),
            "transfer: checked",
        ),
        (
            listed(5, line="YELLOW", station="XFER"),
            minute(25),
            (FIRST, None),
            "transfer: arrival not yet known",
        ),
    ],
    ids=[
        "not-yet-run",
        "line-not-in-feed",
        "no-train-matches",
        "direct",
        "no-line-takes-nearest",
        "transfer",
        "arrival-after-known-range",
    ],
)
def test_ride_finds_the_train_that_ran_a_listed_trip(
    train: dict[str, Any],
    known_until: int,
    expected: tuple[Run | None, int | None],
    counted: str,
) -> None:
    out = rp.Findings()
    lines = {r.line for r in NETWORK.values()}
    got = rp.ride(train, "ORIG", "DEST", NETWORK, lines, known_until, out)
    assert got == expected
    assert out.shown == {counted: 1}


def test_ride_of_a_train_whose_arrival_never_appeared_has_no_arrival() -> None:
    unfinished = Run("U", "RED", "s", ["ORIG", "DEST"], {"ORIG": stop_at(minute(10))})
    out = rp.Findings()
    got = rp.ride(
        listed(10), "ORIG", "DEST", {"U": unfinished}, {"RED"}, KNOWN_UNTIL, out
    )
    assert got == (unfinished, None)
    assert out.shown == {"direct: arrival not yet known": 1}


def test_ride_of_a_transfer_with_no_arrival_at_the_station_has_no_arrival() -> None:
    unfinished = Run("U", "YELLOW", "s", ["ORIG", "XFER"], {"ORIG": stop_at(minute(5))})
    out = rp.Findings()
    runs = {"U": unfinished, "LINK": LINK}
    got = rp.ride(
        listed(5, line="YELLOW", station="XFER"),
        "ORIG",
        "DEST",
        runs,
        {"YELLOW", "ORANGE"},
        KNOWN_UNTIL,
        out,
    )
    assert got == (unfinished, None)
    assert out.shown == {"transfer: arrival not yet known": 1}


def test_ride_of_a_transfer_with_no_connecting_train_has_no_arrival() -> None:
    out = rp.Findings()
    runs = {"FIRST": FIRST}
    got = rp.ride(
        listed(5, line="YELLOW", station="XFER"),
        "ORIG",
        "DEST",
        runs,
        {"YELLOW"},
        KNOWN_UNTIL,
        out,
    )
    assert got == (FIRST, None)


def test_ride_takes_a_connecting_train_leaving_within_recorded_timing() -> None:
    runs = {
        "FIRST": FIRST,
        # leaves 20 seconds before the recorded arrival of FIRST
        "TIGHT": train_run(
            "TIGHT", "ORANGE", {"XFER": minute(10) - 20, "DEST": minute(28)}
        ),
    }
    out = rp.Findings()
    got = rp.ride(
        listed(5, line="YELLOW", station="XFER"),
        "ORIG",
        "DEST",
        runs,
        {"YELLOW", "ORANGE"},
        KNOWN_UNTIL,
        out,
    )
    assert got == (FIRST, minute(28))


def test_a_board_listing_every_fastest_trip_hides_nothing() -> None:
    out = score(
        board(
            [
                listed(5, 30, line="YELLOW", station="XFER"),
                listed(10),
                listed(20),
            ]
        ),
        NETWORK,
    )
    assert out.boards == 1
    assert out.views == {"total": 1}
    assert out.shown == {"transfer: checked": 1, "direct: checked": 2}
    assert not out.missed
    assert not out.suboptimal
    assert list(out.best_trips.values()) == [{"shown": True, "cause": None}] * 3
    assert set(out.shown_trips) == {
        (BOARD, "FIRST", "XFER"),
        (BOARD, "SLOW", None),
        (BOARD, "LAST", None),
    }
    assert not any(t["beaten"] for t in out.shown_trips.values())
    # only a train carrying an estimate is scored for it
    assert out.arrival_error_s == {"transfer": [0]}


def test_a_listed_trip_records_what_the_rider_was_told_and_what_ran() -> None:
    out = score(board([listed(10, 38)]), {"SLOW": SLOW})
    trip = out.shown_trips[BOARD, "SLOW", None]
    assert (trip["transfer"], trip["described"], trip["estimate"]) == (
        False,
        "direct 17:10:00 RED",
        minute(38),
    )
    assert out.departures[BOARD, "SLOW"] == {
        "at": LABEL,
        "board": BOARD,
        "tick": at_ms(0),
        "trip": "SLOW",
        "line": "RED",
        "origin": "ORIG",
        "shown": minute(10),
        "left": minute(10),
    }
    assert out.arrival_error_s == {"direct": [2 * MIN]}


def test_a_transfer_is_described_with_its_station() -> None:
    out = score(
        board([listed(5, 30, line="YELLOW", station="XFER")]),
        {"FIRST": FIRST, "LINK": LINK},
    )
    trip = out.shown_trips[BOARD, "FIRST", "XFER"]
    assert (trip["transfer"], trip["described"]) == (
        True,
        "transfer 17:05:00 YELLOW via XFER",
    )


def test_a_listed_trip_without_an_estimate_counts_its_actual_arrival_as_told() -> None:
    out = score(board([listed(10)]), {"SLOW": SLOW})
    assert out.shown_trips[BOARD, "SLOW", None]["estimate"] == minute(40)


def test_a_slower_trip_above_a_listed_faster_one_is_on_the_board() -> None:
    out = score(board([listed(10, 38, scheduled=True), listed(12, 25)]), RACE)
    slow = out.shown_trips[BOARD, "SLOW", None]
    assert slow["beaten"] is True
    assert slow.get("status") == rp.ON_THE_BOARD
    assert (slow["better"], slow["better_described"], slow["lost_s"]) == (
        Journey(minute(12), minute(25), "EXPRESS", None, None),
        "17:12:00 RED arriving 17:25:00",
        15 * MIN,
    )
    express = out.shown_trips[BOARD, "EXPRESS", None]
    assert express["beaten"] is False
    assert "status" not in express
    assert out.suboptimal == [
        {
            "at": LABEL,
            "board": BOARD,
            "shown": "direct 17:10:00 RED *",
            "rode": "left 17:10:00, arrived 17:40:00",
            "estimated arrival": "17:38:00",
            "better": "17:12:00 RED arriving 17:25:00",
            "lost_s": 15 * MIN,
        }
    ]
    # an estimate is scored separately for a train starred as timetabled
    assert out.arrival_error_s == {"direct starred": [2 * MIN], "direct": [0]}


def test_a_slower_trip_with_the_faster_one_unlisted_names_what_hid_it() -> None:
    out = score(board([listed(10)]), RACE)
    assert out.shown_trips[BOARD, "SLOW", None].get("status") == "direct"
    (row,) = out.suboptimal
    assert row["estimated arrival"] is None
    # the faster train leaves after the last one listed, so the list does not claim to cover it
    assert not out.missed


@pytest.mark.parametrize(
    ("extra_s", "beaten", "failures"),
    [(rp.REGRET_S, False, 0), (rp.REGRET_S + 1, True, 1)],
    ids=["at-the-limit", "past-the-limit"],
)
def test_a_trip_is_beaten_only_by_more_than_the_regret_limit(
    extra_s: int, beaten: bool, failures: int
) -> None:
    runs = {
        "SLOW": train_run(
            "SLOW", "RED", {"ORIG": minute(10), "DEST": minute(25) + extra_s}
        ),
        "EXPRESS": EXPRESS,
    }
    out = score(board([listed(10), listed(12)]), runs)
    assert out.shown_trips[BOARD, "SLOW", None]["beaten"] is beaten
    assert len(out.suboptimal) == failures


def test_a_train_that_already_left_is_scored_but_is_not_a_rider_trip() -> None:
    out = score(board([listed(10, 40)]), RACE, now=11)
    assert not out.shown_trips
    assert not out.departures
    assert len(out.suboptimal) == 1


def test_a_train_with_no_known_arrival_is_a_departure_but_not_a_trip() -> None:
    unfinished = Run("U", "RED", "s", ["ORIG", "DEST"], {"ORIG": stop_at(minute(10))})
    out = score(board([listed(10)]), {"U": unfinished})
    assert set(out.departures) == {(BOARD, "U")}
    assert not out.shown_trips


def test_a_train_that_no_run_matches_is_not_scored() -> None:
    out = score(board([listed(100)]), NETWORK)
    assert out.shown == {"direct: no train that ran matches": 1}
    assert not out.departures
    assert not out.shown_trips
    # a list with no train that ran cannot be said to omit one
    assert not out.best_trips
    assert not out.missed


def test_a_trip_with_no_better_option_known_is_not_beaten() -> None:
    out = rp.Findings()
    rp.evaluate_board(
        board([listed(10)]),
        moment(),
        {"SLOW": SLOW},
        KNOWN_UNTIL,
        rp.BoardTrips([], []),
        out,
    )
    trip = out.shown_trips[BOARD, "SLOW", None]
    assert (trip["beaten"], trip["better"], trip["lost_s"]) == (False, None, None)
    assert trip["better_described"] is None


def test_a_board_that_failed_its_plan_counts_the_error() -> None:
    out = score(board([listed(10)], plan_error="timed out"), {"SLOW": SLOW})
    assert out.errors == {"trip plan: timed out": 1}


def test_a_board_with_no_data_hides_every_trip_leaving_within_the_hour() -> None:
    out = score(board(None), NETWORK)
    assert out.shown == {"no data": 1}
    assert out.views == {"total": 1, "no data": 1}
    assert out.no_data_boards == [{"at": LABEL, "board": BOARD}]
    assert {k: v["shown"] for k, v in out.best_trips.items()} == {
        (BOARD, "FIRST", "XFER"): False,
        (BOARD, "SLOW", None): False,
        (BOARD, "LAST", None): False,
    }
    hidden = out.best_trips[BOARD, "FIRST", "XFER"]
    assert hidden == {
        "shown": False,
        "cause": "no data",
        "at": LABEL,
        "board": BOARD,
        "tick": at_ms(0),
        "now": minute(0),
        "journey": TRANSFER_TRIP,
        "described": "17:05:00 YELLOW via XFER ORANGE arriving 17:30:00",
        "alternative": None,
        "lost_s": None,
    }
    # a board with no data makes no claim about trains, so nothing is counted as omitted
    assert not out.missed


def test_a_board_saying_no_trains_while_one_left_within_the_hour_failed_the_rider() -> (
    None
):
    out = score(board([]), NETWORK)
    assert out.views == {
        "total": 1,
        "no trains while a trip left within an hour": 1,
    }
    assert out.empty_boards == [
        {
            "at": LABEL,
            "board": BOARD,
            "tick": at_ms(0),
            "now": minute(0),
            "journey": TRANSFER_TRIP,
            "described": "17:05:00 YELLOW via XFER ORANGE arriving 17:30:00",
        }
    ]
    assert [m["missed"] for m in out.missed] == [
        "17:05:00 YELLOW via XFER ORANGE arriving 17:30:00",
        "17:10:00 RED arriving 17:40:00",
        "17:20:00 RED arriving 17:50:00",
    ]
    assert {m["planned"] for m in out.missed} == {"no trains"}
    assert {m["lost_s"] for m in out.missed} == {None}


def test_a_board_saying_no_trains_with_none_due_within_the_hour_is_right() -> None:
    out = score(board([]), NETWORK, now=-70)
    assert out.views == {"total": 1}
    assert not out.empty_boards
    assert not out.best_trips
    assert not out.missed


def test_a_list_omitting_a_fastest_trip_counts_it_against_the_next_listed() -> None:
    out = score(board([listed(20, 45)]), NETWORK)
    assert [(m["missed"], m["lost_s"], m["planned"]) for m in out.missed] == [
        (
            "17:05:00 YELLOW via XFER ORANGE arriving 17:30:00",
            20 * MIN,
            "not in the trip plan",
        ),
        ("17:10:00 RED arriving 17:40:00", 10 * MIN, "direct"),
    ]
    first, slow, last = (
        out.best_trips[BOARD, "FIRST", "XFER"],
        out.best_trips[BOARD, "SLOW", None],
        out.best_trips[BOARD, "LAST", None],
    )
    assert first["shown"] is False
    assert first["cause"] == "not in the trip plan"
    assert first["alternative"] == minute(45)
    assert first["lost_s"] == 20 * MIN
    assert slow["shown"] is False
    assert slow["cause"] == "direct"
    assert last == {"shown": True, "cause": None}


def test_the_board_told_arrival_falls_back_to_the_actual_one() -> None:
    out = score(board([listed(20)]), NETWORK)
    hidden = out.best_trips[BOARD, "SLOW", None]
    assert hidden["shown"] is False
    assert hidden["alternative"] == minute(50)


def test_a_fastest_trip_after_the_last_listed_train_is_not_omitted() -> None:
    out = score(board([listed(10)]), {"SLOW": SLOW, "LAST": LAST})
    assert set(out.best_trips) == {(BOARD, "SLOW", None)}
    assert not out.missed


@pytest.mark.parametrize(
    ("extra_s", "omitted"),
    [(rp.REGRET_S, False), (rp.REGRET_S + 1, True)],
    ids=["within-limit", "past-limit"],
)
def test_omitting_a_trip_counts_only_beyond_the_regret_limit(
    extra_s: int, omitted: bool
) -> None:
    runs = {
        "FAST": train_run("FAST", "RED", {"ORIG": minute(10), "DEST": minute(30)}),
        "NEXT": train_run(
            "NEXT", "RED", {"ORIG": minute(20), "DEST": minute(30) + extra_s}
        ),
    }
    out = score(board([listed(20)]), runs)
    assert (len(out.missed) == 1) is omitted
    assert out.best_trips[BOARD, "FAST", None]["shown"] is not omitted


def test_a_trip_omitted_with_no_known_arrival_listed_has_no_comparison() -> None:
    unfinished = Run("U", "RED", "s", ["ORIG", "DEST"], {"ORIG": stop_at(minute(20))})
    runs = {"SLOW": SLOW, "U": unfinished}
    out = score(board([listed(20)]), runs)
    (row,) = out.missed
    assert row["lost_s"] is None
    hidden = out.best_trips[BOARD, "SLOW", None]
    assert hidden["shown"] is False
    assert hidden["alternative"] is None


def test_matched_trips_names_each_listed_train_that_ran() -> None:
    realized: rp.Realized = [
        (listed(5, station="XFER"), FIRST, minute(30)),
        (listed(10), SLOW, minute(40)),
        (listed(60), None, None),
    ]
    assert rp.matched_trips(realized) == {("FIRST", "XFER"), ("SLOW", None)}


@pytest.mark.parametrize(
    ("first", "connecting", "now", "reason"),
    [
        ("not running", None, 0, "first train not running"),
        (None, "not running", 0, "connecting train not running"),
        (None, None, 6, "timetabled to leave before now, with no live first train"),
        (
            None,
            None,
            0,
            "connecting train projected to leave before the first train arrives",
        ),
        (
            {"departs": at_ms(5)},
            None,
            6,
            "connecting train projected to leave before the first train arrives",
        ),
    ],
    ids=[
        "first-not-running",
        "connecting-not-running",
        "left-before-now-with-no-live-train",
        "timing",
        "live-first-train-is-not-stale",
    ],
)
def test_drop_reason_names_the_widget_rule(
    first: dict[str, Any] | str | None,
    connecting: dict[str, Any] | str | None,
    now: float,
    reason: str,
) -> None:
    transfer = planned(first_train=first, connecting_train=connecting)
    assert rp.drop_reason(transfer, at_ms(now)) == reason


def test_planned_status_of_a_direct_trip_needs_no_plan() -> None:
    assert rp.planned_status(board([]), SLOW_TRIP, at_ms(0)) == "direct"


def test_planned_status_finds_a_trip_absent_from_the_plan() -> None:
    # nothing planned within LEG_LATE_S of the departure
    far = board([], [planned(first_min=30, second_min=40, arrives_min=60)])
    assert rp.planned_status(far, TRANSFER_TRIP, at_ms(0)) == "not in the trip plan"


def test_planned_status_uses_the_planned_transfer_nearest_the_trip() -> None:
    plans = board(
        [],
        [
            planned(kept_at=5, first_train={"departs": at_ms(5)}),
            planned(first_min=12, second_min=19, first_train="not running"),
        ],
    )
    assert (
        rp.planned_status(plans, TRANSFER_TRIP, at_ms(0))
        == "kept, then beaten by an estimate"
    )
    later = Journey(minute(11), minute(36), "FIRST", "XFER", "LINK")
    assert (
        rp.planned_status(plans, later, at_ms(0)) == "hidden: first train not running"
    )


CONNECTING = {"FIRST": FIRST, "LINK": LINK}


def check(
    b: dict[str, Any],
    runs: dict[str, Run] = CONNECTING,
    known_until: int = KNOWN_UNTIL,
    now: float = 0,
    realized: rp.Realized | None = None,
) -> rp.Findings:
    out = rp.Findings()
    rp.check_transfers(b, realized or [], runs, known_until, moment(now), out)
    return out


def test_check_transfers_reports_a_transfer_not_yet_run() -> None:
    out = check(board([], [planned()]), known_until=minute(30))
    assert out.transfers == {"transfer dropped: not yet run": 1}
    assert not out.connections


def test_check_transfers_reports_a_line_gtfs_rt_does_not_carry() -> None:
    out = check(board([], [planned(second_line="BLUE")]))
    assert out.transfers == {"transfer dropped: line not in GTFS-RT": 1}


def test_check_transfers_reports_a_plan_no_train_matched() -> None:
    out = check(board([], [planned(first_min=60, second_min=70, arrives_min=90)]))
    assert out.transfers == {"transfer dropped: no train that ran matches": 1}


def test_check_transfers_waits_for_the_first_trains_arrival_at_the_station() -> None:
    unfinished = Run(
        "FIRST", "YELLOW", "s", ["ORIG", "XFER"], {"ORIG": Stop(minute(5), minute(5))}
    )
    out = check(board([], [planned()]), runs={"FIRST": unfinished, "LINK": LINK})
    assert out.transfers == {"transfer dropped: not yet run": 1}


def test_a_kept_grand_meet_that_held_is_a_correct_call() -> None:
    out = check(board([], [planned(kept_at=5, grand_meet=True)]))
    assert out.transfers == {"grand meet kept: connection held": 1}
    assert not out.false_drops
    assert not out.false_keeps


def test_a_dropped_connection_that_held_records_what_the_rider_lost() -> None:
    realized: rp.Realized = [(listed(10), SLOW, minute(40))]
    transfer = planned(
        first_train={"departs": at_ms(5)}, connecting_train="not running"
    )
    out = check(board([], [transfer]), realized=realized)
    (detail,) = out.false_drops.values()
    assert detail == {
        "at": LABEL,
        "board": BOARD,
        "planned": "17:05:00 YELLOW to XFER, 17:12:00 ORANGE",
        "ran": "first reached XFER 17:10:00, connection left 17:12:00",
        "margin_s": 2 * MIN,
        "first_train": "17:05:00",
        "connecting_train": "not running",
        "reason": "connecting train not running",
        "arrived": "17:30:00",
        "lost_s": 10 * MIN,
        "refreshes": 1,
    }


def test_a_dropped_connection_with_no_known_arrival_has_no_loss() -> None:
    link = Run("LINK", "ORANGE", "s", ["XFER", "DEST"], {"XFER": stop_at(minute(12))})
    out = check(board([], [planned()]), runs={"FIRST": FIRST, "LINK": link})
    (detail,) = out.false_drops.values()
    assert detail.get("arrived") is None
    assert detail.get("lost_s") is None


def test_a_false_drop_or_keep_counts_every_refresh_it_persisted() -> None:
    late = {
        "FIRST": FIRST,
        "LINK": train_run(
            "LINK", "ORANGE", {"XFER": minute(10) - 45, "DEST": minute(30)}
        ),
    }
    dropped = rp.Findings()
    kept = rp.Findings()
    for now in (0, 1):
        rp.check_transfers(
            board([], [planned()]), [], CONNECTING, KNOWN_UNTIL, moment(now), dropped
        )
        rp.check_transfers(
            board([], [planned(kept_at=5, second_min=10)]),
            [],
            late,
            KNOWN_UNTIL,
            moment(now),
            kept,
        )
    assert [d.get("refreshes") for d in dropped.false_drops.values()] == [2]
    assert [d.get("refreshes") for d in kept.false_keeps.values()] == [2]


@pytest.mark.parametrize(
    ("shown_at", "kept_at", "shown"),
    [(5, 5, True), (5, 6, True), (6, 6, True), (7, 6, False), (None, 6, False)],
    ids=[
        "at-the-kept-time",
        "at-the-timetable-time",
        "at-the-live-time",
        "at-another-time",
        "not-listed",
    ],
)
def test_a_kept_connection_is_listed_only_when_the_board_shows_it(
    shown_at: float | None, kept_at: float, shown: bool
) -> None:
    trains = (
        [] if shown_at is None else [listed(shown_at, line="YELLOW", station="XFER")]
    )
    out = check(board(trains, [planned(kept_at=kept_at)]))
    (connection,) = out.connections.values()
    assert connection["shown"] is shown
    assert connection["kept"] is True


def test_a_connection_records_what_the_widget_knew_before_it_left() -> None:
    kept = planned(kept_at=5, first_train={"departs": at_ms(5)}, connecting_train=None)
    out = check(board([], [kept]))
    assert out.connections == {
        (BOARD, at_ms(5)): {
            "shown": False,
            "kept": True,
            "made": True,
            "at": LABEL,
            "board": BOARD,
            "planned": "17:05:00 YELLOW to XFER, 17:12:00 ORANGE",
            "tick": at_ms(0),
            "now": minute(0),
            "origin": "ORIG",
            "first_trip": "FIRST",
            "connecting_trip": "LINK",
            "station": "XFER",
            "first_live": True,
            "connecting_live": False,
            "drop_reason": None,
            "margin_s": 2 * MIN,
        }
    }


def test_a_dropped_connection_records_why_it_was_dropped() -> None:
    out = check(board([], [planned(first_train="not running")]))
    (connection,) = out.connections.values()
    assert (connection["kept"], connection["drop_reason"]) == (
        False,
        "first train not running",
    )


def test_a_connection_is_not_counted_once_its_first_train_has_left() -> None:
    out = check(board([], [planned()]), now=5)
    assert out.transfers == {"transfer dropped: connection held": 1}
    assert not out.connections


def test_evaluate_board_checks_planned_transfers_and_marks_missed_trip_status() -> None:
    # the board lists SLOW; the transfer that beats it was dropped for a train not running
    out = score(
        board([listed(10)], [planned(first_train="not running")]),
        NETWORK,
    )
    assert out.shown_trips[BOARD, "SLOW", None]["beaten"] is False
    hidden = out.best_trips[BOARD, "FIRST", "XFER"]
    assert hidden["shown"] is False
    assert hidden["cause"] == "hidden: first train not running"
    assert out.transfers == {"transfer dropped: connection held": 1}
    assert set(out.connections) == {(BOARD, at_ms(5))}
