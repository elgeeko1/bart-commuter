# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for what the recorder requests and records; they make requests only to a local server."""

from __future__ import annotations

import concurrent.futures as cf
import dataclasses
import http.client
import io
import json
import logging
import socket
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import closing
from email.message import Message
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import TCPServer
from typing import NamedTuple, NoReturn

import pytest

from bart_corpus import archive, core, recorder, store
from bart_corpus.timetable import ServiceSpan
from corpus_support import gtfs_feed, ms, plan, poll

WIDGET_SOURCE = """
const API_KEY = "K";
const TRIPS_PLANNED = 4;
const TRIPS_BEFORE = 1;
const RUN_DEADLINE_MS = 15 * 1000;
const REFRESH_MS = 60 * 1000;
"""


def test_widget_settings_reads_the_script_constants() -> None:
    assert recorder.widget_settings(WIDGET_SOURCE) == recorder.WidgetSettings(
        api_key="K",
        refresh_s=60,
        request_timeout_s=15,
        trips_planned=4,
        trips_before=1,
    )


def test_widget_settings_rejects_a_missing_constant() -> None:
    source = WIDGET_SOURCE.replace("const REFRESH_MS = 60 * 1000;", "")
    with pytest.raises(core.CorpusError):
        recorder.widget_settings(source)


def test_widget_settings_reads_the_widget_script() -> None:
    settings = recorder.widget_settings(core.ENGINE.read_text(encoding="utf-8"))
    assert (
        min(settings.refresh_s, settings.request_timeout_s, settings.trips_planned) > 0
    )


def test_refresh_requests_what_the_widget_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[tuple[str, str, str, int, float]] = []
    plans = {
        "WOAK-EMBR": plan([("WOAK", "EMBR")]),
        "ROCK-FRMT": plan([("ROCK", "FRMT")], [("ROCK", "MCAR"), ("MCAR", "FRMT")]),
    }

    def fake_fetch(
        source: str, subject: str, url: str, tick: int, timeout_s: float
    ) -> store.Poll:
        requested.append((source, subject, url, tick, timeout_s))
        return poll(source, tick, plans.get(subject, b"x"), subject=subject)

    monkeypatch.setattr(recorder, "fetch", fake_fetch)
    settings = recorder.widget_settings(WIDGET_SOURCE)
    jobs = {
        job.name: job
        for job in recorder.build_jobs(settings, ["WOAK-EMBR", "ROCK-FRMT"], ["SFIA"])
    }
    assert {name: job.interval_s for name, job in jobs.items()} == {
        "refresh": 60,
        "context": 60,
        "daily": None,
        "elevators": 300,
    }

    polls = jobs["refresh"].run(1000)
    assert sorted((s, subj, tick, t) for s, subj, _, tick, t in requested) == [
        ("depart", "ROCK-FRMT", 1000, 15),
        ("depart", "WOAK-EMBR", 1000, 15),
        ("etd", "ALL", 1000, 15),
        ("etd", "EMBR", 1000, 15),
        ("etd", "FRMT", 1000, 15),
        ("etd", "MCAR", 1000, 15),
        ("etd", "ROCK", 1000, 15),
        ("etd", "SFIA", 1000, 15),
        ("etd", "WOAK", 1000, 15),
        ("tripupdate", "", 1000, 15),
    ]
    # the ETD of the transfer station follows the trip plans that name it
    assert [(p.source, p.subject) for p in polls][-1] == ("etd", "MCAR")
    urls = {(source, subject): url for source, subject, url, _, _ in requested}
    assert (
        "sched.aspx?cmd=depart&orig=WOAK&dest=EMBR&b=1&a=4&key=K&json=y"
        in (urls["depart", "WOAK-EMBR"])
    )
    assert "etd.aspx?cmd=etd&orig=WOAK&key=K&json=y" in urls["etd", "WOAK"]


