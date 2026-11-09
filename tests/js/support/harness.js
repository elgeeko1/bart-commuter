// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

// Scriptable runtime stubs for running the widget script against recorded or synthetic data. Paths are
// relative to the repository root, which must be the working directory.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

export const ENGINE_PATH = "bart-commuter.js";
export const METRICS_PATH = "tests/fixtures/font-metrics.json";
export const GTFS_RT_URL = "https://api.bart.gov/gtfsrt/tripupdate.aspx";
// prefix of the ETD and schedule endpoints
export const LEGACY_API_URL = "https://api.bart.gov/api/";
export const TIME_ZONE = "America/Los_Angeles";

export function readText(path) {
  return readFileSync(path, "utf8");
}

export function readBytes(path) {
  return Array.from(readFileSync(path));
}

// ICU may separate the time and day period with U+202F
export function formatClock(date) {
  return date
    .toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: TIME_ZONE })
    .replace(/\u202f/g, " ");
}

// widget element; every Scriptable widget type is modeled as a node in one tree
export class Node {
  constructor(kind, value) {
    this.kind = kind;
    this.value = value;
    this.children = [];
  }
  add(kind, value) {
    const node = new Node(kind, value);
    this.children.push(node);
    return node;
  }
  addText(text) {
    return this.add("text", text);
  }
  addDate(date) {
    return this.add("date", date);
  }
  addImage(image) {
    return this.add("image", image);
  }
  addStack() {
    return this.add("stack");
  }
  // value is the length, or null for a flexible spacer
  addSpacer(length = null) {
    return this.add("spacer", length);
  }
  setPadding(top, leading, bottom, trailing) {
    this.padding = { top, leading, bottom, trailing };
  }
  layoutVertically() {
    this.vertical = true;
  }
  layoutHorizontally() {
    this.vertical = false;
  }
  topAlignContent() {
    this.contentAlign = "top";
  }
  centerAlignContent() {
    this.contentAlign = "center";
  }
  bottomAlignContent() {
    this.contentAlign = "bottom";
  }
  applyOffsetStyle() {
    this.offset = true;
  }
  rightAlignText() {
    this.align = "right";
  }
  leftAlignText() {}
  centerAlignText() {}
}

// what an offset date reads, since iOS rather than the script spells it
export const DEFAULT_OFFSET = "+4 minutes";
let shownOffset = DEFAULT_OFFSET;

export function setShownOffset(text) {
  shownOffset = text;
}

export function shownText(node) {
  return node.kind === "date" ? shownOffset : node.value;
}

// SF Pro advances and line heights written by tools/measure_fonts.js
let fontMetrics = null;

/**
 * Estimates the size of a widget element as WidgetKit lays it out. Stacks are horizontal unless laid
 * out vertically, a list widget is vertical, a flexible spacer is zero long, and a stack size above
 * zero fixes that dimension.
 * @param {string[]} errors  collects elements that cannot be measured
 * @param {number} textScale  factor applied to text widths, as when text shrinks to fit
 * @returns {{ width: number, height: number }}
 */
export function naturalSize(node, errors, textScale = 1) {
  if (node.kind === "text" || node.kind === "date") {
    fontMetrics ??= JSON.parse(readText(METRICS_PATH));
    const font = fontMetrics[node.font];
    if (!font) {
      errors.push(`unmeasured font: ${node.font}`);
      return { width: 0, height: 0 };
    }
    let width = 0;
    for (const c of shownText(node)) {
      if (!(c in font.advances)) errors.push(`unmeasured character: ${JSON.stringify(c)}`);
      width += font.advances[c] ?? 0;
    }
    return { width: width * textScale, height: font.lineHeight };
  }
  if (node.kind === "image") {
    if (node.imageSize) return { width: node.imageSize.width, height: node.imageSize.height };
    errors.push("unmeasured image");
    return { width: 0, height: 0 };
  }
  const vertical = node.kind === "widget" || node.vertical === true;
  let along = 0;
  let across = 0;
  for (const c of node.children) {
    if (c.kind === "spacer") {
      along += c.value ?? 0;
      continue;
    }
    const size = naturalSize(c, errors, textScale);
    along += vertical ? size.height : size.width;
    across = Math.max(across, vertical ? size.width : size.height);
  }
  const size = vertical ? { width: across, height: along } : { width: along, height: across };
  // a fixed box narrower than its contents clips or shrinks them
  if (node.size?.width > 0 && size.width > node.size.width + 0.05) {
    errors.push(`contents ${size.width.toFixed(1)}pt wide in a ${node.size.width}pt box`);
  }
  if (node.size?.width > 0) size.width = node.size.width;
  if (node.size?.height > 0) size.height = node.size.height;
  return size;
}

