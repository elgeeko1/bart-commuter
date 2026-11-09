# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for the periodic replay of corpus days."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, tzinfo
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO, NoReturn, Self

import pytest

from bart_corpus import archive, core, store, watch
from corpus_support import ms, poll

DAY = "2026-11-06"
TICK = ms("2026-11-06T12:00:00")


class FinishedProcess:
    def wait(self) -> int:
        return 0


class FailedProcess:
    def wait(self) -> int:
        return 1


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> Self:
        return cls(2026, 11, 6, 17, 30)


def ticks_in(path: Path) -> list[int]:
    with closing(sqlite3.connect(path)) as db:
        return [tick for (tick,) in db.execute("SELECT tick FROM poll ORDER BY id")]


def test_snapshot_copies_committed_polls_of_a_recording_still_open(
    tmp_path: Path,
) -> None:
    writer = store.Store(tmp_path / "corpus")
    writer.write([poll("tripupdate", TICK)])
    target = tmp_path / "copy.sqlite"
    watch.snapshot(tmp_path / "corpus" / f"{DAY}.sqlite", target)
    writer.close()
    assert ticks_in(target) == [TICK]


@pytest.mark.parametrize(
    ("since", "extra"),
    [
        (None, []),
        ("2026-11-06T12:00:00", ["--since", "2026-11-06T12:00:00"]),
    ],
    ids=["whole-day", "from-a-time"],
)
def test_replay_day_runs_the_replay_and_keeps_its_output(
    since: str | None,
    extra: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    process = FinishedProcess()

    def popen(
        command: list[str], stdout: BinaryIO, stderr: BinaryIO
    ) -> FinishedProcess:
        commands.append(command)
        stdout.write(b"rider view")
        stderr.write(b"diagnostics")
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    file = tmp_path / f"{DAY}.sqlite"
    script = watch.Script("new", Path("new.js"), 2)
    started: object = watch.replay_day(file, tmp_path, DAY, script, since)
    assert started is process
    assert commands == [
        [
            sys.executable,
            "-m",
            "bart_corpus",
            "replay",
            str(file),
            "--script",
            "new.js",
            "--report",
            str(tmp_path / f"{DAY}-new.json"),
            "--drop-first-trips",
            "2",
            *extra,
        ]
    ]
    assert (tmp_path / f"{DAY}-new.txt").read_bytes() == b"rider view"
    assert (tmp_path / f"{DAY}-new.err").read_bytes() == b"diagnostics"


def test_a_live_day_is_compared_from_a_snapshot_that_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "corpus"
    writer = store.Store(corpus)
    writer.write([poll("tripupdate", TICK)])
    writer.close()
    out = tmp_path / "out"
    out.mkdir()
    replayed: list[tuple[str, list[int], str | None]] = []

    def replay_day(
        file: Path, _out: Path, _day: str, script: watch.Script, since: str | None
    ) -> FinishedProcess:
        replayed.append((script.label, ticks_in(file), since))
        return FinishedProcess()

    def merged_view(reports: list[Path]) -> dict[str, str]:
        return {"report": reports[0].name}

    monkeypatch.setattr(watch, "replay_day", replay_day)
    monkeypatch.setattr(watch, "merged_view", merged_view)
    monkeypatch.setattr(watch, "table", ",".join)
    monkeypatch.setattr(watch, "datetime", FixedDatetime)
    scripts = [
        watch.Script("new", Path("new.js"), 0),
        watch.Script("old", Path("old.js"), 0),
    ]
    since = "2026-11-06T12:00:00"
    watch.compare_day(corpus, out, DAY, scripts, since, final=False)

    assert replayed == [("new", [TICK], since), ("old", [TICK], since)]
    compare = out / f"{DAY}-1730-compare.txt"
    assert compare.read_text(encoding="utf-8") == "new,old\n"
    assert not (out / f"{DAY}-final-compare.txt").exists()
    # the copy stays for the next comparison and the recording is untouched
    assert (out / f"{DAY}.sqlite").exists()
    assert ticks_in(corpus / f"{DAY}.sqlite") == [TICK]
    lines = (out / "history.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [
        {
            "day": DAY,
            "at": "2026-11-06T17:30",
            "final": False,
            "new": {"report": f"{DAY}-new.json"},
            "old": {"report": f"{DAY}-old.json"},
        }
    ]


def test_a_failed_replay_writes_no_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "corpus"
    writer = store.Store(corpus)
    writer.write([poll("tripupdate", TICK)])
    writer.close()
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(watch, "replay_day", lambda *_: FailedProcess())
    script = watch.Script("new", Path("new.js"), 0)
    with pytest.raises(core.CorpusError):
        watch.compare_day(corpus, out, DAY, [script], None, final=True)
    assert list(out.glob("*-compare.txt")) == []
    assert not (out / "history.jsonl").exists()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("new=new.js", ("new", "new.js")),
        ("new=a=b.js", ("new", "a=b.js")),
    ],
)
def test_parse_assignment_splits_at_the_first_equals(
    text: str, expected: tuple[str, str]
) -> None:
    assert watch.parse_assignment(text) == expected