def test_refresh_skips_transfer_stations_of_failed_or_unexpected_trip_plans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []
    via_mcar = plan([("ROCK", "MCAR"), ("MCAR", "FRMT")])
    responses = {
        "ROCK-FRMT": poll("depart", 0, via_mcar, subject="ROCK-FRMT", status=500),
        "FRMT-ROCK": poll("depart", 0, b"{}", subject="FRMT-ROCK"),
    }

    def fake_fetch(
        source: str, subject: str, url: str, tick: int, timeout_s: float
    ) -> store.Poll:
        requested.append(subject)
        return responses.get(subject, poll(source, tick, subject=subject))

    monkeypatch.setattr(recorder, "fetch", fake_fetch)
    settings = recorder.widget_settings(WIDGET_SOURCE)
    (refresh, *_) = recorder.build_jobs(settings, ["ROCK-FRMT", "FRMT-ROCK"], [])
    refresh.run(1000)
    assert "MCAR" not in requested


def test_leg_origins_include_both_ends_of_a_change() -> None:
    # the planner gives the terminal of the Oakland Airport line as both ends of its leg
    to_airport = plan([("EMBR", "COLS"), ("OAKL", "OAKL")])
    from_airport = plan([("OAKL", "OAKL"), ("COLS", "EMBR")])
    assert recorder.leg_origins(to_airport) == {"EMBR", "COLS", "OAKL"}
    assert recorder.leg_origins(from_airport) == {"OAKL", "COLS"}


def test_leg_origins_reads_a_lone_trip() -> None:
    response = json.loads(plan([("ROCK", "MCAR"), ("MCAR", "FRMT")]))
    request = response["root"]["schedule"]["request"]
    request["trip"] = request["trip"][0]
    assert recorder.leg_origins(json.dumps(response).encode()) == {"ROCK", "MCAR"}


def test_jobs_without_a_key_skip_the_legacy_api() -> None:
    settings = recorder.WidgetSettings(
        api_key="",
        refresh_s=60,
        request_timeout_s=10,
        trips_planned=4,
        trips_before=1,
    )
    names = [job.name for job in recorder.build_jobs(settings, ["WOAK-EMBR"], [])]
    assert names == ["refresh", "context", "daily"]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        code, body = (
            (400, b'{"error":"Invalid key"}') if self.path == "/bad" else (200, b"ok")
        )
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    # HTTPServer would look up the host name, which can take seconds
    httpd = TCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_fetch_records_body_and_status(server: str) -> None:
    p = recorder.fetch("etd", "ALL", f"{server}/ok", 7, 5)
    assert (p.source, p.subject, p.tick, p.status, p.body, p.error) == (
        "etd",
        "ALL",
        7,
        200,
        b"ok",
        None,
    )
    assert p.ok


def test_fetch_records_http_error_body(server: str) -> None:
    p = recorder.fetch("etd", "ALL", f"{server}/bad", 7, 5)
    assert (p.status, p.body, p.error) == (400, b'{"error":"Invalid key"}', None)
    assert not p.ok


def test_fetch_records_transport_error() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    p = recorder.fetch("tripupdate", "", f"http://127.0.0.1:{port}/", 7, 5)
    assert p.status is None and p.body is None
    assert p.error is not None


LAST_ARRIVAL = ms("2026-11-07T02:30:00")
FIRST_FRIDAY_DEPARTURE = ms("2026-11-06T05:00:00")
FIRST_SATURDAY_DEPARTURE = ms("2026-11-07T06:00:00")
SPANS = {
    "2026-11-06": ServiceSpan(FIRST_FRIDAY_DEPARTURE, LAST_ARRIVAL),
    "2026-11-07": ServiceSpan(FIRST_SATURDAY_DEPARTURE, ms("2026-11-08T02:30:00")),
    "2026-11-08": None,
}
MINUTE = 60_000


def board(minutes: str) -> bytes:
    estimate = {"minutes": minutes}
    station = {"abbr": "MLPT", "etd": [{"estimate": [estimate]}]}
    return json.dumps({"root": {"time": minutes, "station": [station]}}).encode()


def board_poll(tick: int, minutes: str) -> store.Poll:
    return poll("etd", tick, board(minutes), subject=recorder.ETD_ALL)


