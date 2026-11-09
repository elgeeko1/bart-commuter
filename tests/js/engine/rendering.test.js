// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

// What each widget family draws: symbols, columns, colors, flags and text styles.

import { expect, test } from "bun:test";
import { ENGINE_PATH, directPlan, naturalSize, planResponse, readText, symbols, texts } from "../support/harness.js";
import { assertClean } from "../support/assertions.js";
import {
  B,
  BROKEN_FEED,
  ETD_DOWN,
  ETD_EMBR_WOAK,
  ETD_EMBR_WOAK_LIST,
  ETD_WOAK_EMBR,
  ETD_WOAK_EMBR_LIST,
  FIXTURES,
  MIN,
  NEXT_MORNING,
  NO_DATA,
  NO_DATA_SYMBOL,
  REJECTED_KEY,
  SCHEDULED,
  TRANSFER_DAY_START,
  Y,
  dots,
  etdRequests,
  minute,
  failedSources,
  gtfsRtRequests,
  run,
  transferStep,
  via19th,
  viaMacArthur,
  withEtd,
  withLastPlannedTrip,
} from "../support/engine.js";

// a node and every node below it, in document order
const nodes = (n) => [n, ...n.children.flatMap(nodes)];

test("a tram leads the rectangular Lock Screen title and the inline line", async () => {
  const updated = "arrow.clockwise updated";
  const expected = {
    small: [],
    medium: ["tram.fill BART Commuter", updated],
    large: ["tram.fill BART Commuter", updated],
    accessoryRectangular: ["tram.fill BART", "arrow.clockwise ago"],
    accessoryCircular: ["tram.fill EMBR"],
  };
  for (const [family, symbolsShown] of Object.entries(expected)) {
    const r = await run({ family });
    assertClean(r);
    expect(symbols(r.widget), family).toEqual(symbolsShown);
  }
  const inline = await run({ family: "accessoryInline" });
  expect(
    symbols(inline.widget).map((t) => t.split(" ")[0]),
    "inline",
  ).toEqual(["tram.fill"]);
  const rectangular = (await run({ family: "accessoryRectangular" })).widget;
  const tram = nodes(rectangular).find((n) => n.kind === "image");
  expect([tram.imageSize.width, tram.imageSize.height], "rectangular symbol size").toEqual([11, 11]);
});

test("the rectangular Lock Screen widget has no padding", async () => {
  const r = await run({ family: "accessoryRectangular" });
  assertClean(r);
  expect(r.widget.padding, "padding").toEqual({ top: 0, leading: 0, bottom: 0, trailing: 0 });
});

test("every widget family renders", async () => {
  const expected = {
    small: [
      "West Oakland",
      " \u2192 Embarcadero",
      "11:16 PM",
      "11:25pm",
      "11:34pm",
      "Embarcadero",
      " \u2192 West Oakland",
      "11:11 PM",
      "11:22pm",
      "11:31pm",
    ],
    medium: [...ETD_WOAK_EMBR, ...ETD_EMBR_WOAK],
    large: [...ETD_WOAK_EMBR_LIST, ...ETD_EMBR_WOAK_LIST],
    extraLarge: [...ETD_WOAK_EMBR_LIST, ...ETD_EMBR_WOAK_LIST],
    accessoryRectangular: [
      "BART",
      "West Oakland",
      " \u2192 Embarcadero",
      "(Y) ",
      "11:16 PM",
      "  (B) 11:25pm",
      "Embarcadero",
      " \u2192 West Oakland",
      "(B) ",
      "11:11 PM",
      "  (Y) 11:22pm",
    ],
    accessoryCircular: ["EMBR", "11:11 PM", "to WOAK"],
    accessoryInline: ["WOAK\u2192 11:16 PM  EMBR\u2192 11:11 PM"],
  };
  for (const [family, want] of Object.entries(expected)) {
    const r = await run({ family });
    assertClean(r);
    expect(texts(r.widget), family).toEqual(want);
  }
});

