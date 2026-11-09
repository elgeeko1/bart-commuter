# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for handing recorded refreshes to the widget driver and running a whole replay."""

from __future__ import annotations

import argparse
import io
import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from bart_corpus import gtfs_rt, store
from bart_corpus import replay as rp
from bart_corpus.core import ENGINE, ROOT, CorpusError
from bart_corpus.recorder import ETD_ALL
from corpus_support import MIN, T0, etd_all, gtfs_rt_feed, plan, poll

FIXTURES = ROOT / "tests" / "fixtures"
BOARD = "ORIG-DEST"
COMMUTE = "DEST ORIG"


def tick(minute: float) -> int:
    return int(T0 + minute * MIN) * 1000


def corpus_of(tmp_path: Path, polls: list[store.Poll]) -> Path:
    corpus_store = store.Store(tmp_path)
    corpus_store.write(polls)
    corpus_store.close()
    return next(tmp_path.glob("*.sqlite"))


def trip_plan(trips: int) -> bytes:
    return plan(*[[("WOAK", "EMBR")] for _ in range(trips)])


def lines_of(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def test_write_refreshes_gives_the_driver_each_refresh_as_the_widget_saw_it(
    tmp_path: Path,
) -> None:
    corpus = corpus_of(
        tmp_path,
        [
            poll("etd", tick(0), etd_all("WOAK", "EMBR"), subject=ETD_ALL),
            poll("depart", tick(0), trip_plan(2), subject="WOAK-EMBR"),
            poll("depart", tick(0), trip_plan(2), subject="EMBR-WOAK"),
            # planned one way only, so not a commute
            poll("depart", tick(0), trip_plan(2), subject="ROCK-FRMT"),
            poll("tripupdate", tick(0), b"\x01\xab"),
            poll("etd", tick(1), etd_all("WOAK"), subject=ETD_ALL),
        ],
    )
    path = tmp_path / "refreshes.jsonl"
    with closing(sqlite3.connect(corpus)) as db:
        rp.write_refreshes(db, [tick(0), tick(1)], path, 0)
    first, second = lines_of(path)
    assert (first["tick"], first["now"]) == (tick(0), tick(0) + 5)
    assert first["commutes"] == ["EMBR WOAK"]
    assert set(first["etd"]) == {"WOAK", "EMBR"}
    assert json.loads(first["etd"]["EMBR"])["root"]["station"][0]["abbr"] == "EMBR"
    assert set(first["depart"]) == {"WOAK-EMBR", "EMBR-WOAK", "ROCK-FRMT"}
    assert first["feed"] == "01ab"
    # the next refresh has neither trip plans nor a feed of its own
    assert (second["commutes"], second["depart"], second["feed"]) == ([], {}, None)
    assert set(second["etd"]) == {"WOAK"}


def test_write_refreshes_drops_the_first_trips_of_each_plan(tmp_path: Path) -> None:
    corpus = corpus_of(
        tmp_path,
        [
            poll("depart", tick(0), trip_plan(3), subject="WOAK-EMBR"),
            poll("depart", tick(0), trip_plan(1), subject="EMBR-WOAK"),
        ],
    )
    path = tmp_path / "refreshes.jsonl"
    with closing(sqlite3.connect(corpus)) as db:
        rp.write_refreshes(db, [tick(0)], path, 1)
    (line,) = lines_of(path)
    remaining = {
        pair: json.loads(text)["root"]["schedule"]["request"]["trip"]
        for pair, text in line["depart"].items()
    }
    assert {pair: len(trips) for pair, trips in remaining.items()} == {
        "WOAK-EMBR": 2,
        "EMBR-WOAK": 0,
    }


def test_drop_first_trips_handles_a_plan_of_one_trip_given_as_an_object() -> None:
    one = {"root": {"schedule": {"request": {"trip": {"n": 0}}}}}
    dropped = json.loads(rp.drop_first_trips(json.dumps(one), 1))
    assert dropped["root"]["schedule"]["request"]["trip"] == []


def fake_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert kwargs["cwd"] == ROOT
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_run_driver_returns_the_records_it_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = fake_subprocess(
        monkeypatch, stdout='engine log\n{"tick": 1}\n{"tick": 2}\n'
    )
    records = rp.run_driver(tmp_path / "in.jsonl", Path("bart-commuter.js"))
    assert records == [{"tick": 1}, {"tick": 2}]
    assert calls == [
        [
            "bun",
            rp.DRIVER,
            str(tmp_path / "in.jsonl"),
            str(Path("bart-commuter.js").resolve()),
        ]
    ]


@pytest.mark.parametrize(
    "result",
    [
        {"returncode": 1, "stderr": "syntax error"},
        {"returncode": 1, "stdout": "syntax error"},
        {"stdout": '{"tick": 1}\n{"fatal": "script threw"}\n'},
    ],
    ids=["exit-status-with-stderr", "exit-status-with-stdout", "fatal-record"],
)
def test_run_driver_raises_on_a_failed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: dict[str, Any]
) -> None:
    fake_subprocess(monkeypatch, **result)
    with pytest.raises(CorpusError):
        rp.run_driver(tmp_path / "in.jsonl", tmp_path / "s.js")