def test_service_ends_once_the_board_stops_changing_after_the_last_arrival() -> None:
    end = recorder.EndOfService(LAST_ARRIVAL)
    end.observe([board_poll(LAST_ARRIVAL - 5 * MINUTE, "2")])
    end.observe([board_poll(LAST_ARRIVAL - 4 * MINUTE, "Leaving")])
    frozen = LAST_ARRIVAL - 4 * MINUTE + recorder.BOARD_QUIET_MS
    assert not end.over(frozen - 1)
    assert end.over(frozen)


def test_a_frozen_board_before_the_last_arrival_is_not_the_end() -> None:
    end = recorder.EndOfService(LAST_ARRIVAL)
    end.observe([board_poll(LAST_ARRIVAL - 120 * MINUTE, "5")])
    assert not end.over(LAST_ARRIVAL - MINUTE)


def test_a_train_running_late_keeps_service_open() -> None:
    end = recorder.EndOfService(LAST_ARRIVAL)
    for i in range(30):
        end.observe([board_poll(LAST_ARRIVAL + i * MINUTE, str(30 - i))])
    assert not end.over(LAST_ARRIVAL + 30 * MINUTE)


def test_service_does_not_end_without_seeing_a_board() -> None:
    end = recorder.EndOfService(LAST_ARRIVAL)
    end.observe([poll("etd", LAST_ARRIVAL, b"not json", subject=recorder.ETD_ALL)])
    assert not end.over(LAST_ARRIVAL + 120 * MINUTE)


def test_recording_wakes_ahead_of_the_first_train_within_its_own_service_day() -> None:
    saturday = SPANS["2026-11-07"]
    assert recorder.wake_ms("2026-11-07", saturday) == (
        FIRST_SATURDAY_DEPARTURE - recorder.PRE_SERVICE_LEAD_MS
    )
    start_ms, _ = core.day_bounds_ms("2026-11-07")
    early = ServiceSpan(start_ms + MINUTE, LAST_ARRIVAL)
    assert recorder.wake_ms("2026-11-07", early) == start_ms
    assert recorder.wake_ms("2026-11-07", None) == start_ms


def test_poll_service_stops_after_the_last_train(tmp_path: Path) -> None:
    day = core.service_day(core.now_ms())
    runs: list[int] = []

    def run(tick: int) -> list[store.Poll]:
        runs.append(tick)
        return [poll("tripupdate", tick)]

    class EndsAfterOneRun(recorder.EndOfService):
        def over(self, epoch_ms: int) -> bool:
            return bool(runs)

    corpus_store = store.Store(tmp_path)
    recorder.poll_service(
        corpus_store, [recorder.Job("daily", None, run)], day, EndsAfterOneRun(0)
    )
    corpus_store.close()
    assert len(runs) == 1
    assert (tmp_path / f"{day}.sqlite").exists()


def test_poll_service_waits_without_a_timeout_on_a_daily_job_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = core.service_day(core.now_ms())
    release = threading.Event()
    runs: list[int] = []
    timeouts: list[float | None] = []
    real_wait = cf.wait

    def run(tick: int) -> list[store.Poll]:
        release.wait()
        runs.append(tick)
        return [poll("tripupdate", tick)]

    # the job stays in flight until poll_service has chosen its timeout
    def wait(
        futures: Iterable[cf.Future[list[store.Poll]]],
        timeout: float | None,
        return_when: str,
    ) -> tuple[set[cf.Future[list[store.Poll]]], set[cf.Future[list[store.Poll]]]]:
        timeouts.append(timeout)
        release.set()
        return real_wait(futures, timeout, return_when)

    class EndsAfterOneRun(recorder.EndOfService):
        def over(self, epoch_ms: int) -> bool:
            return bool(runs)

    monkeypatch.setattr(cf, "wait", wait)
    corpus_store = store.Store(tmp_path)
    recorder.poll_service(
        corpus_store, [recorder.Job("daily", None, run)], day, EndsAfterOneRun(0)
    )
    corpus_store.close()
    assert timeouts == [None]
    assert len(runs) == 1


