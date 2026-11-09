# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Poll BART as the widget does, one file per service day, idle between days."""

from __future__ import annotations

import concurrent.futures as cf
import dataclasses
import http.client
import json
import logging
import math
import re
import signal
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bart_corpus.archive import ARCHIVE_DIR, archive_file, close_out
from bart_corpus.core import (
    ENGINE,
    CorpusError,
    clock,
    day_bounds_ms,
    next_service_day,
    now_ms,
    service_day,
)
from bart_corpus.store import Poll, Store
from bart_corpus.timetable import ServiceSpan, service_span

LEGACY_API_URL = "https://api.bart.gov/api"
GTFS_RT_URL = "https://api.bart.gov/gtfsrt"
TRIP_UPDATES_URL = f"{GTFS_RT_URL}/tripupdate.aspx"
STATIC_GTFS_URL = "https://www.bart.gov/dev/schedules/google_transit.zip"
USER_AGENT = "bart-commuter-tracker"

MINUTE_S = 60
ELEVATOR_INTERVAL_S = 300
DAILY_RETRY_S = 600
# estimates reach the boards about an hour before the first train
PRE_SERVICE_LEAD_MS = 90 * 60_000
# no train runs on a board unchanged this long after the last arrival in the timetable
BOARD_QUIET_MS = 15 * 60_000
IDLE_CHECK_S = 60
# commutes of direct trains, timed transfers, direct trains against timed transfers, untimed
# transfers at Bay Fair and at MacArthur, and the Oakland Airport line
DEFAULT_PAIRS = (
    "WOAK-EMBR",
    "EMBR-WOAK",
    "POWL-ROCK",
    "ROCK-POWL",
    "ROCK-FRMT",
    "FRMT-ROCK",
    "DBRK-EMBR",
    "EMBR-DBRK",
    "DUBL-FRMT",
    "FRMT-DUBL",
    "PHIL-DBRK",
    "DBRK-PHIL",
    "EMBR-OAKL",
    "OAKL-EMBR",
)
ETD_ALL = "ALL"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WidgetSettings:
    """Request settings of the widget script."""

    api_key: str
    refresh_s: int
    # the longest the widget waits for a request, its run deadline
    request_timeout_s: int
    trips_planned: int
    trips_before: int


def widget_settings(source: str) -> WidgetSettings:
    """Reads the request settings from the widget script source."""

    def constant(name: str, value: str) -> str:
        match = re.search(rf"^const {name} = {value};$", source, re.MULTILINE)
        if match is None:
            raise CorpusError(f"{name} not found in the widget script")
        return match.group(1)

    return WidgetSettings(
        api_key=constant("API_KEY", r'"([^"]*)"'),
        refresh_s=int(constant("REFRESH_MS", r"(\d+) \* 1000")),
        request_timeout_s=int(constant("RUN_DEADLINE_MS", r"(\d+) \* 1000")),
        trips_planned=int(constant("TRIPS_PLANNED", r"(\d+)")),
        trips_before=int(constant("TRIPS_BEFORE", r"(\d+)")),
    )


def engine_settings() -> WidgetSettings:
    return widget_settings(ENGINE.read_text(encoding="utf-8"))


def fetch(source: str, subject: str, url: str, tick: int, timeout_s: float) -> Poll:
    """Requests a URL, recording the status and body of an HTTP error, or the text of a transport error."""
    requested_at = now_ms()
    start = time.monotonic()
    status: int | None = None
    error: str | None = None
    body: bytes | None = None
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            body = e.read()
        except (OSError, http.client.HTTPException) as read_error:
            error = f"{type(read_error).__name__}: {read_error}"
    except (OSError, http.client.HTTPException) as e:
        error = f"{type(e).__name__}: {e}"
    elapsed_ms = round((time.monotonic() - start) * 1000)
    return Poll(source, subject, tick, requested_at, elapsed_ms, status, error, body)


def fetch_all(
    requests: Sequence[tuple[str, str, str]], tick: int, timeout_s: float
) -> list[Poll]:
    """Fetches (source, subject, url) requests concurrently, so a slow one delays no other."""
    with cf.ThreadPoolExecutor(max_workers=len(requests)) as pool:
        return list(
            pool.map(lambda r: fetch(r[0], r[1], r[2], tick, timeout_s), requests)
        )


