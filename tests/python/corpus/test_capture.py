# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for capturing live fixtures; requests are faked."""

from __future__ import annotations

from pathlib import Path

import pytest

from bart_corpus import core, fixtures, store
from corpus_support import plan


def fake_fetch(
    responses: dict[str, store.Poll],
) -> object:
    def fetch(
        source: str, subject: str, url: str, tick: int, timeout_s: float
    ) -> store.Poll:
        if source in responses:
            return responses[source]
        body = (
            plan([("ROCK", "MCAR"), ("MCAR", "FRMT")]) if "depart" in source else b"x"
        )
        return store.Poll(source, subject, tick, tick, 1, 200, None, body)

    return fetch


def test_capture_writes_the_layout_the_harness_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fixtures, "fetch", fake_fetch({}))
    fixtures.capture(tmp_path)
    names = {p.name for p in tmp_path.iterdir()}
    assert {
        "tripupdate.pb",
        "etd-WOAK.json",
        "sched-WOAK.json",
        "depart-ROCK-FRMT.json",
        "etd-MCAR.json",
    } <= names


def test_capture_keeps_a_rejected_key_response_for_the_live_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rejected = store.Poll("etd-WOAK.json", "", 0, 0, 1, 400, None, b"Invalid key")
    monkeypatch.setattr(fixtures, "fetch", fake_fetch({"etd-WOAK.json": rejected}))
    fixtures.capture(tmp_path)
    assert (tmp_path / "etd-WOAK.json").read_bytes() == b"Invalid key"


def test_capture_fails_when_the_feed_cannot_be_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    feed = store.Poll("tripupdate", "", 0, 0, 1, None, "timed out", None)
    monkeypatch.setattr(fixtures, "fetch", fake_fetch({"tripupdate": feed}))
    with pytest.raises(core.CorpusError):
        fixtures.capture(tmp_path)


@pytest.mark.parametrize(
    ("error", "body"),
    [("timed out", None), (None, None)],
    ids=["request-failed", "no-body"],
)
def test_capture_fails_when_a_response_cannot_be_fetched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: str | None,
    body: bytes | None,
) -> None:
    failed = store.Poll("etd-WOAK.json", "", 0, 0, 1, None, error, body)
    monkeypatch.setattr(fixtures, "fetch", fake_fetch({"etd-WOAK.json": failed}))
    with pytest.raises(core.CorpusError):
        fixtures.capture(tmp_path)
    assert not (tmp_path / "etd-WOAK.json").exists()