def test_poll_service_stops_when_the_service_day_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never_run(tick: int) -> list[store.Poll]:
        raise AssertionError("ran after the service day changed")

    monkeypatch.setattr(recorder, "now_ms", lambda: ms("2026-11-07T03:00:00"))
    corpus_store = store.Store(tmp_path)
    recorder.poll_service(
        corpus_store, [recorder.Job("daily", None, never_run)], "2026-11-06", None
    )
    corpus_store.close()
    assert list(tmp_path.iterdir()) == []


class FakeHost:
    """Replaces the clock, sleeping and polling of the recorder; stops after a set number of sleeps."""

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, start: int, sleeps: int
    ) -> None:
        self.now = start
        self.sleeps_left = sleeps
        self.woke_at: list[int] = []
        self.recorded: list[str] = []
        monkeypatch.setattr(recorder, "now_ms", lambda: self.now)
        monkeypatch.setattr(recorder, "sleep_until", self.sleep_until)
        monkeypatch.setattr(recorder, "poll_service", self.poll_service)
        monkeypatch.setattr("signal.signal", lambda *_: None)

    def sleep_until(self, epoch_ms: int) -> None:
        self.woke_at.append(epoch_ms)
        self.sleeps_left -= 1
        if not self.sleeps_left:
            raise KeyboardInterrupt
        self.now = epoch_ms

    def poll_service(
        self,
        corpus_store: store.Store,
        jobs: Sequence[recorder.Job],
        day: str,
        end: recorder.EndOfService | None,
    ) -> None:
        corpus_store.write([poll("tripupdate", self.now)])
        self.recorded.append(day)
        self.now = SPANS[day].last_arrival_ms + 20 * MINUTE  # type: ignore[union-attr]


def test_each_service_day_is_closed_before_idling_until_the_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = FakeHost(monkeypatch, ms("2026-11-06T12:00:00"), sleeps=2)
    recorder.record_service_days(tmp_path, [], SPANS.__getitem__)
    assert host.recorded == ["2026-11-06", "2026-11-07"]
    assert host.woke_at == [
        FIRST_SATURDAY_DEPARTURE - recorder.PRE_SERVICE_LEAD_MS,
        core.day_bounds_ms("2026-11-08")[0],
    ]
    assert store.corpus_files(tmp_path) == []
    assert archive.archived_days(tmp_path / archive.ARCHIVE_DIR) == [
        "2026-11-06",
        "2026-11-07",
    ]


def test_starting_before_service_idles_until_the_first_train_nears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = FakeHost(monkeypatch, ms("2026-11-07T03:30:00"), sleeps=1)
    recorder.record_service_days(tmp_path, [], SPANS.__getitem__)
    assert host.recorded == []
    assert host.woke_at == [FIRST_SATURDAY_DEPARTURE - recorder.PRE_SERVICE_LEAD_MS]


def test_starting_after_the_last_train_records_nothing_for_that_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = FakeHost(monkeypatch, LAST_ARRIVAL + 5 * MINUTE, sleeps=1)
    recorder.record_service_days(tmp_path, [], SPANS.__getitem__)
    assert host.recorded == []
    assert list(tmp_path.iterdir()) == []


def test_a_recording_left_by_an_earlier_day_is_archived_on_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus_store = store.Store(tmp_path)
    corpus_store.write([poll("tripupdate", ms("2026-11-05T12:00:00"))])
    corpus_store.close()
    FakeHost(monkeypatch, ms("2026-11-07T03:30:00"), sleeps=1)
    recorder.record_service_days(tmp_path, [], SPANS.__getitem__)
    assert archive.archived_days(tmp_path / archive.ARCHIVE_DIR) == ["2026-11-05"]


def test_widget_settings_read_the_shipped_script() -> None:
    settings = recorder.widget_settings(core.ENGINE.read_text(encoding="utf-8"))
    assert settings.trips_planned > 0


def test_idling_before_service_ends_in_recording_that_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = FakeHost(monkeypatch, ms("2026-11-07T03:30:00"), sleeps=2)
    recorder.record_service_days(tmp_path, [], SPANS.__getitem__)
    assert host.recorded == ["2026-11-07"]
    assert host.woke_at == [
        FIRST_SATURDAY_DEPARTURE - recorder.PRE_SERVICE_LEAD_MS,
        core.day_bounds_ms("2026-11-08")[0],
    ]