// stack of the refresh symbol, which holds no train information
const isUpdated = (node) =>
  node.kind === "stack" && node.children.some((c) => c.kind === "image" && c.value === "arrow.clockwise");

// labels checked on their own, leaving the times and stations of a widget
const LABELS = ["BART Commuter"];

// rendered strings apart from labels in document order, with a clock time and its smaller period as one string
export function texts(node) {
  const parts = node.children.flatMap((c) => {
    if (c.kind === "text") return LABELS.includes(c.value) ? [] : [c.value];
    if (c.kind === "image" || isUpdated(c)) return [];
    return texts(c);
  });
  return parts.reduce((joined, part) => {
    if (/^ [AP]M/.test(part) && /^\d+:\d\d$/.test(joined.at(-1))) joined[joined.length - 1] += part;
    else joined.push(part);
    return joined;
  }, []);
}

// SF Symbol names of images in document order, each with the text that follows it in its stack
export function symbols(node) {
  return node.children.flatMap((c, i) => {
    if (c.kind !== "image") return symbols(c);
    const next = node.children.slice(i + 1).find((n) => n.kind === "text");
    return [`${c.value} ${next ? next.value : ""}`.trim()];
  });
}

// a Date whose current time is now, epoch ms or a function returning them
export function fakeDate(now) {
  const current = typeof now === "function" ? now : () => now;
  return class FakeDate extends Date {
    constructor(...a) {
      if (a.length === 0) super(current());
      else super(...a);
    }
    static now() {
      return current();
    }
  };
}

// stations a fixture capture holds
export const ETD_STATIONS = ["WOAK", "EMBR", "POWL", "ROCK"];
export const SCHEDULE_STATIONS = ["WOAK", "EMBR"];
export const PLANNED_TRIPS = ["WOAK-EMBR", "EMBR-WOAK"];

// reads a fixture capture directory; plans lists the captured trip planner pairs, and
// stations the captured ETD stations
export function readFixtures(dir, { withEtd, plans = PLANNED_TRIPS, stations = ETD_STATIONS }) {
  const etd = {};
  const schedule = {};
  const depart = {};
  if (withEtd) {
    for (const abbr of stations) etd[abbr] = readText(`${dir}/etd-${abbr}.json`);
    for (const abbr of SCHEDULE_STATIONS) schedule[abbr] = readText(`${dir}/sched-${abbr}.json`);
    for (const pair of plans) depart[pair] = readText(`${dir}/depart-${pair}.json`);
  }
  return { etd, schedule, depart, feed: readBytes(`${dir}/tripupdate.pb`) };
}

// protobuf encoding for hand-built GTFS-RT feeds
export function varint(n) {
  const out = [];
  while (n >= 0x80) {
    out.push((n % 0x80) | 0x80);
    n = Math.floor(n / 0x80);
  }
  return [...out, n];
}

export function field(num, bytes) {
  return [...varint(num * 8 + 2), ...varint(bytes.length), ...bytes];
}

export function varintField(num, n) {
  return [...varint(num * 8), ...varint(n)];
}

export function ascii(text) {
  return [...text].map((c) => c.charCodeAt(0));
}

/**
 * Builds a GTFS-RT feed of trips that depart their platforms a minute apart, from firstMin after now,
 * with a header timestamp ageSec before now, in epoch ms.
 * @param {{ platforms: string[], firstMin?: number, relationship?: number, skipped?: number[] }[]} trips
 *   relationship is the schedule_relationship of the trip; skipped lists indexes of SKIPPED stops
 */
