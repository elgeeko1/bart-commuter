# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for comparing the rider experience of replay reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from bart_corpus import compare
from bart_corpus import replay as rp
from bart_corpus.core import CorpusError

MISSED = "Rider missed a listed connection"
HIDDEN = "Board had no data"


def experience(
    rider: str, of: str, total: int, failed: int, faults: dict[str, dict[str, int]]
) -> rp.Experience:
    return {
        "rider": rider,
        "of": of,
        "total": total,
        "failed": failed,
        "faults": faults,
    }


def write_report(path: Path, rider: dict[str, rp.Experience]) -> Path:
    path.write_text(json.dumps({"rider": rider}), encoding="utf-8")
    return path


def test_merged_view_sums_the_reports_of_one_label(tmp_path: Path) -> None:
    first = write_report(
        tmp_path / "a.json",
        {
            "connection_missed": experience(
                MISSED, "listed connections", 10, 2, {rp.OUR_FAULT: {"late": 2}}
            ),
            "no_data": experience(HIDDEN, "board views", 4, 0, {}),
        },
    )
    second = write_report(
        tmp_path / "b.json",
        {
            "connection_missed": experience(
                MISSED,
                "listed connections",
                5,
                3,
                {
                    rp.OUR_FAULT: {"late": 1, "rounding": 1},
                    rp.SCHEDULE_EXCEPTION: {rp.CHANGED_LATER: 1},
                },
            ),
        },
    )
    assert compare.merged_view([first, second]) == {
        "connection_missed": experience(
            MISSED,
            "listed connections",
            15,
            5,
            {
                rp.OUR_FAULT: {"late": 3, "rounding": 1},
                rp.SCHEDULE_EXCEPTION: {rp.CHANGED_LATER: 1},
            },
        ),
        "no_data": experience(HIDDEN, "board views", 4, 0, {}),
    }


def test_merged_view_of_a_single_report_is_that_report(tmp_path: Path) -> None:
    item = experience(MISSED, "listed connections", 8, 1, {rp.OUR_FAULT: {"late": 1}})
    path = write_report(tmp_path / "a.json", {"connection_missed": item})
    assert compare.merged_view([path]) == {"connection_missed": item}


@pytest.mark.parametrize(
    ("n", "of", "text"),
    [(1, 4, "1 (25.0%)"), (0, 7, "0 (0.0%)"), (3, 0, "3")],
    ids=["share", "none-failed", "no-total-has-no-percentage"],
)
def test_cell_shows_a_count_with_its_share_of_the_total(
    n: int, of: int, text: str
) -> None:
    assert compare.cell(n, of) == text


def test_table_lists_each_experience_then_fault_then_why() -> None:
    old = {
        "connection_missed": experience(
            MISSED,
            "listed connections",
            10,
            3,
            {rp.SCHEDULE_EXCEPTION: {"later": 1}, rp.OUR_FAULT: {"late": 2}},
        ),
    }
    new = {
        "connection_missed": experience(
            MISSED,
            "listed connections",
            20,
            5,
            {rp.OUR_FAULT: {"rounding": 4, "late": 1}},
        ),
    }
    assert compare.table({"old": old, "new": new}).splitlines() == [
        "| Rider experience, then whose fault, then why | old | new |",
        "| --- | --- | --- |",
        "| **Rider missed a listed connection** (out of listed connections) | 3 (30.0%) | 5 (25.0%) |",
        "| &nbsp;&nbsp;our fault | 2 (20.0%) | 5 (25.0%) |",
        "| &nbsp;&nbsp;&nbsp;&nbsp;rounding | 0 (0.0%) | 4 (20.0%) |",
        "| &nbsp;&nbsp;&nbsp;&nbsp;late | 2 (20.0%) | 1 (5.0%) |",
        "| &nbsp;&nbsp;schedule exception | 1 (10.0%) | 0 (0.0%) |",
        "| &nbsp;&nbsp;&nbsp;&nbsp;later | 1 (10.0%) | 0 (0.0%) |",
    ]


def test_table_shows_only_the_experience_when_no_one_failed() -> None:
    view = {"no_data": experience(HIDDEN, "board views", 0, 0, {})}
    assert compare.table({"only": view}).splitlines()[2:] == [
        "| **Board had no data** (out of board views) | 0 |"
    ]


def test_parse_reports_splits_a_label_from_its_reports() -> None:
    assert compare.parse_reports("old=a.json,dir/b.json") == (
        "old",
        [Path("a.json"), Path("dir/b.json")],
    )


@pytest.mark.parametrize("text", ["a.json", "old="], ids=["no-label", "no-reports"])
def test_parse_reports_rejects_a_malformed_argument(text: str) -> None:
    with pytest.raises(CorpusError):
        compare.parse_reports(text)


def test_run_prints_one_column_per_label_summing_its_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    item = experience(MISSED, "listed connections", 10, 2, {rp.OUR_FAULT: {"late": 2}})
    a = write_report(tmp_path / "a.json", {"connection_missed": item})
    b = write_report(tmp_path / "b.json", {"connection_missed": item})
    c = write_report(tmp_path / "c.json", {"connection_missed": item})
    parser = argparse.ArgumentParser()
    compare.configure(parser)
    compare.run(parser.parse_args([f"old={a},{b}", f"new={c}"]))
    assert capsys.readouterr().out.splitlines() == [
        "| Rider experience, then whose fault, then why | old | new |",
        "| --- | --- | --- |",
        "| **Rider missed a listed connection** (out of listed connections) | 4 (20.0%) | 2 (20.0%) |",
        "| &nbsp;&nbsp;our fault | 4 (20.0%) | 2 (20.0%) |",
        "| &nbsp;&nbsp;&nbsp;&nbsp;late | 4 (20.0%) | 2 (20.0%) |",
    ]
