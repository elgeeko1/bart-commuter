// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

// Trips with a transfer: legs matched to live trains, connection rules, and the fastest trips.

import { expect, test } from "bun:test";
import { directPlan, etdResponse, feedOfTrips, planResponse, runScript, texts } from "../support/harness.js";
import { assertClean } from "../support/assertions.js";
import {
  FIXTURES,
  MIN,
  ON_TIME,
  ORANGE,
  R,
  TRANSFER_DAY_START,
  Y,
  dots,
  failedSources,
  replayStep,
  transferStep,
  via19th,
  viaMacArthur,
} from "../support/engine.js";

// cut off before its first message ends, so decoding fails
const TRUNCATED_FEED = FIXTURES.feed.slice(0, 20);

test("transfer day 8:00 AM: trips with a transfer replace a permanent No trains", async () => {
  const r = await replayStep(transferStep(0, ON_TIME));
  expect(r.output, "output").toEqual(
    "Rockridge to Fremont in 4 minutes, transfer at MacArthur, next 24 and 44. Fremont to Rockridge in 6 minutes, transfer at 19th St Oakland, next 26 and 46.",
  );
  expect(failedSources(r), "failed sources").toEqual([
    "ROCK-MCAR GTFS-RT",
    "MCAR-FRMT GTFS-RT",
    "FRMT-19TH GTFS-RT",
    "19TH-ROCK GTFS-RT",
  ]);
  const medium = r.widgets.medium.widget;
  expect(texts(medium).slice(0, 6), "medium").toEqual([
    "Rockridge",
    "to Fremont",
    "8:04 AM",
    "MacArthur",
    "8:24am",
    "8:44am",
  ]);
  // the departure dot marks the line of the first train, and the transfer dot the line of the connecting train
  expect(dots(medium).slice(0, 2), "medium dots").toEqual([Y, ORANGE]);
  const small = texts(r.widgets.small.widget);
  expect(small.slice(0, 5), "small").toEqual(["Rockridge", " \u2192 Fremont", "8:04 AM", "MacArthur", "8:24am"]);
  // a large widget names the transfer of later trips too; the fourth trip is past the live lists, so it keeps its timetable connection
  const large = texts(r.widgets.large.widget);
  expect(large.slice(4, 10), "large").toEqual(["8:24 am", "MacArthur", "8:44 am", "MacArthur", "9:04 am", "MacArthur"]);
  // the Lock Screen shows the next trip alone, transfer or not
  expect(texts(r.widgets.accessoryRectangular.widget).slice(1, 6), "rectangular").toEqual([
    "Rockridge",
    " \u2192 Fremont",
    "(Y) ",
    "8:04 AM",
    "  (Y) 8:24am",
  ]);
  expect(texts(r.widgets.accessoryInline.widget), "inline").toEqual(["ROCK\u2192 8:04 AM  FRMT\u2192 8:06 AM"]);
});

test("transfer day 8:00 AM: the widest transfer station name shows in full in every family", async () => {
  // El Cerrito del Norte, the widest station name, as a transfer station in both directions
  const viaDeln = (departs) => [
    [1, "ROCK", "DELN", departs, departs + 3 * MIN],
    [4, "DELN", "FRMT", departs + 6 * MIN, departs + 48 * MIN],
  ];
  const step = transferStep(0, { outbound: [4, 24].map((m) => viaDeln(TRANSFER_DAY_START + m * MIN)), etdDown: true });
  const small = await step.run({ family: "small" });
  assertClean(small);
  expect(texts(small.widget), "small").toContain("El Cerrito del Norte");
  const medium = await step.run({ family: "medium" });
  assertClean(medium);
  expect(texts(medium.widget), "medium").toContain("El Cerrito del Norte");
  const large = await step.run({ family: "large" });
  assertClean(large);
  expect(
    texts(large.widget).filter((t) => t === "El Cerrito del Norte" || t === "DELN"),
    "large",
  ).toEqual(["El Cerrito del Norte", "El Cerrito del Norte"]);
});

