# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""The corpus on disk: one SQLite file per BART service day."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from bart_corpus.core import HTTP_OK, service_day

# sources the widget requests on a refresh, polled together
REFRESH_SOURCES = ("etd", "tripupdate", "depart")
DAY_GLOB = "????-??-??.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS body (
  sha256 TEXT PRIMARY KEY,
  size INTEGER NOT NULL,
  data BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS poll (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  subject TEXT NOT NULL,
  tick INTEGER NOT NULL,
  requested_at INTEGER NOT NULL,
  elapsed_ms INTEGER NOT NULL,
  status INTEGER,
  error TEXT,
  sha256 TEXT REFERENCES body (sha256)
);
CREATE INDEX IF NOT EXISTS poll_source_tick ON poll (source, subject, tick);
"""

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Poll:
    """One request and its outcome; times are epoch milliseconds."""

    source: str
    subject: str
    # scheduled time of the job run that made the request
    tick: int
    requested_at: int
    elapsed_ms: int
    status: int | None
    error: str | None
    body: bytes | None

    @property
    def ok(self) -> bool:
        return self.status == HTTP_OK and self.body is not None and self.error is None


def open_db(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode = WAL")
    db.executescript(SCHEMA)
    return db


def write_body(db: sqlite3.Connection, body: bytes) -> str:
    """Stores a response body once and returns its digest."""
    digest = hashlib.sha256(body).hexdigest()
    db.execute(
        "INSERT OR IGNORE INTO body (sha256, size, data) VALUES (?, ?, ?)",
        (digest, len(body), zlib.compress(body, 9)),
    )
    return digest


class Store:
    """Writes polls into the corpus file of their service day."""

    def __init__(self, corpus: Path) -> None:
        corpus.mkdir(parents=True, exist_ok=True)
        self.corpus = corpus
        self._day: str | None = None
        self._db: sqlite3.Connection | None = None

    def _connection(self, day: str) -> sqlite3.Connection:
        if self._db is None or self._day != day:
            self.close()
            path = self.corpus / f"{day}.sqlite"
            self._db = open_db(path)
            self._day = day
            log.info("writing %s", path)
        return self._db

    def write(self, polls: Iterable[Poll]) -> None:
        for p in polls:
            db = self._connection(service_day(p.tick))
            digest = None if p.body is None else write_body(db, p.body)
            db.execute(
                "INSERT INTO poll (source, subject, tick, requested_at, elapsed_ms, status, error, sha256)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    p.source,
                    p.subject,
                    p.tick,
                    p.requested_at,
                    p.elapsed_ms,
                    p.status,
                    p.error,
                    digest,
                ),
            )
        if self._db is not None:
            self._db.commit()

    def close(self) -> None:
        if self._db is not None:
            self._db.commit()
            self._db.close()
            self._db = None


def corpus_files(corpus: Path) -> list[Path]:
    return sorted(corpus.glob(DAY_GLOB))


def remove_recording(path: Path) -> None:
    for sibling in path.parent.glob(f"{path.name}*"):
        sibling.unlink()


def read_body(db: sqlite3.Connection, digest: str) -> bytes:
    (data,) = db.execute("SELECT data FROM body WHERE sha256 = ?", (digest,)).fetchone()
    return zlib.decompress(data)


def refresh_ticks(db: sqlite3.Connection) -> list[int]:
    """Returns the ticks of the refreshes in a corpus file, in order."""
    placeholders = ",".join("?" * len(REFRESH_SOURCES))
    rows = db.execute(
        f"SELECT DISTINCT tick FROM poll WHERE source IN ({placeholders}) ORDER BY tick",
        REFRESH_SOURCES,
    ).fetchall()
    return [tick for (tick,) in rows]