class FailingBody(io.BytesIO):
    """A response body that fails when read."""

    def __init__(self, failure: Exception) -> None:
        super().__init__()
        self.failure = failure

    def read(self, size: int | None = -1, /) -> bytes:
        raise self.failure


@pytest.mark.parametrize(
    "failure",
    [ConnectionResetError("reset"), http.client.IncompleteRead(b"")],
)
def test_fetch_keeps_the_status_when_an_error_body_cannot_be_read(
    failure: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = urllib.error.HTTPError(
        "http://bart.test/", 503, "unavailable", Message(), FailingBody(failure)
    )

    def urlopen(request: urllib.request.Request, timeout: float) -> NoReturn:
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    p = recorder.fetch("etd", "ALL", "http://bart.test/", 7, 5)
    assert (p.status, p.body) == (503, None)
    assert p.error is not None
    assert not p.ok


class Requested(NamedTuple):
    source: str
    subject: str
    url: str
    tick: int
    timeout_s: float


def stub_fetch(
    monkeypatch: pytest.MonkeyPatch, responses: Mapping[str, store.Poll] | None = None
) -> list[Requested]:
    """Answers each request from responses by source, or with success; returns the requests made."""
    requested: list[Requested] = []
    answers = responses or {}

    def fake_fetch(
        source: str, subject: str, url: str, tick: int, timeout_s: float
    ) -> store.Poll:
        requested.append(Requested(source, subject, url, tick, timeout_s))
        return answers.get(source, poll(source, tick, subject=subject))

    monkeypatch.setattr(recorder, "fetch", fake_fetch)
    return requested


def query(url: str) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


SETTINGS = recorder.widget_settings(WIDGET_SOURCE)
KEYLESS = dataclasses.replace(SETTINGS, api_key="")


def jobs_of(settings: recorder.WidgetSettings) -> dict[str, recorder.Job]:
    return {job.name: job for job in recorder.build_jobs(settings, ["WOAK-EMBR"], [])}


def test_context_requests_alerts_and_advisories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = stub_fetch(monkeypatch)
    polls = jobs_of(SETTINGS)["context"].run(1000)
    assert [p.source for p in polls] == ["alerts", "bsa", "count"]
    advisories = {r.source: query(r.url) for r in requested if r.source != "alerts"}
    assert advisories == {
        "bsa": {"cmd": "bsa", "key": "K", "json": "y"},
        "count": {"cmd": "count", "key": "K", "json": "y"},
    }
    assert {(r.tick, r.timeout_s) for r in requested} == {(1000, 15)}


def test_context_without_a_key_requests_only_alerts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = stub_fetch(monkeypatch)
    polls = jobs_of(KEYLESS)["context"].run(1000)
    assert [p.source for p in polls] == ["alerts"]
    assert [r.source for r in requested] == ["alerts"]


def test_elevators_requests_the_elevator_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = stub_fetch(monkeypatch)
    polls = jobs_of(SETTINGS)["elevators"].run(1000)
    assert [(p.source, p.tick) for p in polls] == [("elev", 1000)]
    assert query(requested[0].url) == {"cmd": "elev", "key": "K", "json": "y"}


def stations_body(*abbrs: str) -> bytes:
    stations = [{"abbr": abbr} for abbr in abbrs]
    return json.dumps({"root": {"stations": {"station": stations}}}).encode()


def test_daily_without_a_key_downloads_only_the_static_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = stub_fetch(monkeypatch)
    polls = jobs_of(KEYLESS)["daily"].run(1000)
    assert [p.source for p in polls] == ["gtfs-static"]
    assert [r.url for r in requested] == [recorder.STATIC_GTFS_URL]


def test_daily_downloads_each_station_timetable_for_the_service_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # past midnight, the service day is still the calendar day before
    tick = ms("2026-11-07T01:00:00")
    stns = poll("stns", tick, stations_body("ROCK", "WOAK"))
    requested = stub_fetch(monkeypatch, {"stns": stns})
    polls = jobs_of(SETTINGS)["daily"].run(tick)
    assert [(p.source, p.subject) for p in polls] == [
        ("gtfs-static", ""),
        ("stns", ""),
        ("stnsched", "ROCK"),
        ("stnsched", "WOAK"),
    ]
    assert {r.subject: query(r.url) for r in requested if r.source == "stnsched"} == {
        station: {
            "cmd": "stnsched",
            "orig": station,
            "date": "11/06/2026",
            "key": "K",
            "json": "y",
        }
        for station in ("ROCK", "WOAK")
    }


def test_daily_stops_when_the_station_list_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = poll("stns", 1000, b"down", status=500)
    requested = stub_fetch(monkeypatch, {"stns": failed})
    polls = jobs_of(SETTINGS)["daily"].run(1000)
    assert polls[-1] == failed
    assert "stnsched" not in {r.source for r in requested}


@pytest.mark.parametrize(
    "body",
    [b"not json", b"{}", b'{"root":{"stations":{"station":[1]}}}'],
    ids=["not-json", "missing-root", "station-not-an-object"],
)
def test_daily_counts_an_unexpected_station_list_as_a_failure(
    body: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested = stub_fetch(monkeypatch, {"stns": poll("stns", 1000, body)})
    polls = jobs_of(SETTINGS)["daily"].run(1000)
    # the response is kept for inspection and fails the run, so the job retries
    assert polls[-1].body == body
    assert polls[-1].error is not None
    assert not all(p.ok for p in polls)
    assert "stnsched" not in {r.source for r in requested}


class FakeClock:
    """Stands in for the time module of the recorder; sleeping advances it."""

    def __init__(self, start_s: float) -> None:
        self.now_s = start_s
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now_s

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now_s += seconds

    def now_ms(self) -> int:
        return round(self.now_s * 1000)


def install(monkeypatch: pytest.MonkeyPatch, clock: FakeClock) -> FakeClock:
    monkeypatch.setattr(recorder, "time", clock)
    monkeypatch.setattr(recorder, "now_ms", clock.now_ms)
    return clock


def no_polls(tick: int) -> list[store.Poll]:
    return []


START_S = ms("2026-11-06T12:00:00") // 1000
DAY = "2026-11-06"
NEXT_DAY_S = core.day_bounds_ms(DAY)[1] // 1000


def test_an_interval_job_waits_for_its_next_wall_clock_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, FakeClock(START_S + 10))
    job = recorder.Job("refresh", 60, no_polls)
    assert not job.due(START_S + 59)
    assert job.due(START_S + 60)
    assert job.tick(START_S + 119) == (START_S + 60) * 1000


def test_a_failed_run_does_not_reschedule_an_interval_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, FakeClock(START_S + 10))
    job = recorder.Job("refresh", 60, no_polls)
    job.finished([poll("tripupdate", 0, status=500)], START_S + 11)
    assert job.next_due == START_S + 60