test("transfer day 8:10 AM: a first train 2 minutes late still makes its connection", async () => {
  const r = await replayStep(transferStep(10, { ...ON_TIME, rock: [6, 24, 44] }));
  // the 8:14 AM train runs at 8:16, reaching MacArthur a minute before the connection leaves
  expect(r.output.split(". ")[0], "output").toEqual(
    "Rockridge to Fremont in 6 minutes, transfer at MacArthur, next 24 and 44",
  );
});

test("a first train 2 minutes late makes its connection at any second within the minute of a run", async () => {
  // at 8:10:20 the 8:14 AM planned train leaves in 6 minutes, at 8:16:20
  const r = await transferStep(10 + 20 / 60, { ...ON_TIME, rock: [6, 24, 44] }).run({ mode: "shortcuts" });
  assertClean(r);
  expect(r.output.split(". ")[0], "output").toEqual(
    "Rockridge to Fremont in 6 minutes, transfer at MacArthur, next 24 and 44",
  );
});

test("transfer day 8:20 AM: a first train reaching MacArthur after its connection leaves misses it", async () => {
  // the 8:24 AM train 3 minutes late reaches MacArthur at 8:30, as its connection leaves
  const held = await transferStep(20, { ...ON_TIME, rock: [7, 24, 44], mcar: [10, 30, 50] }).run({ mode: "shortcuts" });
  assertClean(held);
  expect(held.output.split(". ")[0], "3 minutes late").toEqual(
    "Rockridge to Fremont in 7 minutes, transfer at MacArthur, next 24 and 44",
  );
  // 4 minutes late it reaches MacArthur at 8:31, so its trip is dropped; the next trip makes the next connection
  const r = await replayStep(transferStep(20, { ...ON_TIME, rock: [8, 24, 44], mcar: [10, 30, 50] }));
  expect(r.output.split(". ")[0], "4 minutes late").toEqual(
    "Rockridge to Fremont in 24 minutes, transfer at MacArthur, next 44 and 64",
  );
  expect(texts(r.widgets.medium.widget).slice(2, 3), "next trip live").toEqual(["8:44 AM"]);
});

test("transfer day 8:40 AM: a cancelled connecting train drops its trip", async () => {
  // the feed lists every train within its horizon, and the MacArthur ETD lists Orange trains without the one
  // 10 minutes out
  const orange = { platforms: ["K30-1", "K20-1", "K10-1", "A10-1"], firstMin: 30 };
  const r = await replayStep(transferStep(40, { ...ON_TIME, mcar: [30, 50, 70], feed: [orange] }));
  expect(r.output.split(". ")[0], "output").toEqual(
    "Rockridge to Fremont in 24 minutes, transfer at MacArthur, next 44 and 64",
  );
});

test("transfer day 8:50 AM: the earliest of two live trains in the window of a leg runs it", async () => {
  // a first train 4 minutes late reaches MacArthur a minute after the first of two Orange trains in the
  // connection window leaves, 4 minutes before the second
  const r = await replayStep(transferStep(50, { ...ON_TIME, rock: [8, 24, 44], mcar: [10, 15, 30] }));
  // the first runs the connection, which the trip then misses
  expect(r.output.split(". ")[0], "output").toEqual(
    "Rockridge to Fremont in 24 minutes, transfer at MacArthur, next 44 and 64",
  );
});

test("transfer day 9:00 AM: live times down show timetable trips with transfers", async () => {
  const r = await replayStep(transferStep(60, { etdDown: true }));
  expect(r.output, "output").toEqual(
    "Rockridge to Fremont scheduled in 4 minutes, transfer at MacArthur, next 24 and 44. Fremont to Rockridge scheduled in 6 minutes, transfer at 19th St Oakland, next 26 and 46.",
  );
  expect(texts(r.widgets.medium.widget).slice(2, 4), "medium").toEqual(["9:04 AM", "MacArthur"]);
  expect(texts(r.widgets.accessoryInline.widget), "inline").toEqual(["ROCK\u2192 9:04 AM  FRMT\u2192 9:06 AM"]);
});