def legacy_url(endpoint: str, params: dict[str, str], key: str) -> str:
    query = urllib.parse.urlencode({**params, "key": key, "json": "y"})
    return f"{LEGACY_API_URL}/{endpoint}?{query}"


def etd_url(station: str, key: str) -> str:
    return legacy_url("etd.aspx", {"cmd": "etd", "orig": station}, key)


def trip_plan_url(pair: str, settings: WidgetSettings) -> str:
    orig, dest = pair.split("-")
    params = {
        "cmd": "depart",
        "orig": orig,
        "dest": dest,
        # planner counts of trips before and after now
        "b": str(settings.trips_before),
        "a": str(settings.trips_planned),
    }
    return legacy_url("sched.aspx", params, settings.api_key)


def station_schedule_url(station: str, day: str, key: str) -> str:
    """Returns the URL of the timetable of a station on an ISO service day."""
    date = datetime.fromisoformat(day).strftime("%m/%d/%Y")
    params = {"cmd": "stnsched", "orig": station, "date": date}
    return legacy_url("sched.aspx", params, key)


@dataclass
class Job:
    """Requests run together, every interval_s seconds on the wall clock or, without one, once per service day."""

    name: str
    interval_s: int | None
    run: Callable[[int], list[Poll]]
    next_due: float = 0.0
    done_day: str | None = None

    def __post_init__(self) -> None:
        if self.interval_s is not None:
            self.next_due = self._aligned_after(time.time())

    def _aligned_after(self, t: float) -> float:
        assert self.interval_s is not None
        return (math.floor(t / self.interval_s) + 1) * self.interval_s

    def due(self, t: float) -> bool:
        if t < self.next_due:
            return False
        return self.interval_s is not None or self.done_day != service_day(
            round(t * 1000)
        )

    def wake_at(self, t: float) -> float:
        """Returns when this job next needs attention."""
        if self.interval_s is None and self.done_day == service_day(round(t * 1000)):
            return t + MINUTE_S
        return self.next_due

    def tick(self, t: float) -> int:
        """Returns the epoch ms of the slot a run starting at t belongs to."""
        if self.interval_s is None:
            return round(t * 1000)
        return math.floor(t / self.interval_s) * self.interval_s * 1000

    def started(self, t: float) -> None:
        # a daily job is not due again until its run finishes
        self.next_due = math.inf if self.interval_s is None else self._aligned_after(t)

    def finished(self, polls: Sequence[Poll], t: float) -> None:
        if self.interval_s is not None:
            return
        if all(p.ok for p in polls):
            self.next_due = 0.0
            self.done_day = service_day(polls[0].tick)
            log.info(
                "%s: %d responses for service day %s",
                self.name,
                len(polls),
                self.done_day,
            )
        else:
            self.next_due = t + DAILY_RETRY_S
            log.warning("%s: incomplete, retrying in %d s", self.name, DAILY_RETRY_S)


def leg_origins(response: bytes) -> set[str]:
    """Returns the stations where the legs of the trips in a trip planner response start or change trains.

    Change stations count from both legs' ends, since the planner gives the Oakland Airport line's
    terminal as both ends of its leg.
    """
    trips = json.loads(response)["root"]["schedule"]["request"]["trip"]
    # the planner gives a lone trip as an object
    if isinstance(trips, dict):
        trips = [trips]
    stations: set[str] = set()
    for trip in trips:
        legs = trip["leg"]
        stations |= {leg["@origin"] for leg in legs}
        stations |= {leg["@destination"] for leg in legs[:-1]}
    return stations


