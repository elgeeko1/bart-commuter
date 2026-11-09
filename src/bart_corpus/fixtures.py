# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Fixtures for the test harness: live captures and moments exported from the corpus."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bart_corpus.core import HTTP_OK, CorpusError, clock, now_ms, service_day
from bart_corpus.recorder import (
    ETD_ALL,
    TRIP_UPDATES_URL,
    engine_settings,
    etd_url,
    fetch,
    leg_origins,
    station_schedule_url,
    trip_plan_url,
)
from bart_corpus.store import REFRESH_SOURCES, read_body, refresh_ticks


def etd_by_station(response: bytes) -> dict[str, str]:
    """Splits an ETD response for all stations into single-station responses."""
    document: dict[str, Any] = json.loads(response)
    root = document["root"]
    split = {}
    for station in root["station"]:
        # single-station responses carry an empty message
        single = {**document, "root": {**root, "station": [station], "message": ""}}
        split[station["abbr"]] = json.dumps(
            single, ensure_ascii=False, separators=(",", ":")
        )
    return split


FIXTURE_NAMES: dict[str, Callable[[str], str]] = {
    "tripupdate": lambda _: "tripupdate.pb",
    "alerts": lambda _: "alerts.pb",
    "bsa": lambda _: "bsa.json",
    "count": lambda _: "count.json",
    "elev": lambda _: "elev.json",
    "depart": lambda pair: f"depart-{pair}.json",
    "stns": lambda _: "stns.json",
    "stnsched": lambda abbr: f"sched-{abbr}.json",
    "gtfs-static": lambda _: "google_transit.zip",
}


@dataclass(frozen=True)
class Refresh:
    """The responses of one refresh as fixture files, by name, with the latest of each other source.

    The snapshot gives the tick, the epoch ms to run the script at, the stations whose ETD comes from
    the all-station response, and each response that is absent or failed.
    """

    files: dict[str, bytes]
    snapshot: dict[str, Any]


def load_refresh(
    db: sqlite3.Connection, tick: int, sources: Sequence[str] | None = None
) -> Refresh:
    """Reads the refresh at a tick, with the latest response of each other source at or before it.

    A station's own ETD response replaces its part of the all-station response, and a failed one
    leaves the station without ETD, as the widget would have seen it. sources limits the sources read.
    """
    rows = db.execute(
        """
        SELECT source, subject, tick, requested_at, status, error, sha256 FROM poll AS p
        WHERE tick <= ? AND id = (
          SELECT id FROM poll AS q WHERE q.source = p.source AND q.subject = p.subject AND q.tick <= ?
          ORDER BY tick DESC, id DESC LIMIT 1)
        ORDER BY source, subject
        """,
        (tick, tick),
    ).fetchall()
    if sources is not None:
        rows = [r for r in rows if r[0] in sources]
    # the all-station ETD response first, so single-station responses replace its parts
    rows.sort(key=lambda r: (r[0], r[1]) != ("etd", ETD_ALL))
    files: dict[str, bytes] = {}
    missing: list[str] = []
    split: set[str] = set()
    now: int | None = None
    for source, subject, poll_tick, requested_at, code, error, digest in rows:
        label = f"{source} {subject}".strip()
        single_etd = source == "etd" and subject != ETD_ALL
        if source in REFRESH_SOURCES and poll_tick != tick:
            # ETD of a transfer station on its own is requested only at ticks whose trip plans use it
            if not single_etd:
                missing.append(f"{label}: not polled at this tick")
            continue
        if code != HTTP_OK or error is not None or digest is None:
            missing.append(f"{label}: {error or f'HTTP {code}'}")
            if single_etd:
                files.pop(f"etd-{subject}.json", None)
                split.discard(subject)
            continue
        data = read_body(db, digest)
        if source == "etd":
            # the script dates ETD estimates from its own clock at fetch time
            now = requested_at if now is None else min(now, requested_at)
        if source == "etd" and not single_etd:
            for abbr, response in etd_by_station(data).items():
                files[f"etd-{abbr}.json"] = response.encode()
                split.add(abbr)
        elif single_etd:
            files[f"etd-{subject}.json"] = data
            split.discard(subject)
        else:
            files[FIXTURE_NAMES[source](subject)] = data
    snapshot = {
        "tick": tick,
        "now": tick if now is None else now,
        "etd_split": sorted(split),
        "missing": missing,
    }
    return Refresh(files, snapshot)


