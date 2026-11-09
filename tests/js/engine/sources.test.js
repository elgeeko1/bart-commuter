// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

// Source selection and the run deadline: which sources answer, what is requested, and what a failure leaves.

import { expect, test } from "bun:test";
import { texts } from "../support/harness.js";
import { assertClean } from "../support/assertions.js";
import {
  B,
  BROKEN_FEED,
  DEADLINE_MS,
  ETD_DOWN,
  ON_TIME,
  REJECTED_KEY,
  RT_EMBR_WOAK,
  RT_EMBR_WOAK_LIST,
  RT_WOAK_EMBR,
  RT_WOAK_EMBR_LIST,
  UNKNOWN,
  Y,
  dots,
  failedSources,
  feedWarnings,
  gtfsRtRequests,
  latencyOf,
  run,
  transferStep,
} from "../support/engine.js";

test("rejected API key: GTFS-RT alone serves every family", async () => {
  const expected = {
    small: [
      "West Oakland",
      " \u2192 Embarcadero",
      "11:15 PM",
      "11:25pm",
      "11:34pm",
      "Embarcadero",
      " \u2192 West Oakland",
      "11:10 PM",
      "11:22pm",
      "11:30pm",
    ],
    medium: [...RT_WOAK_EMBR, ...RT_EMBR_WOAK],
    large: [...RT_WOAK_EMBR_LIST, ...RT_EMBR_WOAK_LIST],
    extraLarge: [...RT_WOAK_EMBR_LIST, ...RT_EMBR_WOAK_LIST],
    accessoryRectangular: [
      "BART",
      "West Oakland",
      " \u2192 Embarcadero",
      "11:15 PM",
      "  11:25pm",
      "Embarcadero",
      " \u2192 West Oakland",
      "(B) ",
      "11:10 PM",
      "  (Y) 11:22pm",
    ],
    accessoryCircular: ["EMBR", "11:10 PM", "to WOAK"],
    accessoryInline: ["WOAK\u2192 11:15 PM  EMBR\u2192 11:10 PM"],
  };
  for (const [family, want] of Object.entries(expected)) {
    const r = await run({ family, ...REJECTED_KEY });
    assertClean(r);
    expect(texts(r.widget), family).toEqual(want);
    expect(gtfsRtRequests(r).length, `${family} GTFS-RT requests`).toEqual(1);
    expect(feedWarnings(r), `${family} GTFS-RT warnings`).toEqual([]);
  }
});

test("a source that never answers fails at the deadline, as a failed source does", async () => {
  const cases = {
    ETD: { stalled: ["etd"], failed: ETD_DOWN },
    schedule: { stalled: ["schedule"], failed: { scheduleFailure: "not-json" } },
    "GTFS-RT": { stalled: ["feed"], failed: { fixtures: BROKEN_FEED } },
    "every source": {
      stalled: ["etd", "schedule", "feed"],
      failed: { ...ETD_DOWN, scheduleFailure: "not-json", fixtures: BROKEN_FEED },
    },
  };
  for (const [name, { stalled, failed }] of Object.entries(cases)) {
    const launches = {
      medium: { family: "medium" },
      accessoryRectangular: { family: "accessoryRectangular" },
      shortcuts: { mode: "shortcuts", params: { shortcut: "woak embr" } },
    };
    for (const [launch, opts] of Object.entries(launches)) {
      const what = `${name} ${launch}`;
      const r = await run({ ...opts, latency: latencyOf(Object.fromEntries(stalled.map((k) => [k, Infinity]))) });
      assertClean(r);
      expect(r.elapsedMs, `${what} ends at the deadline`).toEqual(DEADLINE_MS);
      const want = await run({ ...opts, ...failed });
      expect(r.widget && texts(r.widget), `${what} widget`).toEqual(want.widget && texts(want.widget));
      expect(r.output, `${what} output`).toEqual(want.output);
      expect(failedSources(r).sort(), `${what} failed sources`).toEqual(failedSources(want).sort());
      expect([...new Set(r.timers.map((t) => t.ms))], `${what} deadline`).toEqual([DEADLINE_MS]);
    }
  }
});

