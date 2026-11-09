# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
"""Runs the bart-corpus command line."""

import sys

from bart_corpus.cli import main

if __name__ == "__main__":
    sys.exit(main())