test("rectangular Lock Screen widget marks the line of each next train with a letter", async () => {
  // GTFS-RT cannot tell the line of the first West Oakland train
  const r = await run({ family: "accessoryRectangular", ...REJECTED_KEY });
  assertClean(r);
  expect(
    texts(r.widget).filter((t) => t.startsWith("(")),
    "letters",
  ).toEqual(["(B) "]);
  expect(dots(r.widget), "dots").toEqual([]);
  const empty = await run({ family: "accessoryRectangular", ...REJECTED_KEY, fixtures: BROKEN_FEED });
  expect(texts(empty.widget), "no data").toEqual([
    "BART",
    "West Oakland",
    " \u2192 Embarcadero",
    NO_DATA,
    "Embarcadero",
    " \u2192 West Oakland",
    NO_DATA,
  ]);
  expect(
    texts(empty.widget).filter((t) => t.startsWith("(")),
    "no letters without a train",
  ).toEqual([]);
});

test("large widget lists later trains for one station", async () => {
  // the first seven trains of the station
  const r = await run({ family: "large", params: { widget: "embr" } });
  assertClean(r);
  expect(texts(r.widget), "large widget").toEqual([
    ...["Embarcadero", "11:11 PM"],
    ...["11:13 pm", "11:22 pm", "11:22 pm", "11:31 pm", "11:32 pm", "11:41 pm"],
  ]);
});

test("lines without live data come from the timetable", async () => {
  // the Oakland Airport line is in neither ETD nor GTFS-RT
  const empty = (abbr) => JSON.stringify({ root: { station: [{ name: abbr, abbr }] } });
  const fixtures = { ...FIXTURES, etd: { ...FIXTURES.etd, COLS: empty("COLS"), OAKL: empty("OAKL") } };
  const r = await run({
    fixtures,
    family: "accessoryInline",
    scheduleFailure: "not-json",
    params: { widget: "cols oakl" },
  });
  assertClean(r);
  expect(texts(r.widget), "inline").toEqual(["COLS\u2192 --  OAKL\u2192 --"]);
  expect(etdRequests(r).length + gtfsRtRequests(r).length, "live requests").toEqual(0);
  expect(
    failedSources(r).filter((s) => s.startsWith("COLS-OAKL")),
    "failed sources",
  ).toEqual(["COLS-OAKL schedule"]);
});

test("small widget marks the lines of the next and following trains", async () => {
  const r = await run({ family: "small" });
  assertClean(r);
  expect(dots(r.widget), "dots").toEqual([Y, B, Y, B, Y, B]);
});

test("later-train dots are quieter than the dot of the next departure", async () => {
  const r = await run({ family: "small" });
  const colors = nodes(r.widget)
    .filter((c) => c.size?.width > 0 && c.backgroundColor)
    .map((c) => c.backgroundColor.hex);
  expect(colors.slice(0, 3)).toEqual(["#ffff33", "#0e5b75", "#8e8e29"]);
});

test("medium widget splits the row into equal left-aligned columns", async () => {
  const r = await run({ family: "medium" });
  assertClean(r);
  const row = r.widget.children[0].children.find((c) => c.children?.filter((k) => k.kind === "stack").length === 2);
  const columns = row.children.filter((c) => c.kind === "stack");
  expect(columns.length, "columns").toEqual(2);
  const flexible = (n) => n?.kind === "spacer" && n.value === null;
  for (const col of columns) {
    const lines = col.children.filter((c) => c.kind === "stack");
    expect(
      lines.some((l) => flexible(l.children[0])),
      "lines start flush left",
    ).toEqual(false);
    expect(flexible(lines[0].children.at(-1)), "title line fills the column").toEqual(true);
  }
});