test("transfer day 9:10 AM: every source down shows no data", async () => {
  const step = transferStep(70, { etdDown: true });
  const r = await step.run({ mode: "shortcuts", scheduleFailure: "not-json" });
  assertClean(r);
  expect(r.output, "output").toEqual("No data, Rockridge to Fremont. No data, Fremont to Rockridge.");
});

test("transfer day 12:40 AM: the Grand Meet holds for a late first train", async () => {
  // the 12:44 AM train runs 6 minutes late, reaching MacArthur 2 minutes after its 12:51 AM connection
  const meet = TRANSFER_DAY_START + (16 * 60 + 40) * MIN;
  const step = transferStep(16 * 60 + 40, {
    rock: [10],
    mcar: [11],
    outbound: [viaMacArthur(meet + 4 * MIN, 4), viaMacArthur(meet + 279 * MIN)],
    inbound: [via19th(meet + 256 * MIN)],
  });
  const r = await replayStep(step);
  expect(r.output, "output").toEqual(
    "Rockridge to Fremont in 10 minutes, transfer at MacArthur, last train. No trains, Fremont to Rockridge.",
  );
});

test("a transfer shows only when it arrives sooner than every later direct train", async () => {
  // Downtown Berkeley to Embarcadero: Red trains run direct in 20 minutes, Orange then Yellow change at MacArthur
  const now = TRANSFER_DAY_START;
  const viaMcar = (departs, wait) => [
    [4, "DBRK", "MCAR", now + departs * MIN, now + (departs + 5) * MIN],
    [1, "MCAR", "EMBR", now + (departs + 5 + wait) * MIN, now + (departs + 19 + wait) * MIN],
  ];
  const direct = (departs) => [[7, "DBRK", "EMBR", now + departs * MIN, now + (departs + 20) * MIN]];
  const fixtures = {
    etd: {
      DBRK: etdResponse("DBRK", "Downtown Berkeley", [
        ["RED", "MLBR", "Millbrae", "South", [10, 30, 50]],
        ["ORANGE", "BERY", "Berryessa", "South", [2, 5, 22]],
      ]),
      MCAR: etdResponse("MCAR", "MacArthur", [["YELLOW", "MLBR", "Millbrae", "South", [9, 19, 29]]]),
      EMBR: etdResponse("EMBR", "Embarcadero", [["RED", "RICH", "Richmond", "North", [3, 23]]]),
    },
    schedule: {},
    depart: {
      // the 8:02 AM trip arrives at 8:23, before the 8:10 AM Red train reaches Embarcadero at 8:30; the
      // 8:05 AM trip arrives at 8:33, after it, so the direct train beats it
      "DBRK-EMBR": planResponse([viaMcar(2, 2), viaMcar(5, 9), direct(10), direct(30)]),
      "EMBR-DBRK": planResponse([3, 23, 43, 63].map((m) => [[8, "EMBR", "DBRK", now + m * MIN, now + (m + 20) * MIN]])),
    },
    feed: TRUNCATED_FEED,
  };
  const r = await runScript({ now, fixtures, mode: "shortcuts", params: { shortcut: "dbrk embr" } });
  assertClean(r);
  // the list ends with the trip plan, after which a faster trip with a transfer may be missing
  expect(r.output, "output").toEqual(
    "Downtown Berkeley to Embarcadero in 2 minutes, transfer at MacArthur, next 10 and 30. Embarcadero to Downtown Berkeley in 3 minutes, next 23.",
  );
  const large = await runScript({ now, fixtures, family: "large", params: { widget: "dbrk embr" } });
  assertClean(large);
  expect(texts(large.widget).slice(2, 7), "large").toEqual([
    "8:02 AM",
    "MacArthur",
    "8:10 am",
    "8:30 am",
    "Embarcadero",
  ]);
  expect(dots(large.widget).slice(0, 4), "large dots").toEqual([ORANGE, Y, R, R]);
});