def build_jobs(
    settings: WidgetSettings, pairs: Sequence[str], stations: Sequence[str]
) -> list[Job]:
    """Returns the polling jobs; Legacy API requests need a key.

    A refresh requests ETD for every station at once, and for each station of the pairs and of
    stations on its own, as the widget does. Then, as the widget does to check a trip with a
    transfer, it requests ETD on its own for each station where a planned trip's leg starts.
    The rare change station on a line without live data adds a request the widget skips.
    """
    key = settings.api_key
    timeout_s = settings.request_timeout_s
    etd_stations = sorted({*stations, *(s for pair in pairs for s in pair.split("-"))})

    def refresh(tick: int) -> list[Poll]:
        requests = [("tripupdate", "", TRIP_UPDATES_URL)]
        if key:
            requests += [
                ("etd", orig, etd_url(orig, key)) for orig in (ETD_ALL, *etd_stations)
            ]
            requests += [
                ("depart", pair, trip_plan_url(pair, settings)) for pair in pairs
            ]
        polls = fetch_all(requests, tick, timeout_s)
        legs: set[str] = set()
        for p in polls:
            if p.source != "depart" or not p.ok:
                continue
            assert p.body is not None
            try:
                legs |= leg_origins(p.body)
            except (ValueError, KeyError, TypeError) as e:
                log.warning("depart %s: unexpected trip plan: %r", p.subject, e)
        transfers = [
            ("etd", orig, etd_url(orig, key))
            for orig in sorted(legs - set(etd_stations))
        ]
        return polls + (fetch_all(transfers, tick, timeout_s) if transfers else [])

    def context(tick: int) -> list[Poll]:
        requests = [("alerts", "", f"{GTFS_RT_URL}/alerts.aspx")]
        if key:
            for cmd in ("bsa", "count"):
                requests.append((cmd, "", legacy_url("bsa.aspx", {"cmd": cmd}, key)))
        return fetch_all(requests, tick, timeout_s)

    def elevators(tick: int) -> list[Poll]:
        url = legacy_url("bsa.aspx", {"cmd": "elev"}, key)
        return [fetch("elev", "", url, tick, timeout_s)]

    def daily(tick: int) -> list[Poll]:
        polls = [fetch("gtfs-static", "", STATIC_GTFS_URL, tick, timeout_s)]
        if not key:
            return polls
        stns_url = legacy_url("stn.aspx", {"cmd": "stns"}, key)
        stns = fetch("stns", "", stns_url, tick, timeout_s)
        if not stns.ok:
            return [*polls, stns]
        assert stns.body is not None
        try:
            abbrs = [
                s["abbr"] for s in json.loads(stns.body)["root"]["stations"]["station"]
            ]
        except (ValueError, KeyError, TypeError) as e:
            return [
                *polls,
                dataclasses.replace(stns, error=f"unexpected station list: {e!r}"),
            ]
        polls.append(stns)
        day = service_day(tick)
        for abbr in abbrs:
            url = station_schedule_url(abbr, day, key)
            polls.append(fetch("stnsched", abbr, url, tick, timeout_s))
        return polls

    jobs = [
        Job("refresh", settings.refresh_s, refresh),
        Job("context", MINUTE_S, context),
        Job("daily", None, daily),
    ]
    if key:
        jobs.append(Job("elevators", ELEVATOR_INTERVAL_S, elevators))
    return jobs


def save_polls(store: Store, job: Job, polls: Sequence[Poll]) -> None:
    store.write(polls)
    for p in polls:
        if not p.ok:
            log.warning("%s %s: %s", p.source, p.subject, p.error or f"HTTP {p.status}")
    job.finished(polls, time.time())


def board_state(response: bytes) -> str:
    """Returns the estimates of an ETD response in a form comparable between refreshes."""
    root = json.loads(response)["root"]
    return json.dumps(root.get("station", []), sort_keys=True)


class EndOfService:
    """Decides when the last train of a service day has left the boards.

    The BART board freezes on its last estimate overnight instead of emptying, so the end is a board
    that has stopped changing. The timetable's last arrival keeps a midday outage from counting.
    """

    def __init__(self, last_arrival_ms: int) -> None:
        self._last_arrival_ms = last_arrival_ms
        self._board: str | None = None
        self._changed_ms = 0

    def observe(self, polls: Sequence[Poll]) -> None:
        for p in polls:
            if p.source != "etd" or p.subject != ETD_ALL or not p.ok:
                continue
            assert p.body is not None
            try:
                board = board_state(p.body)
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                log.warning("etd %s: unexpected board: %r", p.subject, e)
                continue
            if board != self._board:
                self._board = board
                self._changed_ms = p.tick

    def over(self, epoch_ms: int) -> bool:
        return (
            self._board is not None
            and epoch_ms >= self._last_arrival_ms
            and epoch_ms - self._changed_ms >= BOARD_QUIET_MS
        )