test("full-width rules divide stacked trips and follow the app header", async () => {
  const bars = (w) => nodes(w).filter((c) => c.size?.height === 1);
  for (const [family, count] of [
    ["small", 1],
    ["large", 2],
    ["medium", 1],
  ]) {
    const r = await run({ family });
    expect(bars(r.widget).length, family).toEqual(count);
    for (const bar of bars(r.widget))
      expect(
        bar.children.some((c) => c.kind === "spacer" && c.value === null),
        `${family} full width`,
      ).toEqual(true);
  }
  const one = await run({ family: "large", params: { widget: "embr" } });
  expect(bars(one.widget).length, "one station").toEqual(1);
});

test("large widget lists later trains beside each next train", async () => {
  const r = await run({ family: "large" });
  assertClean(r);
  const root = r.widget.children[0];
  const rows = root.children.filter(
    (c) => c.kind === "stack" && !c.size && c.children.filter((k) => k.kind === "stack" && k.vertical).length === 2,
  );
  expect(rows.length, "one row per trip").toEqual(2);
  for (const row of rows)
    expect(texts(row.children.filter((k) => k.kind === "stack")[1]).length > 1, "later trains").toEqual(true);
});

test("no widget family shows a countdown or a live-or-timetable mark", async () => {
  for (const family of ["small", "medium", "large", "accessoryRectangular", "accessoryCircular", "accessoryInline"]) {
    const r = await run({ family, scheduleFailure: null });
    expect(nodes(r.widget).filter((c) => c.kind === "date" && !c.offset).length, `${family} live dates`).toEqual(0);
    expect(texts(r.widget).filter((t) => /\d min|\*|^now$/i.test(t)).length, `${family} text`).toEqual(0);
  }
});

test("AM and PM are smaller than the time of the next departure", async () => {
  const expected = {
    small: ["boldRoundedSystemFont(26)", "boldRoundedSystemFont(13)"],
    medium: ["boldRoundedSystemFont(26)", "boldRoundedSystemFont(13)"],
    accessoryRectangular: ["boldRoundedSystemFont(13)", "boldRoundedSystemFont(9)"],
  };
  for (const [family, fonts] of Object.entries(expected)) {
    const r = await run({ family });
    const period = nodes(r.widget).find((c) => c.kind === "text" && /^ [AP]M/.test(c.value));
    const clockTime = nodes(r.widget).find((c) => c.kind === "text" && /^\d+:\d\d$/.test(c.value));
    expect([clockTime.font, period.font], family).toEqual(fonts);
  }
});

test("widgets show the live age of their data, with no tap target of their own", async () => {
  const updatedOf = (w) =>
    nodes(w).find(
      (c) => c.kind === "stack" && c.children.some((i) => i.kind === "image" && i.value === "arrow.clockwise"),
    );
  for (const family of ["medium", "large", "accessoryRectangular"]) {
    const r = await run({ family });
    const updated = updatedOf(r.widget);
    expect(
      updated.children.filter((c) => c.kind === "text").map((c) => c.value),
      `${family} label`,
    ).toEqual(family === "accessoryRectangular" ? ["ago"] : ["updated", "ago"]);
    expect(nodes(updated).filter((c) => c.kind === "date" && c.offset).length, `${family} age`).toEqual(1);
    expect(nodes(r.widget).filter((c) => c.kind === "stack" && c.url).length, `${family} buttons`).toEqual(0);
  }
});

test("Home Screen colors follow the appearance, and Lock Screen colors do not", async () => {
  const medium = (await run({ family: "medium" })).widget;
  expect(medium.backgroundColor.lightHex, "background").toEqual("#ffffff");
  const shown = nodes(medium).filter((c) => c.kind === "text" || c.kind === "date");
  expect(
    shown.every((c) => c.textColor.lightHex),
    "text",
  ).toEqual(true);
  const dotted = nodes(medium).filter((c) => c.size && c.backgroundColor);
  expect(
    dotted.every((c) => c.backgroundColor.lightHex),
    "dots",
  ).toEqual(true);
  for (const family of ["accessoryRectangular", "accessoryCircular"]) {
    const widget = (await run({ family })).widget;
    expect(nodes(widget).filter((c) => c.textColor?.lightHex).length, family).toEqual(0);
  }
});