def test_a_daily_job_rests_until_the_next_service_day_once_it_succeeds() -> None:
    job = recorder.Job("daily", None, no_polls)
    assert job.due(START_S)
    job.started(START_S)
    assert not job.due(START_S + 1)
    job.finished([poll("gtfs-static", START_S * 1000)], START_S + 1)
    assert job.done_day == DAY
    assert not job.due(START_S + 2)
    assert job.wake_at(START_S + 2) == START_S + 2 + recorder.MINUTE_S
    assert job.due(NEXT_DAY_S)


def test_a_failed_daily_job_retries_after_a_delay() -> None:
    job = recorder.Job("daily", None, no_polls)
    job.started(START_S)
    job.finished([poll("gtfs-static", START_S * 1000, status=500)], START_S + 1)
    retry_at = START_S + 1 + recorder.DAILY_RETRY_S
    assert job.done_day is None
    assert not job.due(retry_at - 1)
    assert job.due(retry_at)
    assert job.wake_at(START_S + 2) == retry_at


def recorded(corpus: Path, day: str) -> list[tuple[str, int, int | None, str | None]]:
    with closing(sqlite3.connect(corpus / f"{day}.sqlite")) as db:
        return db.execute(
            "SELECT source, tick, status, error FROM poll ORDER BY id"
        ).fetchall()


