#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
#
# Captures live BART data and checks the script against it. Run by hand, since results depend on service
# at that moment.
set -euo pipefail

cd "$(dirname "$0")/../.."
capture=$(mktemp -d)
trap 'rm -rf "$capture"' EXIT
python3 -m bart_corpus capture "${capture}"
BART_CAPTURE="${capture}" bun test ./tests/live/live.test.js
