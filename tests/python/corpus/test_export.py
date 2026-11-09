# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for exporting a moment of the corpus as fixtures."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from bart_corpus import core, fixtures, store
from corpus_support import etd_all, ms, poll


@pytest.fixture
def corpus(tmp_path: Path) -> tuple[Path, int]:
    """Two refreshes. On the second, trip updates and the single-station ETD of EMBR fail, WOAK has
    a single-station ETD, and one trip plan and the ETD of a transfer station are not polled."""
    t = ms("2026-11-06T08:00:00")
    t2 = t + 60_000
    corpus_store = store.Store(tmp_path / "corpus")
    corpus_store.write([poll("stnsched", t - 3_600_000, b"sched", subject="WOAK")])
    corpus_store.write(
        [
            poll("etd", t, etd_all("WOAK"), subject="ALL"),
            poll("tripupdate", t, b"pb1"),
            poll("depart", t, b"plan1", subject="WOAK-EMBR"),
            poll("depart", t, b"plan1", subject="ROCK-POWL"),
            poll("etd", t, b"mcar alone", subject="MCAR"),
        ]
    )
    corpus_store.write(
        [
            poll("etd", t2, etd_all("WOAK", "EMBR", "POWL"), subject="ALL"),
            poll("etd", t2, b"woak alone", subject="WOAK"),
            poll("etd", t2, b"error", subject="EMBR", status=500),
            poll("tripupdate", t2, None, status=None, error="URLError: timed out"),
            poll("depart", t2, b"plan2", subject="WOAK-EMBR"),
            poll("bsa", t2 + 1, b"advisory"),
        ]
    )
    corpus_store.close()
    return tmp_path / "corpus", t


def test_export_writes_latest_tick_and_reports_failures(
    corpus: tuple[Path, int], tmp_path: Path
) -> None:
    path, t = corpus
    out = tmp_path / "out"
    snapshot = fixtures.export(path, t + 75_000, out)

    assert (snapshot["tick"], snapshot["now"]) == (t + 60_000, t + 60_005)
    assert snapshot["etd_split"] == ["POWL"]
    assert [m.split(":")[0] for m in snapshot["missing"]] == [
        "depart ROCK-POWL",
        "etd EMBR",
        "tripupdate",
    ]
    assert json.loads((out / "snapshot.json").read_text()) == snapshot
    assert sorted(p.name for p in out.iterdir()) == [
        "depart-WOAK-EMBR.json",
        "etd-POWL.json",
        "etd-WOAK.json",
        "sched-WOAK.json",
        "snapshot.json",
    ]
    assert (out / "etd-WOAK.json").read_bytes() == b"woak alone"
    assert (out / "depart-WOAK-EMBR.json").read_bytes() == b"plan2"
    assert (out / "sched-WOAK.json").read_bytes() == b"sched"


def test_export_splits_etd_into_single_station_responses(
    corpus: tuple[Path, int], tmp_path: Path
) -> None:
    path, t = corpus
    out = tmp_path / "out"
    fixtures.export(path, t + 75_000, out)

    document = json.loads((out / "etd-POWL.json").read_text())
    assert document["?xml"] == {"@version": "1.0"}
    assert document["root"]["message"] == ""
    assert [s["abbr"] for s in document["root"]["station"]] == ["POWL"]


def test_export_leaves_out_responses_after_the_tick(
    corpus: tuple[Path, int], tmp_path: Path
) -> None:
    path, t = corpus
    out = tmp_path / "out"
    snapshot = fixtures.export(path, t + 10_000, out)

    assert snapshot == {"tick": t, "now": t + 5, "etd_split": ["WOAK"], "missing": []}
    assert (out / "tripupdate.pb").read_bytes() == b"pb1"
    assert (out / "depart-WOAK-EMBR.json").read_bytes() == b"plan1"
    assert not (out / "bsa.json").exists()
    assert not (out / "etd-EMBR.json").exists()


@pytest.mark.parametrize(
    "time",
    ["2026-11-06T07:59:59", "2026-11-08T08:00:00"],
    ids=["before-first-tick", "no-file"],
)
def test_export_rejects_times_outside_the_corpus(
    corpus: tuple[Path, int], tmp_path: Path, time: str
) -> None:
    path, _ = corpus
    with pytest.raises(core.CorpusError):
        fixtures.export(path, ms(time), tmp_path / "out")


def test_export_refuses_existing_directory(
    corpus: tuple[Path, int], tmp_path: Path
) -> None:
    path, t = corpus
    with pytest.raises(core.CorpusError):
        fixtures.export(path, t, tmp_path)


def test_load_refresh_reads_only_the_sources_asked_for(
    corpus: tuple[Path, int],
) -> None:
    path, t = corpus
    with closing(sqlite3.connect(path / "2026-11-06.sqlite")) as db:
        refresh = fixtures.load_refresh(db, t + 60_000, ("stnsched",))
    assert refresh.files == {"sched-WOAK.json": b"sched"}
    assert refresh.snapshot == {
        "tick": t + 60_000,
        "now": t + 60_000,
        "etd_split": [],
        "missing": [],
    }