def record(path: Path, line: dict[str, Any]) -> dict[str, Any]:
    return {"tick": line["tick"], "from": path.name}


@pytest.mark.parametrize(
    ("cpus", "shards"),
    [(None, 1), (2, 2), (16, 5)],
    ids=["unknown-cpu-count", "fewer-cpus-than-refreshes", "more-cpus-than-refreshes"],
)
def test_replay_refreshes_shards_by_cpu_and_keeps_refresh_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cpus: int | None, shards: int
) -> None:
    corpus = corpus_of(tmp_path, [poll("tripupdate", tick(n), b"x") for n in range(5)])
    seen: list[str] = []

    def driver(path: Path, script: Path) -> list[dict[str, Any]]:
        assert script == Path("engine.js")
        seen.append(path.name)
        return [record(path, line) for line in lines_of(path)]

    monkeypatch.setattr(os, "cpu_count", lambda: cpus)
    monkeypatch.setattr(rp, "run_driver", driver)
    ticks = [tick(n) for n in range(5)]
    with closing(sqlite3.connect(corpus)) as db:
        records = rp.replay_refreshes(db, ticks, Path("engine.js"), 0)
    assert [r["tick"] for r in records] == ticks
    assert len(seen) == shards


def test_replay_refreshes_of_no_refreshes_runs_no_driver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = corpus_of(tmp_path, [poll("tripupdate", tick(0), b"x")])

    def driver(path: Path, script: Path) -> list[dict[str, Any]]:
        raise AssertionError(f"ran the driver on {path} with {script}")

    monkeypatch.setattr(rp, "run_driver", driver)
    with closing(sqlite3.connect(corpus)) as db:
        assert rp.replay_refreshes(db, [], ENGINE, 0) == []


