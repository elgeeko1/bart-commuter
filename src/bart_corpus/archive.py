# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Compressed copies of finished corpus days, small enough for git.

Each service day becomes one DAY.sqlite.xz: a SQLite file with raw bodies, whose solid xz compression
removes what consecutive polls share, which the per-body zlib of a corpus file cannot. A normal corpus
file is restored from it with unpack.
"""

from __future__ import annotations

import hashlib
import logging
import lzma
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from bart_corpus.core import CorpusError
from bart_corpus.store import (
    corpus_files,
    open_db,
    read_body,
    remove_recording,
    write_body,
)

ARCHIVE_DIR = "archive"
SUFFIX = ".sqlite.xz"
# GitHub warns about files over 50 MB and rejects them over 100 MB
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
XZ_PRESET = 9 | lzma.PRESET_EXTREME
ARCHIVE_SCHEMA = """
CREATE TABLE body (id INTEGER PRIMARY KEY, data BLOB NOT NULL);
CREATE TABLE poll (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  subject TEXT NOT NULL,
  tick INTEGER NOT NULL,
  requested_at INTEGER NOT NULL,
  elapsed_ms INTEGER NOT NULL,
  status INTEGER,
  error TEXT,
  body_id INTEGER REFERENCES body (id)
);
"""
POLL_COLUMNS = "source, subject, tick, requested_at, elapsed_ms, status, error"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Archived:
    file: Path
    size_bytes: int
    polls: int
    bodies: int


def archive_file(archive: Path, day: str) -> Path:
    return archive / f"{day}{SUFFIX}"


def archived_days(archive: Path) -> list[str]:
    return sorted(p.name.removesuffix(SUFFIX) for p in archive.glob(f"*{SUFFIX}"))


def _write_compact(path: Path, db: sqlite3.Connection) -> int:
    """Writes the polls of a corpus file with raw bodies, in first-seen order; returns the distinct bodies."""
    ids: dict[str, int] = {}
    with closing(sqlite3.connect(path)) as compact:
        compact.executescript(ARCHIVE_SCHEMA)
        for poll_id, *columns, digest in db.execute(
            f"SELECT id, {POLL_COLUMNS}, sha256 FROM poll ORDER BY id"
        ):
            body_id: int | None = None
            if digest is not None:
                body_id = ids.get(digest)
                if body_id is None:
                    body_id = ids[digest] = len(ids) + 1
                    compact.execute(
                        "INSERT INTO body VALUES (?, ?)",
                        (body_id, read_body(db, digest)),
                    )
            compact.execute(
                "INSERT INTO poll VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (poll_id, *columns, body_id),
            )
        compact.commit()
    return len(ids)


@contextmanager
def _opened(file: Path) -> Iterator[sqlite3.Connection]:
    """Yields a connection to a decompressed copy of an archive file."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite") as scratch:
        with lzma.open(file) as compressed:
            shutil.copyfileobj(compressed, scratch)
        scratch.flush()
        with closing(sqlite3.connect(scratch.name)) as db:
            yield db


def _rows(db: sqlite3.Connection) -> Iterator[tuple[object, ...]]:
    """Yields the polls of an archive with the bytes of each body, in poll order."""
    yield from db.execute(
        f"SELECT poll.id, {POLL_COLUMNS}, data"
        " FROM poll LEFT JOIN body ON poll.body_id = body.id ORDER BY poll.id"
    )


def _digest(body: object) -> str | None:
    return hashlib.sha256(body).hexdigest() if isinstance(body, bytes) else None


def _check_matches(source: sqlite3.Connection, file: Path, day: str) -> None:
    """Raises unless an archive file holds exactly the polls and bodies of a corpus file."""
    expected = source.execute(
        f"SELECT id, {POLL_COLUMNS}, sha256 FROM poll ORDER BY id"
    )
    with _opened(file) as db:
        (count,) = db.execute("SELECT COUNT(*) FROM poll").fetchone()
        (total,) = source.execute("SELECT COUNT(*) FROM poll").fetchone()
        if count != total:
            raise CorpusError(f"{day}: archive holds {count} of {total} polls")
        for want, row in zip(expected, _rows(db), strict=True):
            if want != (*row[:-1], _digest(row[-1])):
                raise CorpusError(f"{day}: archive differs at poll {want[0]}")


def pack_day(path: Path, archive: Path) -> Archived:
    """Writes the compressed copy of a corpus file into archive, replacing nothing."""
    day = path.stem
    target = archive_file(archive, day)
    if target.exists():
        raise CorpusError(f"already packed: {target}")
    archive.mkdir(parents=True, exist_ok=True)
    with (
        closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db,
        tempfile.TemporaryDirectory(dir=archive) as tmp,
    ):
        (polls,) = db.execute("SELECT COUNT(*) FROM poll").fetchone()
        if not polls:
            raise CorpusError(f"no polls in {path}")
        compact = Path(tmp) / "compact.sqlite"
        bodies = _write_compact(compact, db)
        staged = Path(tmp) / target.name
        with (
            compact.open("rb") as source,
            lzma.open(staged, "wb", preset=XZ_PRESET) as compressed,
        ):
            shutil.copyfileobj(source, compressed)
        size = staged.stat().st_size
        if size > MAX_ARCHIVE_BYTES:
            raise CorpusError(f"{target.name}: {size} bytes exceeds the archive limit")
        _check_matches(db, staged, day)
        staged.rename(target)
    return Archived(target, size, polls, bodies)


def close_out(corpus: Path, before: str) -> list[str]:
    """Archives and removes each recording of a service day before the given one.

    Returns the days left unarchived; a failure keeps the recording, so it loses nothing.
    """
    failed = []
    for path in corpus_files(corpus):
        day = path.stem
        if day >= before:
            continue
        try:
            packed = pack_day(path, corpus / ARCHIVE_DIR)
        except (CorpusError, OSError, sqlite3.Error, lzma.LZMAError, ValueError) as e:
            log.error("%s: not archived: %r", day, e)
            failed.append(day)
            continue
        remove_recording(path)
        log.info(
            "%s: archived %d polls in %.2f MB",
            day,
            packed.polls,
            packed.size_bytes / 1e6,
        )
    return failed


def verify_day(archive: Path, day: str) -> list[str]:
    """Returns what is wrong with an archived day, nothing when it reads back whole."""
    file = archive_file(archive, day)
    if not file.exists():
        return [f"{file.name}: missing"]
    try:
        with _opened(file) as db:
            (result,) = db.execute("PRAGMA integrity_check").fetchone()
            sum(1 for _ in _rows(db))
    except (lzma.LZMAError, EOFError, sqlite3.Error) as e:
        return [f"{file.name}: {e}"]
    return [] if result == "ok" else [f"{file.name}: {result}"]


def unpack_day(archive: Path, day: str, out: Path) -> Path:
    """Restores an archived day as a normal corpus file at out/DAY.sqlite."""
    target = out / f"{day}.sqlite"
    if target.exists():
        raise CorpusError(f"already exists: {target}")
    wrong = verify_day(archive, day)
    if wrong:
        raise CorpusError(f"{day}: {'; '.join(wrong)}")
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        staged = Path(tmp) / target.name
        with closing(open_db(staged)) as db, _opened(archive_file(archive, day)) as src:
            for poll_id, *columns, body in _rows(src):
                digest = write_body(db, body) if isinstance(body, bytes) else None
                db.execute(
                    "INSERT INTO poll VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (poll_id, *columns, digest),
                )
            db.commit()
        shutil.move(staged, target)
    return target