test("the circular widget shows the first trip before 1 PM and the return trip after", async () => {
  const circular = async (now) => {
    const times = [10, 30, 50, 70].map((m) => now + m * 60000);
    const depart = {
      "WOAK-EMBR": directPlan(1, "WOAK", "EMBR", times, 7),
      "EMBR-WOAK": directPlan(2, "EMBR", "WOAK", times, 7),
    };
    const r = await run({ now, fixtures: { ...BROKEN_FEED, depart }, family: "accessoryCircular", ...ETD_DOWN });
    assertClean(r);
    return r.widget;
  };
  const before = await circular(Date.UTC(2026, 10, 5, 20, 59));
  expect(texts(before)[0], "12:59 PM").toEqual("WOAK");
  expect(symbols(before)[0], "symbol before the station").toEqual("tram.fill WOAK");
  expect(texts(await circular(Date.UTC(2026, 10, 5, 21, 0)))[0], "1:00 PM").toEqual("EMBR");
});

test("every line in the network has a Lock Screen letter", () => {
  const source = readText(ENGINE_PATH);
  const keys = (name) => {
    const body = new RegExp(`^const ${name} = \\{([^}]*)\\}`, "m").exec(source)[1];
    return [...body.matchAll(/([A-Z]+):/g)].map((m) => m[1]).sort();
  };
  expect(keys("LINE_LETTERS"), "lines").toEqual(keys("LINE_COLORS"));
});

test("one station renders one row in compact families", async () => {
  const expected = {
    small: ["Embarcadero", "11:11 PM", "11:13pm", "11:22pm"],
    accessoryRectangular: ["BART", "Embarcadero", "(B) ", "11:11 PM", "  (B) 11:13pm"],
    accessoryCircular: ["EMBR", "11:11 PM"],
    accessoryInline: ["EMBR\u2192 11:11 PM"],
  };
  for (const [family, want] of Object.entries(expected)) {
    const r = await run({ family, params: { widget: "embr" } });
    assertClean(r);
    expect(texts(r.widget), family).toEqual(want);
  }
});

test("a heading too wide for full names shows the destination as a code", async () => {
  const headings = (r) =>
    texts(r.widget).filter((t, i, all) => t.startsWith(" \u2192 ") || all[i + 1]?.startsWith(" \u2192 "));
  const expected = {
    small: {
      "12th 16th": ["12th St Oakland", " \u2192 16TH", "16th St Mission", " \u2192 12TH"],
      "dbrk ssan": ["Downtown Berkeley", " \u2192 SSAN", "South San Francisco", " \u2192 DBRK"],
    },
    accessoryRectangular: {
      "12th 16th": ["12th St Oakland", " \u2192 16th St Mission", "16th St Mission", " \u2192 12th St Oakland"],
      "dbrk ssan": ["Downtown Berkeley", " \u2192 SSAN", "South San Francisco", " \u2192 DBRK"],
    },
  };
  for (const [family, pairs] of Object.entries(expected)) {
    for (const [stations, want] of Object.entries(pairs)) {
      const r = await run({ family, params: { widget: stations }, ...REJECTED_KEY, fixtures: BROKEN_FEED });
      expect(r.thrown, `${family} ${stations} thrown`).toEqual(null);
      expect(headings(r), `${family} ${stations}`).toEqual(want);
    }
  }
});

test("heading widths cover every station", () => {
  const source = readText(ENGINE_PATH);
  const codes = (name) => {
    const body = new RegExp(`^const ${name} = \\{([\\s\\S]*?)^\\};`, "m").exec(source)[1];
    return [...body.matchAll(/^ {2}"?(\w+)"?:/gm)].map((m) => m[1]).sort();
  };
  expect(codes("HEADING_WIDTHS"), "stations").toEqual(codes("STATIONS"));
});

