# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Checks recordings against the queries the widget script makes now.

A recording is complete when it spans the service its timetable gives without a long gap and made
every query of the current recorder, with the trip planner's current counts of trips before and after
now.
"""

from __future__ import annotations

import itertools
import json
import sqlite3
import tempfile
import zipfile
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from bart_corpus.archive import ARCHIVE_DIR, archive_file, archived_days, unpack_day
from bart_corpus.core import HTTP_OK, CorpusError, service_day
from bart_corpus.recorder import DEFAULT_PAIRS, ETD_ALL, WidgetSettings, wake_ms
from bart_corpus.store import corpus_files, read_body, remove_recording
from bart_corpus.timetable import ServiceSpan, service_span

# a refresh at most this far after recording should begin counts as reaching it
EDGE_GRACE_S = 180
# hosts sleep and networks drop; a longer gap between refreshes loses service a replay would need
MAX_GAP_S = 300
# requested once per station or per service day, so only their presence can be checked
PER_DAY_SOURCES = ("stns", "stnsched", "gtfs-static")
SUBJECTLESS_SOURCES = ("tripupdate", "alerts", "bsa", "count", "elev")


class ProblemKind(StrEnum):
    IN_PROGRESS = "in-progress"
    STARTS_LATE = "starts-late"
    ENDS_EARLY = "ends-early"
    GAP = "gap"
    MISSING_QUERY = "missing-query"
    PLANNER_MISMATCH = "planner-mismatch"
    EMPTY = "empty"


@dataclass(frozen=True)
class Problem:
    kind: ProblemKind
    detail: str


@dataclass(frozen=True)
class DayAudit:
    day: str
    path: Path
    problems: list[Problem] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.problems

    @property
    def in_progress(self) -> bool:
        return any(p.kind is ProblemKind.IN_PROGRESS for p in self.problems)


@dataclass(frozen=True)
class ExpectedQueries:
    """What a complete recording requested: (source, subject) pairs and the trip planner counts."""

    queries: frozenset[tuple[str, str]]
    trips_before: int
    trips_planned: int


def expected_queries(
    settings: WidgetSettings, pairs: Sequence[str] = DEFAULT_PAIRS
) -> ExpectedQueries:
    stations = {s for pair in pairs for s in pair.split("-")}
    queries = {(source, "") for source in SUBJECTLESS_SOURCES}
    queries |= {("etd", s) for s in (ETD_ALL, *stations)}
    queries |= {("depart", pair) for pair in pairs}
    return ExpectedQueries(
        frozenset(queries), settings.trips_before, settings.trips_planned
    )


def recorded_span(db: sqlite3.Connection, day: str) -> ServiceSpan | None:
    """Returns the service span in the timetable of a recording, None without a readable one."""
    row = db.execute(
        "SELECT sha256 FROM poll WHERE source = 'gtfs-static' AND status = ? AND sha256 IS NOT NULL"
        " ORDER BY tick LIMIT 1",
        (HTTP_OK,),
    ).fetchone()
    if row is None:
        return None
    try:
        return service_span(read_body(db, row[0]), day)
    except (zipfile.BadZipFile, KeyError, ValueError):
        return None


def planner_counts(response: bytes) -> tuple[int, int] | None:
    """Returns the trip planner counts of trips before and after now that a response echoes."""
    schedule: dict[str, Any] = json.loads(response)["root"]["schedule"]
    if "before" not in schedule or "after" not in schedule:
        return None
    return int(schedule["before"]), int(schedule["after"])


def audit_file(
    path: Path, expected: ExpectedQueries, now_ms: int, *, closed: bool = False
) -> DayAudit:
    day = path.stem
    problems: list[Problem] = []
    in_progress = not closed and day >= service_day(now_ms)
    if in_progress:
        problems.append(Problem(ProblemKind.IN_PROGRESS, "service day not over"))
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        ticks = [
            tick
            for (tick,) in db.execute(
                "SELECT DISTINCT tick FROM poll WHERE source = 'tripupdate' ORDER BY tick"
            )
        ]
        if not ticks:
            problems.append(Problem(ProblemKind.EMPTY, "no refreshes recorded"))
            return DayAudit(day, path, problems)
        span = recorded_span(db, day)
        if span is not None:
            late_s = (ticks[0] - wake_ms(day, span)) // 1000
            if late_s > EDGE_GRACE_S:
                problems.append(
                    Problem(
                        ProblemKind.STARTS_LATE,
                        f"{late_s} s after recording should begin",
                    )
                )
            early_s = (span.last_arrival_ms - ticks[-1]) // 1000
            if early_s > 0 and not in_progress:
                problems.append(
                    Problem(
                        ProblemKind.ENDS_EARLY, f"{early_s} s before the last arrival"
                    )
                )
        for before, after in itertools.pairwise(ticks):
            if (after - before) // 1000 > MAX_GAP_S:
                problems.append(
                    Problem(
                        ProblemKind.GAP,
                        f"{(after - before) // 1000} s without a refresh",
                    )
                )
        made = set(db.execute("SELECT DISTINCT source, subject FROM poll"))
        for source, subject in sorted(expected.queries - made):
            problems.append(
                Problem(ProblemKind.MISSING_QUERY, f"{source} {subject}".strip())
            )
        for source in PER_DAY_SOURCES:
            if not any(s == source for s, _ in made):
                problems.append(Problem(ProblemKind.MISSING_QUERY, source))
        digests = db.execute(
            "SELECT DISTINCT sha256 FROM poll WHERE source = 'depart' AND status = ? AND sha256 IS NOT NULL",
            (HTTP_OK,),
        ).fetchall()
        mismatched: set[tuple[int, int]] = set()
        for (digest,) in digests:
            try:
                counts = planner_counts(read_body(db, digest))
            except (ValueError, KeyError, TypeError):
                continue
            if counts is not None and counts != (
                expected.trips_before,
                expected.trips_planned,
            ):
                mismatched.add(counts)
        for before_count, after_count in sorted(mismatched):
            problems.append(
                Problem(
                    ProblemKind.PLANNER_MISMATCH,
                    f"recorded b={before_count} a={after_count}",
                )
            )
    return DayAudit(day, path, problems)


def audit(corpus: Path, expected: ExpectedQueries, now_ms: int) -> list[DayAudit]:
    """Audits every recording, live and archived."""
    archive = corpus / ARCHIVE_DIR
    live = corpus_files(corpus)
    days = archived_days(archive)
    if not live and not days:
        raise CorpusError(f"no recordings in {corpus}")
    results = [audit_file(path, expected, now_ms) for path in live]
    with tempfile.TemporaryDirectory() as tmp:
        for day in days:
            restored = unpack_day(archive, day, Path(tmp))
            result = audit_file(restored, expected, now_ms, closed=True)
            results.append(replace(result, path=archive_file(archive, day)))
            remove_recording(restored)
    return sorted(results, key=lambda r: r.day)


def prunable(audits: list[DayAudit]) -> list[DayAudit]:
    """Returns the recordings that are over and failed the audit."""
    return [a for a in audits if not a.complete and not a.in_progress]
