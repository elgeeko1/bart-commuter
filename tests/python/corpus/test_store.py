# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for the corpus files and the coverage report."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from bart_corpus import store
from corpus_support import ms, poll


def test_store_writes_by_service_day_and_deduplicates_bodies(tmp_path: Path) -> None:
    before = ms("2026-11-07T02:59:30")
    after = ms("2026-11-07T03:00:00")
    corpus_store = store.Store(tmp_path)
    corpus_store.write([poll("bsa", before, b"same"), poll("bsa", before + 1, b"same")])
    corpus_store.write(
        [poll("bsa", after, None, status=None, error="URLError: timed out")]
    )
    corpus_store.close()

    with closing(sqlite3.connect(tmp_path / "2026-11-06.sqlite")) as db:
        assert db.execute("SELECT COUNT(*) FROM poll").fetchone() == (2,)
        assert db.execute("SELECT COUNT(*) FROM body").fetchone() == (1,)
    with closing(sqlite3.connect(tmp_path / "2026-11-07.sqlite")) as db:
        rows = db.execute("SELECT status, error, sha256 FROM poll").fetchall()
    assert rows == [(None, "URLError: timed out", None)]


def test_remove_recording_removes_sqlite_sidecars(tmp_path: Path) -> None:
    (tmp_path / "2026-11-06.sqlite").write_bytes(b"db")
    (tmp_path / "2026-11-06.sqlite-wal").write_bytes(b"wal")
    (tmp_path / "other.sqlite").write_bytes(b"keep")
    store.remove_recording(tmp_path / "2026-11-06.sqlite")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["other.sqlite"]
