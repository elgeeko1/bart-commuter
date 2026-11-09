// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

// Checks against freshly captured BART data, in the directory BART_CAPTURE names.

import { expect, test } from "bun:test";
import { ETD_STATIONS, readFixtures, readText, runScript, texts } from "../js/support/harness.js";
import { assertClean } from "../js/support/assertions.js";

const CAPTURE = process.env.BART_CAPTURE;

const PLANS = ["WOAK-EMBR", "EMBR-WOAK", "POWL-ROCK", "ROCK-POWL", "ROCK-FRMT", "FRMT-ROCK"];
// Rockridge and Fremont share no line; the capture adds ETD where each leg of their trips starts
const TRANSFER_PLANS = ["ROCK-FRMT", "FRMT-ROCK"];
const legStations = TRANSFER_PLANS.flatMap((pair) =>
  JSON.parse(readText(`${CAPTURE}/depart-${pair}.json`)).root.schedule.request.trip.flatMap((t) =>
    t.leg.map((l) => l["@origin"]),
  ),
);
const LIVE = readFixtures(CAPTURE, {
  withEtd: true,
  plans: PLANS,
  stations: [...new Set([...ETD_STATIONS, ...legStations])],
});
// ETD rounds to whole minutes from its own clock, so the sources may differ by a minute or two
const TOLERANCE_MIN = 2;
// commutes and a single station, each compared between sources
const STATION_LISTS = ["woak embr", "powl rock", "embr"];
// a GTFS-RT field overrunning its parent message, which forces the timetable fallback
const BROKEN_FEED = [0x0a, 0x02, 0x12, 0x05, 1, 2, 3, 4, 5];
// legacy API sources simulated as rejecting the key, leaving GTFS-RT
const GTFS_RT_ONLY = { etdFailure: "invalid-key", scheduleFailure: "invalid-key" };

function run(opts) {
  return runScript({ now: Date.now(), fixtures: LIVE, ...opts });
}

// first-departure minutes per sentence of the spoken summary; null when a board has no trains or no data
function firstMinutes(summary) {
  return summary.split(". ").map((sentence) => {
    if (sentence.startsWith("No trains") || sentence.startsWith("No data")) return null;
    if (sentence.includes("leaving now")) return 0;
    const m = sentence.match(/ in (\d+) minutes?/);
    if (!m) throw new Error(`unparsed summary sentence: ${sentence}`);
    return Number(m[1]);
  });
}

test("GTFS-RT alone renders boards", async () => {
  for (const widget of STATION_LISTS) {
    const r = await run({ family: "medium", ...GTFS_RT_ONLY, params: { widget } });
    assertClean(r);
    expect(texts(r.widget), widget).not.toContain("No data");
    expect(
      r.warnings.filter((w) => w.includes(" GTFS-RT: ")),
      `${widget} GTFS-RT warnings`,
    ).toEqual([]);
  }
});

test("shipped API key is accepted and ETD renders the default commute", async () => {
  const r = await run({ family: "medium" });
  assertClean(r);
  const t = texts(r.widget);
  for (const name of ["West Oakland", "Embarcadero"]) expect(t, "medium widget").toContain(name);
  expect(r.warnings, "warnings").toEqual([]);
});

test("schedule API accepts the shipped key and serves the default commute", async () => {
  const r = await run({ family: "medium", etdFailure: "not-json", fixtures: { ...LIVE, feed: BROKEN_FEED } });
  assertClean(r);
  expect(texts(r.widget), "medium widget").not.toContain("No data");
  expect(r.warnings.length, "warnings").toEqual(4);
});

test("trips with a transfer serve stations without a shared line", async () => {
  const r = await run({ mode: "shortcuts", params: { shortcut: "rock frmt" } });
  assertClean(r);
  // after the last connection of the night a board has no trips
  for (const sentence of r.output.split(". ")) {
    if (sentence.startsWith("No data")) throw new Error(`no data: ${r.output}`);
    if (!sentence.startsWith("No trains") && !sentence.includes(", transfer at "))
      throw new Error(`no transfer: ${sentence}`);
  }
  const medium = await run({ family: "medium", params: { widget: "rock frmt" } });
  assertClean(medium);
});

test("ETD and GTFS-RT agree on the next departure", async () => {
  for (const shortcut of STATION_LISTS) {
    const etd = await run({ mode: "shortcuts", params: { shortcut } });
    const rt = await run({ mode: "shortcuts", ...GTFS_RT_ONLY, params: { shortcut } });
    assertClean(etd);
    assertClean(rt);
    const a = firstMinutes(etd.output);
    const b = firstMinutes(rt.output);
    a.forEach((m, i) => {
      // a train in its last minute may already be gone from one source
      if (m === null || b[i] === null || m <= 1) return;
      if (Math.abs(m - b[i]) > TOLERANCE_MIN)
        throw new Error(`${shortcut} board ${i}: ETD ${m} min, GTFS-RT ${b[i]} min`);
    });
  }
});
