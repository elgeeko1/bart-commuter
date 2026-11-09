// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

// GTFS-RT decoding, validation, and matching trips to a commute.

import { expect, test } from "bun:test";
import { ascii, feedOfTrips, field, symbols, texts, varintField } from "../support/harness.js";
import { assertClean } from "../support/assertions.js";
import {
  BROKEN_FEED,
  FIXTURES,
  MIN,
  NOW,
  NO_DATA,
  NO_DATA_COMMUTE,
  NO_DATA_SYMBOL,
  ONE_TRAIN_WOAK,
  REJECTED_KEY,
  failedSources,
  feedWarnings,
  gtfsRtRequests,
  minute,
  run,
  setDeparture,
  withEtd,
  withLastPlannedTrip,
} from "../support/engine.js";

// West Oakland then Embarcadero platforms
const WESTBOUND = ["M10-1", "M16-1"];

const feedOf = (...trips) => feedOfTrips(trips, { now: NOW });

// a run with this GTFS-RT feed as its only source, every legacy API endpoint rejecting the key
const runFeedOnly = (feed, opts) => run({ ...opts, ...REJECTED_KEY, fixtures: { ...FIXTURES, feed } });

// fixtures whose ETD response for SFO lists no trains
const withEmptySfo = (fixtures) => ({
  ...fixtures,
  etd: { ...fixtures.etd, SFIA: JSON.stringify({ root: { station: [{ name: "SFO", abbr: "SFIA" }] } }) },
});

test("stale GTFS-RT feed with a rejected key shows no data and logs every source", async () => {
  const r = await run({ now: NOW + 10 * 60 * 1000, family: "medium", ...REJECTED_KEY });
  assertClean(r);
  expect(texts(r.widget), "medium widget").toEqual(NO_DATA_COMMUTE);
  expect(feedWarnings(r), "warnings").toEqual(["WOAK-EMBR GTFS-RT", "EMBR-WOAK GTFS-RT"]);
  expect(
    failedSources(r).filter((s) => s.startsWith("WOAK-EMBR")),
    "failed sources",
  ).toEqual(["WOAK-EMBR schedule", "WOAK-EMBR ETD", "WOAK-EMBR GTFS-RT"]);
});

test("truncated GTFS-RT feed is rejected once per board", async () => {
  const r = await run({ family: "accessoryRectangular", ...REJECTED_KEY, fixtures: BROKEN_FEED });
  assertClean(r);
  expect(texts(r.widget), "rectangular").toEqual([
    "BART",
    "West Oakland",
    " \u2192 Embarcadero",
    NO_DATA,
    "Embarcadero",
    " \u2192 West Oakland",
    NO_DATA,
  ]);
  expect(gtfsRtRequests(r).length, "GTFS-RT requests").toEqual(1);
  expect(feedWarnings(r), "warnings").toEqual(["WOAK-EMBR GTFS-RT", "EMBR-WOAK GTFS-RT"]);
});

test("GTFS-RT field overrunning its parent message is rejected and logged", async () => {
  // header (field 1, 2 bytes) holding a field 2 that claims 5 bytes
  const r = await runFeedOnly([0x0a, 0x02, 0x12, 0x05, 1, 2, 3, 4, 5], { family: "medium" });
  assertClean(r);
  expect(texts(r.widget), "medium widget").toEqual(NO_DATA_COMMUTE);
  expect(feedWarnings(r)[0], "warning").toEqual("WOAK-EMBR GTFS-RT");
});

test("ETD trains that may skip the destination are not confirmed", async () => {
  // some southbound Yellow trains to Millbrae pass SFO without stopping
  const r = await run({
    fixtures: withEmptySfo(FIXTURES),
    family: "medium",
    scheduleFailure: "not-json",
    params: { widget: "embr sfia" },
  });
  assertClean(r);
  expect(texts(r.widget).slice(0, 2), "Embarcadero to SFO").toEqual(["Embarcadero", "to SFO"]);
  expect(symbols(r.widget)[2], "Embarcadero to SFO symbol").toEqual(NO_DATA_SYMBOL);
  expect(
    failedSources(r).filter((s) => s.startsWith("EMBR-SFIA")),
    "failed sources",
  ).toEqual(["EMBR-SFIA schedule", "EMBR-SFIA ETD", "EMBR-SFIA GTFS-RT"]);
});

