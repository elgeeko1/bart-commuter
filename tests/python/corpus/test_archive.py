# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for archiving finished corpus days and restoring them."""

from __future__ import annotations

import sqlite3
import zlib
from contextlib import closing
from pathlib import Path

import pytest

from bart_corpus import archive, core, store
from corpus_support import ms, poll

DAY = "2026-11-06"
NEXT_DAY = "2026-11-07"
HOUR = 3_600_000
START = ms("2026-11-06T03:00:00")


def recording(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    writer = store.Store(corpus)
    writer.write(
        [
            poll("etd", START, b"same"),
            poll("etd", START + 60_000, b"same"),
            poll("tripupdate", START + 90_000, None, status=None, error="timed out"),
            poll("etd", START + HOUR, b"next hour"),
        ]
    )
    writer.close()
    return corpus / f"{DAY}.sqlite"


def rows(path: Path) -> list[tuple[object, ...]]:
    with closing(sqlite3.connect(path)) as db:
        return db.execute(
            "SELECT id, source, subject, tick, requested_at, elapsed_ms, status, error, sha256 FROM poll ORDER BY id"
        ).fetchall()


def bodies(path: Path) -> dict[str, bytes]:
    with closing(sqlite3.connect(path)) as db:
        return {
            digest: zlib.decompress(data)
            for digest, data in db.execute("SELECT sha256, data FROM body")
        }


def test_pack_then_unpack_restores_every_poll_and_body(tmp_path: Path) -> None:
    source = recording(tmp_path)
    archive_dir = tmp_path / "archive"
    archive.pack_day(source, archive_dir)
    restored = archive.unpack_day(archive_dir, DAY, tmp_path / "restored")
    assert rows(restored) == rows(source)
    assert bodies(restored) == bodies(source)


def test_a_day_packs_to_one_file_counting_each_body_once(tmp_path: Path) -> None:
    packed = archive.pack_day(recording(tmp_path), tmp_path / "archive")
    assert [p.name for p in (tmp_path / "archive").iterdir()] == [packed.file.name]
    assert (packed.polls, packed.bodies) == (4, 2)
    assert packed.size_bytes < archive.MAX_ARCHIVE_BYTES


def test_packing_a_packed_day_is_refused(tmp_path: Path) -> None:
    source = recording(tmp_path)
    archive.pack_day(source, tmp_path / "archive")
    with pytest.raises(core.CorpusError):
        archive.pack_day(source, tmp_path / "archive")


def test_a_packed_day_verifies_clean(tmp_path: Path) -> None:
    archive.pack_day(recording(tmp_path), tmp_path / "archive")
    assert archive.verify_day(tmp_path / "archive", DAY) == []


def test_a_corrupted_archive_fails_verification_and_is_not_restored(
    tmp_path: Path,
) -> None:
    archive_dir = tmp_path / "archive"
    archive.pack_day(recording(tmp_path), archive_dir)
    target = archive.archive_file(archive_dir, DAY)
    target.write_bytes(target.read_bytes()[:-4] + b"\0\0\0\0")
    assert archive.verify_day(archive_dir, DAY)
    with pytest.raises(core.CorpusError):
        archive.unpack_day(archive_dir, DAY, tmp_path / "restored")
    assert not (tmp_path / "restored" / f"{DAY}.sqlite").exists()


def test_a_missing_archive_fails_verification(tmp_path: Path) -> None:
    assert archive.verify_day(tmp_path / "archive", DAY)


def test_close_out_archives_finished_days_and_keeps_the_day_in_progress(
    tmp_path: Path,
) -> None:
    source = recording(tmp_path)
    corpus = source.parent
    assert archive.close_out(corpus, DAY) == []
    assert source.exists()
    assert archive.close_out(corpus, NEXT_DAY) == []
    assert not source.exists()
    assert archive.archived_days(corpus / archive.ARCHIVE_DIR) == [DAY]


def test_close_out_keeps_a_recording_it_could_not_archive(tmp_path: Path) -> None:
    source = recording(tmp_path)
    corpus = source.parent
    archive_dir = corpus / archive.ARCHIVE_DIR
    archive_dir.mkdir()
    archive.archive_file(archive_dir, DAY).write_bytes(b"in the way")
    assert archive.close_out(corpus, NEXT_DAY) == [DAY]
    assert source.exists()


@pytest.mark.parametrize(
    "tamper",
    [
        "DELETE FROM poll WHERE id = 1",
        "UPDATE poll SET status = 500 WHERE id = 1",
        "UPDATE body SET data = x'00' WHERE id = 1",
    ],
    ids=["lost-poll", "changed-poll", "changed-body"],
)
def test_a_pack_that_differs_from_its_recording_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    write_compact = archive._write_compact

    def tampered(path: Path, db: sqlite3.Connection) -> int:
        written = write_compact(path, db)
        with closing(sqlite3.connect(path)) as compact:
            compact.execute(tamper)
            compact.commit()
        return written

    monkeypatch.setattr(archive, "_write_compact", tampered)
    archive_dir = tmp_path / "archive"
    with pytest.raises(core.CorpusError):
        archive.pack_day(recording(tmp_path), archive_dir)
    assert list(archive_dir.iterdir()) == []


def test_packing_a_recording_without_polls_is_refused(tmp_path: Path) -> None:
    source = tmp_path / f"{DAY}.sqlite"
    store.open_db(source).close()
    archive_dir = tmp_path / "archive"
    with pytest.raises(core.CorpusError):
        archive.pack_day(source, archive_dir)
    assert archive.archived_days(archive_dir) == []


def test_an_archive_over_the_size_limit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(archive, "MAX_ARCHIVE_BYTES", 1)
    archive_dir = tmp_path / "archive"
    with pytest.raises(core.CorpusError):
        archive.pack_day(recording(tmp_path), archive_dir)
    assert list(archive_dir.iterdir()) == []


def test_unpacking_over_an_existing_recording_is_refused(tmp_path: Path) -> None:
    archive_dir = tmp_path / "archive"
    archive.pack_day(recording(tmp_path), archive_dir)
    existing = tmp_path / "restored" / f"{DAY}.sqlite"
    existing.parent.mkdir()
    existing.write_bytes(b"keep me")
    with pytest.raises(core.CorpusError):
        archive.unpack_day(archive_dir, DAY, existing.parent)
    assert existing.read_bytes() == b"keep me"
