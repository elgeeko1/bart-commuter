// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

// What a board lists: lines, turn-backs, one-station boards and Siri, against the captured responses.

import { expect, test } from "bun:test";
import { texts } from "../support/harness.js";
import { assertClean } from "../support/assertions.js";
import {
  B,
  BROKEN_FEED,
  CAPTURED,
  ETD_DOWN,
  ETD_EMBR_WOAK,
  ETD_WOAK_EMBR,
  NOW,
  PUBLIC_KEY,
  Y,
  dots,
  etdRequests,
  failedSources,
  gtfsRtRequests,
  queryOf,
  run,
  withEtd,
} from "../support/engine.js";

test("captured fixtures answer the queries the script sends", async () => {
  // query parameters compared: trips before and after now in a trip plan, and the date of a timetable
  const compared = { depart: ["b", "a"], stnsched: ["date"] };
  const seen = new Set();
  for (const stations of ["woak embr", "woak", "embr"]) {
    // without live trains, a single station falls back to its timetable
    const r = await run({ family: "medium", params: { widget: stations }, ...ETD_DOWN, fixtures: BROKEN_FEED });
    for (const url of r.requests) {
      const sent = queryOf(url);
      if (!(sent.cmd in compared)) continue;
      const key = sent.cmd === "depart" ? `${sent.orig}-${sent.dest}` : sent.orig;
      const fixture = sent.cmd === "depart" ? CAPTURED.depart[key] : CAPTURED.schedule[key];
      const captured = queryOf(JSON.parse(fixture).root.uri["#cdata-section"]);
      for (const name of compared[sent.cmd]) expect(captured[name], `${sent.cmd} ${key} ${name}`).toEqual(sent[name]);
      seen.add(`${sent.cmd} ${key}`);
    }
  }
  expect([...seen].sort(), "fixtures compared").toEqual([
    "depart EMBR-WOAK",
    "depart WOAK-EMBR",
    "stnsched EMBR",
    "stnsched WOAK",
  ]);
});

test("shipped script: default commute comes from ETD with the public BART key", async () => {
  const r = await run({ family: "medium" });
  assertClean(r);
  expect(texts(r.widget), "medium widget").toEqual([...ETD_WOAK_EMBR, ...ETD_EMBR_WOAK]);
  expect(etdRequests(r).length, "ETD requests").toEqual(2);
  for (const url of etdRequests(r)) expect(url, "ETD request key").toContain(`key=${PUBLIC_KEY}`);
  // the feed adds any train ETD omits
  expect(gtfsRtRequests(r).length, "GTFS-RT requests").toEqual(1);
  expect(failedSources(r), "failed sources").toEqual([]);
  expect(r.widget.refreshAfterDate.getTime(), "refresh date").toEqual(NOW + 60 * 1000);
});

test("commute merges every direct line, each departure marked with its line color", async () => {
  const r = await run({ family: "medium" });
  assertClean(r);
  expect(dots(r.widget), "line markers").toEqual([Y, B, Y, B, Y, B]);
});

test("commute excludes lines that do not serve both stations", async () => {
  // Powell St also has Blue trains northbound, which do not stop at Rockridge
  const r = await run({ family: "medium", params: { widget: "powl rock" } });
  assertClean(r);
  expect(texts(r.widget), "medium widget").toEqual([
    ...["Powell St", "to Rockridge", "11:19 PM", "11:38pm", "11:58pm"],
    ...["Rockridge", "to Powell St", "11:20 PM", "11:40pm", "12:00am"],
  ]);
  expect(dots(r.widget), "line markers").toEqual([Y, Y, Y, Y, Y, Y]);
});

test("commute excludes trains that turn back before the second station", async () => {
  // Antioch trains relabeled to end at MacArthur, which is before Rockridge, with the feed down because
  // it would list the Antioch trains running through
  const fixtures = withEtd("POWL", (json) => {
    json.root.station[0].etd.find((e) => e.abbreviation === "ANTC").abbreviation = "MCAR";
  });
  const r = await run({
    fixtures: { ...fixtures, feed: BROKEN_FEED.feed },
    family: "accessoryInline",
    scheduleFailure: "not-json",
    params: { widget: "powl rock" },
  });
  assertClean(r);
  expect(texts(r.widget), "inline").toEqual(["POWL\u2192 12:20 AM  ROCK\u2192 11:20 PM"]);
  // a sole train is not called the last without the timetable
  expect(failedSources(r), "failed sources").toEqual([
    "POWL-ROCK schedule",
    "ROCK-POWL schedule",
    "POWL-ROCK GTFS-RT",
    "ROCK-POWL GTFS-RT",
  ]);
});

test("one station shows every train leaving it in one column", async () => {
  const r = await run({ family: "medium", params: { widget: "embr" } });
  assertClean(r);
  expect(texts(r.widget), "medium widget").toEqual(["Embarcadero", "11:11 PM", "11:13pm", "11:22pm"]);
  // Blue to Dublin/Pleasanton, Blue to Daly City, then Yellow trains each way
  expect(dots(r.widget), "line markers").toEqual([B, B, Y]);
  expect(etdRequests(r).length, "ETD requests").toEqual(1);
});

test("Siri speaks commute and single-station summaries", async () => {
  const commute = await run({ mode: "siri" });
  assertClean(commute);
  const want =
    "West Oakland to Embarcadero in 6 minutes, next 15 and 24. Embarcadero to West Oakland in 1 minute, next 12 and 21.";
  expect(commute.spoken, "commute spoken").toEqual(want);
  expect(commute.output, "commute shortcut output").toEqual(want);
  const single = await run({ mode: "siri", params: { shortcut: "embr" } });
  assertClean(single);
  expect(single.spoken, "single spoken").toEqual("Embarcadero in 1 minute, next 3 and 12.");
});
