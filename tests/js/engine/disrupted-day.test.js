// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

// A disrupted morning, step by step, from normal service through a Transbay Tube hold to recovery.

import { expect, test } from "bun:test";
import { directPlan, etdResponse, feedOfTrips, runScript, symbols, texts } from "../support/harness.js";
import { cancelled, failedSources, replayStep, westbound } from "../support/engine.js";

// 7:50 AM standard time on a Tuesday
const DISRUPTED_DAY_START = Date.UTC(2026, 10, 3, 15, 50);
const WESTBOUND = ["M10-1", "M16-1"];
const EASTBOUND = ["M16-2", "M10-2"];
const SKIPS_EMBARCADERO = { platforms: ["M10-1", "M16-1", "M20-1"], skipped: [1] };
const eastbound = (lists) =>
  etdResponse(
    "EMBR",
    "Embarcadero",
    lists.map(([color, dest, name, estimates]) => [color, dest, name, "North", estimates]),
  );

/**
 * One step of the disrupted day.
 * @param {number} min  minutes after DISRUPTED_DAY_START
 * @param {{ woak?: string, embr?: string, feed?: object[], feedAgeSec?: number, brokenFeed?: boolean,
 *   etdDown?: boolean, scheduleDown?: boolean, planMins?: number[] }} sources  ETD responses and GTFS-RT
 *   trips; the trip plan has a Yellow train planMins after each step in both directions, by default 8,
 *   28, 48 and 68
 */
function disruptedStep(min, sources) {
  const now = DISRUPTED_DAY_START + min * 60000;
  const departures = (sources.planMins ?? [8, 28, 48, 68]).map((m) => now + m * 60000);
  const feed = feedOfTrips(sources.feed ?? [], { now, ageSec: sources.feedAgeSec ?? 0 });
  const fixtures = {
    etd: { WOAK: sources.woak ?? westbound([]), EMBR: sources.embr ?? eastbound([]) },
    schedule: {},
    depart: {
      "WOAK-EMBR": directPlan(1, "WOAK", "EMBR", departures, 7),
      "EMBR-WOAK": directPlan(2, "EMBR", "WOAK", departures, 7),
    },
    feed: sources.brokenFeed ? feed.slice(0, 20) : feed,
  };
  const opts = {
    now,
    fixtures,
    params: { widget: "woak embr" },
    etdFailure: sources.etdDown ? "not-json" : null,
    scheduleFailure: sources.scheduleDown ? "not-json" : null,
  };
  return { now, run: (more) => runScript({ ...opts, ...more }) };
}

test("disrupted day 7:50 AM: normal service", async () => {
  const r = await replayStep(
    disruptedStep(0, {
      woak: westbound([
        ["YELLOW", "SFIA", "SF Airport", [4, 24]],
        ["RED", "MLBR", "Millbrae", [9, 29]],
        ["GREEN", "DALY", "Daly City", [14]],
      ]),
      embr: eastbound([
        ["RED", "RICH", "Richmond", [2, 22]],
        ["BLUE", "DUBL", "Dublin/Pleasanton", [7]],
        ["YELLOW", "ANTC", "Antioch", [12]],
      ]),
    }),
  );
  expect(r.output, "output").toEqual(
    "West Oakland to Embarcadero in 4 minutes, next 9 and 14. Embarcadero to West Oakland in 2 minutes, next 7 and 12.",
  );
  expect(failedSources(r), "failed sources").toEqual([]);
});