export function feedOfTrips(trips, { now, ageSec = 0 }) {
  const nowSec = now / 1000;
  const entities = trips.flatMap(({ platforms, firstMin = 1, relationship, skipped = [] }) => {
    const descriptor = relationship === undefined ? [] : field(1, varintField(4, relationship));
    const updates = platforms.flatMap((p, k) => {
      const stop = [...field(4, ascii(p)), ...field(3, varintField(2, nowSec + 60 * (firstMin + k)))];
      return field(2, skipped.includes(k) ? [...stop, ...varintField(5, 1)] : stop);
    });
    return field(2, field(3, [...descriptor, ...updates]));
  });
  return [...field(1, varintField(3, nowSec - ageSec)), ...entities];
}

/**
 * Builds an ETD response for a station.
 * @param {[string, string, string, string, (number | { minutes: number, cancelled: boolean })[]][]} lists
 *   destination lists of line color, destination code and name, direction, and estimates in minutes
 */
export function etdResponse(abbr, name, lists) {
  const etd = lists.map(([color, dest, destination, direction, estimates]) => ({
    destination,
    abbreviation: dest,
    limited: "0",
    estimate: estimates.map((e) => {
      const { minutes, cancelled } = typeof e === "number" ? { minutes: e, cancelled: false } : e;
      return { minutes: String(minutes), direction, color, cancelflag: cancelled ? "1" : "0" };
    }),
  }));
  // ETD omits etd when a station has no departures
  const station = etd.length > 0 ? { name, abbr, etd } : { name, abbr };
  return JSON.stringify({ root: { station: [station] } });
}

// a trip planner time and date, "hh:mm AM" and "MM/DD/YYYY" in the BART time zone
export function plannerTime(ms) {
  const date = new Date(ms);
  const time = date.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", timeZone: TIME_ZONE });
  const day = date.toLocaleDateString("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: TIME_ZONE,
  });
  return { time: time.replace(/\u202f/g, " "), day };
}

/**
 * Builds a trip planner response.
 * @param {[number, string, string, number, number][][]} trips  the legs of each trip in order, as a schedule
 *   route number, origin and destination codes, and departure and arrival epoch ms
 */
export function planResponse(trips) {
  const trip = trips.map((legs) => ({
    "@origin": legs[0][1],
    "@destination": legs[legs.length - 1][2],
    leg: legs.map(([route, from, to, departs, arrives], i) => {
      const [d, a] = [plannerTime(departs), plannerTime(arrives)];
      return {
        "@order": String(i + 1),
        "@origin": from,
        "@destination": to,
        "@origTimeMin": d.time,
        "@origTimeDate": d.day,
        "@destTimeMin": a.time,
        "@destTimeDate": a.day,
        "@line": `ROUTE ${route}`,
      };
    }),
  }));
  return JSON.stringify({ root: { schedule: { request: { trip } } } });
}

/**
 * Builds a trip planner response of direct trips.
 * @param {number[]} departures  epoch ms
 * @param {number} runMin  minutes from origin to destination
 */
export function directPlan(route, from, to, departures, runMin) {
  return planResponse(departures.map((t) => [[route, from, to, t, t + runMin * 60000]]));
}

const scriptFunctions = new Map();

/**
 * A script file as a function of names, resolving once the script and after have run.
 * The file loads as a module, in strict mode unlike Scriptable, because coverage reports only modules. The
 * wrapper stays on the first and last lines of the script to keep its line numbers.
 * @param {string} path
 * @param {string[]} names  identifiers the script sees as parameters, fixed by the first call for a file
 * @param {string} [after]  code run at the end of the script in its scope, fixed by the first call for a file
 * @returns {(...values: unknown[]) => Promise<void>}
 */