def warnings_of_recorder(caplog: pytest.LogCaptureFixture) -> int:
    return sum(
        r.levelno == logging.WARNING and r.name == recorder.__name__
        for r in caplog.records
    )


def test_failed_polls_are_stored_and_warned_about(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    install(monkeypatch, FakeClock(START_S))
    tick = START_S * 1000
    polls = [
        poll("gtfs-static", tick),
        poll("stns", tick, b"down", status=500),
        poll("stnsched", tick, None, status=None, error="timed out"),
    ]
    job = recorder.Job("daily", None, no_polls)
    corpus_store = store.Store(tmp_path)
    recorder.save_polls(corpus_store, job, polls)
    corpus_store.close()
    assert recorded(tmp_path, DAY) == [
        ("gtfs-static", tick, 200, None),
        ("stns", tick, 500, None),
        ("stnsched", tick, None, "timed out"),
    ]
    # one for each failed poll and one for the incomplete job
    assert warnings_of_recorder(caplog) == 3
    assert job.next_due == START_S + recorder.DAILY_RETRY_S


def test_sleep_until_waits_in_short_steps_until_the_time_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = install(monkeypatch, FakeClock(START_S))
    recorder.sleep_until((START_S + 150) * 1000)
    assert clock.sleeps == [recorder.IDLE_CHECK_S, recorder.IDLE_CHECK_S, 30]
    assert clock.now_s == START_S + 150


def test_sleep_until_a_past_time_does_not_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = install(monkeypatch, FakeClock(START_S))
    recorder.sleep_until((START_S - 1) * 1000)
    assert clock.sleeps == []


def test_sleep_until_stops_when_the_host_slept_past_the_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SuspendingClock(FakeClock):
        def sleep(self, seconds: float) -> None:
            super().sleep(seconds)
            self.now_s += 3600

    clock = install(monkeypatch, SuspendingClock(START_S))
    recorder.sleep_until((START_S + 150) * 1000)
    assert clock.sleeps == [recorder.IDLE_CHECK_S]


def test_timetable_source_reads_the_span_of_a_day_from_the_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feed = poll("gtfs-static", 0, gtfs_feed("05:00:00", "26:30:00"))
    requested = stub_fetch(monkeypatch, {"gtfs-static": feed})
    assert recorder.timetable_source(15)(DAY) == ServiceSpan(
        FIRST_FRIDAY_DEPARTURE, LAST_ARRIVAL
    )
    assert [(r.source, r.url, r.timeout_s) for r in requested] == [
        ("gtfs-static", recorder.STATIC_GTFS_URL, 15)
    ]


def zip_without_tables() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("agency.txt", "agency_id\nBART\n")
    return out.getvalue()


@pytest.mark.parametrize(
    "response",
    [
        poll("gtfs-static", 0, b"down", status=503),
        poll("gtfs-static", 0, None, status=None, error="timed out"),
        poll("gtfs-static", 0, b"not a zip"),
        poll("gtfs-static", 0, zip_without_tables()),
        poll("gtfs-static", 0, gtfs_feed("soon", "later")),
    ],
    ids=["http-error", "transport-error", "not-a-zip", "missing-table", "bad-time"],
)
def test_timetable_source_warns_and_gives_no_span_without_a_usable_feed(
    response: store.Poll,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    stub_fetch(monkeypatch, {"gtfs-static": response})
    assert recorder.timetable_source(15)(DAY) is None
    assert warnings_of_recorder(caplog) == 1


class EndsOnceSaved(recorder.EndOfService):
    """Ends service once the polls of a run have been saved."""

    def __init__(self) -> None:
        super().__init__(0)
        self.saved = False

    def observe(self, polls: Sequence[store.Poll]) -> None:
        self.saved = True

    def over(self, epoch_ms: int) -> bool:
        return self.saved


PollFuture = cf.Future[list[store.Poll]]
# bounds a wait on another thread, so a failure cannot hang the suite
FAILSAFE_S = 10


def test_poll_service_sleeps_until_a_job_is_due_then_runs_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = install(monkeypatch, FakeClock(START_S + 10))
    ticks: list[int] = []

    def run(tick: int) -> list[store.Poll]:
        ticks.append(tick)
        return [poll("tripupdate", tick)]

    first_slot_ms = (START_S + 60) * 1000
    corpus_store = store.Store(tmp_path)
    recorder.poll_service(
        corpus_store, [recorder.Job("refresh", 60, run)], DAY, EndsOnceSaved()
    )
    corpus_store.close()
    assert clock.sleeps == [50]
    assert ticks == [first_slot_ms]
    assert recorded(tmp_path, DAY) == [("tripupdate", first_slot_ms, 200, None)]


def test_a_slot_arriving_during_a_slow_run_is_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = install(monkeypatch, FakeClock(START_S + 10))
    release = threading.Event()
    ticks: list[int] = []

    def slow(tick: int) -> list[store.Poll]:
        ticks.append(tick)
        assert release.wait(FAILSAFE_S)
        return [poll("tripupdate", tick)]

    job = recorder.Job("refresh", 60, slow)
    clock.now_s = START_S + 60
    real_wait = cf.wait
    waits = 0

    def fake_wait(
        fs: Iterable[PollFuture], timeout: float | None, return_when: str
    ) -> tuple[set[PollFuture], set[PollFuture]]:
        nonlocal waits
        waits += 1
        if waits == 1:
            # the next slot arrives before the first run returns
            clock.now_s += 60
            return set(), set(fs)
        release.set()
        return real_wait(fs, None, return_when)

    monkeypatch.setattr(cf, "wait", fake_wait)
    corpus_store = store.Store(tmp_path)
    recorder.poll_service(corpus_store, [job], DAY, EndsOnceSaved())
    corpus_store.close()
    first_slot_ms = (START_S + 60) * 1000
    assert ticks == [first_slot_ms]
    assert recorded(tmp_path, DAY) == [("tripupdate", first_slot_ms, 200, None)]
    assert warnings_of_recorder(caplog) == 1


class OneWorkerPool(cf.ThreadPoolExecutor):
    """Runs one job at a time, so that a second job stays queued."""

    def __init__(self, max_workers: int) -> None:
        super().__init__(max_workers=1)


def test_interrupting_saves_the_run_in_flight_and_cancels_a_queued_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = install(monkeypatch, FakeClock(START_S + 10))
    started = threading.Event()
    release = threading.Event()
    queued_runs: list[int] = []

    def in_flight(tick: int) -> list[store.Poll]:
        started.set()
        assert release.wait(FAILSAFE_S)
        return [poll("tripupdate", tick)]

    def queued(tick: int) -> list[store.Poll]:
        queued_runs.append(tick)
        return [poll("alerts", tick)]

    jobs = [
        recorder.Job("refresh", 60, in_flight),
        recorder.Job("context", 60, queued),
    ]
    clock.now_s = START_S + 60
    real_as_completed = cf.as_completed

    def fake_wait(
        fs: Iterable[PollFuture], timeout: float | None, return_when: str
    ) -> NoReturn:
        assert started.wait(FAILSAFE_S)
        raise KeyboardInterrupt

    def releasing_as_completed(fs: Iterable[PollFuture]) -> Iterator[PollFuture]:
        # the queued job has been cancelled by now, so it cannot start when the worker frees up
        release.set()
        return real_as_completed(fs)

    monkeypatch.setattr(cf, "ThreadPoolExecutor", OneWorkerPool)
    monkeypatch.setattr(cf, "wait", fake_wait)
    monkeypatch.setattr(cf, "as_completed", releasing_as_completed)
    corpus_store = store.Store(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        recorder.poll_service(corpus_store, jobs, DAY, None)
    corpus_store.close()
    assert queued_runs == []
    assert recorded(tmp_path, DAY) == [("tripupdate", (START_S + 60) * 1000, 200, None)]