test("before service, a trip plan of transfers alone serves a board with a direct line", async () => {
  // no live train yet, and the first planned trips change at MacArthur
  const now = TRANSFER_DAY_START;
  const viaMcar = (departs) => [
    [4, "DBRK", "MCAR", now + departs * MIN, now + (departs + 5) * MIN],
    [1, "MCAR", "EMBR", now + (departs + 7) * MIN, now + (departs + 21) * MIN],
  ];
  const fixtures = {
    etd: {
      DBRK: etdResponse("DBRK", "Downtown Berkeley", []),
      MCAR: etdResponse("MCAR", "MacArthur", []),
      EMBR: etdResponse("EMBR", "Embarcadero", []),
    },
    schedule: {},
    depart: { "DBRK-EMBR": planResponse([viaMcar(50), viaMcar(70)]), "EMBR-DBRK": planResponse([]) },
    feed: feedOfTrips([], { now }),
  };
  const r = await runScript({ now, fixtures, mode: "shortcuts", params: { shortcut: "dbrk embr" } });
  assertClean(r);
  expect(r.output.split(". ")[0], "output").toEqual(
    "Downtown Berkeley to Embarcadero scheduled in 50 minutes, transfer at MacArthur, next 70",
  );
});

test("a trip arriving no sooner than a later trip is dropped", async () => {
  // two trips meet the same 8:20 AM connection, so the earlier one gets there no sooner
  const now = TRANSFER_DAY_START;
  const meeting = (departs) => [
    [1, "ROCK", "MCAR", now + departs * MIN, now + (departs + 3) * MIN],
    [4, "MCAR", "FRMT", now + 20 * MIN, now + 62 * MIN],
  ];
  const step = transferStep(0, { etdDown: true, outbound: [meeting(4), meeting(14), viaMacArthur(now + 24 * MIN)] });
  const r = await step.run({ mode: "shortcuts" });
  assertClean(r);
  expect(r.output.split(". ")[0], "output").toEqual(
    "Rockridge to Fremont scheduled in 14 minutes, transfer at MacArthur, next 24",
  );
});

// Walnut Creek and Downtown Berkeley share no line, so trips change trains at MacArthur. wcrk lists ETD
// minutes at Walnut Creek; feed lists GTFS-RT trips, and the feed is broken without them.
function walnutCreekStep(trips, { wcrk = [5, 25], etdDown = false, feed = null } = {}) {
  const now = TRANSFER_DAY_START + 30 * MIN;
  return {
    now,
    run: (more) =>
      runScript({
        now,
        fixtures: {
          etd: {
            WCRK: etdResponse("WCRK", "Walnut Creek", [["YELLOW", "MLBR", "Millbrae", "South", wcrk]]),
            // the 8:35 AM train reaches MacArthur at 8:53; the first connection leaves a minute later, the second 4
            MCAR: etdResponse("MCAR", "MacArthur", [["ORANGE", "RICH", "Richmond", "North", [24, 47]]]),
          },
          schedule: {},
          depart: { "WCRK-DBRK": planResponse(trips(now)), "DBRK-WCRK": planResponse([]) },
          feed: feed ? feedOfTrips(feed, { now }) : TRUNCATED_FEED,
        },
        params: { shortcut: "wcrk dbrk" },
        mode: "shortcuts",
        etdFailure: etdDown ? "not-json" : null,
        ...more,
      }),
  };
}

// a trip leaving Walnut Creek at departs, changing at MacArthur after wait minutes
const viaMacArthurNorth = (departs, wait) => [
  [1, "WCRK", "MCAR", departs, departs + 18 * MIN],
  [3, "MCAR", "DBRK", departs + (18 + wait) * MIN, departs + (23 + wait) * MIN],
];

