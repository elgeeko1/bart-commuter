# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for the bart-corpus command line, end to end on a small corpus."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from bart_corpus import archive, audit, cli, core, recorder, store
from bart_corpus.timetable import ServiceSpan
from corpus_support import ms, poll


@pytest.mark.parametrize(
    "command",
    [
        "record",
        "status",
        "audit",
        "prune",
        "capture",
        "export",
        "pack",
        "unpack",
        "verify",
        "replay",
        "compare",
        "meets",
        "watch",
    ],
)
def test_every_command_documents_itself(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main([command, "--help"])
    assert raised.value.code == 0
    assert capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["record", "corpus", "--pairs", "woak-embr"],
        ["record", "corpus", "--interval", "0"],
        ["pack", "corpus", "yesterday"],
    ],
)
def test_invalid_arguments_exit_with_usage(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(argv)
    assert raised.value.code == 2


def test_status_on_a_missing_corpus_fails_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["status", str(tmp_path / "absent")]) == 1
    assert capsys.readouterr().err


def test_prune_lists_without_deleting_unless_asked(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    # a recording of a single tick long ago is incomplete
    tick = ms("2026-01-05T12:00:00")
    writer = store.Store(corpus)
    writer.write([poll("tripupdate", tick)])
    writer.close()
    recording = corpus / "2026-01-05.sqlite"

    assert cli.main(["prune", str(corpus)]) == 0
    assert recording.exists()
    assert cli.main(["prune", str(corpus), "--delete"]) == 0
    assert not recording.exists()


def test_pack_archives_finished_days_and_status_reports_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = tmp_path / "corpus"
    writer = store.Store(corpus)
    writer.write([poll("tripupdate", ms("2026-01-05T12:00:00"))])
    writer.close()
    assert cli.main(["pack", str(corpus)]) == 0
    assert not (corpus / "2026-01-05.sqlite").exists()
    assert cli.main(["verify", str(corpus)]) == 0
    capsys.readouterr()
    assert cli.main(["status", str(corpus)]) == 0
    assert "2026-01-05" in capsys.readouterr().out


DAY = "2026-01-05"
SETTINGS = recorder.WidgetSettings(
    api_key="K", refresh_s=60, request_timeout_s=15, trips_planned=4, trips_before=1
)


def no_polls(tick: int) -> list[store.Poll]:
    return []


def no_span(day: str) -> ServiceSpan | None:
    return None


class FakeRecorder:
    """Replaces what the record command assembles, keeping what it was given."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.settings: recorder.WidgetSettings | None = None
        self.pairs: Sequence[str] = ()
        self.stations: Sequence[str] = ()
        self.timetable_timeout_s: int | None = None
        self.jobs = [recorder.Job("daily", None, no_polls)]
        self.started: tuple[Path, Sequence[recorder.Job], object] | None = None
        monkeypatch.setattr(cli, "engine_settings", lambda: SETTINGS)
        monkeypatch.setattr(cli, "build_jobs", self.build_jobs)
        monkeypatch.setattr(cli, "timetable_source", self.timetable_source)
        monkeypatch.setattr(cli, "record_service_days", self.record_service_days)

    def build_jobs(
        self,
        settings: recorder.WidgetSettings,
        pairs: Sequence[str],
        stations: Sequence[str],
    ) -> list[recorder.Job]:
        self.settings, self.pairs, self.stations = settings, pairs, stations
        return self.jobs

    def timetable_source(self, timeout_s: int) -> Callable[[str], ServiceSpan | None]:
        self.timetable_timeout_s = timeout_s
        return no_span

    def record_service_days(
        self,
        corpus: Path,
        jobs: Sequence[recorder.Job],
        timetable: Callable[[str], ServiceSpan | None],
    ) -> None:
        self.started = (corpus, jobs, timetable)


def test_record_defaults_to_the_settings_of_the_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRecorder(monkeypatch)
    assert cli.main(["record", str(tmp_path)]) == 0
    assert fake.settings == SETTINGS
    assert list(fake.pairs) == list(recorder.DEFAULT_PAIRS)
    assert list(fake.stations) == []
    assert fake.timetable_timeout_s == SETTINGS.request_timeout_s
    assert fake.started == (tmp_path, fake.jobs, no_span)


def test_record_options_override_the_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRecorder(monkeypatch)
    argv = ["record", str(tmp_path), "--interval", "5", "--key", "MINE"]
    argv += ["--pairs", "ROCK-FRMT", "--stations", "SFIA"]
    assert cli.main(argv) == 0
    assert fake.settings == recorder.WidgetSettings(
        api_key="MINE",
        refresh_s=5,
        request_timeout_s=15,
        trips_planned=4,
        trips_before=1,
    )
    assert list(fake.pairs) == ["ROCK-FRMT"]
    assert list(fake.stations) == ["SFIA"]


@pytest.mark.parametrize(
    ("options", "warnings"),
    [(["--key", ""], 1), ([], 0)],
    ids=["empty-key", "script-key"],
)
def test_record_warns_only_when_recording_without_a_key(
    options: list[str],
    warnings: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    FakeRecorder(monkeypatch)
    assert cli.main(["record", str(tmp_path), *options]) == 0
    logged = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(logged) == warnings


def write_recording(corpus: Path, tick: int) -> None:
    writer = store.Store(corpus)
    writer.write([poll("tripupdate", tick)])
    writer.close()


def test_audit_json_gives_the_problems_of_each_day(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    gap = audit.Problem(audit.ProblemKind.GAP, "600 s without a refresh")
    audits = [
        audit.DayAudit("2026-01-05", tmp_path / "2026-01-05.sqlite", [gap]),
        audit.DayAudit("2026-01-06", tmp_path / "2026-01-06.sqlite"),
    ]
    monkeypatch.setattr(cli, "audit", lambda *_: audits)
    assert cli.main(["audit", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == [
        {
            "day": "2026-01-05",
            "complete": False,
            "problems": [{"kind": "gap", "detail": "600 s without a refresh"}],
        },
        {"day": "2026-01-06", "complete": True, "problems": []},
    ]


def test_audit_reports_each_problem_of_a_recording(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = tmp_path / "corpus"
    write_recording(corpus, ms("2026-01-05T12:00:00"))
    assert cli.main(["audit", str(corpus), "--json"]) == 0
    [row] = json.loads(capsys.readouterr().out)
    assert row["day"] == DAY
    assert not row["complete"]
    assert row["problems"]

    assert cli.main(["audit", str(corpus)]) == 0
    out = capsys.readouterr().out
    assert DAY in out
    for problem in row["problems"]:
        assert problem["kind"] in out
        assert problem["detail"] in out


def test_capture_writes_into_the_directory_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    targets: list[Path] = []
    monkeypatch.setattr(cli, "capture", targets.append)
    assert cli.main(["capture", str(tmp_path)]) == 0
    assert targets == [tmp_path]


def test_export_prints_the_snapshot_of_the_moment_asked_for(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[Path, int, Path]] = []
    snapshot: dict[str, Any] = {"tick": 7, "sources": ["etd", "tripupdate"]}

    def export(corpus: Path, at_ms: int, out: Path) -> dict[str, Any]:
        calls.append((corpus, at_ms, out))
        return snapshot

    monkeypatch.setattr(cli, "export", export)
    corpus, out = tmp_path / "corpus", tmp_path / "out"
    argv = ["export", str(corpus), "2026-11-06T17:00:00", str(out)]
    assert cli.main(argv) == 0
    # a time without an offset is BART time
    assert calls == [(corpus, core.parse_time("2026-11-06T17:00:00"), out)]
    assert json.loads(capsys.readouterr().out) == snapshot


def test_pack_fails_and_keeps_a_recording_it_cannot_archive(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    recording = corpus / f"{DAY}.sqlite"
    # a recording without polls cannot be archived
    store.open_db(recording).close()
    assert cli.main(["pack", str(corpus)]) == 1
    assert DAY in capsys.readouterr().err
    assert recording.exists()


def test_unpack_restores_an_archived_day(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = tmp_path / "corpus"
    tick = ms("2026-01-05T12:00:00")
    write_recording(corpus, tick)
    assert cli.main(["pack", str(corpus)]) == 0
    capsys.readouterr()
    out = tmp_path / "restored"
    assert cli.main(["unpack", str(corpus), DAY, str(out)]) == 0
    restored = out / f"{DAY}.sqlite"
    assert capsys.readouterr().out.strip() == str(restored)
    with closing(sqlite3.connect(restored)) as db:
        assert db.execute("SELECT source, tick FROM poll").fetchall() == [
            ("tripupdate", tick)
        ]


def test_verify_fails_on_a_damaged_archive_and_names_the_day(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = tmp_path / "corpus"
    write_recording(corpus, ms("2026-01-05T12:00:00"))
    assert cli.main(["pack", str(corpus)]) == 0
    capsys.readouterr()
    archive.archive_file(corpus / archive.ARCHIVE_DIR, DAY).write_bytes(b"garbage")
    assert cli.main(["verify", str(corpus), DAY]) == 1
    captured = capsys.readouterr()
    assert DAY in captured.out
    assert captured.err
