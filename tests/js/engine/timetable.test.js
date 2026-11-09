// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

// The timetable as the last resort, and the last train of the service.

import { expect, test } from "bun:test";
import { symbols, texts } from "../support/harness.js";
import { assertClean } from "../support/assertions.js";
import {
  B,
  BROKEN_FEED,
  ETD_DOWN,
  ETD_EMBR_WOAK,
  ETD_WOAK_EMBR,
  NO_DATA_COMMUTE,
  NO_DATA_SYMBOL,
  ONE_TRAIN_WOAK,
  RT_EMBR_WOAK,
  RT_WOAK_EMBR,
  SCHEDULED,
  Y,
  dots,
  minute,
  run,
  scheduleRequests,
  setDeparture,
  withLastPlannedTrip,
} from "../support/engine.js";

// timetable departures after an ETD outage and a GTFS-RT failure
const SCHEDULED_WOAK_EMBR = ["West Oakland", "to Embarcadero", "11:13 PM", "11:24pm", "11:33pm"];
const SCHEDULED_EMBR_WOAK = ["Embarcadero", "to West Oakland", "11:10 PM", "11:20pm", "11:30pm"];

test("live data failure falls back to the timetable, marked as scheduled", async () => {
  const r = await run({ family: "medium", ...SCHEDULED });
  assertClean(r);
  expect(texts(r.widget), "medium widget").toEqual([...SCHEDULED_WOAK_EMBR, ...SCHEDULED_EMBR_WOAK]);
  expect(dots(r.widget), "line markers").toEqual([Y, B, Y, B, Y, B]);
  expect(scheduleRequests(r).length, "schedule requests").toEqual(2);
  expect(r.warnings.length, "warnings").toEqual(4);
});

test("scheduled times are named in speech alone", async () => {
  const expected = {
    small: [
      "West Oakland",
      " \u2192 Embarcadero",
      "11:13 PM",
      "11:24pm",
      "11:33pm",
      "Embarcadero",
      " \u2192 West Oakland",
      "11:10 PM",
      "11:20pm",
      "11:30pm",
    ],
    accessoryRectangular: [
      "BART",
      "West Oakland",
      " \u2192 Embarcadero",
      "(Y) ",
      "11:13 PM",
      "  (B) 11:24pm",
      "Embarcadero",
      " \u2192 West Oakland",
      "(B) ",
      "11:10 PM",
      "  (Y) 11:20pm",
    ],
    accessoryCircular: ["EMBR", "11:10 PM", "to WOAK"],
    accessoryInline: ["WOAK\u2192 11:13 PM  EMBR\u2192 11:10 PM"],
  };
  for (const [family, want] of Object.entries(expected)) {
    const r = await run({ family, ...SCHEDULED });
    assertClean(r);
    expect(texts(r.widget), family).toEqual(want);
  }
  const siri = await run({ mode: "siri", ...SCHEDULED });
  assertClean(siri);
  expect(siri.spoken, "spoken").toEqual(
    "West Oakland to Embarcadero scheduled in 3 minutes, next 14 and 23. Embarcadero to West Oakland scheduled to leave now, next 10 and 20.",
  );
});

test("one station falls back to the station timetable", async () => {
  const r = await run({ family: "medium", ...SCHEDULED, params: { widget: "embr" } });
  assertClean(r);
  expect(texts(r.widget), "medium widget").toEqual(["Embarcadero", "11:10 PM", "11:11pm", "11:20pm"]);
  expect(scheduleRequests(r).length, "schedule requests").toEqual(1);
});

test("live GTFS-RT data is preferred over the timetable", async () => {
  const r = await run({ family: "medium", ...ETD_DOWN });
  assertClean(r);
  expect(texts(r.widget), "medium widget").toEqual([...RT_WOAK_EMBR, ...RT_EMBR_WOAK]);
  // a commute asks the trip planner only for trips with a transfer
  expect(
    scheduleRequests(r).filter((u) => !u.includes("cmd=depart")),
    "timetable requests",
  ).toEqual([]);
  expect(r.warnings.length, "warnings").toEqual(2);
});