test("transfer day 8:30 AM: a connection holds when it leaves no earlier than the first train arrives", async () => {
  const trips = (now) => [viaMacArthurNorth(now + 5 * MIN, 1), viaMacArthurNorth(now + 25 * MIN, 4)];
  const r = await walnutCreekStep(trips).run();
  assertClean(r);
  expect(r.output, "output").toEqual(
    "Walnut Creek to Downtown Berkeley in 5 minutes, transfer at MacArthur, next 25. No trains, Downtown Berkeley to Walnut Creek.",
  );
  // 2 minutes late, the 8:35 AM train reaches MacArthur at 8:55, a minute after its connection leaves
  const late = await walnutCreekStep(trips, { wcrk: [7, 25] }).run();
  assertClean(late);
  expect(late.output.split(". ")[0], "2 minutes late").toEqual(
    "Walnut Creek to Downtown Berkeley in 25 minutes, transfer at MacArthur",
  );
});

test("an untimed transfer counts the delay of a late first train in whole minutes", async () => {
  // at 8:00:20 the 8:05 AM planned train leaves in 7 minutes, so it reaches MacArthur at 8:25, 2 minutes
  // before its connection, which is past the live list at MacArthur
  const now = TRANSFER_DAY_START + 20000;
  const fixtures = {
    etd: {
      WCRK: etdResponse("WCRK", "Walnut Creek", [["YELLOW", "MLBR", "Millbrae", "South", [7, 25]]]),
      MCAR: etdResponse("MCAR", "MacArthur", [["ORANGE", "RICH", "Richmond", "North", [3, 6, 9]]]),
    },
    schedule: {},
    depart: {
      "WCRK-DBRK": planResponse([viaMacArthurNorth(now + 5 * MIN, 4), viaMacArthurNorth(now + 25 * MIN, 4)]),
      "DBRK-WCRK": planResponse([]),
    },
    feed: TRUNCATED_FEED,
  };
  const r = await runScript({ now, fixtures, mode: "shortcuts", params: { shortcut: "wcrk dbrk" } });
  assertClean(r);
  // the last trip of the plan stays though its train leaves at 8:25:20, past the last planned departure
  expect(r.output.split(". ")[0], "output").toEqual(
    "Walnut Creek to Downtown Berkeley scheduled in 7 minutes, transfer at MacArthur, next 25",
  );
});

// Dublin/Pleasanton to Fremont, changing at Bay Fair onto a train the plan times to leave as the first arrives
function bayFairStep(dublin, bayFair = [24, 44, 64]) {
  const now = TRANSFER_DAY_START;
  const viaBayFair = (departs) => [
    [11, "DUBL", "BAYF", departs, departs + 19 * MIN],
    [4, "BAYF", "FRMT", departs + 19 * MIN, departs + 38 * MIN],
  ];
  const fixtures = {
    etd: {
      DUBL: etdResponse("DUBL", "Dublin/Pleasanton", [["BLUE", "DALY", "Daly City", "South", dublin]]),
      BAYF: etdResponse("BAYF", "Bay Fair", [["ORANGE", "BERY", "Berryessa", "South", bayFair]]),
    },
    schedule: {},
    depart: {
      "DUBL-FRMT": planResponse([viaBayFair(now + 5 * MIN), viaBayFair(now + 25 * MIN)]),
      "FRMT-DUBL": planResponse([]),
    },
    feed: TRUNCATED_FEED,
  };
  return runScript({ now, fixtures, mode: "shortcuts", params: { shortcut: "dubl frmt" } });
}

