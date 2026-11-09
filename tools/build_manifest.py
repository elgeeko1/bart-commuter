# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Write the release manifest of bart-commuter.js, the engine that the loader downloads.

The manifest gives the engine's version from its version line, its run() interface version from
ENGINE_API, and the SHA-256 of its bytes.

Usage: python3 tools/build_manifest.py (OUT_DIR | --print)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "bart-commuter.js"
MANIFEST_NAME = "manifest.json"
VERSION_LINE = re.compile(r"^// Version: (\d+\.\d+\.\d+)$", re.MULTILINE)
API_LINE = re.compile(r"^const ENGINE_API = (\d+);$", re.MULTILINE)


class BuildError(Exception):
    pass


class Manifest(TypedDict):
    version: str
    api: int
    sha256: str


def only_match(pattern: re.Pattern[str], source: str, what: str) -> str:
    found = pattern.findall(source)
    if len(found) != 1:
        raise BuildError(f"expected one {what}, found {len(found)}")
    return str(found[0])


def manifest(engine: bytes) -> Manifest:
    source = engine.decode("utf-8")
    return {
        "version": only_match(VERSION_LINE, source, "version line"),
        "api": int(only_match(API_LINE, source, "ENGINE_API declaration")),
        "sha256": hashlib.sha256(engine).hexdigest(),
    }


def main(argv: list[str]) -> int:
    assert __doc__ is not None
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("out_dir", type=Path, nargs="?")
    destination.add_argument(
        "--print", action="store_true", help="write the manifest to standard output"
    )
    args = parser.parse_args(argv[1:])
    text = json.dumps(manifest(ENGINE.read_bytes()), indent=2) + "\n"
    if args.print:
        sys.stdout.write(text)
        return 0
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / MANIFEST_NAME).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
