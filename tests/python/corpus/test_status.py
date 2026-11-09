# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for reporting corpus coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from bart_corpus import archive, core, status, store
from corpus_support import ms, poll


def test_status_reports_coverage_of_each_source(tmp_path: Path) -> None:
    t = ms("2026-11-06T12:00:00")
    corpus_store = store.Store(tmp_path)
    corpus_store.write(
        [poll("etd", t), poll("etd", t + 30_000), poll("etd", t + 60_000, status=503)]
    )
    corpus_store.write([poll("etd", t + 120_000, b"y")])
    corpus_store.close()

    (day,) = status.status(tmp_path)
    (etd,) = day.sources
    assert day.day == "2026-11-06"
    assert (etd.source, etd.polls, etd.ok, etd.distinct) == ("etd", 4, 3, 2)
    assert etd.max_gap_ms == 90_000


def test_status_of_a_corpus_without_recordings_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(core.CorpusError):
        status.status(tmp_path)


def test_status_lists_archived_and_live_days_in_order(tmp_path: Path) -> None:
    writer = store.Store(tmp_path)
    writer.write([poll("etd", ms("2026-11-06T12:00:00"))])
    writer.close()
    archive.close_out(tmp_path, "2026-11-07")
    writer = store.Store(tmp_path)
    writer.write([poll("etd", ms("2026-11-07T12:00:00"))])
    writer.close()

    packed, live = status.status(tmp_path)
    assert (packed.day, packed.sources) == ("2026-11-06", [])
    assert (
        packed.size_bytes
        == archive.archive_file(tmp_path / archive.ARCHIVE_DIR, packed.day)
        .stat()
        .st_size
    )
    assert live.day == "2026-11-07"
    assert [s.source for s in live.sources] == ["etd"]


def test_format_status_tabulates_each_source_of_a_live_day() -> None:
    first = ms("2026-11-06T12:00:00")
    last = first + 120_000
    day = status.DayStatus(
        "2026-11-06",
        2_500_000,
        [
            status.SourceStatus("bsa", 1, 1, 1, first, first, None),
            status.SourceStatus("etd", 4, 3, 2, first, last, 90_400),
        ],
    )
    lines = status.format_status([day]).splitlines()
    assert lines[0].split()[:2] == ["2026-11-06", "2.5"]
    rows = {line.split()[0]: line for line in lines[2:] if line}
    assert rows["bsa"].split()[:5] == ["bsa", "1", "1", "1", "-"]
    assert rows["etd"].split()[:5] == ["etd", "4", "3", "2", "90"]
    assert core.clock(first) in rows["etd"]
    assert core.clock(last) in rows["etd"]


def test_format_status_gives_an_archived_day_one_line() -> None:
    lines = status.format_status(
        [status.DayStatus("2026-11-06", 1_200_000, [])]
    ).splitlines()
    assert len(lines) == 1
    assert lines[0].split()[:2] == ["2026-11-06", "1.2"]