test("an unscheduled ETD train has a white dot and the letter U", async () => {
  const fixtures = withEtd("EMBR", (json) => {
    const est = json.root.station[0].etd.flatMap((e) => e.estimate).find((e) => e.minutes === "1");
    Object.assign(est, { color: "WHITE", hexcolor: "#ffffff", dynamicflag: "1" });
  });
  const rect = await run({ family: "accessoryRectangular", params: { widget: "embr" }, fixtures });
  assertClean(rect);
  expect(texts(rect.widget), "rectangular").toEqual(["BART", "Embarcadero", "(U) ", "11:11 PM", "  (B) 11:13pm"]);
  const small = await run({ family: "small", params: { widget: "embr" }, fixtures });
  expect(dots(small.widget)[0], "dot").toEqual("#ffffff");
});

test("an unexpected ETD line color fails ETD for that station", async () => {
  const fixtures = withEtd("EMBR", (json) => {
    json.root.station[0].etd[0].estimate[0].color = "PURPLE";
  });
  const r = await run({ family: "small", params: { widget: "embr" }, fixtures });
  assertClean(r);
  expect(failedSources(r), "failed sources").toContain("EMBR ETD");
  expect(gtfsRtRequests(r).length, "GTFS-RT fallback").toEqual(1);
});

test("a widget sets no URL, so its When Interacting setting applies", async () => {
  for (const family of ["small", "medium", "large", "accessoryRectangular", "accessoryCircular", "accessoryInline"]) {
    const r = await run({ family });
    assertClean(r);
    expect(r.widget.url, family).toBeUndefined();
  }
});

test("a last-train line and a transfer start under the departure time, and later trains start flush left", async () => {
  const width = (c) => (c.kind === "spacer" ? c.value : c.kind === "image" ? c.imageSize.width : c.size.width);
  const offset = (line, target) =>
    line.children.slice(0, line.children.indexOf(target)).reduce((sum, c) => sum + width(c), 0);
  const isDot = (c) => c.size?.width > 0 && c.backgroundColor;
  const firstContent = (line) => line.children.find((c) => c.kind !== "spacer" && !isDot(c));
  const check = (widget, name) => {
    let flags = 0;
    for (const parent of nodes(widget)) {
      const lines = parent.children.filter((c) => c.kind === "stack");
      lines.forEach((line, i) => {
        const symbol = line.children.find((c) => c.kind === "image");
        if (symbol?.value === "flag.checkered" && firstContent(line) === symbol) {
          flags += 1;
          expect(offset(line, symbol), `${name} flag`).toEqual(offset(lines[i - 1], firstContent(lines[i - 1])));
        }
        if (symbol?.value === "arrow.triangle.branch")
          expect(offset(line, symbol), `${name} transfer`).toEqual(offset(lines[i - 1], firstContent(lines[i - 1])));
      });
    }
    return flags;
  };
  const meet = TRANSFER_DAY_START + (16 * 60 + 40) * MIN;
  const transferLast = transferStep(16 * 60 + 40, {
    rock: [10],
    mcar: [11],
    outbound: [viaMacArthur(meet + 4 * MIN, 4), viaMacArthur(meet + 279 * MIN)],
    inbound: [via19th(meet + 256 * MIN)],
  });
  const directLast = withLastPlannedTrip(BROKEN_FEED, "WOAK-EMBR", 1);
  for (const family of ["small", "medium", "large"]) {
    const transfer = await transferLast.run({ family });
    assertClean(transfer);
    expect(check(transfer.widget, `${family} transfer`), `${family} transfer flags`).toEqual(1);
    const direct = await run({ fixtures: directLast, family, ...ETD_DOWN });
    assertClean(direct);
    expect(check(direct.widget, `${family} direct`), `${family} direct flags`).toEqual(1);
  }
  // a Large list row has the flag and words after its time
  const later = await run({
    fixtures: withLastPlannedTrip(BROKEN_FEED, "WOAK-EMBR", 2),
    family: "large",
    ...ETD_DOWN,
  });
  assertClean(later);
  expect(check(later.widget, "large later"), "large later lines below").toEqual(0);
  const row = nodes(later.widget).find((n) =>
    n.children.some((c) => c.kind === "image" && c.value === "flag.checkered"),
  );
  expect(texts(row).length, "large later time alone").toEqual(1);
  expect(/\d:\d\d/.test(texts(row)[0]), "large later time").toEqual(true);
  expect(row.children[0].value, "large later indent").toEqual(8);
  for (const family of ["small", "medium"]) {
    const rows = nodes((await run({ family })).widget).filter((n) => n.children.filter(isDot).length === 2);
    expect(rows.length, `${family} later rows`).toEqual(2);
    for (const row of rows) expect(Boolean(isDot(row.children[0])), `${family} later trains`).toEqual(true);
  }
});