test("the requests of every launch race one deadline that each settled request clears", async () => {
  for (const mode of ["widget", "app", "siri", "shortcuts"]) {
    const r = await run({
      mode,
      family: mode === "widget" ? "medium" : null,
      params: { shortcut: "woak embr", query: mode === "app" ? "woak embr" : undefined },
    });
    assertClean(r);
    expect(r.requests.length > 0 && r.timers.length === r.requests.length, `${mode} one timer per request`).toEqual(
      true,
    );
    expect(
      r.timers.every((t) => t.ms === DEADLINE_MS && !t.repeats && t.invalidated),
      `${mode} deadline`,
    ).toEqual(true);
  }
});

test("answers just inside the deadline are used, and one just past it falls back", async () => {
  const instant = await run({ family: "medium" });
  const inside = await run({
    family: "medium",
    latency: latencyOf({ etd: DEADLINE_MS - 1, schedule: DEADLINE_MS - 1, feed: DEADLINE_MS - 1 }),
  });
  assertClean(inside);
  expect(inside.warnings, "inside warnings").toEqual(instant.warnings);
  expect(inside.elapsedMs, "inside elapsed").toEqual(DEADLINE_MS - 1);
  expect(texts(inside.widget), "inside widget").toEqual(texts(instant.widget));
  const past = await run({ family: "medium", latency: latencyOf({ etd: DEADLINE_MS + 1, feed: DEADLINE_MS - 1 }) });
  assertClean(past);
  expect(failedSources(past), "past failed sources").toEqual(["WOAK-EMBR ETD", "EMBR-WOAK ETD"]);
  expect(past.elapsedMs, "past elapsed").toEqual(DEADLINE_MS);
  const failed = await run({ family: "medium", ...ETD_DOWN });
  expect(texts(past.widget), "past widget").toEqual(texts(failed.widget));
});

test("a transfer round left without time falls back to the plan times for its legs", async () => {
  const step = transferStep(0, ON_TIME);
  const instant = await step.run({ mode: "shortcuts" });
  // the plan answers late, leaving no time for the ETD of the transfer stations
  const late = await step.run({
    mode: "shortcuts",
    latency: (url) =>
      url.includes("cmd=depart") ? DEADLINE_MS - 100 : url.includes("orig=MCAR") || url.includes("orig=19TH") ? 200 : 0,
  });
  assertClean(late);
  expect(late.elapsedMs, "elapsed").toEqual(DEADLINE_MS);
  expect(
    failedSources(late)
      .filter((s) => s.endsWith(" ETD"))
      .sort(),
    "timed out legs",
  ).toEqual(["19TH-ROCK ETD", "MCAR-FRMT ETD"]);
  expect(late.output, "output").toEqual(
    instant.output.replace(/(Rockridge to Fremont|Fremont to Rockridge) in/g, "$1 scheduled in"),
  );
});

test("under long-tailed latencies a run ends by the deadline and times out only for late answers", async () => {
  // deterministic pseudo-random numbers in [0, 1) from a 32-bit seed (mulberry32)
  let a = 20261108;
  const rand = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  // log-normal around a 0.5 s median, wide enough that a few answers take over 15 s, and 2% never answer
  const draw = () =>
    rand() < 0.02
      ? Infinity
      : Math.round(500 * Math.exp(1.6 * Math.sqrt(-2 * Math.log(1 - rand())) * Math.cos(2 * Math.PI * rand())));
  let late = 0;
  for (let i = 0; i < 300; i++) {
    const sent = [];
    const latency = () => {
      const ms = draw();
      sent.push(ms);
      return ms;
    };
    const opts =
      i % 2 === 0
        ? { family: ["small", "medium", "large", "accessoryRectangular"][i % 4] }
        : { mode: "shortcuts", params: { shortcut: "woak embr" } };
    const r = await run({ ...opts, latency });
    assertClean(r);
    const what = `run ${i}`;
    expect(r.elapsedMs <= DEADLINE_MS, `${what} ends by the deadline, not ${r.elapsedMs}`).toEqual(true);
    const timedOut = r.warnings.some((w) => w.endsWith(": timed out"));
    expect(timedOut, `${what} times out exactly when it reaches the deadline`).toEqual(r.elapsedMs === DEADLINE_MS);
    if (!timedOut)
      expect(
        sent.every((ms) => ms < DEADLINE_MS),
        `${what} waited for every answer`,
      ).toEqual(true);
    if (r.widget === null && r.output === null) throw new Error(`${what} rendered nothing`);
    late += timedOut;
  }
  expect(late > 0 && late < 300, `some runs and not all reach the deadline, ${late}`).toEqual(true);
});

