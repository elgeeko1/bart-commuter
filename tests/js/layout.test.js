// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

// Checks that every station choice fits each widget on a 6.1-inch iPhone with the widest departure
// times, and commutes whose trips change trains at stations from the widest name down, or run direct:
// full-size text fits the height, and text shrunk no further than MIN_TEXT_SCALE fits the width. Sizes
// are estimated from measured SF Pro metrics.

import { afterAll, beforeAll, expect, test } from "bun:test";
import {
  DEFAULT_OFFSET,
  ENGINE_PATH,
  METRICS_PATH,
  naturalSize,
  planResponse,
  readFixtures,
  readText,
  runScript,
  setShownOffset,
  symbols,
} from "./support/harness.js";
import { assertClean } from "./support/assertions.js";

// widget sizes in points for a 390x844 screen, from the Widgets specifications of the Apple Human
// Interface Guidelines
const WIDGET_SIZES = {
  small: { width: 158, height: 158 },
  medium: { width: 338, height: 158 },
  large: { width: 338, height: 354 },
  accessoryRectangular: { width: 160, height: 72 },
};
// the widest age under an hour, the text the updated line must fit
const WIDEST_OFFSET = "+44 minutes";
// every station choice in one family, run sequentially
const SWEEP_TIMEOUT_MS = 120 * 1000;
// smallest acceptable text size, as a fraction of the size requested by the script
const MIN_TEXT_SCALE = 0.8;
// trains in the timetable of each synthetic trip, more than any family shows
const TRAINS = 8;
// examples printed for a family that does not fit
const EXAMPLES = 8;

