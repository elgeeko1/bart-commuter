// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

// Layout of the widget images, which throws when the script warns unexpectedly or a widget cannot be measured.

import { expect, test } from "bun:test";
import { galleryImages, readmeImages } from "../../tools/render/layout.js";

const RENDER_TIMEOUT_MS = 60 * 1000;

test(
  "the README images lay out",
  async () => {
    expect((await readmeImages()).map((image) => image.name)).toEqual(["widgets-dark", "widgets-light"]);
  },
  RENDER_TIMEOUT_MS,
);

test(
  "every gallery state lays out in both appearances",
  async () => {
    const names = (await galleryImages()).map((image) => image.name);
    expect(names.filter((name) => name.endsWith("-dark")).length).toEqual(
      names.filter((name) => name.endsWith("-light")).length,
    );
    expect(names.length).toBeGreaterThan(0);
  },
  RENDER_TIMEOUT_MS,
);
