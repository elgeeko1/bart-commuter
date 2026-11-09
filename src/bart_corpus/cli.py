# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""The bart-corpus command line."""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from bart_corpus import compare, replay, timed_transfers, watch
from bart_corpus.archive import (
    ARCHIVE_DIR,
    archived_days,
    close_out,
    unpack_day,
    verify_day,
)
from bart_corpus.audit import audit, expected_queries, prunable
from bart_corpus.core import CorpusError, now_ms, parse_time, service_day
from bart_corpus.fixtures import capture, export
from bart_corpus.recorder import (
    DEFAULT_PAIRS,
    build_jobs,
    engine_settings,
    record_service_days,
    timetable_source,
)
from bart_corpus.status import format_status, status
from bart_corpus.store import remove_recording

log = logging.getLogger("bart_corpus")


def positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"not a positive integer: {text}")
    return value


def matching(pattern: str, example: str) -> Callable[[str], str]:
    """Returns an argument type accepting text that matches a pattern."""
    compiled = re.compile(pattern)

    def parse(text: str) -> str:
        if not compiled.match(text):
            raise argparse.ArgumentTypeError(f"not {example}: {text}")
        return text

    return parse


station_pair = matching(
    r"^[A-Z0-9]{4}-[A-Z0-9]{4}$", "a station pair such as WOAK-EMBR"
)
station_code = matching(r"^[A-Z0-9]{4}$", "a station code such as WOAK")
day_name = matching(r"^\d{4}-\d{2}-\d{2}$", "a service day such as 2026-11-08")


def run_record(args: argparse.Namespace) -> None:
    settings = engine_settings()
    if args.key is not None:
        settings = dataclasses.replace(settings, api_key=args.key)
    if args.interval is not None:
        settings = dataclasses.replace(settings, refresh_s=args.interval)
    if not settings.api_key:
        log.warning("no API key; recording GTFS-RT and static GTFS only")
    log.info(
        "refreshing every %d s with a %d s request timeout",
        settings.refresh_s,
        settings.request_timeout_s,
    )
    record_service_days(
        args.corpus,
        build_jobs(settings, args.pairs, args.stations),
        timetable_source(settings.request_timeout_s),
    )


def run_status(args: argparse.Namespace) -> None:
    print(format_status(status(args.corpus)))


def run_audit(args: argparse.Namespace) -> None:
    audits = audit(args.corpus, expected_queries(engine_settings()), now_ms())
    if args.json:
        rows = [
            {
                "day": a.day,
                "complete": a.complete,
                "problems": [dataclasses.asdict(p) for p in a.problems],
            }
            for a in audits
        ]
        print(json.dumps(rows, indent=2))
        return
    for a in audits:
        print(f"{a.day}: {'complete' if a.complete else 'incomplete'}")
        for problem in a.problems:
            print(f"  {problem.kind.value}: {problem.detail}")


def run_prune(args: argparse.Namespace) -> None:
    audits = audit(args.corpus, expected_queries(engine_settings()), now_ms())
    for a in prunable(audits):
        kinds = sorted({p.kind.value for p in a.problems})
        print(f"{a.path}: {', '.join(kinds)}")
        if args.delete:
            remove_recording(a.path)


def run_capture(args: argparse.Namespace) -> None:
    capture(args.out)


def run_export(args: argparse.Namespace) -> None:
    print(json.dumps(export(args.corpus, args.time, args.out), indent=2))


def run_pack(args: argparse.Namespace) -> None:
    failed = close_out(args.corpus, service_day(now_ms()))
    if failed:
        raise CorpusError(f"not archived: {', '.join(failed)}")


def run_unpack(args: argparse.Namespace) -> None:
    print(unpack_day(args.corpus / ARCHIVE_DIR, args.day, args.out))


def run_verify(args: argparse.Namespace) -> None:
    archive = args.corpus / ARCHIVE_DIR
    days = args.days or archived_days(archive)
    failed = False
    for day in days:
        wrong = verify_day(archive, day)
        print(f"{day}: {'ok' if not wrong else '; '.join(wrong)}")
        failed = failed or bool(wrong)
    if failed:
        raise CorpusError("verification failed")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="bart-corpus", description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    def add(
        name: str, help_text: str, run: Callable[[argparse.Namespace], None]
    ) -> argparse.ArgumentParser:
        command = commands.add_parser(name, help=help_text, description=help_text)
        command.set_defaults(run=run)
        return command

    record = add(
        "record",
        "record each service day to its own compressed file until interrupted",
        run_record,
    )
    record.add_argument("corpus", type=Path)
    record.add_argument(
        "--interval",
        type=positive_int,
        help="seconds between refreshes; defaults to the script's REFRESH_MS",
    )
    record.add_argument(
        "--pairs", type=station_pair, nargs="*", default=list(DEFAULT_PAIRS)
    )
    record.add_argument(
        "--stations",
        type=station_code,
        nargs="*",
        default=[],
        help="stations to request ETD for on their own, besides those of the pairs",
    )
    record.add_argument(
        "--key", help="Legacy API key; defaults to the script's API_KEY, empty to skip"
    )

    add("status", "report corpus coverage", run_status).add_argument(
        "corpus", type=Path
    )

    audit_cmd = add(
        "audit", "check recordings against the current recorder's queries", run_audit
    )
    audit_cmd.add_argument("corpus", type=Path)
    audit_cmd.add_argument("--json", action="store_true")

    prune = add("prune", "list finished recordings that failed the audit", run_prune)
    prune.add_argument("corpus", type=Path)
    prune.add_argument("--delete", action="store_true", help="delete what is listed")

    capture_cmd = add(
        "capture", "download live BART responses as test fixtures", run_capture
    )
    capture_cmd.add_argument("out", type=Path, help="directory to write into")

    export_cmd = add(
        "export", "write test fixtures for a moment in the corpus", run_export
    )
    export_cmd.add_argument("corpus", type=Path)
    export_cmd.add_argument(
        "time", type=parse_time, help="ISO 8601 time, BART time without an offset"
    )
    export_cmd.add_argument("out", type=Path, help="new directory")

    add(
        "pack", "archive and remove the recordings of finished days", run_pack
    ).add_argument("corpus", type=Path)

    unpack = add("unpack", "restore an archived day as a corpus file", run_unpack)
    unpack.add_argument("corpus", type=Path)
    unpack.add_argument("day", type=day_name)
    unpack.add_argument("out", type=Path, help="directory to write DAY.sqlite into")

    verify = add("verify", "check that archived days read back whole", run_verify)
    verify.add_argument("corpus", type=Path)
    verify.add_argument(
        "days",
        type=day_name,
        nargs="*",
        help="service days; every archived day without them",
    )

    replay.configure(
        add("replay", "replay a corpus file through the widget script", replay.run)
    )
    compare.configure(
        add("compare", "compare replay reports side by side", compare.run)
    )
    timed_transfers.configure(
        add("meets", "measure waits at BART's timed transfers", timed_transfers.run)
    )
    watch.configure(add("watch", "replay and compare scripts periodically", watch.run))
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        args.run(args)
    except CorpusError as e:
        print(e, file=sys.stderr)
        return 1
    return 0
