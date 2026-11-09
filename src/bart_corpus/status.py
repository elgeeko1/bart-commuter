# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Coverage of a corpus: polls, successes and gaps per day and source."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from bart_corpus.archive import ARCHIVE_DIR, archive_file, archived_days
from bart_corpus.core import CorpusError, clock
from bart_corpus.store import corpus_files


@dataclass(frozen=True)
class SourceStatus:
    source: str
    polls: int
    ok: int
    distinct: int
    first_ms: int
    last_ms: int
    # longest gap between ticks with a success, None with fewer than two successes
    max_gap_ms: int | None


@dataclass(frozen=True)
class DayStatus:
    day: str
    size_bytes: int
    sources: list[SourceStatus]


def status(corpus: Path) -> list[DayStatus]:
    archive = corpus / ARCHIVE_DIR
    live = corpus_files(corpus)
    archived = archived_days(archive)
    if not live and not archived:
        raise CorpusError(f"no recordings in {corpus}")
    days = [
        DayStatus(day, archive_file(archive, day).stat().st_size, [])
        for day in archived
    ]
    for path in live:
        size = sum(p.stat().st_size for p in path.parent.glob(f"{path.name}*"))
        with closing(sqlite3.connect(path)) as db:
            rows = db.execute(
                """
                SELECT source, COUNT(*), SUM(status = 200 AND error IS NULL), COUNT(DISTINCT sha256),
                  MIN(requested_at), MAX(requested_at),
                  (SELECT MAX(gap) FROM (
                     SELECT tick - LAG(tick) OVER (ORDER BY tick) AS gap FROM (
                       SELECT DISTINCT tick FROM poll AS q
                       WHERE q.source = p.source AND q.status = 200 AND q.error IS NULL)))
                FROM poll AS p GROUP BY source ORDER BY source
                """
            ).fetchall()
        days.append(DayStatus(path.stem, size, [SourceStatus(*row) for row in rows]))
    return sorted(days, key=lambda d: d.day)


def format_status(days: list[DayStatus]) -> str:
    header = f"{'source':<12} {'polls':>7} {'ok':>7} {'distinct':>8} {'max gap s':>9}  first, last (BART time)"
    lines = []
    for day in days:
        if not day.sources:
            lines += [f"{day.day}  {day.size_bytes / 1e6:.1f} MB archived", ""]
            continue
        lines += [f"{day.day}  {day.size_bytes / 1e6:.1f} MB", header]
        for s in day.sources:
            gap_s = "-" if s.max_gap_ms is None else str(round(s.max_gap_ms / 1000))
            lines.append(
                f"{s.source:<12} {s.polls:>7} {s.ok:>7} {s.distinct:>8} {gap_s:>9}  {clock(s.first_ms)}, {clock(s.last_ms)}"
            )
        lines.append("")
    return "\n".join(lines)
