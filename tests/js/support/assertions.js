// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

import { expect } from "bun:test";

export function assertClean(r) {
  if (r.thrown) throw new Error(`script threw: ${r.thrown.message}`);
  expect(r.harnessErrors).toEqual([]);
}