test("a Bay Fair connection the plan times to leave as the first train arrives needs it on time", async () => {
  const onTime = await bayFairStep([5, 25]);
  assertClean(onTime);
  expect(onTime.output.split(". ")[0], "on time").toEqual(
    "Dublin/Pleasanton to Fremont in 5 minutes, transfer at Bay Fair, next 25",
  );
  // the 8:05 AM train leaves 2 minutes late, so it reaches Bay Fair 2 minutes after its connection leaves
  const late = await bayFairStep([7, 25]);
  assertClean(late);
  expect(late.output.split(". ")[0], "2 minutes late").toEqual(
    "Dublin/Pleasanton to Fremont in 25 minutes, transfer at Bay Fair",
  );
});

test("a timed connection without live times waits for a late first train", async () => {
  // the 8:05 AM train is predicted 2 minutes late, and the Bay Fair list starts after its 8:24 AM connection;
  // the train starts its run at Dublin/Pleasanton, so it shows at its timetable time
  const r = await bayFairStep([7, 25], [44, 64]);
  assertClean(r);
  expect(r.output.split(". ")[0], "output").toEqual(
    "Dublin/Pleasanton to Fremont scheduled in 5 minutes, transfer at Bay Fair, next 25",
  );
});

// Dublin/Pleasanton to Bay Fair on direct trains that start their run at Dublin/Pleasanton
function dublinDirectStep(dublin, departures = [5, 25]) {
  const now = TRANSFER_DAY_START;
  const fixtures = {
    etd: {
      DUBL: etdResponse("DUBL", "Dublin/Pleasanton", [["BLUE", "DALY", "Daly City", "South", dublin]]),
      BAYF: etdResponse("BAYF", "Bay Fair", []),
    },
    schedule: {},
    depart: {
      "DUBL-BAYF": directPlan(
        11,
        "DUBL",
        "BAYF",
        departures.map((m) => now + m * MIN),
        19,
      ),
      "BAYF-DUBL": planResponse([]),
    },
    feed: TRUNCATED_FEED,
  };
  return runScript({ now, fixtures, mode: "shortcuts", params: { shortcut: "dubl bayf" } });
}

test("a live train starting its run at the origin of the board shows the earlier of its timetable and live times", async () => {
  // predicted at 8:07 and 8:28 for the 8:05 and 8:25 AM departures
  const late = await dublinDirectStep([7, 28]);
  assertClean(late);
  expect(late.output.split(". ")[0], "predicted late").toEqual("Dublin/Pleasanton to Bay Fair in 5 minutes, next 25");
  // a prediction earlier than the timetable stands
  const early = await dublinDirectStep([4, 25]);
  assertClean(early);
  expect(early.output.split(". ")[0], "predicted early").toEqual("Dublin/Pleasanton to Bay Fair in 4 minutes, next 25");
  // the 7:58 AM departure is past and its train, predicted at 8:03, shows as leaving
  const past = await dublinDirectStep([3, 25], [-2, 25]);
  assertClean(past);
  expect(past.output.split(". ")[0], "timetable past").toEqual("Dublin/Pleasanton to Bay Fair leaving now, next 25");
});

test("a live train at the start of its line takes the latest timetable departure it may run", async () => {
  // a train predicted at 8:09 may run the 8:05 or the 8:10 AM departure, so it keeps its live time
  const r = await dublinDirectStep([9, 28], [5, 10, 25]);
  assertClean(r);
  expect(r.output.split(". ")[0], "output").toEqual("Dublin/Pleasanton to Bay Fair in 9 minutes, next 25");
});