def wake_ms(day: str, span: ServiceSpan | None) -> int:
    """Returns when to start recording a service day, its start when the timetable is unknown."""
    start_ms, _ = day_bounds_ms(day)
    if span is None:
        return start_ms
    return max(start_ms, span.first_departure_ms - PRE_SERVICE_LEAD_MS)


def sleep_until(epoch_ms: int) -> None:
    # short sleeps, since the monotonic clock can stop while the host sleeps
    while (remaining_s := (epoch_ms - now_ms()) / 1000) > 0:
        time.sleep(min(remaining_s, IDLE_CHECK_S))


def timetable_source(timeout_s: int) -> Callable[[str], ServiceSpan | None]:
    """Returns a lookup of the span of each service day in a freshly downloaded static GTFS feed."""

    def span(day: str) -> ServiceSpan | None:
        poll = fetch("gtfs-static", "", STATIC_GTFS_URL, now_ms(), timeout_s)
        if not poll.ok:
            log.warning("timetable: %s", poll.error or f"HTTP {poll.status}")
            return None
        assert poll.body is not None
        try:
            return service_span(poll.body, day)
        except (zipfile.BadZipFile, KeyError, ValueError) as e:
            log.warning("timetable: unexpected feed: %r", e)
            return None

    return span


def poll_service(
    store: Store, jobs: Sequence[Job], day: str, end: EndOfService | None
) -> None:
    """Runs the jobs until the last train has left the boards or the service day changes.

    Skips a run while the previous run of the job is in flight.
    """
    pending: dict[cf.Future[list[Poll]], Job] = {}

    def finish(future: cf.Future[list[Poll]]) -> None:
        job = pending.pop(future)
        polls = future.result()
        save_polls(store, job, polls)
        if end is not None:
            end.observe(polls)

    def running() -> bool:
        now = now_ms()
        return service_day(now) == day and not (end is not None and end.over(now))

    with cf.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        try:
            while running():
                t = time.time()
                for job in jobs:
                    if not job.due(t):
                        continue
                    tick = job.tick(t)
                    job.started(t)
                    if job in pending.values():
                        log.warning(
                            "%s: previous run still in flight, skipping", job.name
                        )
                        continue
                    pending[pool.submit(job.run, tick)] = job
                wake = min(job.wake_at(t) for job in jobs)
                timeout = max(0.0, wake - time.time())
                if not pending:
                    time.sleep(timeout)
                    continue
                # a daily job in flight has no wake time until it finishes
                done, _ = cf.wait(
                    pending, None if math.isinf(wake) else timeout, cf.FIRST_COMPLETED
                )
                for future in done:
                    finish(future)
        finally:
            for future in list(pending):
                if future.cancel():
                    del pending[future]
            for future in cf.as_completed(list(pending)):
                finish(future)


def day_is_over(
    corpus: Path, day: str, span: ServiceSpan | None, epoch_ms: int
) -> bool:
    """Tells whether a service day has nothing left to record."""
    if archive_file(corpus / ARCHIVE_DIR, day).exists():
        return True
    return (
        span is not None
        and epoch_ms >= span.last_arrival_ms
        and not (corpus / f"{day}.sqlite").exists()
    )


def record_service_days(
    corpus: Path,
    jobs: Sequence[Job],
    timetable: Callable[[str], ServiceSpan | None],
) -> None:
    """Records each service day to its own compressed file, idle between days, until interrupted."""
    # SIGTERM stops the recorder like Ctrl-C
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        while True:
            now = now_ms()
            day = service_day(now)
            close_out(corpus, day)
            span = timetable(day)
            if day_is_over(corpus, day, span, now):
                following = next_service_day(day)
                wake = wake_ms(following, timetable(following))
                log.info("%s is closed; idle until %s", day, clock(wake))
                sleep_until(wake)
                continue
            wake = wake_ms(day, span)
            if now < wake:
                log.info("%s has not begun; idle until %s", day, clock(wake))
                sleep_until(wake)
                continue
            end = None if span is None else EndOfService(span.last_arrival_ms)
            store = Store(corpus)
            try:
                poll_service(store, jobs, day, end)
            finally:
                store.close()
            close_out(corpus, next_service_day(day))
    except KeyboardInterrupt:
        log.info("stopped")