const SOURCE = readText(ENGINE_PATH);
const METRICS = JSON.parse(readText(METRICS_PATH));
const NAMES = Object.fromEntries([...SOURCE.matchAll(/^ {2}"?(\w+)"?: \{ name: "([^"]+)"/gm)].map((m) => [m[1], m[2]]));
const CODES = Object.keys(NAMES);
const PAIRS = CODES.flatMap((a) => CODES.filter((b) => b !== a).map((b) => [a, b]));
const LETTERS = new Function(`return ${/^const LINE_LETTERS = (\{[^}]*\});/m.exec(SOURCE)[1]}`)();
const ROUTES = [...SOURCE.matchAll(/\{ number: (\d+), line: "(\w+)"/g)].map((m) => ({
  number: Number(m[1]),
  line: m[2],
}));
// truncated, so GTFS-RT fails and every trip falls back to the timetable
const BROKEN_FEED = readFixtures("tests/fixtures", { withEtd: false }).feed.slice(0, 5000);

function textWidth(font, text) {
  return [...text].reduce((sum, c) => sum + METRICS[font].advances[c], 0);
}

// the route whose Lock Screen letter is widest
const ROUTE = ROUTES.reduce((a, b) =>
  textWidth("semiboldSystemFont(10)", `(${LETTERS[b.line]}) `) >
  textWidth("semiboldSystemFont(10)", `(${LETTERS[a.line]}) `)
    ? b
    : a,
);

// the hour whose clock times are widest, and its widest minutes from 10 through 58 in departure order
const CLOCK_FONT = "italicSystemFont(14)";
const HOURS = Array.from({ length: 24 }, (_, h24) => ({
  h24,
  label: `${h24 % 12 || 12}:`,
  period: h24 < 12 ? "AM" : "PM",
}));
const clockText = (hour, minute) => `${hour.label}${String(minute).padStart(2, "0")} ${hour.period}`;
const HOUR = HOURS.reduce((a, b) =>
  textWidth(CLOCK_FONT, clockText(b, 0)) > textWidth(CLOCK_FONT, clockText(a, 0)) ? b : a,
);
const MINUTES = Array.from({ length: 49 }, (_, i) => i + 10)
  .sort((a, b) => textWidth(CLOCK_FONT, clockText(HOUR, b)) - textWidth(CLOCK_FONT, clockText(HOUR, a)) || a - b)
  .slice(0, TRAINS)
  .sort((a, b) => a - b);
const TIMES = MINUTES.map((m) => clockText(HOUR, m));
// shortly before the first train, on a standard-time day
const TEST_NOW = Date.UTC(2026, 10, 5, HOUR.h24 + 8, MINUTES[0]) - 5 * 60000;

// widest text of a transfer line, the station name at the larger transfer size
const TRANSFER_FONT = "systemFont(12)";
const BY_WIDTH = [...CODES].sort((a, b) => textWidth(TRANSFER_FONT, NAMES[b]) - textWidth(TRANSFER_FONT, NAMES[a]));
const departs = (minute) => Date.UTC(2026, 10, 5, HOUR.h24 + 8, minute);
const RUN_MS = 30 * 60000;

// which trips of a commute change trains
const ALL_TRANSFER = () => true;
const ALL_DIRECT = () => false;
const ONE_WAY_TRANSFERS = (from, to) => from < to;

const directLegs = (from, to, departure) => [[ROUTE.number, from, to, departure, departure + RUN_MS]];

/**
 * Plans the trips at the given minutes, each changing trains at the (k + i)-th widest station name when
 * changesTrains holds for the trip.
 */
function plannedTrips(from, to, k, minutes, changesTrains) {
  if (!changesTrains(from, to)) return minutes.map((m) => directLegs(from, to, departs(m)));
  const stations = BY_WIDTH.filter((c) => c !== from && c !== to);
  return minutes.map((m, i) => [
    [ROUTE.number, from, stations[(k + i) % stations.length], departs(m), departs(m) + 60000],
    [ROUTE.number, stations[(k + i) % stations.length], to, departs(m) + 120000, departs(m) + RUN_MS],
  ]);
}

// plans trips at each time but the last, then a direct trip that arrives after them all
function plan(from, to, k, changesTrains) {
  const last = departs(MINUTES[MINUTES.length - 1]);
  return planResponse([...plannedTrips(from, to, k, MINUTES.slice(0, -1), changesTrains), directLegs(from, to, last)]);
}
const STATION_SCHEDULE = JSON.stringify({
  root: { station: { item: TIMES.map((t) => ({ "@line": `ROUTE ${ROUTE.number}`, "@origTime": t })) } },
});
const plannedFixtures = (changesTrains) => ({
  etd: {},
  schedule: Object.fromEntries(CODES.map((c) => [c, STATION_SCHEDULE])),
  depart: Object.fromEntries(PAIRS.map(([a, b], k) => [`${a}-${b}`, plan(a, b, k, changesTrains)])),
  feed: BROKEN_FEED,
});

const CHOICES = [...CODES.map((c) => [c]), ...PAIRS];

// a timetable of n trains that ends the service day, so the final listed train is the last
function lastTrainFixtures(n, changesTrains) {
  const nextDay = departs(24 * 60);
  const lastPlan = (from, to, k) =>
    planResponse([...plannedTrips(from, to, k, MINUTES.slice(0, n), changesTrains), directLegs(from, to, nextDay)]);
  const schedule = JSON.stringify({
    root: { station: { item: TIMES.slice(0, n).map((t) => ({ "@line": `ROUTE ${ROUTE.number}`, "@origTime": t })) } },
  });
  return {
    ...plannedFixtures(changesTrains),
    schedule: Object.fromEntries(CODES.map((c) => [c, schedule])),
    depart: Object.fromEntries(PAIRS.map(([a, b], k) => [`${a}-${b}`, lastPlan(a, b, k)])),
  };
}

// trains each family lists per trip when every trip has a transfer
const LISTED = { large: 4, accessoryRectangular: 2 };

const SCENARIOS = [
  ...Object.entries(WIDGET_SIZES).flatMap(([family, size]) => [
    { family, size, name: "", fixtures: plannedFixtures(ALL_TRANSFER) },
    {
      family,
      size,
      name: " on the last train",
      fixtures: lastTrainFixtures(LISTED[family] ?? 3, ALL_TRANSFER),
      flag: "every",
    },
  ]),
  // the small widget enlarges its text when trips have no transfer row; a planned direct trip without a
  // direct line in the network is never the last train
  ...[
    [" with direct trips", ALL_DIRECT, 0, "some"],
    [" with one direct trip", ONE_WAY_TRANSFERS, 1, "every"],
  ].flatMap(([name, changesTrains, transferRows, flag]) => [
    { family: "small", size: WIDGET_SIZES.small, name, fixtures: plannedFixtures(changesTrains), transferRows },
    {
      family: "small",
      size: WIDGET_SIZES.small,
      name: `${name} on the last train`,
      fixtures: lastTrainFixtures(3, changesTrains),
      transferRows,
      flag,
    },
  ]),
];

beforeAll(() => setShownOffset(WIDEST_OFFSET));
afterAll(() => setShownOffset(DEFAULT_OFFSET));

for (const { family, size, name: scenario, fixtures, flag, transferRows } of SCENARIOS) {
  test(
    `every station choice fits the ${family} widget${scenario}`,
    async () => {
      const overflows = [];
      let flagged = 0;
      for (const codes of CHOICES) {
        const r = await runScript({
          now: TEST_NOW,
          family,
          params: { widget: codes.join(" ") },
          etdFailure: "not-json",
          fixtures,
        });
        assertClean(r);
        // the script shows an error as a widget of one 12pt line
        expect(r.widget.children[0].font, `${codes.join(" ")} error widget`).not.toEqual("systemFont(12)");
        const errors = [];
        const full = naturalSize(r.widget, errors);
        const shrunk = naturalSize(r.widget, errors, MIN_TEXT_SCALE);
        expect(errors, `${codes.join(" ")} measurement`).toEqual([]);
        const hasFlag = symbols(r.widget).some((s) => s.startsWith("flag.checkered"));
        if (hasFlag) flagged += 1;
        if (flag === "every") expect(hasFlag, `${codes.join(" ")} flag`).toEqual(true);
        if (transferRows !== undefined && codes.length === 2)
          expect(
            symbols(r.widget).filter((s) => s.startsWith("arrow.triangle.branch")).length,
            `${codes.join(" ")} transfer rows`,
          ).toEqual(transferRows);
        // Scriptable does not document its default padding, so a widget without one is assumed to have none
        const pad = r.widget.padding ?? { top: 0, leading: 0, bottom: 0, trailing: 0 };
        const over = {
          width: shrunk.width - (size.width - pad.leading - pad.trailing),
          height: full.height - (size.height - pad.top - pad.bottom),
        };
        if (over.width > 0 || over.height > 0) overflows.push({ codes: codes.join(" "), ...over });
      }
      if (flag) expect(flagged, "flagged choices").toBeGreaterThan(0);
      if (overflows.length === 0) return;
      const worst = (key) =>
        overflows
          .filter((o) => o[key] > 0)
          .sort((a, b) => b[key] - a[key])
          .slice(0, EXAMPLES)
          .map((o) => `${o.codes} +${o[key].toFixed(1)}`);
      throw new Error(
        `${overflows.length} of ${CHOICES.length} choices overflow; widest: ${worst("width").join(", ") || "none"}; ` +
          `tallest: ${worst("height").join(", ") || "none"}`,
      );
    },
    SWEEP_TIMEOUT_MS,
  );
}