test("a planned leg is not running only when the feed reaches past it", async () => {
  // the MacArthur ETD list has no Orange train near the 8:57 AM connection
  const trips = (now) => [viaMacArthurNorth(now + 5 * MIN, 4), viaMacArthurNorth(now + 25 * MIN, 4)];
  // the feed lists every train within its horizon, and its next Orange train leaves MacArthur at 9:10 AM
  const orange = { platforms: ["A10-2", "K10-2", "K20-2", "K30-2", "R10-2", "R20-2"], firstMin: 37 };
  const missing = await walnutCreekStep(trips, { feed: [orange] }).run();
  assertClean(missing);
  expect(missing.output.split(". ")[0], "missing").toEqual(
    "Walnut Creek to Downtown Berkeley in 25 minutes, transfer at MacArthur",
  );
  // ETD may omit a train, so without the feed the connection is unknown
  const unlisted = await walnutCreekStep(trips).run();
  assertClean(unlisted);
  expect(unlisted.output.split(". ")[0], "unlisted").toEqual(
    "Walnut Creek to Downtown Berkeley scheduled in 5 minutes, transfer at MacArthur, next 25",
  );
});

test("a planned leg from the end of its line is unknown when ETD omits it", async () => {
  // Dublin/Pleasanton lists the 8:25 AM train but not the 8:05 AM one waiting to leave
  const r = await bayFairStep([25, 45]);
  assertClean(r);
  expect(r.output.split(". ")[0], "output").toEqual(
    "Dublin/Pleasanton to Fremont scheduled in 5 minutes, transfer at Bay Fair, next 25",
  );
});

test("a planned connection holds when no leg has live data", async () => {
  // the same 1-minute connection, with live times down
  const r = await walnutCreekStep((now) => [viaMacArthurNorth(now + 5 * MIN, 1)], { etdDown: true }).run();
  assertClean(r);
  expect(r.output.split(". ")[0], "output").toEqual(
    "Walnut Creek to Downtown Berkeley scheduled in 5 minutes, transfer at MacArthur",
  );
});

test("a GTFS-RT train of unknown line leaves its leg unknown", async () => {
  // the feed has the first train, and a connecting train that fits both Orange and Red
  const feed = [
    { platforms: ["C40-1", "C30-1", "C20-1", "C10-1", "K30-1"], firstMin: 5 },
    { platforms: ["K30-2", "R10-2", "R20-2"], firstMin: 27 },
  ];
  const r = await walnutCreekStep((now) => [viaMacArthurNorth(now + 5 * MIN, 4)], { etdDown: true, feed }).run();
  assertClean(r);
  expect(r.output.split(". ")[0], "output").toEqual(
    "Walnut Creek to Downtown Berkeley scheduled in 5 minutes, transfer at MacArthur",
  );
});

test("a transfer tied on arrival with a direct train is dropped", async () => {
  const now = TRANSFER_DAY_START;
  const fixtures = {
    etd: {
      DBRK: etdResponse("DBRK", "Downtown Berkeley", [
        ["RED", "MLBR", "Millbrae", "South", [10, 30]],
        ["ORANGE", "BERY", "Berryessa", "South", [10]],
      ]),
      MCAR: etdResponse("MCAR", "MacArthur", [["YELLOW", "MLBR", "Millbrae", "South", [17]]]),
      EMBR: etdResponse("EMBR", "Embarcadero", []),
    },
    schedule: {},
    depart: {
      // leaving with the Red train and arriving with it at 8:30
      "DBRK-EMBR": planResponse([
        [
          [4, "DBRK", "MCAR", now + 10 * MIN, now + 15 * MIN],
          [1, "MCAR", "EMBR", now + 17 * MIN, now + 30 * MIN],
        ],
        [[7, "DBRK", "EMBR", now + 10 * MIN, now + 30 * MIN]],
        [[7, "DBRK", "EMBR", now + 30 * MIN, now + 50 * MIN]],
      ]),
      "EMBR-DBRK": planResponse([]),
    },
    feed: TRUNCATED_FEED,
  };
  const r = await runScript({ now, fixtures, mode: "shortcuts", params: { shortcut: "dbrk embr" } });
  assertClean(r);
  expect(r.output.split(". ")[0], "output").toEqual("Downtown Berkeley to Embarcadero in 10 minutes, next 30");
});

