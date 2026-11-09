// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

// How the script is launched and what it reads from its parameters, and the service day boundary.

import { expect, test } from "bun:test";
import { texts } from "../support/harness.js";
import { assertClean } from "../support/assertions.js";
import {
  BROKEN_FEED,
  ETD_DOWN,
  ETD_EMBR_WOAK,
  ETD_WOAK_EMBR,
  run,
  scheduleRequests,
  withEtd,
} from "../support/engine.js";

// a Siri run for Oakland Airport with ETD and the feed down, from a station timetable of BART-local "hh:mm AM" times
function runOakl(now, times) {
  const items = times.map((t) => ({ "@line": "ROUTE 19", "@origTime": t }));
  const fixtures = { ...BROKEN_FEED, schedule: { OAKL: JSON.stringify({ root: { station: { item: items } } }) } };
  return run({ now, mode: "siri", params: { shortcut: "oakl" }, ...ETD_DOWN, fixtures });
}

test("the service day starts at 3 AM on the night daylight time starts", async () => {
  // 3:30 AM PDT on 2027-03-14, an hour of wall clock after 2 AM PST
  const r = await runOakl(Date.UTC(2027, 2, 14, 10, 30), ["05:00 AM", "07:58 AM"]);
  assertClean(r);
  expect(
    scheduleRequests(r).map((u) => /&date=([^&]+)&/.exec(u)[1]),
    "service date",
  ).toContain("03%2F14%2F2027");
  expect(r.output, "output").toEqual("Oakland Airport scheduled in 90 minutes, next 268.");
});

test("a time repeated when daylight time ends is its first occurrence", async () => {
  const times = ["11:50 PM", "01:43 AM"];
  // 1:20 AM PDT on 2027-11-07, before the clock falls back
  const before = await runOakl(Date.UTC(2027, 10, 7, 8, 20), times);
  assertClean(before);
  expect(before.output, "before").toEqual("Oakland Airport scheduled in 23 minutes, last train.");
  // 1:20 AM PST, an hour later, when the 1:43 AM train has left
  const after = await runOakl(Date.UTC(2027, 10, 7, 9, 20), times);
  assertClean(after);
  expect(
    scheduleRequests(after).map((u) => /&date=([^&]+)&/.exec(u)[1]),
    "service date",
  ).toContain("11%2F06%2F2027");
  expect(after.output, "after").toEqual("No trains, Oakland Airport.");
});

test("Shortcuts input selects stations without speaking", async () => {
  const r = await run({ mode: "shortcuts", params: { shortcut: "rock powl" } });
  assertClean(r);
  expect(r.output, "shortcut output").toEqual(
    "Rockridge to Powell St in 10 minutes, next 30 and 50. Powell St to Rockridge in 9 minutes, next 28 and 48.",
  );
  expect(r.spoken, "spoken").toEqual(null);
  expect(r.presented, "presented").toEqual(null);
});

test("in-app run presents a medium widget for the URL query stations", async () => {
  const r = await run({ mode: "app", params: { query: "rock powl" } });
  assertClean(r);
  expect(texts(r.presented).slice(0, 2), "presented widget").toEqual(["Rockridge", "to Powell St"]);
  const defaults = await run({ mode: "app" });
  assertClean(defaults);
  expect(texts(defaults.presented), "default stations").toEqual([...ETD_WOAK_EMBR, ...ETD_EMBR_WOAK]);
});

test("station lists ignore case, spacing, and punctuation", async () => {
  for (const widget of ["woak embr", "WOAK,EMBR", " woak - embr ", "Woak/Embr", "\n  woak   embr\n"]) {
    const r = await run({ family: "accessoryInline", params: { widget } });
    assertClean(r);
    expect(texts(r.widget), JSON.stringify(widget)).toEqual(["WOAK\u2192 11:16 PM  EMBR\u2192 11:11 PM"]);
  }
});

test("empty or punctuation-only station lists use the defaults", async () => {
  for (const widget of ["", "   ", " , ; "]) {
    const r = await run({ family: "accessoryInline", params: { widget } });
    assertClean(r);
    expect(texts(r.widget), JSON.stringify(widget)).toEqual(["WOAK\u2192 11:16 PM  EMBR\u2192 11:11 PM"]);
  }
});

test("a leaving train shows its departure time", async () => {
  const fixtures = withEtd("EMBR", (json) => {
    const dublin = json.root.station[0].etd.find((e) => e.abbreviation === "DUBL");
    dublin.estimate[0].minutes = "Leaving";
  });
  const r = await run({ fixtures, family: "accessoryCircular", params: { widget: "woak embr" } });
  assertClean(r);
  expect(texts(r.widget), "circular").toEqual(["EMBR", "11:10 PM", "to WOAK"]);
});

test("cancelled ETD departures are dropped", async () => {
  const fixtures = withEtd("ROCK", (json) => {
    json.root.station[0].etd.find((e) => e.abbreviation === "MLBR").estimate[0].cancelflag = "1";
  });
  const r = await run({ fixtures, family: "accessoryInline", params: { widget: "rock powl" } });
  assertClean(r);
  expect(texts(r.widget), "inline").toEqual(["ROCK\u2192 11:40 PM  POWL\u2192 11:19 PM"]);
});

test("invalid station lists show an error in widgets and throw elsewhere", async () => {
  const cases = {
    "XYZ embr": "unknown station: XYZ",
    "powl n": "unknown station: N",
    "woak embr powl": "max two stations",
    "powl POWL": "same station twice: POWL",
  };
  for (const [widget, message] of Object.entries(cases)) {
    const r = await run({ family: "medium", params: { widget } });
    expect(texts(r.widget), widget).toEqual([message]);
  }
  const app = await run({ mode: "app", params: { query: "rock xyz" } });
  expect(app.thrown, "in-app error").not.toBeNull();
  expect(app.requests, "requests before validation").toEqual([]);
});