test("ETD trains ending at the destination are confirmed even where some trips skip it", async () => {
  const fixtures = withEtd("EMBR", (json) => {
    for (const e of json.root.station[0].etd) if (e.abbreviation === "MLBR") e.abbreviation = "SFIA";
  });
  const r = await run({ fixtures: withEmptySfo(fixtures), family: "accessoryInline", params: { widget: "embr sfia" } });
  assertClean(r);
  // the empty ETD list at SFO leaves its trains to the feed
  expect(texts(r.widget), "inline").toEqual(["EMBR\u2192 11:22 PM  SFIA\u2192 11:29 PM"]);
});

test("unconfirmed trains after the shown trains do not block a board", async () => {
  // Blue trains relabeled as Red to Millbrae, which always stop at SFO, ahead of the Yellow trains
  const fixtures = withEtd("WOAK", (json) => {
    for (const e of json.root.station[0].etd) {
      if (e.abbreviation === "DALY") {
        e.abbreviation = "MLBR";
        for (const x of e.estimate) x.color = "RED";
      }
      if (e.abbreviation === "MLBR" && e.estimate[0].color === "YELLOW") {
        for (const x of e.estimate) x.minutes = String(Number(x.minutes) + 60);
      }
    }
  });
  const r = await run({ fixtures: withEmptySfo(fixtures), family: "accessoryInline", params: { widget: "woak sfia" } });
  assertClean(r);
  expect(texts(r.widget), "inline").toEqual(["WOAK\u2192 11:25 PM  SFIA\u2192 11:29 PM"]);
});

test("GTFS-RT feed listing final stops is rejected", async () => {
  // a Dublin/Pleasanton train listing Dublin/Pleasanton itself, which the feed normally omits
  const r = await runFeedOnly(feedOf({ platforms: ["L20-1", "L30-1"] }), { family: "medium" });
  assertClean(r);
  expect(texts(r.widget), "medium widget").toEqual(NO_DATA_COMMUTE);
  expect(feedWarnings(r)[0], "warning").toEqual("WOAK-EMBR GTFS-RT");
});

test("GTFS-RT matches a hand-built trip by its listed stops", async () => {
  // departing West Oakland and Embarcadero one and two minutes from NOW
  const r = await runFeedOnly(feedOf({ platforms: WESTBOUND }), { family: "accessoryInline" });
  assertClean(r);
  expect(texts(r.widget), "inline").toEqual(["WOAK\u2192 11:11 PM  EMBR\u2192 --"]);
});

test("GTFS-RT drops cancelled trips and skipped stops", async () => {
  const feed = feedOf(
    { platforms: WESTBOUND, firstMin: 1, relationship: 3 },
    // skips Embarcadero on its way to Montgomery St
    { platforms: [...WESTBOUND, "M20-1"], firstMin: 3, skipped: [1] },
    { platforms: WESTBOUND, firstMin: 5, relationship: 0 },
  );
  const r = await runFeedOnly(feed, { mode: "shortcuts" });
  assertClean(r);
  expect(r.output, "output").toEqual("West Oakland to Embarcadero in 5 minutes. No data, Embarcadero to West Oakland.");
});

