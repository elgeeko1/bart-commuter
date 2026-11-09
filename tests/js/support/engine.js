// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

// Shared state of the engine tests: the captured fixtures, the time they were captured at, and helpers.
// Expected values derive from tests/fixtures and must be updated whenever it is recaptured.

import { expect } from "bun:test";
import {
  GTFS_RT_URL,
  directPlan,
  etdResponse,
  feedOfTrips,
  naturalSize,
  planResponse,
  plannerTime,
  readFixtures,
  runScript,
} from "./harness.js";
import { assertClean } from "./assertions.js";

// two seconds after the GTFS-RT feed header timestamp, 11:10 PM
export const NOW = 1793949005 * 1000;
// start of the minute this many minutes after the minute of NOW
export const minute = (min) => NOW - (NOW % 60000) + min * 60000;
// 5:11 AM, the first trip of the next service day
export const NEXT_MORNING = minute(361);
export const CAPTURED = readFixtures("tests/fixtures", { withEtd: true });
// timetables of commutes beyond the captured ones, every 20 minutes; they hold no transfers
export const FIXTURES = {
  ...CAPTURED,
  depart: {
    ...CAPTURED.depart,
    "POWL-ROCK": directPlan(2, "POWL", "ROCK", [9, 29, 49, 69].map(minute), 17),
    "ROCK-POWL": directPlan(1, "ROCK", "POWL", [10, 30, 50, 70].map(minute), 17),
    "WOAK-SFIA": directPlan(7, "WOAK", "SFIA", [14, 34, 54, 74].map(minute), 26),
    "SFIA-WOAK": directPlan(8, "SFIA", "WOAK", [5, 25, 45, 65].map(minute), 26),
    "EMBR-SFIA": directPlan(7, "EMBR", "SFIA", [15, 35, 55, 75].map(minute), 24),
    "SFIA-EMBR": directPlan(8, "SFIA", "EMBR", [5, 25, 45, 65].map(minute), 24),
  },
};
export const PUBLIC_KEY = "MW9S-E7SL-26DU-VV8V";

// the transfer line of a direct trip is blank
export const ETD_WOAK_EMBR = ["West Oakland", "to Embarcadero", "11:16 PM", "11:25pm", "11:34pm"];
export const ETD_EMBR_WOAK = ["Embarcadero", "to West Oakland", "11:11 PM", "11:22pm", "11:31pm"];
export const RT_WOAK_EMBR = ["West Oakland", "to Embarcadero", "11:15 PM", "11:25pm", "11:34pm"];
export const RT_EMBR_WOAK = ["Embarcadero", "to West Oakland", "11:10 PM", "11:22pm", "11:30pm"];
// large widgets list later trains; ETD lists stop where the reports of a destination run out
export const ETD_WOAK_EMBR_LIST = [
  "West Oakland",
  " \u2192 Embarcadero",
  "11:16 PM",
  "11:25 pm",
  "11:34 pm",
  "11:45 pm",
  "11:54 pm",
];
export const ETD_EMBR_WOAK_LIST = [
  "Embarcadero",
  " \u2192 West Oakland",
  "11:11 PM",
  "11:22 pm",
  "11:31 pm",
  "11:41 pm",
  "11:51 pm",
];
// GTFS-RT lists departures until 43 minutes after NOW, 11:53 PM
export const RT_WOAK_EMBR_LIST = [
  "West Oakland",
  " \u2192 Embarcadero",
  "11:15 PM",
  "11:25 pm",
  "11:34 pm",
  "11:45 pm",
];
export const RT_EMBR_WOAK_LIST = [
  "Embarcadero",
  " \u2192 West Oakland",
  "11:10 PM",
  "11:22 pm",
  "11:30 pm",
  "11:41 pm",
  "11:50 pm",
];
export const NO_DATA = "No data";
// a Home Screen board without data shows a symbol in place of its departure time
export const NO_DATA_SYMBOL = "icloud.slash";
export const NO_DATA_COMMUTE = ["West Oakland", "to Embarcadero", "Embarcadero", "to West Oakland"];
// line marker colors
export const Y = "#ffff33";
export const B = "#0099cc";
export const R = "#ff0000";
export const ORANGE = "#ff9933";
export const UNKNOWN = "#8e8e93";
// a rejected key fails every legacy API endpoint
export const REJECTED_KEY = { etdFailure: "invalid-key", scheduleFailure: "invalid-key" };
// ETD outage with a working key
export const ETD_DOWN = { etdFailure: "not-json" };
export const BROKEN_FEED = { ...FIXTURES, feed: FIXTURES.feed.slice(0, 5000) };

