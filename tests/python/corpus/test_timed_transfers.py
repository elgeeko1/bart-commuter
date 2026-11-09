# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for the timed transfer measurements; they make no requests."""

from __future__ import annotations

import argparse
import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from bart_corpus import core, gtfs_rt, store, timetable, train_runs
from bart_corpus import timed_transfers as tt
from corpus_support import T0, gtfs_rt_feed, poll

DAY = "2026-11-06"
ORIGIN = timetable.gtfs_origin_s(DAY)
FIVE_PM = 17 * 3600


def run(
    trip_id: str, line: str, stations: list[str], times: dict[str, int]
) -> train_runs.Run:
    stops = {s: gtfs_rt.Stop(ORIGIN + t, ORIGIN + t) for s, t in times.items()}
    return train_runs.Run(trip_id, line, "s", stations, stops)


# a Yellow train from Antioch meets an Orange train from Richmond at MacArthur; a Red train
# follows the Orange one from Richmond
YELLOW = ["ANTC", "ROCK", "MCAR", "19TH", "12TH", "WOAK"]
ORANGE = ["RICH", "ASHB", "MCAR", "19TH", "12TH", "LAKE", "BERY"]
RED = ["RICH", "ASHB", "MCAR", "19TH", "12TH", "WOAK"]


def test_useful_transfers_go_somewhere_new_without_doubling_back() -> None:
    yellow = run("Y", "YELLOW", YELLOW, {})
    orange = run("O", "ORANGE", ORANGE, {})
    red = run("R", "RED", RED, {})
    assert tt.useful(yellow, orange, "MCAR")
    assert tt.useful(orange, yellow, "MCAR")
    # the Red train comes from where the Orange one does and goes nowhere the Yellow one does not
    assert not tt.useful(orange, red, "MCAR")
    assert not tt.useful(red, yellow, "MCAR")
    back = run("B", "BLUE", ["WOAK", "12TH", "19TH", "MCAR", "ROCK", "ANTC"], {})
    assert not tt.useful(yellow, back, "MCAR")


def test_meets_compare_how_late_each_train_was() -> None:
    yellow = run("Y", "YELLOW", YELLOW, {"MCAR": FIVE_PM + 150})
    orange = run("O", "ORANGE", ORANGE, {"MCAR": FIVE_PM + 140})
    static = timetable.StaticGtfs(
        stations={},
        routes={},
        trip_routes={},
        trip_stations={},
        # the Orange train is timed to leave a minute after the Yellow one arrives
        trip_times={
            "Y": {"MCAR": (FIVE_PM, FIVE_PM)},
            "O": {"MCAR": (FIVE_PM + 60, FIVE_PM + 60)},
        },
    )
    (meet,) = tt.meets({"Y": yellow, "O": orange}, static, ORIGIN, [("MCAR", "s")])
    assert (meet.arriving, meet.connecting, meet.timetable_gap_s) == ("Y", "O", 60)
    assert (
        meet.arrived_late_s,
        meet.left_late_s,
        meet.needed_wait_s,
        meet.margin_s,
    ) == (150, 80, 90, -10)
    assert tt.bucket(meet.needed_wait_s) == "1-2 min"


def test_timed_transfers_come_from_the_widget_script() -> None:
    source = core.ENGINE.read_text(encoding="utf-8")
    assert tt.timed_transfers(source) == (
        ("19TH", "n"),
        ("MCAR", "s"),
        ("BAYF", "n"),
        ("BAYF", "s"),
    )


def meet(arriving: str, arrived: int, margin_s: int) -> tt.Meet:
    """A meet at MacArthur whose connection, timetabled a minute after arrival, needed a 30 s wait."""
    return tt.Meet(
        "MCAR",
        "s",
        arriving,
        "Y",
        f"C-{arriving}",
        "O",
        60,
        arrived,
        90,
        arrived + margin_s,
        0,
    )


def test_summary_counts_connections_held_tight_and_missed() -> None:
    at = ORIGIN + FIVE_PM
    found = [meet("A", at, 10), meet("B", at + 600, -20), meet("C", at + 1200, -45)]
    (row,) = tt.tabulate(found)
    assert (row.station, row.direction, row.bucket) == ("MCAR", "s", "0-1 min")
    assert (row.meets, row.held, row.tight, row.missed) == (3, 1, 1, 1)


def test_merge_keeps_each_meet_once_per_service_day() -> None:
    at = ORIGIN + FIVE_PM
    today = meet("A", at, 10)
    tomorrow = meet("A", at + 24 * 3600, -45)
    assert tt.merge([tomorrow, today, meet("A", at, 10)]) == [today, tomorrow]


def test_meets_skip_an_arriving_train_the_timetable_lacks() -> None:
    yellow = run("Y", "YELLOW", YELLOW, {"MCAR": FIVE_PM})
    orange = run("O", "ORANGE", ORANGE, {"MCAR": FIVE_PM + 60})
    static = timetable.StaticGtfs(
        stations={},
        routes={},
        trip_routes={},
        trip_stations={},
        trip_times={"O": {"MCAR": (FIVE_PM + 60, FIVE_PM + 60)}},
    )
    assert tt.meets({"Y": yellow, "O": orange}, static, ORIGIN, [("MCAR", "s")]) == []


@pytest.mark.parametrize(
    ("needed_s", "label"),
    [(-30, 0), (0, 0), (1, 1), (60, 1), (61, 2), (300, 5), (301, 6)],
)
def test_bucket_groups_the_wait_a_connection_needed(needed_s: int, label: int) -> None:
    assert tt.bucket(needed_s) == tt.BUCKET_LABELS[label]