test("disrupted day 8:10 AM: a hold delays trains and cancels some", async () => {
  const r = await replayStep(
    disruptedStep(20, {
      woak: westbound([
        ["YELLOW", "SFIA", "SF Airport", [23, 43]],
        ["RED", "MLBR", "Millbrae", [cancelled(28)]],
        ["GREEN", "DALY", "Daly City", [cancelled(33)]],
        ["BLUE", "DALY", "Daly City", [52]],
      ]),
      embr: eastbound([
        ["RED", "RICH", "Richmond", [cancelled(16), 36]],
        ["BLUE", "DUBL", "Dublin/Pleasanton", [41]],
      ]),
    }),
  );
  // the Blue train is past the end of the Yellow list, where a Yellow train may be missing
  expect(r.output, "output").toEqual(
    "West Oakland to Embarcadero in 23 minutes, next 43. Embarcadero to West Oakland in 36 minutes, next 41.",
  );
  expect(texts(r.widgets.medium.widget).slice(4, 7), "medium return trip").toEqual([
    "Embarcadero",
    "to West Oakland",
    "8:46 AM",
  ]);
});

test("disrupted day 8:15 AM: a train ETD reports cancelled stays off the board though the feed lists it", async () => {
  const r = await replayStep(
    disruptedStep(25, {
      woak: westbound([["RED", "MLBR", "Millbrae", [cancelled(8)]]]),
      embr: eastbound([["RED", "RICH", "Richmond", [6]]]),
      feed: [
        { platforms: WESTBOUND, firstMin: 8 },
        { platforms: WESTBOUND, firstMin: 15 },
        { platforms: EASTBOUND, firstMin: 6 },
      ],
    }),
  );
  expect(r.output, "output").toEqual(
    "West Oakland to Embarcadero in 15 minutes. Embarcadero to West Oakland in 6 minutes.",
  );
});

test("disrupted day 8:30 AM: next trains over an hour away show their times", async () => {
  const r = await replayStep(
    disruptedStep(40, {
      woak: westbound([["BLUE", "DALY", "Daly City", [75]]]),
      embr: eastbound([["GREEN", "BERY", "Berryessa", [68]]]),
    }),
  );
  // a sole train is not the last while the timetable has later trains
  expect(r.output, "output").toEqual(
    "West Oakland to Embarcadero in 75 minutes. Embarcadero to West Oakland in 68 minutes.",
  );
  const medium = r.widgets.medium.widget;
  expect(texts(medium), "medium").toEqual([
    "West Oakland",
    "to Embarcadero",
    "9:45 AM",
    "Embarcadero",
    "to West Oakland",
    "9:38 AM",
  ]);
  expect(texts(r.widgets.small.widget).slice(0, 3), "small").toEqual([
    "West Oakland",
    " \u2192 Embarcadero",
    "9:45 AM",
  ]);
  expect(texts(r.widgets.accessoryInline.widget), "inline").toEqual(["WOAK\u2192 9:45 AM  EMBR\u2192 9:38 AM"]);
  expect(texts(r.widgets.accessoryCircular.widget), "circular").toEqual(["WOAK", "9:45 AM", "to EMBR"]);
});

test("disrupted day 8:50 AM: no trains predicted in one direction leave the timetable past the feed horizon", async () => {
  const r = await replayStep(disruptedStep(60, { embr: eastbound([["RED", "RICH", "Richmond", [12, 40]]]) }));
  expect(r.output, "output").toEqual(
    "West Oakland to Embarcadero scheduled in 48 minutes, next 68. Embarcadero to West Oakland in 12 minutes, next 40.",
  );
  expect(texts(r.widgets.small.widget).slice(0, 3), "small").toEqual([
    "West Oakland",
    " \u2192 Embarcadero",
    "9:38 AM",
  ]);
});

test("disrupted day 8:55 AM: no trains predicted in one direction and none left in the timetable", async () => {
  // the next westbound trip in the plan leaves on the next service day
  const r = await replayStep(
    disruptedStep(65, { embr: eastbound([["RED", "RICH", "Richmond", [7, 35]]]), planMins: [24 * 60] }),
  );
  expect(r.output, "output").toEqual(
    "No trains, West Oakland to Embarcadero. Embarcadero to West Oakland in 7 minutes, next 35.",
  );
  expect(texts(r.widgets.small.widget).slice(0, 3), "small").toEqual([
    "West Oakland",
    " \u2192 Embarcadero",
    "Embarcadero",
  ]);
  expect(symbols(r.widgets.small.widget)[0], "small no trains").toEqual("nosign");
});