export function run(opts) {
  return runScript({ now: NOW, fixtures: FIXTURES, ...opts });
}

// a request latency by source, in simulated ms: etd, schedule (the trip planner too) or feed
export function latencyOf(ms) {
  return (url) => ms[url.includes("etd.aspx") ? "etd" : url.includes("sched.aspx") ? "schedule" : "feed"] ?? 0;
}

export const DEADLINE_MS = 15 * 1000;

// the board and source each warning names, without its reason
export function failedSources(r) {
  return r.warnings.map((w) => w.split(":")[0]);
}

// the boards whose GTFS-RT feed failed
export function feedWarnings(r) {
  return failedSources(r).filter((s) => s.endsWith(" GTFS-RT"));
}

export function etdRequests(r) {
  return r.requests.filter((u) => u.includes("etd.aspx"));
}

export function scheduleRequests(r) {
  return r.requests.filter((u) => u.includes("sched.aspx"));
}

export function gtfsRtRequests(r) {
  return r.requests.filter((u) => u === GTFS_RT_URL);
}

// line marker colors in document order
// a line color mixed with the dark widget background, as later-train dots are drawn
function fadedHex(hex) {
  const mixed = ["1c", "1c", "1e"].map((bg, i) => {
    const [line, background] = [parseInt(hex.slice(1 + 2 * i, 3 + 2 * i), 16), parseInt(bg, 16)];
    return Math.round(background + (line - background) * 0.5);
  });
  return `#${mixed.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

const LINE_OF_FADED = Object.fromEntries(
  ["#0099cc", "#339933", "#b0bec7", "#ff9933", "#ff0000", "#ffff33", "#ffffff"].map((hex) => [fadedHex(hex), hex]),
);

// the line color of each dot in document order
export function dots(node) {
  return node.children.flatMap((c) => {
    if (!(c.size?.width > 0 && c.backgroundColor)) return dots(c);
    return [LINE_OF_FADED[c.backgroundColor.hex] ?? c.backgroundColor.hex];
  });
}

export function withEtd(station, mutate) {
  const json = JSON.parse(FIXTURES.etd[station]);
  mutate(json);
  return { ...FIXTURES, etd: { ...FIXTURES.etd, [station]: JSON.stringify(json) } };
}

// sets the departure of a one-leg trip in a trip planner fixture
export function setDeparture(trip, ms) {
  const { time, day } = plannerTime(ms);
  Object.assign(trip.leg[0], { "@origTimeMin": time, "@origTimeDate": day });
}

// trip planner fixture whose trips after the kept ones leave on the next service day
export function withLastPlannedTrip(fixtures, pair, kept = 1) {
  const json = JSON.parse(fixtures.depart[pair]);
  // the plan lists its trips timetabled before now first, then the next
  const { before, request } = json.root.schedule;
  request.trip.slice(Number(before) + kept).forEach((t, i) => setDeparture(t, NEXT_MORNING + i * 60000));
  return { ...fixtures, depart: { ...fixtures.depart, [pair]: JSON.stringify(json) } };
}

// the only remaining train from West Oakland toward Embarcadero is the 11:16 PM Yellow train, with the feed,
// which lists later trains, down
export const ONE_TRAIN_WOAK = {
  ...withEtd("WOAK", (json) => {
    const station = json.root.station[0];
    station.etd = station.etd.filter((e) => e.abbreviation !== "DALY");
    for (const e of station.etd) if (e.abbreviation === "MLBR") e.estimate = e.estimate.slice(0, 1);
  }),
  feed: BROKEN_FEED.feed,
};

// query parameters of a URL, decoded
export function queryOf(url) {
  return Object.fromEntries(
    url
      .slice(url.indexOf("?") + 1)
      .split("&")
      .map((p) => p.split("=").map(decodeURIComponent)),
  );
}

export const westbound = (lists) =>
  etdResponse(
    "WOAK",
    "West Oakland",
    lists.map(([color, dest, name, estimates]) => [color, dest, name, "South", estimates]),
  );

// Rockridge and Fremont share no line, so trips change trains at MacArthur southbound and 19th St
// northbound; replayed from 8:00 AM standard time on a Tuesday
export const TRANSFER_DAY_START = Date.UTC(2026, 10, 3, 16, 0);

export const cancelled = (minutes) => ({ minutes, cancelled: true });

export const MIN = 60000;

// runs a step as Siri and in every family, which must render and measure cleanly
export async function replayStep(step) {
  const spoken = await step.run({ mode: "shortcuts" });
  assertClean(spoken);
  const widgets = {};
  for (const family of ["small", "medium", "large", "accessoryRectangular", "accessoryCircular", "accessoryInline"]) {
    const r = await step.run({ family });
    assertClean(r);
    if (!family.startsWith("accessory")) {
      const errors = [];
      naturalSize(r.widget, errors);
      expect(errors, `${family} measurement`).toEqual([]);
    }
    widgets[family] = r;
  }
  return { output: spoken.output, warnings: spoken.warnings, widgets };
}

// a southbound trip leaving Rockridge at departs, changing at MacArthur after wait minutes
export const viaMacArthur = (departs, wait = 3) => [
  [1, "ROCK", "MCAR", departs, departs + 3 * MIN],
  [4, "MCAR", "FRMT", departs + (3 + wait) * MIN, departs + (45 + wait) * MIN],
];

// a northbound trip leaving Fremont at departs, changing at 19th St after wait minutes
export const via19th = (departs, wait = 1) => [
  [3, "FRMT", "19TH", departs, departs + 37 * MIN],
  [2, "19TH", "ROCK", departs + (37 + wait) * MIN, departs + (43 + wait) * MIN],
];

/**
 * One step of the transfer day, minutes after TRANSFER_DAY_START, with trips planned 4, 24, 44 and 64
 * minutes out from Rockridge and 6, 26, 46 and 66 from Fremont unless overridden.
 * @param {{ rock?: number[], mcar?: number[], frmt?: number[], t19?: number[], etdDown?: boolean,
 *   outbound?: number[][][], inbound?: number[][][], feed?: object[] }} live  ETD minutes at each station,
 *   planned trips, and GTFS-RT trips; the feed is broken without them
 */
export function transferStep(min, live) {
  const now = TRANSFER_DAY_START + min * MIN;
  const fixtures = {
    etd: {
      ROCK: rockSouth(live.rock ?? []),
      MCAR: mcarSouth(live.mcar ?? []),
      FRMT: frmtNorth(live.frmt ?? []),
      "19TH": nineteenthNorth(live.t19 ?? []),
    },
    schedule: {},
    depart: {
      "ROCK-FRMT": planResponse(live.outbound ?? [4, 24, 44, 64].map((m) => viaMacArthur(now + m * MIN))),
      "FRMT-ROCK": planResponse(live.inbound ?? [6, 26, 46, 66].map((m) => via19th(now + m * MIN))),
    },
    feed: live.feed ? feedOfTrips(live.feed, { now }) : FIXTURES.feed.slice(0, 20),
  };
  const opts = { now, fixtures, params: { widget: "rock frmt" }, etdFailure: live.etdDown ? "not-json" : null };
  return { now, run: (more) => runScript({ ...opts, ...more }) };
}

export const rockSouth = (minutes) =>
  etdResponse("ROCK", "Rockridge", [["YELLOW", "MLBR", "Millbrae", "South", minutes]]);

export const mcarSouth = (minutes) =>
  etdResponse("MCAR", "MacArthur", [["ORANGE", "BERY", "Berryessa", "South", minutes]]);

export const frmtNorth = (minutes) =>
  etdResponse("FRMT", "Fremont", [["ORANGE", "RICH", "Richmond", "North", minutes]]);

export const nineteenthNorth = (minutes) =>
  etdResponse("19TH", "19th St Oakland", [["YELLOW", "ANTC", "Antioch", "North", minutes]]);

// every connection on time: transfers wait 3 minutes at MacArthur and 1 minute at 19th St
export const ON_TIME = { rock: [4, 24, 44], mcar: [10, 30, 50], frmt: [6, 26, 46], t19: [44, 64, 84] };

export const SCHEDULED = { ...ETD_DOWN, fixtures: BROKEN_FEED };
