# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for tools/build_manifest.py."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import build_manifest as bm

ENGINE = b"// Version: 1.2.3\nconst ENGINE_API = 4;\nmodule.exports = { api: ENGINE_API, run };\n"


def test_manifest_reads_version_and_interface_and_hashes_the_engine() -> None:
    assert bm.manifest(ENGINE) == {
        "version": "1.2.3",
        "api": 4,
        "sha256": hashlib.sha256(ENGINE).hexdigest(),
    }


@pytest.mark.parametrize(
    "engine",
    [
        b"const ENGINE_API = 1;\n",
        b"// Version: 1.2.3\n// Version: 1.2.4\nconst ENGINE_API = 1;\n",
        b"// Version: 1.2\nconst ENGINE_API = 1;\n",
        b"// Version: 1.2.3\n",
        b"// Version: 1.2.3\nconst ENGINE_API = 1;\nconst ENGINE_API = 2;\n",
    ],
)
def test_manifest_needs_one_version_and_one_interface(engine: bytes) -> None:
    with pytest.raises(bm.BuildError):
        bm.manifest(engine)


def test_main_writes_the_repository_engine_manifest(tmp_path: Path) -> None:
    assert bm.main(["build_manifest.py", str(tmp_path)]) == 0
    written = json.loads((tmp_path / bm.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert written == bm.manifest(bm.ENGINE.read_bytes())