def test_summary_reports_each_station_and_wait() -> None:
    at = ORIGIN + FIVE_PM
    found = [
        meet("A", at, 10),
        meet("B", at + 600, -20),
        replace(meet("C", at + 1200, -45), left_late_s=40),
        # needs a wait of 340 s, past the last bucket
        replace(meet("D", at + 1800, 5), arrived_late_s=400),
        replace(meet("E", at, 10), station="BAYF", dir="n"),
    ]
    lines = tt.summarize(found).splitlines()
    assert lines[0].split()[0] == "5"
    headers = [line.split() for line in lines[1:] if line and not line[0].isspace()]
    assert [(h[0], h[1].rstrip(":"), h[2]) for h in headers] == [
        ("BAYF", "n", "1"),
        ("MCAR", "s", "4"),
    ]
    rows = [line.split()[-6:] for line in lines if line.startswith("  0-1 min")]
    assert rows == [["1", "1", "0", "0", "0", "0"], ["3", "1", "1", "1", "0", "40"]]
    (over,) = [line.split()[-6:] for line in lines if line.startswith("  over")]
    assert over == ["1", "1", "0", "0", "0", "0"]


def test_timed_transfers_require_the_table_in_the_script() -> None:
    with pytest.raises(core.CorpusError):
        tt.timed_transfers("const OTHER = [];")


def static_feed() -> bytes:
    """Static GTFS of a Yellow train timed to meet an Orange one at MacArthur at 5 PM."""
    stop_times = [
        ("Y", "ROCK", "16:56:00", 1),
        ("Y", "MCAR", "17:00:00", 2),
        ("Y", "19TH", "17:05:00", 3),
        ("O", "ASHB", "16:57:00", 1),
        ("O", "MCAR", "17:01:00", 2),
        ("O", "LAKE", "17:10:00", 3),
    ]
    files = {
        "stops.txt": "stop_id,parent_station\n"
        + "".join(f"{s},\n" for s in ("ROCK", "MCAR", "19TH", "ASHB", "LAKE")),
        "routes.txt": "route_id,route_short_name\n1,Yellow-S\n2,Orange-S\n",
        "trips.txt": "trip_id,route_id\nY,1\nO,2\n",
        "stop_times.txt": "trip_id,stop_id,arrival_time,departure_time,stop_sequence\n"
        + "".join(f"{t},{s},{at},{at},{n}\n" for t, s, at, n in stop_times),
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return out.getvalue()


def recording(corpus: Path) -> Path:
    """A day whose feed reports the Yellow train 30 s late and the Orange one 10 s late at MacArthur."""
    onward = gtfs_rt_feed({"Y": [("19TH", T0 + 300)], "O": [("LAKE", T0 + 600)]})
    feeds = [
        (
            T0,
            gtfs_rt_feed(
                {
                    "Y": [("MCAR", T0 + 30), ("19TH", T0 + 300)],
                    "O": [("MCAR", T0 + 70), ("LAKE", T0 + 600)],
                }
            ),
        ),
        (T0 + 120, onward),
        (T0 + 180, onward),
    ]
    writer = store.Store(corpus)
    writer.write([poll("gtfs-static", T0 * 1000, static_feed())])
    for tick_s, data in feeds:
        writer.write([poll("tripupdate", tick_s * 1000, data)])
    writer.close()
    return corpus / f"{DAY}.sqlite"


def test_read_meets_measures_a_recorded_day(tmp_path: Path) -> None:
    (found,) = tt.read_meets(recording(tmp_path), [("MCAR", "s")])
    assert (found.arriving, found.arriving_line) == ("Y", "YELLOW")
    assert (found.connecting, found.connecting_line) == ("O", "ORANGE")
    assert (found.arrived, found.left) == (T0 + 30, T0 + 70)
    assert (found.arrived_late_s, found.left_late_s) == (30, 10)


def command_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *argv: str
) -> argparse.Namespace:
    """Parses meets arguments with a widget script that lists only MacArthur southbound."""
    engine = tmp_path / "engine.js"
    engine.write_text(
        'const TIMED_TRANSFERS = [\n  { station: "MCAR", dir: "s" },\n];\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(tt, "ENGINE", engine)
    parser = argparse.ArgumentParser()
    tt.configure(parser)
    return parser.parse_args(argv)


def test_run_prints_the_meets_and_writes_each_to_the_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = recording(tmp_path)
    report = tmp_path / "meets.json"
    tt.run(command_line(monkeypatch, tmp_path, str(path), "--report", str(report)))

    expected = tt.read_meets(path, [("MCAR", "s")])
    assert capsys.readouterr().out == tt.summarize(expected) + "\n"
    (row,) = json.loads(report.read_text(encoding="utf-8"))
    assert (row["arriving"], row["connecting"]) == ("Y", "O")
    assert (row["margin_s"], row["needed_wait_s"]) == (40, -30)


def test_run_without_a_report_only_prints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    corpus = tmp_path / "corpus"
    tt.run(command_line(monkeypatch, tmp_path, str(recording(corpus))))
    assert capsys.readouterr().out.split()[0] == "1"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["corpus", "engine.js"]


def test_run_rejects_a_missing_corpus_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = command_line(monkeypatch, tmp_path, str(tmp_path / "absent.sqlite"))
    with pytest.raises(core.CorpusError):
        tt.run(args)