test("disrupted day 9:10 AM: ETD down, GTFS-RT drops cancelled trips and skipped stops", async () => {
  const r = await replayStep(
    disruptedStep(80, {
      etdDown: true,
      feed: [
        { platforms: WESTBOUND, firstMin: 3, relationship: 3 },
        { ...SKIPS_EMBARCADERO, firstMin: 6 },
        { platforms: WESTBOUND, firstMin: 11 },
        { platforms: EASTBOUND, firstMin: 8 },
        { platforms: EASTBOUND, firstMin: 50 },
      ],
    }),
  );
  // the 50-minute train is past the feed horizon, where later trains may be missing
  expect(r.output, "output").toEqual(
    "West Oakland to Embarcadero in 11 minutes. Embarcadero to West Oakland in 8 minutes.",
  );
  expect(
    texts(r.widgets.medium.widget).map((t) => t.slice(-1)),
    "live times",
  ).not.toContain("*");
});

test("disrupted day 9:20 AM: ETD down and no westbound trains in the feed leave the timetable past its horizon", async () => {
  const r = await replayStep(disruptedStep(90, { etdDown: true, feed: [{ platforms: EASTBOUND, firstMin: 4 }] }));
  // the timetable trains 8 and 28 minutes away are missing from the live feed, so they are not running
  expect(r.output, "output").toEqual(
    "West Oakland to Embarcadero scheduled in 48 minutes, next 68. Embarcadero to West Oakland in 4 minutes.",
  );
});

test("disrupted day 9:25 AM: a trip plan ending within the feed horizon is no data", async () => {
  const r = await replayStep(
    disruptedStep(95, { etdDown: true, feed: [{ platforms: EASTBOUND, firstMin: 4 }], planMins: [5, 10, 15, 20] }),
  );
  expect(r.output, "output").toEqual("No data, West Oakland to Embarcadero. Embarcadero to West Oakland in 4 minutes.");
});

test("disrupted day 9:30 AM: ETD down and a stale feed fall back to the timetable", async () => {
  const r = await replayStep(
    disruptedStep(100, { etdDown: true, feed: [{ platforms: WESTBOUND, firstMin: 5 }], feedAgeSec: 600 }),
  );
  expect(r.output, "output").toEqual(
    "West Oakland to Embarcadero scheduled in 8 minutes, next 28 and 48. Embarcadero to West Oakland scheduled in 8 minutes, next 28 and 48.",
  );
  expect(failedSources(r), "failed sources").toContain("WOAK-EMBR GTFS-RT");
  expect(texts(r.widgets.accessoryRectangular.widget).slice(0, 5), "rectangular").toEqual([
    "BART",
    "West Oakland",
    " \u2192 Embarcadero",
    "(Y) ",
    "9:38 AM",
  ]);
});

test("disrupted day 9:50 AM: every source down shows no data", async () => {
  const r = await replayStep(disruptedStep(120, { etdDown: true, brokenFeed: true, scheduleDown: true }));
  expect(r.output, "output").toEqual("No data, West Oakland to Embarcadero. No data, Embarcadero to West Oakland.");
  expect(texts(r.widgets.accessoryInline.widget), "inline").toEqual(["WOAK\u2192 --  EMBR\u2192 --"]);
});

test("disrupted day 10:15 AM: service recovers", async () => {
  const r = await replayStep(
    disruptedStep(145, {
      woak: westbound([
        ["YELLOW", "SFIA", "SF Airport", [3, 23]],
        ["RED", "MLBR", "Millbrae", [8]],
      ]),
      embr: eastbound([
        ["GREEN", "BERY", "Berryessa", [5]],
        ["BLUE", "DUBL", "Dublin/Pleasanton", [10]],
      ]),
    }),
  );
  expect(r.output, "output").toEqual(
    "West Oakland to Embarcadero in 3 minutes, next 8 and 23. Embarcadero to West Oakland in 5 minutes, next 10.",
  );
  expect(failedSources(r), "failed sources").toEqual([]);
});
