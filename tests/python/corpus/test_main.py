# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for running the package as python -m bart_corpus."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

from bart_corpus import store
from corpus_support import ms, poll


def exit_code(monkeypatch: pytest.MonkeyPatch, *argv: str) -> object:
    monkeypatch.setattr(sys, "argv", ["bart_corpus", *argv])
    with pytest.raises(SystemExit) as raised:
        runpy.run_module("bart_corpus", run_name="__main__")
    return raised.value.code


def test_running_the_module_exits_zero_after_a_command_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    writer = store.Store(tmp_path)
    writer.write([poll("tripupdate", ms("2026-01-05T12:00:00"))])
    writer.close()
    assert exit_code(monkeypatch, "status", str(tmp_path)) == 0
    assert "2026-01-05" in capsys.readouterr().out


def test_running_the_module_exits_nonzero_when_a_command_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert exit_code(monkeypatch, "status", str(tmp_path / "absent")) == 1
    assert capsys.readouterr().err