export function scriptFunction(path, names, after = "") {
  const file = resolve(path);
  if (!scriptFunctions.has(file)) {
    const filter = new RegExp(`^${file.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`);
    Bun.plugin({
      name: file,
      setup(build) {
        build.onLoad({ filter }, () => ({
          loader: "js",
          contents: `export default function (${names.join(", ")}) { return (async () => {${readText(file).trimEnd()} ${after} })(); }`,
        }));
      },
    });
    scriptFunctions.set(file, import.meta.require(file).default);
  }
  return scriptFunctions.get(file);
}

// resolves once pending microtasks have all run
const idle = () => new Promise((resolve) => setImmediate(resolve));

/**
 * Runs the widget script once against fixture data.
 * @param {object} opts
 * @param {string} [opts.script]  path of the script to run
 * @param {number} opts.now  epoch ms seen by the script
 * @param {"widget"|"siri"|"app"|"shortcuts"} opts.mode  how Scriptable launched the script
 * @param {string|null} opts.family  config.widgetFamily in widget mode
 * @param {{ widget?: string, shortcut?: string, query?: string }} opts.params
 * @param {null|"invalid-key"|"not-json"} opts.etdFailure  simulated ETD failure
 * @param {null|"invalid-key"|"not-json"} opts.scheduleFailure  simulated schedule and trip planner failure
 * @param {{ etd: Object<string, string>, schedule: Object<string, string>, depart: Object<string, string>,
 *   feed: number[] }} opts.fixtures
 * @param {string} [opts.epilogue]  code run after run() in the scope of the script, which sees its declarations
 * @param {(url: string) => number} [opts.latency]  simulated ms from sending a request to its answer;
 *   Infinity never answers. Time is simulated: Date and timers follow a clock that advances from now
 *   to each answer and timer in turn, and result.elapsedMs is the clock when the run ends.
 */