@pytest.mark.parametrize("text", ["", "new", "=new.js", "new="])
def test_parse_assignment_rejects_anything_else(text: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        watch.parse_assignment(text)


class StopWatching(Exception):
    pass


class WatchRun:
    """Parses a watch command, then runs one pass of it with the comparison of each day stubbed."""

    def __init__(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, now: str
    ) -> None:
        self.corpus = tmp_path / "corpus"
        self.out = tmp_path / "work" / "out"
        (self.corpus / archive.ARCHIVE_DIR).mkdir(parents=True)
        self.compared: list[tuple[str, str | None, bool]] = []
        self.scripts: list[list[watch.Script]] = []
        self.slept: list[float] = []
        self.failures: dict[str, Exception] = {}
        monkeypatch.setattr(watch, "now_ms", lambda: ms(now))
        monkeypatch.setattr(watch, "compare_day", self.compare_day)
        monkeypatch.setattr(watch, "time", SimpleNamespace(sleep=self.sleep))

    def live(self, *days: str) -> None:
        for day in days:
            (self.corpus / f"{day}.sqlite").touch()

    def archived(self, *days: str) -> None:
        for day in days:
            archive.archive_file(self.corpus / archive.ARCHIVE_DIR, day).touch()

    def compare_day(
        self,
        corpus: Path,
        out: Path,
        day: str,
        scripts: list[watch.Script],
        since: str | None,
        final: bool,
    ) -> None:
        assert (corpus, out) == (self.corpus, self.out)
        self.compared.append((day, since, final))
        self.scripts.append(scripts)
        if day in self.failures:
            raise self.failures[day]

    def sleep(self, seconds: float) -> NoReturn:
        self.slept.append(seconds)
        raise StopWatching

    def run_once(self, *options: str) -> None:
        parser = argparse.ArgumentParser()
        watch.configure(parser)
        args = parser.parse_args(
            [
                str(self.corpus),
                "--out",
                str(self.out),
                "--script",
                "new=new.js",
                "--script",
                "old=old.js",
                *options,
            ]
        )
        with pytest.raises(StopWatching):
            watch.run(args)


def test_watch_compares_each_unfinished_day_and_then_sleeps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = WatchRun(tmp_path, monkeypatch, "2026-11-07T12:00:00")
    run.live("2026-11-03", "2026-11-05", "2026-11-06", "2026-11-07")
    run.archived("2026-11-04")
    run.out.mkdir(parents=True)
    (run.out / "2026-11-06-final-compare.txt").touch()
    run.run_once(
        "--first-day",
        "2026-11-04",
        "--since",
        "2026-11-05=2026-11-05T12:00:00",
        "--drop-first-trips",
        "new=2",
        "--interval-hours",
        "2",
    )
    assert run.compared == [
        # archived, so over
        ("2026-11-04", None, True),
        # still a live file, but an earlier service day
        ("2026-11-05", "2026-11-05T12:00:00", True),
        # the service day in progress
        ("2026-11-07", None, False),
    ]
    assert run.scripts[0] == [
        watch.Script("new", Path("new.js"), 2),
        watch.Script("old", Path("old.js"), 0),
    ]
    assert run.slept == [2 * watch.HOUR_S]


def test_watch_creates_its_output_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = WatchRun(tmp_path, monkeypatch, "2026-11-07T12:00:00")
    run.run_once()
    assert run.out.is_dir()
    assert run.compared == []


@pytest.mark.parametrize(
    "failure",
    [
        core.CorpusError("replay failed"),
        OSError("disk full"),
        ValueError("bad report"),
        KeyError("trips"),
        sqlite3.DatabaseError("malformed"),
    ],
    ids=["corpus", "os", "value", "key", "sqlite"],
)
def test_a_day_that_fails_is_logged_and_the_next_day_is_still_compared(
    failure: Exception,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    run = WatchRun(tmp_path, monkeypatch, "2026-11-07T12:00:00")
    run.live("2026-11-05", "2026-11-06")
    run.failures["2026-11-05"] = failure
    run.run_once()
    assert [day for day, _, _ in run.compared] == ["2026-11-05", "2026-11-06"]
    assert [r.levelname for r in caplog.records if r.name == watch.__name__] == [
        "ERROR"
    ]


def test_an_archived_day_is_replayed_and_its_copy_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "corpus"
    writer = store.Store(corpus)
    writer.write([poll("tripupdate", ms("2026-11-06T12:00:00"))])
    writer.close()
    archive.close_out(corpus, "2026-11-07")
    out = tmp_path / "out"
    out.mkdir()
    replayed: list[bool] = []

    def replay_day(file: Path, *_: object) -> FinishedProcess:
        replayed.append(file.exists())
        return FinishedProcess()

    monkeypatch.setattr(watch, "replay_day", replay_day)
    monkeypatch.setattr(watch, "merged_view", lambda _: {})
    monkeypatch.setattr(watch, "table", lambda _: "table")
    script = watch.Script("new", Path("new.js"), 0)
    watch.compare_day(corpus, out, DAY, [script], None, final=True)
    assert replayed == [True]
    assert (out / f"{DAY}-final-compare.txt").exists()
    assert not (out / f"{DAY}.sqlite").exists()