test("a later train that is the last of the service carries a flag", async () => {
  const fixtures = withLastPlannedTrip(BROKEN_FEED, "WOAK-EMBR", 2);
  const flags = (r) => symbols(r.widget).filter((s) => s.startsWith("flag.checkered"));
  for (const family of ["small", "medium", "large", "accessoryRectangular"]) {
    const r = await run({ fixtures, family, ...ETD_DOWN });
    assertClean(r);
    expect(flags(r).length, `${family} flag`).toEqual(1);
    expect(texts(r.widget).includes("last train"), `${family} words`).toEqual(false);
  }
});

test("the Lock Screen marks a last next departure with a flag", async () => {
  const rectangular = await run({
    fixtures: withLastPlannedTrip(BROKEN_FEED, "WOAK-EMBR"),
    family: "accessoryRectangular",
    ...ETD_DOWN,
  });
  assertClean(rectangular);
  expect(symbols(rectangular.widget).includes("flag.checkered last train"), "rectangular").toEqual(true);
  // late in the day the circular widget shows the return trip
  const circular = await run({
    fixtures: withLastPlannedTrip(BROKEN_FEED, "EMBR-WOAK"),
    family: "accessoryCircular",
    ...ETD_DOWN,
  });
  assertClean(circular);
  expect(symbols(circular.widget), "circular").toEqual(["tram.fill EMBR", "flag.checkered"]);
  expect(texts(circular.widget).includes("last train"), "circular words").toEqual(false);
});

test("a Large widget without data shows the no-data symbol for each trip", async () => {
  const r = await run({ family: "large", ...SCHEDULED, scheduleFailure: "not-json" });
  assertClean(r);
  expect(symbols(r.widget).filter((s) => s === NO_DATA_SYMBOL).length, "no data symbols").toEqual(2);
});

test("a next departure that is the last has the flag and words below its time", async () => {
  const fixtures = withLastPlannedTrip(BROKEN_FEED, "WOAK-EMBR");
  for (const family of ["small", "medium", "large"]) {
    const r = await run({ fixtures, family, ...ETD_DOWN });
    assertClean(r);
    expect(symbols(r.widget).includes("flag.checkered last train"), family).toEqual(true);
  }
  const large = await run({ fixtures, family: "large", ...ETD_DOWN });
  const stacks = large.widget.children[0].children.filter(
    (c) => c.kind === "stack" && c.children.filter((k) => k.kind === "stack").length === 2,
  );
  const columns = stacks[0].children.filter((c) => c.kind === "stack");
  expect(texts(columns[0]).includes("last train"), "under the time").toEqual(true);
  expect(texts(columns[1]).length, "list empty").toEqual(0);
  expect(symbols(columns[1]).length, "no symbol").toEqual(0);
});