test("trips from the trip planner with two transfers are dropped", async () => {
  const now = TRANSFER_DAY_START;
  const three = [
    [1, "ROCK", "MCAR", now + 4 * MIN, now + 7 * MIN],
    [3, "MCAR", "12TH", now + 9 * MIN, now + 12 * MIN],
    [4, "12TH", "FRMT", now + 14 * MIN, now + 50 * MIN],
  ];
  const step = transferStep(0, { ...ON_TIME, outbound: [three, viaMacArthur(now + 24 * MIN)] });
  const r = await step.run({ mode: "shortcuts" });
  assertClean(r);
  expect(r.output.split(". ")[0], "output").toEqual("Rockridge to Fremont in 24 minutes, transfer at MacArthur");
});

test("Oakland Airport legs from the trip planner change trains at Coliseum", async () => {
  const now = TRANSFER_DAY_START;
  // the planner gives the terminal of the airport line as both ends of its leg
  const fixtures = {
    etd: {},
    schedule: {},
    depart: {
      "EMBR-OAKL": planResponse([
        [
          [6, "EMBR", "COLS", now + 10 * MIN, now + 25 * MIN],
          [20, "OAKL", "OAKL", now + 28 * MIN, now + 36 * MIN],
        ],
      ]),
      "OAKL-EMBR": planResponse([
        [
          [19, "OAKL", "OAKL", now + 5 * MIN, now + 13 * MIN],
          [5, "COLS", "EMBR", now + 16 * MIN, now + 31 * MIN],
        ],
      ]),
    },
    feed: null,
  };
  const r = await runScript({ now, fixtures, mode: "shortcuts", params: { shortcut: "embr oakl" } });
  expect(r.output, "output").toEqual(
    "Embarcadero to Oakland Airport scheduled in 10 minutes, transfer at Coliseum. Oakland Airport to Embarcadero scheduled in 5 minutes, transfer at Coliseum.",
  );
});

test("times from the trip planner carry their calendar dates across midnight", async () => {
  // at 11:50 PM, a trip leaving at 12:10 AM the next calendar day still belongs to tonight
  const now = TRANSFER_DAY_START + (15 * 60 + 50) * MIN;
  const step = transferStep(15 * 60 + 50, { etdDown: true, outbound: [viaMacArthur(now + 20 * MIN)], inbound: [] });
  const r = await step.run({ mode: "shortcuts" });
  assertClean(r);
  expect(r.output.split(". ")[0], "output").toEqual(
    "Rockridge to Fremont scheduled in 20 minutes, transfer at MacArthur",
  );
});

test("a commute whose trip plan holds only transfers shows its direct trains and logs the plan", async () => {
  const now = TRANSFER_DAY_START;
  const viaTransfer = [
    [4, "DBRK", "MCAR", now + 4 * MIN, now + 10 * MIN],
    [1, "MCAR", "EMBR", now + 13 * MIN, now + 30 * MIN],
  ];
  const r = await runScript({
    now,
    mode: "shortcuts",
    params: { shortcut: "dbrk embr" },
    fixtures: {
      etd: {
        DBRK: etdResponse("DBRK", "Downtown Berkeley", [["RED", "MLBR", "Millbrae", "South", [5, 25]]]),
        EMBR: etdResponse("EMBR", "Embarcadero", [["RED", "RICH", "Richmond", "North", [6, 26]]]),
      },
      schedule: {},
      depart: {
        "DBRK-EMBR": planResponse([viaTransfer]),
        "EMBR-DBRK": directPlan(8, "EMBR", "DBRK", [now + 6 * MIN, now + 26 * MIN], 25),
      },
      feed: TRUNCATED_FEED,
    },
  });
  assertClean(r);
  expect(r.warnings, "warnings").toContain("DBRK-EMBR schedule: no direct trip in the trip plan");
  expect(r.output.split(". ")[0], "output").toEqual("Downtown Berkeley to Embarcadero in 5 minutes, next 25");
});
