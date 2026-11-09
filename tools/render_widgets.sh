#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause
#
# Renders the README widget images into doc/ on macOS, or with --gallery DIR, every widget family in
# each service state into DIR.
set -euo pipefail

cd "$(dirname "$0")/.."
if [[ $# -eq 0 ]]; then
  mode=readme dir=doc
elif [[ $# -eq 2 && $1 == --gallery ]]; then
  mode=gallery dir=$2
  mkdir -p "${dir}"
else
  echo "usage: $0 [--gallery DIR]" >&2
  exit 2
fi
images=$(mktemp)
trap 'rm -f "$images"' EXIT
bun tools/render/layout.js "${mode}" >"${images}"
osascript -l JavaScript tools/render/draw.js "${images}" "${dir}"