test("Medium columns keep their later-train and last-train rows level", async () => {
  const direct = (route, from, to, t) => [[route, from, to, t, t + 25 * MIN]];
  const transfer = (t) => [
    [4, "DBRK", "MCAR", t, t + 5 * MIN],
    [1, "MCAR", "EMBR", t + 6 * MIN, t + 21 * MIN],
  ];
  const outbound = [
    transfer(minute(5)),
    direct(7, "DBRK", "EMBR", minute(20)),
    direct(7, "DBRK", "EMBR", NEXT_MORNING),
  ];
  const inbound = (...times) => planResponse(times.map((t) => direct(8, "EMBR", "DBRK", t)));
  const isRow = (c) => c.kind === "stack" && c.size?.height > 0;
  const top = (col) => {
    let y = 0;
    for (const c of col.children) {
      if (isRow(c)) return y;
      y += c.kind === "spacer" ? (c.value ?? 0) : naturalSize(c, []).height;
    }
    throw new Error("no later-train row");
  };
  for (const [name, back, lastLine] of [
    ["later trains on both", inbound(minute(8), minute(22), NEXT_MORNING), false],
    ["a last-train line beside later trains", inbound(minute(8), NEXT_MORNING), true],
  ]) {
    const r = await run({
      family: "medium",
      params: { widget: "dbrk embr" },
      fixtures: { ...BROKEN_FEED, depart: { "DBRK-EMBR": planResponse(outbound), "EMBR-DBRK": back } },
      ...ETD_DOWN,
    });
    assertClean(r);
    const row = r.widget.children[0].children.find(
      (c) => c.kind === "stack" && c.children.filter((k) => k.kind === "stack").length === 2,
    );
    const [left, right] = row.children.filter((c) => c.kind === "stack");
    expect(
      symbols(left).some((s) => s.startsWith("arrow.triangle.branch")),
      `${name} transfer`,
    ).toEqual(true);
    expect(
      symbols(right).some((s) => s.startsWith("arrow.triangle.branch")),
      `${name} direct`,
    ).toEqual(false);
    expect(texts(right).includes("last train"), `${name} words`).toEqual(lastLine);
    expect(top(right), name).toEqual(top(left));
  }
});

test("the updated line ends the Medium header and Small has none", async () => {
  const isUpdated = (c) => c.kind === "stack" && c.children.some((k) => k.value === "arrow.clockwise");
  const lineOf = (w) => nodes(w).find((c) => c.children.some(isUpdated));
  expect(lineOf((await run({ family: "small" })).widget), "small").toBeUndefined();
  const medium = lineOf((await run({ family: "medium" })).widget);
  expect(
    medium.children.at(-1).children.some((k) => k.value === "arrow.clockwise"),
    "medium",
  ).toEqual(true);
  expect(symbols(medium)[0], "medium header").toEqual("tram.fill BART Commuter");
  expect(nodes(medium).find((c) => c.value === "updated").font, "medium size").toEqual("systemFont(9)");
});

test("a last train is marked in plain gray on the Home Screen, regular weight, at the size of later trains", async () => {
  const fixtures = withLastPlannedTrip(BROKEN_FEED, "WOAK-EMBR");
  const expectedFont = {
    small: "systemFont(11)",
    medium: "systemFont(12)",
    large: "systemFont(14)",
    accessoryRectangular: "systemFont(10)",
  };
  for (const family of ["small", "medium", "large", "accessoryRectangular"]) {
    const r = await run({ fixtures, family, ...ETD_DOWN });
    assertClean(r);
    const words = nodes(r.widget).find((c) => c.value === "last train");
    expect(words.font, `${family} font`).toEqual(expectedFont[family]);
    if (family === "accessoryRectangular") continue;
    const flag = nodes(r.widget).find((c) => c.value === "flag.checkered");
    expect(words.textColor.lightHex, `${family} words`).toEqual("#6c6c70");
    expect(flag.tintColor.lightHex, `${family} flag`).toEqual("#6c6c70");
  }
});

