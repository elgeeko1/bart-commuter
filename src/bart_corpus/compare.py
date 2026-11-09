# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Compare the rider experience of replay reports side by side, as a Markdown table."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from bart_corpus.core import CorpusError
from bart_corpus.replay import FAULTS, Experience

View = dict[str, Experience]


def merged_view(paths: list[Path]) -> View:
    """Returns the summed rider view of replay reports."""
    view: View = {}
    for path in paths:
        for key, item in json.loads(path.read_text(encoding="utf-8"))["rider"].items():
            into = view.setdefault(
                key,
                {
                    "rider": item["rider"],
                    "of": item["of"],
                    "total": 0,
                    "failed": 0,
                    "faults": {},
                },
            )
            into["total"] += item["total"]
            into["failed"] += item["failed"]
            for fault, whys in item["faults"].items():
                for why, n in whys.items():
                    counts = into["faults"].setdefault(fault, {})
                    counts[why] = counts.get(why, 0) + n
    return view


def cell(n: int, of: int) -> str:
    return f"{n} ({n / of:.1%})" if of else str(n)


def _row(name: str, counts: dict[str, int], totals: dict[str, int]) -> str:
    cells = " | ".join(cell(counts[label], totals[label]) for label in counts)
    return f"| {name} | {cells} |"


def table(views: dict[str, View]) -> str:
    """Formats each experience, then its faults, then the reasons for each fault, one column per label."""
    labels = list(views)
    lines = [
        "| Rider experience, then whose fault, then why | " + " | ".join(labels) + " |",
        "| --- |" + " --- |" * len(labels),
    ]
    for key, first in views[labels[0]].items():
        items = {label: views[label][key] for label in labels}
        totals: dict[str, int] = {label: items[label]["total"] for label in labels}
        lines.append(
            _row(
                f"**{first['rider']}** (out of {first['of']})",
                {label: items[label]["failed"] for label in labels},
                totals,
            )
        )
        for fault in [
            f for f in FAULTS if any(f in items[label]["faults"] for label in labels)
        ]:
            by_why: dict[str, dict[str, int]] = {
                label: items[label]["faults"].get(fault, {}) for label in labels
            }
            lines.append(
                _row(
                    f"&nbsp;&nbsp;{fault}",
                    {label: sum(by_why[label].values()) for label in labels},
                    totals,
                )
            )
            whys: dict[str, int] = defaultdict(int)
            for counts in by_why.values():
                for why, n in counts.items():
                    whys[why] += n
            for why in sorted(whys, key=lambda w: -whys[w]):
                lines.append(
                    _row(
                        f"&nbsp;&nbsp;&nbsp;&nbsp;{why}",
                        {label: by_why[label].get(why, 0) for label in labels},
                        totals,
                    )
                )
    return "\n".join(lines)


def parse_reports(text: str) -> tuple[str, list[Path]]:
    label, separator, paths = text.partition("=")
    if not separator or not paths:
        raise CorpusError(f"not LABEL=REPORT[,REPORT...]: {text}")
    return label, [Path(p) for p in paths.split(",")]


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "reports",
        type=parse_reports,
        nargs="+",
        metavar="LABEL=REPORT[,REPORT...]",
        help="replay reports written by bart-corpus replay --report; a label's reports are summed",
    )


def run(args: argparse.Namespace) -> None:
    print(table({label: merged_view(paths) for label, paths in args.reports}))