test("GTFS-RT departure dated a day before the arrival at its stop is read on the arrival day", async () => {
  // BART dates a departure just after midnight on the previous day when the arrival is before midnight
  const event = (num, ms) => field(num, varintField(2, ms / 1000));
  const stop = (platform, arrives, departs) =>
    field(2, [...field(4, ascii(platform)), ...event(2, arrives), ...event(3, departs)]);
  const trip = [
    ...stop("M10-1", NOW + 5 * MIN, NOW + 5 * MIN + 18000 - 24 * 60 * MIN),
    ...stop("M16-1", NOW + 12 * MIN, NOW + 12 * MIN),
  ];
  const feed = [...field(1, varintField(3, NOW / 1000)), ...field(2, field(3, trip))];
  const r = await runFeedOnly(feed, { mode: "shortcuts" });
  assertClean(r);
  expect(r.output.split(". ")[0], "output").toEqual("West Oakland to Embarcadero in 5 minutes");
});

test("GTFS-RT rejects an unexpected trip schedule relationship", async () => {
  const r = await runFeedOnly(feedOf({ platforms: WESTBOUND, relationship: 6 }), { mode: "shortcuts" });
  assertClean(r);
  expect(failedSources(r), "failed sources").toContain("WOAK-EMBR GTFS-RT");
});

test("GTFS-RT rejects an unexpected stop schedule relationship", async () => {
  const stop = [...field(4, ascii(WESTBOUND[0])), ...field(3, varintField(2, NOW / 1000 + 60)), ...varintField(5, 9)];
  const r = await runFeedOnly([...feedOf(), ...field(2, field(3, field(2, stop)))], { mode: "shortcuts" });
  assertClean(r);
  expect(r.warnings, "warnings").toContain("WOAK-EMBR GTFS-RT: unexpected stop schedule relationship: 9");
});

test("GTFS-RT rejects a field of an unsupported wire type", async () => {
  const r = await runFeedOnly([...feedOf(), (9 << 3) | 3], { mode: "shortcuts" });
  assertClean(r);
  expect(r.warnings, "warnings").toContain("WOAK-EMBR GTFS-RT: unsupported protobuf wire type 3");
});

test("GTFS-RT skips fixed-width fields it does not read", async () => {
  const trip = { platforms: WESTBOUND };
  const plain = await runFeedOnly(feedOf(trip), { mode: "shortcuts" });
  // 0xff bytes would read as an unterminated varint if the decoder did not skip them
  const fixed64 = [(9 << 3) | 1, ...Array(8).fill(0xff)];
  const fixed32 = [(10 << 3) | 5, ...Array(4).fill(0xff)];
  const r = await runFeedOnly([...feedOf(trip), ...fixed64, ...fixed32], { mode: "shortcuts" });
  assertClean(r);
  expect(plain.output, "plain output").toContain("West Oakland to Embarcadero in 1 minute");
  expect(r.output, "output").toEqual(plain.output);
  expect(r.warnings, "warnings").toEqual(plain.warnings);
});

test("GTFS-RT trains past the feed horizon are not shown", async () => {
  const feed = (firstMins) => feedOf(...firstMins.map((firstMin) => ({ platforms: WESTBOUND, firstMin })));
  const both = await runFeedOnly(feed([30, 50]), { mode: "shortcuts" });
  assertClean(both);
  expect(both.output.split(". ")[0], "within the horizon").toEqual("West Oakland to Embarcadero in 30 minutes");
  const late = await runFeedOnly(feed([50]), { mode: "shortcuts" });
  assertClean(late);
  expect(late.output.split(". ")[0], "past the horizon").toEqual("No data, West Oakland to Embarcadero");
});

test("a live train a minute ahead of its timetable time is still the last train", async () => {
  // the sole ETD train leaves at 11:16:05 PM, and the last timetable trip at 11:17 PM is the same train
  const later = withLastPlannedTrip(ONE_TRAIN_WOAK, "WOAK-EMBR");
  const plan = JSON.parse(later.depart["WOAK-EMBR"]);
  setDeparture(plan.root.schedule.request.trip[0], minute(7));
  const r = await run({
    mode: "shortcuts",
    fixtures: { ...later, depart: { ...later.depart, "WOAK-EMBR": JSON.stringify(plan) } },
  });
  assertClean(r);
  expect(r.output.split(". ")[0], "output").toEqual("West Oakland to Embarcadero in 6 minutes, last train");
});