def parse_args(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    rp.configure(parser)
    return parser.parse_args(argv)


def test_configure_replays_the_whole_day_with_the_shipped_engine_by_default() -> None:
    args = parse_args("corpus/day.sqlite")
    assert args.corpus_file == Path("corpus/day.sqlite")
    assert (args.report, args.since, args.until) == (None, None, None)
    assert args.script == ENGINE
    assert args.drop_first_trips == 0


def test_configure_reads_refresh_limits_as_bart_time() -> None:
    args = parse_args(
        "day.sqlite",
        "--since",
        "2026-11-06T17:00:00",
        "--until",
        "2026-11-06T17:15:00",
        "--drop-first-trips",
        "2",
        "--report",
        "out.json",
        "--script",
        "old.js",
    )
    assert (args.since, args.until) == (tick(0), tick(15))
    assert args.drop_first_trips == 2
    assert args.report == Path("out.json")
    assert args.script == Path("old.js")


def timetable_zip() -> bytes:
    """A static GTFS feed of one trip, A on the Red line, ORIG 17:10 and DEST 17:40."""
    files = {
        "stops.txt": "stop_id,parent_station\nORIG,\nDEST,\n",
        "routes.txt": "route_id,route_short_name\n1,Red-S\n",
        "trips.txt": "trip_id,route_id\nA,1\n",
        "stop_times.txt": (
            "trip_id,stop_sequence,stop_id,arrival_time,departure_time\n"
            "A,1,ORIG,17:10:00,17:10:00\nA,2,DEST,17:40:00,17:40:00\n"
        ),
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return out.getvalue()


def recorded_day(tmp_path: Path) -> Path:
    """A day on which train A leaves ORIG at 10 minutes past T0 and reaches DEST at 40."""
    leaves, arrives = T0 + 10 * MIN, T0 + 40 * MIN
    both = {"A": [("ORIG", leaves), ("DEST", arrives)]}
    rest = {"A": [("DEST", arrives)]}
    empty: dict[str, list[tuple[str, int]]] = {}
    polls = [poll("gtfs-static", tick(0), timetable_zip())] + [
        poll("tripupdate", tick(minute), gtfs_rt_feed(trips))
        for minute, trips in ((0, both), (15, rest), (39, rest), (80, empty))
    ]
    return corpus_of(tmp_path / "corpus", polls)


def train_a() -> dict[str, Any]:
    return {
        "departs": tick(10),
        "arrives": tick(40),
        "line": "RED",
        "transfer": None,
        "scheduled": False,
    }


def stub_driver(
    monkeypatch: pytest.MonkeyPatch, trains: list[dict[str, Any]]
) -> list[int]:
    """Replaces the driver with one showing trains at the first refresh; returns the ticks replayed."""
    replayed: list[int] = []

    def driver(path: Path, script: Path) -> list[dict[str, Any]]:
        assert script == ENGINE
        records = []
        for line in lines_of(path):
            replayed.append(line["tick"])
            board = {
                "board": BOARD,
                "trains": trains,
                "transfers": [],
                "planError": None,
            }
            records.append(
                {
                    "tick": line["tick"],
                    "now": line["tick"],
                    "commute": COMMUTE,
                    "thrown": None,
                    "harnessErrors": [],
                    "warnings": [],
                    "boards": [board] if line["tick"] == tick(0) else None,
                }
            )
        return records

    monkeypatch.setattr(rp, "run_driver", driver)
    return replayed


def test_run_reports_a_rider_shown_the_train_that_ran(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    corpus = recorded_day(tmp_path)
    replayed = stub_driver(monkeypatch, [train_a()])
    report = tmp_path / "report.json"
    rp.run(
        parse_args(
            str(corpus),
            "--until",
            "2026-11-06T17:15:00",
            "--report",
            str(report),
        )
    )
    # drivers run in parallel, so the ticks arrive in any order
    assert sorted(replayed) == [tick(0), tick(15)]
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "replayed 2 refreshes; trains known until 18:18:30"
    assert out[1] == "2 runs, 1 boards"
    assert "      1  no capture" in out
    written = json.loads(report.read_text("utf-8"))
    assert written["failures"] == []
    assert written["errors"] == {"no capture": 1}
    assert written["shown"] == {"direct: checked": 1}
    assert written["rider"]["faster_trip_hidden"]["total"] == 1
    assert written["rider"]["train_gone"]["total"] == 1
    assert [r["tick"] for r in written["records"]] == [tick(0), tick(15)]


def test_run_attributes_failures_from_the_recorded_day(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    corpus = recorded_day(tmp_path)
    stub_driver(monkeypatch, [])
    report = tmp_path / "report.json"
    rp.run(parse_args(str(corpus), "--report", str(report)))
    written = json.loads(report.read_text("utf-8"))
    assert written["rider"]["no_trains"]["faults"] == {
        rp.OUR_FAULT: {"the widget's data had a trip within the hour": 1}
    }
    assert written["rider"]["faster_trip_hidden"]["faults"] == {
        rp.OUR_FAULT: {"the board said no trains": 1}
    }
    assert {r["item"] for r in written["failures"]} == {
        "faster_trip_hidden",
        "no_trains",
    }
    assert "rider-facing failures: 2" in capsys.readouterr().out


def test_run_replays_only_the_requested_refreshes_and_writes_no_report_unasked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    corpus = recorded_day(tmp_path)
    replayed = stub_driver(monkeypatch, [train_a()])
    rp.run(
        parse_args(
            str(corpus),
            "--since",
            "2026-11-06T17:15:00",
            "--until",
            "2026-11-06T17:15:00",
        )
    )
    assert replayed == [tick(15)]
    assert capsys.readouterr().out.startswith("replayed 1 refreshes")
    assert [p.name for p in tmp_path.iterdir()] == ["corpus"]


def test_run_rejects_a_corpus_file_that_does_not_exist(tmp_path: Path) -> None:
    with pytest.raises(CorpusError):
        rp.run(parse_args(str(tmp_path / "missing.sqlite")))


def test_run_rejects_a_script_that_does_not_exist(tmp_path: Path) -> None:
    corpus = recorded_day(tmp_path)
    with pytest.raises(CorpusError):
        rp.run(parse_args(str(corpus), "--script", str(tmp_path / "missing.js")))


def feed_time_ms(feed: bytes) -> int:
    """Returns the time of a GTFS-RT feed, from FeedMessage.header (1), FeedHeader.timestamp (3)."""
    header = next(value for number, value in gtfs_rt.fields(feed) if number == 1)
    seconds = next(value for number, value in gtfs_rt.message(header) if number == 3)
    assert isinstance(seconds, int)
    return seconds * 1000


def test_the_real_widget_scores_a_recorded_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # bun is installed beside the interpreter running the tests
    monkeypatch.setenv(
        "PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"
    )
    feed = (FIXTURES / "tripupdate.pb").read_bytes()
    now = feed_time_ms(feed) + 2000
    polls = [poll("tripupdate", now, feed)]
    for station in ("WOAK", "EMBR"):
        polls.append(
            poll(
                "etd",
                now,
                (FIXTURES / f"etd-{station}.json").read_bytes(),
                subject=station,
            )
        )
    for pair in ("WOAK-EMBR", "EMBR-WOAK"):
        polls.append(
            poll(
                "depart",
                now,
                (FIXTURES / f"depart-{pair}.json").read_bytes(),
                subject=pair,
            )
        )
    corpus = corpus_of(tmp_path, polls)
    with closing(sqlite3.connect(corpus)) as db:
        records = rp.replay_refreshes(db, [now], ENGINE, 0)
    (replayed,) = records
    assert replayed["commute"] == "EMBR WOAK"
    assert replayed["thrown"] is None
    assert replayed["harnessErrors"] == []
    assert sorted(b["board"] for b in replayed["boards"]) == ["EMBR-WOAK", "WOAK-EMBR"]
    assert all(isinstance(b["trains"], list) for b in replayed["boards"])
    out = rp.Findings()
    trips = rp.trips_by_board(records, {}, now // 1000)
    rp.evaluate(replayed, {}, now // 1000, trips, out)
    assert (out.records, out.boards, not out.errors) == (1, 2, True)