def export(corpus: Path, at_ms: int, out: Path) -> dict[str, Any]:
    """Writes the last refresh at or before a time, as load_refresh reads it, as fixtures.

    Returns the snapshot, also written to snapshot.json.
    """
    path = corpus / f"{service_day(at_ms)}.sqlite"
    if not path.exists():
        raise CorpusError(f"no corpus file for {clock(at_ms)}: {path}")
    if out.exists():
        raise CorpusError(f"already exists: {out}")
    with closing(sqlite3.connect(path)) as db:
        ticks = [t for t in refresh_ticks(db) if t <= at_ms]
        if not ticks:
            raise CorpusError(f"no refresh in {path} at or before {clock(at_ms)}")
        loaded = load_refresh(db, ticks[-1])
    out.mkdir(parents=True)
    for name, data in loaded.files.items():
        (out / name).write_bytes(data)
    (out / "snapshot.json").write_text(
        json.dumps(loaded.snapshot, indent=2) + "\n", encoding="utf-8"
    )
    return loaded.snapshot


CAPTURE_ETD_STATIONS = ("WOAK", "EMBR", "POWL", "ROCK")
CAPTURE_SCHEDULE_STATIONS = ("WOAK", "EMBR")
CAPTURE_PAIRS = (
    "WOAK-EMBR",
    "EMBR-WOAK",
    "POWL-ROCK",
    "ROCK-POWL",
    "ROCK-FRMT",
    "FRMT-ROCK",
)
# Rockridge and Fremont share no line, so their trips need ETD where each leg starts
CAPTURE_TRANSFER_PAIRS = ("ROCK-FRMT", "FRMT-ROCK")
CAPTURE_TIMEOUT_S = 20


def capture(out: Path) -> None:
    """Downloads live BART responses into a directory in the layout the test harness reads.

    Legacy API responses are kept whatever their status, since ETD reports a rejected key in the body of
    an HTTP 400 that the live checks assert on.
    """
    settings = engine_settings()
    key = settings.api_key
    out.mkdir(parents=True, exist_ok=True)

    def get(name: str, url: str) -> bytes:
        poll = fetch(name, "", url, now_ms(), CAPTURE_TIMEOUT_S)
        if poll.error is not None or poll.body is None:
            raise CorpusError(f"{name}: {poll.error}")
        (out / name).write_bytes(poll.body)
        return poll.body

    def get_etd(station: str) -> None:
        get(f"etd-{station}.json", etd_url(station, key))

    feed = fetch("tripupdate", "", TRIP_UPDATES_URL, now_ms(), CAPTURE_TIMEOUT_S)
    if not feed.ok or feed.body is None:
        raise CorpusError(f"tripupdate: {feed.error or feed.status}")
    (out / "tripupdate.pb").write_bytes(feed.body)

    day = service_day(now_ms())
    for station in CAPTURE_ETD_STATIONS:
        get_etd(station)
    for station in CAPTURE_SCHEDULE_STATIONS:
        get(f"sched-{station}.json", station_schedule_url(station, day, key))
    plans: dict[str, bytes] = {}
    for pair in CAPTURE_PAIRS:
        plans[pair] = get(f"depart-{pair}.json", trip_plan_url(pair, settings))
    legs = {s for pair in CAPTURE_TRANSFER_PAIRS for s in leg_origins(plans[pair])}
    for station in sorted(legs - set(CAPTURE_ETD_STATIONS)):
        get_etd(station)