export function runScript({
  script = ENGINE_PATH,
  now,
  mode = "widget",
  family = null,
  params = {},
  etdFailure = null,
  scheduleFailure = null,
  fixtures,
  epilogue = "",
  latency = () => 0,
}) {
  const result = {
    widget: null,
    presented: null,
    output: null,
    spoken: null,
    thrown: null,
    warnings: [],
    requests: [],
    harnessErrors: [],
    timers: [],
    elapsedMs: 0,
  };

  // simulated time: pending answers and timers, run in order of time, then of scheduling
  let clock = now;
  let sequence = 0;
  const events = [];
  const schedule = (ms, fire) => {
    const event = { at: clock + ms, sequence: sequence++, fire, cancelled: false };
    events.push(event);
    return event;
  };
  // answers a request latency(url) ms after it is sent, with what respond returns or throws
  const answer = (url, respond) =>
    new Promise((resolve, reject) => {
      const ms = latency(url);
      if (ms === Infinity) return;
      schedule(ms, () => {
        try {
          resolve(respond());
        } catch (e) {
          reject(e);
        }
      });
    });

  class Request {
    constructor(url) {
      this.url = url;
      result.requests.push(url);
    }
    // a legacy API answer as its status and body
    legacyAnswer() {
      const isSchedule = this.url.includes("/sched.aspx?");
      const failure = isSchedule ? scheduleFailure : etdFailure;
      if (failure === "invalid-key") {
        const body = {
          root: { message: { error: { text: "Invalid key", details: "The api key was missing or invalid." } } },
        };
        return { status: 400, text: JSON.stringify(body) };
      }
      if (failure === "not-json") return { status: 502, text: "<html><body>502 Bad Gateway</body></html>" };
      const plan = this.url.match(/[?&]cmd=depart&orig=(\w+)&dest=(\w+)&/);
      const m = plan || this.url.match(/[?&]orig=(\w+)&/);
      const key = m && (plan ? `${m[1]}-${m[2]}` : m[1]);
      const table = plan ? fixtures.depart : isSchedule ? fixtures.schedule : fixtures.etd;
      if (!m || !(key in table)) {
        result.harnessErrors.push(`no fixture for ${this.url}`);
        throw new Error("missing fixture");
      }
      return { status: 200, text: table[key] };
    }
    load() {
      return answer(this.url, () => this.respond());
    }
    respond() {
      if (this.url.startsWith(LEGACY_API_URL)) {
        const { status, text } = this.legacyAnswer();
        this.response = { statusCode: status };
        return { toRawString: () => text };
      }
      if (this.url !== GTFS_RT_URL) {
        result.harnessErrors.push(`unexpected binary request ${this.url}`);
        throw new Error("unexpected request");
      }
      this.response = { statusCode: 200 };
      return { getBytes: () => fixtures.feed.slice() };
    }
  }

  class ListWidget extends Node {
    constructor() {
      super("widget");
    }
    async presentMedium() {
      result.presented = this;
    }
  }

  const scope = {
    Date: fakeDate(() => clock),
    Color: class Color {
      constructor(hex) {
        this.hex = hex;
      }
      static white() {
        return new Color("#ffffff");
      }
      // hex is the dark appearance, the one the layout tests see
      static dynamic(light, dark) {
        return Object.assign(new Color(dark.hex), { lightHex: light.hex });
      }
    },
    Size: class Size {
      constructor(width, height) {
        Object.assign(this, { width, height });
      }
    },
    Font: new Proxy({}, { get: (_, name) => (size) => `${String(name)}(${size})` }),
    DateFormatter: class DateFormatter {
      // only the 12-hour English format is modeled
      string(date) {
        if (this.dateFormat !== "h:mm a" || this.locale !== "en") {
          result.harnessErrors.push(`unmodeled DateFormatter: ${this.dateFormat} ${this.locale}`);
        }
        return formatClock(date);
      }
    },
    SFSymbol: { named: (name) => ({ image: name }) },
    Request,
    ListWidget,
    Timer: {
      schedule(ms, repeats, callback) {
        const event = schedule(ms, callback);
        const timer = {
          ms,
          repeats,
          invalidated: false,
          invalidate: () => (timer.invalidated = event.cancelled = true),
        };
        result.timers.push(timer);
        return timer;
      },
    },
    Script: {
      setWidget: (w) => {
        result.widget = w;
      },
      setShortcutOutput: (v) => {
        result.output = v;
      },
      complete() {},
    },
    Speech: {
      speak: (t) => {
        result.spoken = t;
      },
    },
    console: {
      log() {},
      warn: (m) => result.warnings.push(m),
      error: (m) => result.harnessErrors.push(`console.error: ${m}`),
    },
    config: {
      runsInWidget: mode === "widget",
      runsInAccessoryWidget: mode === "widget" && String(family).startsWith("accessory"),
      widgetFamily: mode === "widget" ? family : null,
      runsWithSiri: mode === "siri",
      runsInApp: mode === "app",
    },
    args: {
      widgetParameter: params.widget ?? null,
      shortcutParameter: params.shortcut ?? null,
      queryParameters: params.query === undefined ? {} : { stations: params.query },
    },
    module: { exports: {} },
  };

  // the exported run() of the script renders the boards
  const body = scriptFunction(
    script,
    [...Object.keys(scope), "EPILOGUE"],
    "await module.exports.run(); await eval(EPILOGUE);",
  );
  let done = false;
  body(...Object.values(scope), `(async () => {\n${epilogue}\n})();`).then(
    () => (done = true),
    (e) => {
      result.thrown = e;
      done = true;
    },
  );
  // runs the pending work of the script, then advances the clock to the next events due together and fires
  // them in the order they were scheduled, until the run ends
  return (async () => {
    for (;;) {
      await idle();
      if (done) break;
      const pending = events.filter((e) => !e.cancelled);
      if (pending.length === 0) {
        result.harnessErrors.push("the run waits for an answer that never comes");
        break;
      }
      clock = Math.min(...pending.map((e) => e.at));
      const due = pending.filter((e) => e.at === clock).sort((x, y) => x.sequence - y.sequence);
      for (const e of due) events.splice(events.indexOf(e), 1);
      for (const e of due) if (!e.cancelled) e.fire();
    }
    result.elapsedMs = clock - now;
    return result;
  })();
}