test("a commute requests its trip plan with its live trains", async () => {
  const r = await run({ family: "medium" });
  assertClean(r);
  const first = (pattern) => r.requests.findIndex((u) => pattern.test(u));
  for (const [from, to] of [
    ["WOAK", "EMBR"],
    ["EMBR", "WOAK"],
  ]) {
    const plan = first(new RegExp(`cmd=depart&orig=${from}&dest=${to}&`));
    expect(plan >= 0 && plan < first(new RegExp(`cmd=etd&orig=${from}&`)), `${from}-${to} plan before ETD`).toEqual(
      true,
    );
  }
});

test("GTFS-RT colors a departure only when its stops fit one line", async () => {
  // westbound Yellow and Blue trips at West Oakland also fit Red and Green
  const commute = await run({ family: "medium", ...REJECTED_KEY });
  expect(dots(commute.widget), "commute line markers").toEqual([UNKNOWN, UNKNOWN, Y, B, Y, B]);
  const single = await run({ family: "medium", ...REJECTED_KEY, params: { widget: "embr" } });
  assertClean(single);
  expect(dots(single.widget), "single line markers").toEqual([B, UNKNOWN, UNKNOWN]);
});

test("GTFS-RT infers the terminal stop the feed omits", async () => {
  // southbound trips end at Balboa Park in the feed but continue to Daly City
  const r = await run({ family: "medium", ...REJECTED_KEY, params: { widget: "embr daly" } });
  assertClean(r);
  expect(texts(r.widget), "medium widget").toEqual([
    ...["Embarcadero", "to Daly City", "11:13 PM", "11:22pm", "11:32pm"],
    ...["Daly City", "to Embarcadero", "11:12 PM", "11:23pm", "11:32pm"],
  ]);
});

test("GTFS-RT trips listing only their first station match by the omitted final stop", async () => {
  // trips leaving Millbrae list Millbrae alone, which is also where southbound routes end
  const r = await run({ family: "accessoryInline", ...REJECTED_KEY, params: { widget: "mlbr sfia" } });
  assertClean(r);
  expect(texts(r.widget), "inline").toEqual(["MLBR\u2192 11:16 PM  SFIA\u2192 --"]);
});

test("rejected API key: falls back to one shared GTFS-RT fetch and warns", async () => {
  const r = await run({ family: "medium", ...REJECTED_KEY });
  assertClean(r);
  expect(texts(r.widget), "medium widget").toEqual([...RT_WOAK_EMBR, ...RT_EMBR_WOAK]);
  expect(gtfsRtRequests(r).length, "GTFS-RT requests").toEqual(1);
  // the trip planner, asked for trips with a transfer, shares the key
  expect(failedSources(r), "failed sources").toEqual([
    "WOAK-EMBR schedule",
    "EMBR-WOAK schedule",
    "WOAK-EMBR ETD",
    "EMBR-WOAK ETD",
  ]);
});

test("non-JSON ETD response: falls back to GTFS-RT and warns", async () => {
  const r = await run({ family: "accessoryRectangular", ...ETD_DOWN });
  assertClean(r);
  expect(texts(r.widget), "rectangular").toEqual([
    "BART",
    "West Oakland",
    " \u2192 Embarcadero",
    "11:15 PM",
    "  11:25pm",
    "Embarcadero",
    " \u2192 West Oakland",
    "(B) ",
    "11:10 PM",
    "  (Y) 11:22pm",
  ]);
  expect(r.warnings.length, "warnings").toEqual(2);
});