test("scheduled times after midnight stay in the same service day", async () => {
  // 12:20 AM
  const now = minute(70);
  const r = await run({ now, family: "large", ...SCHEDULED, params: { widget: "woak" } });
  assertClean(r);
  // after midnight the API defaults to the timetable of the next service day
  expect(scheduleRequests(r)[0], "service date").toContain("&date=11%2F05%2F2026&");
  expect(texts(r.widget), "large widget").toEqual([
    "West Oakland",
    "12:24 AM",
    "12:29 am",
    "12:33 am",
    "12:39 am",
    "1:03 am",
  ]);
});

test("a sole live train confirmed by the timetable is the last train", async () => {
  const fixtures = withLastPlannedTrip(ONE_TRAIN_WOAK, "WOAK-EMBR");
  const r = await run({ fixtures, family: "medium" });
  assertClean(r);
  expect(texts(r.widget), "medium widget").toEqual([...ETD_WOAK_EMBR.slice(0, 3), "last train", ...ETD_EMBR_WOAK]);
  const small = await run({ fixtures, family: "small" });
  expect(texts(small.widget).slice(0, 4), "small widget").toEqual([
    "West Oakland",
    " \u2192 Embarcadero",
    "11:16 PM",
    "last train",
  ]);
  const rect = await run({ fixtures, family: "accessoryRectangular" });
  expect(texts(rect.widget).slice(0, 6), "rectangular").toEqual([
    "BART",
    "West Oakland",
    " \u2192 Embarcadero",
    "(Y) ",
    "11:16 PM",
    "last train",
  ]);
  const siri = await run({ fixtures, mode: "siri" });
  expect(siri.spoken.split(". ")[0], "spoken").toEqual("West Oakland to Embarcadero in 6 minutes, last train");
});

test("a sole live train is not the last when the timetable continues", async () => {
  // the default plan lists more trips tonight than it returns
  const r = await run({ fixtures: ONE_TRAIN_WOAK, family: "medium" });
  assertClean(r);
  expect(texts(r.widget), "medium widget").toEqual([...ETD_WOAK_EMBR.slice(0, 3), ...ETD_EMBR_WOAK]);
  // a timetable trip after the live train means more trains may come
  const later = withLastPlannedTrip(ONE_TRAIN_WOAK, "WOAK-EMBR");
  const plan = JSON.parse(later.depart["WOAK-EMBR"]);
  setDeparture(plan.root.schedule.request.trip[Number(plan.root.schedule.before)], minute(10));
  const r2 = await run({
    fixtures: { ...later, depart: { ...later.depart, "WOAK-EMBR": JSON.stringify(plan) } },
    family: "medium",
  });
  assertClean(r2);
  expect(texts(r2.widget), "later timetable trip").toEqual([...ETD_WOAK_EMBR.slice(0, 3), ...ETD_EMBR_WOAK]);
});

test("a trip plan of one trip, which the planner gives as an object, is read", async () => {
  const plan = JSON.parse(BROKEN_FEED.depart["WOAK-EMBR"]);
  const { before, request } = plan.root.schedule;
  request.trip = request.trip[Number(before)];
  const fixtures = { ...BROKEN_FEED, depart: { ...BROKEN_FEED.depart, "WOAK-EMBR": JSON.stringify(plan) } };
  const r = await run({ fixtures, family: "medium", ...ETD_DOWN });
  assertClean(r);
  expect(texts(r.widget).slice(0, 3), "medium widget").toEqual(["West Oakland", "to Embarcadero", "11:13 PM"]);
});

test("a sole timetable train is the last when the plan reaches the next service day", async () => {
  const fixtures = withLastPlannedTrip(BROKEN_FEED, "WOAK-EMBR");
  const r = await run({ fixtures, family: "medium", ...ETD_DOWN });
  assertClean(r);
  expect(texts(r.widget).slice(0, 4), "medium widget").toEqual([
    "West Oakland",
    "to Embarcadero",
    "11:13 PM",
    "last train",
  ]);
});

test("timetable failure after live failures leaves the boards without data", async () => {
  const r = await run({ family: "medium", ...SCHEDULED, scheduleFailure: "not-json" });
  assertClean(r);
  expect(texts(r.widget), "medium widget").toEqual(NO_DATA_COMMUTE);
  expect(symbols(r.widget).slice(2, 4), "no data symbols").toEqual([NO_DATA_SYMBOL, NO_DATA_SYMBOL]);
  expect(r.warnings.length, "warnings").toEqual(6);
});
