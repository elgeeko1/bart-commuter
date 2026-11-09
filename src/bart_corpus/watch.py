# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Replay corpus days periodically with scripts to compare, keeping the numbers of each comparison.

Every interval, copies each service day that has no final comparison, with SQLite's backup API or from
its archive, replays it with every script, and writes DAY-HHMM-compare.txt; a day that is over also gets
DAY-final-compare.txt. Each comparison's rider view is appended to history.jsonl.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bart_corpus.archive import ARCHIVE_DIR, archived_days, unpack_day
from bart_corpus.compare import merged_view, table
from bart_corpus.core import CorpusError, now_ms, service_day
from bart_corpus.store import corpus_files, remove_recording

log = logging.getLogger(__name__)
HOUR_S = 3600


@dataclass(frozen=True)
class Script:
    label: str
    path: Path
    drop_first_trips: int


def snapshot(source: Path, target: Path) -> None:
    with (
        closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as src,
        closing(sqlite3.connect(target)) as dst,
    ):
        src.backup(dst)


def replay_day(
    file: Path, out: Path, day: str, script: Script, since: str | None
) -> subprocess.Popen[bytes]:
    report = out / f"{day}-{script.label}"
    command = [
        sys.executable,
        "-m",
        "bart_corpus",
        "replay",
        str(file),
        "--script",
        str(script.path),
        "--report",
        f"{report}.json",
        "--drop-first-trips",
        str(script.drop_first_trips),
    ]
    if since is not None:
        command += ["--since", since]
    with open(f"{report}.txt", "wb") as stdout, open(f"{report}.err", "wb") as stderr:
        return subprocess.Popen(command, stdout=stdout, stderr=stderr)


def compare_day(
    corpus: Path,
    out: Path,
    day: str,
    scripts: list[Script],
    since: str | None,
    final: bool,
) -> None:
    file = out / f"{day}.sqlite"
    live = corpus / f"{day}.sqlite"
    if live.exists():
        snapshot(live, file)
    else:
        remove_recording(file)
        unpack_day(corpus / ARCHIVE_DIR, day, out)
    jobs = {s.label: replay_day(file, out, day, s, since) for s in scripts}
    for label, process in jobs.items():
        if process.wait() != 0:
            raise CorpusError(
                f"{day} {label}: replay failed, see {out / f'{day}-{label}.err'}"
            )
    views = {s.label: merged_view([out / f"{day}-{s.label}.json"]) for s in scripts}
    text = table(views) + "\n"
    now = datetime.now()
    (out / f"{day}-{now:%H%M}-compare.txt").write_text(text, encoding="utf-8")
    if final:
        (out / f"{day}-final-compare.txt").write_text(text, encoding="utf-8")
    entry = {"day": day, "at": f"{now:%Y-%m-%dT%H:%M}", "final": final, **views}
    with (out / "history.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    if final:
        remove_recording(file)
    log.info("%s compared%s", day, " (final)" if final else "")


def parse_assignment(text: str) -> tuple[str, str]:
    key, separator, value = text.partition("=")
    if not separator or not key or not value:
        raise argparse.ArgumentTypeError(f"not KEY=VALUE: {text}")
    return key, value


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("corpus", type=Path)
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="directory for snapshots and comparisons",
    )
    parser.add_argument(
        "--script",
        type=parse_assignment,
        action="append",
        required=True,
        metavar="LABEL=PATH",
        help="widget script to replay; repeat for each script to compare",
    )
    parser.add_argument(
        "--drop-first-trips",
        type=parse_assignment,
        action="append",
        default=[],
        metavar="LABEL=N",
        help="trips to drop from each recorded trip plan for one script, as for replay",
    )
    parser.add_argument(
        "--first-day", help="earliest service day to replay, YYYY-MM-DD"
    )
    parser.add_argument(
        "--since",
        type=parse_assignment,
        action="append",
        default=[],
        metavar="DAY=TIME",
        help="first refresh to replay on one service day, ISO 8601, BART time",
    )
    parser.add_argument("--interval-hours", type=float, default=3.0)


def run(args: argparse.Namespace) -> None:
    drops = {label: int(n) for label, n in args.drop_first_trips}
    since = dict(args.since)
    scripts = [
        Script(label, Path(path), drops.get(label, 0)) for label, path in args.script
    ]
    args.out.mkdir(parents=True, exist_ok=True)
    while True:
        current = service_day(now_ms())
        live = {path.stem for path in corpus_files(args.corpus)}
        for day in sorted(live | set(archived_days(args.corpus / ARCHIVE_DIR))):
            if (args.first_day and day < args.first_day) or (
                args.out / f"{day}-final-compare.txt"
            ).exists():
                continue
            try:
                compare_day(
                    args.corpus,
                    args.out,
                    day,
                    scripts,
                    since.get(day),
                    final=day < current or day not in live,
                )
            except (CorpusError, OSError, ValueError, KeyError, sqlite3.Error) as e:
                log.error("%s: %r", day, e)
        time.sleep(args.interval_hours * HOUR_S)