test("a Small widget enlarges its text as fewer trips have a transfer row", async () => {
  const direct = (route, from, to, t) => [[route, from, to, t, t + 25 * MIN]];
  const outboundTransfer = (t) => [
    [4, "DBRK", "MCAR", t, t + 5 * MIN],
    [1, "MCAR", "EMBR", t + 6 * MIN, t + 21 * MIN],
  ];
  const inboundTransfer = (t) => [
    [2, "EMBR", "MCAR", t, t + 15 * MIN],
    [3, "MCAR", "DBRK", t + 16 * MIN, t + 21 * MIN],
  ];
  const outboundDirect = (t) => direct(7, "DBRK", "EMBR", t);
  const inboundDirect = (t) => direct(8, "EMBR", "DBRK", t);
  const plan = (trip) => planResponse([minute(5), minute(15), minute(25)].map(trip));
  const fontsOf = (widget) => ({
    departure: nodes(widget).find((n) => n.kind === "text" && /^\d+:\d\d$/.test(n.value)).font,
    later: nodes(widget).find((n) => n.kind === "text" && /^\d+:\d\d[ap]m$/.test(n.value)).font,
  });
  for (const [name, outbound, inbound, departure, later] of [
    ["two", outboundTransfer, inboundTransfer, 24, "10"],
    ["one", outboundTransfer, inboundDirect, 24, "10"],
    ["none", outboundDirect, inboundDirect, 26, "11"],
  ]) {
    const r = await run({
      family: "small",
      params: { widget: "dbrk embr" },
      fixtures: { ...BROKEN_FEED, depart: { "DBRK-EMBR": plan(outbound), "EMBR-DBRK": plan(inbound) } },
      ...ETD_DOWN,
    });
    assertClean(r);
    expect(fontsOf(r.widget), name).toEqual({
      departure: `boldRoundedSystemFont(${departure})`,
      later: `italicSystemFont(${later})`,
    });
  }
  const oneStation = await run({ family: "small", params: { widget: "embr" } });
  expect(fontsOf(oneStation.widget).departure, "one station").toEqual("boldRoundedSystemFont(30)");
});

test("a Small heading uses the largest size that shows full names unshrunk", async () => {
  const shown = async (widget, sizes) => {
    const r = await run({ family: "small", params: { widget } });
    const names = nodes(r.widget).filter((n) => n.kind === "text" && n.font.startsWith("boldSystemFont"));
    expect([...new Set(names.map((n) => n.font))], widget).toEqual([`boldSystemFont(${sizes})`]);
  };
  await shown("rock powl", 13);
  await shown("woak embr", 11);
});

test("later trains are italic and carry no label", async () => {
  const laterTimes = (widget) => nodes(widget).filter((n) => n.kind === "text" && /^\d+:\d\d ?[ap]m$/.test(n.value));
  for (const [family, font] of [
    ["small", "italicSystemFont(11)"],
    ["medium", "italicSystemFont(12)"],
    ["large", "italicSystemFont(14)"],
  ]) {
    const { widget } = await run({ family });
    expect(new Set(laterTimes(widget).map((n) => n.font)), family).toEqual(new Set([font]));
    expect(nodes(widget).filter((n) => n.value === "next:").length, `${family} label`).toEqual(0);
  }
});

test("later trains use a lower-case period and the next departure an upper-case one", async () => {
  const r = await run({ family: "medium" });
  assertClean(r);
  const times = texts(r.widget).filter((t) => /^\d+:\d\d ?[AaPp]/.test(t));
  expect(/ [AP]M$/.test(times[0]), "next departure").toEqual(true);
  expect(/\d[ap]m$/.test(times[1]), "later train").toEqual(true);
});
