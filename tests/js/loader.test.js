// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

// Tests of the loader, and of the compact copy of it that README.md carries, against an in-memory Scriptable
// file system and network, including the engine run through it. The loader evaluates engines in the global
// scope, so each run installs its Scriptable stubs as globals and the tests run one at a time. BART_LOADER_RUNS
// sets the number of runs in the random sequence test.

import { beforeAll, describe, expect, test } from "bun:test";
import { Buffer } from "node:buffer";
import { createHash } from "node:crypto";
import {
  ENGINE_PATH,
  GTFS_RT_URL,
  LEGACY_API_URL,
  Node,
  fakeDate,
  formatClock,
  readBytes,
  readFixtures,
  runScript,
  scriptFunction,
  texts,
} from "./support/harness.js";
import { python } from "./support/python.js";

const SEQUENCE_RUNS = Number(process.env.BART_LOADER_RUNS ?? 300);
const ENGINE_BYTES = readBytes(ENGINE_PATH);
const ENGINE_MANIFEST = python("tools/build_manifest.py", "--print");
// the loader the current tests run
let runCurrentLoader = null;
const RELEASES = "https://github.com/elgeeko1/bart-commuter/releases";
const MANIFEST_URL = `${RELEASES}/latest/download/manifest.json`;
const engineUrl = (version) => `${RELEASES}/download/v${version}/bart-commuter.js`;
const ENGINE_DIR = "/docs/bart-commuter";
const STATE_FILE = `${ENGINE_DIR}/state.json`;
const OFFLINE_MESSAGE = "The Internet connection appears to be offline.";
// a network response that never arrives
const HANG = { hang: true };
// a well-formed checksum of no engine the tests serve
const WRONG_SHA256 = "0".repeat(64);
// deadlines of a run, from its start
const INSTALL_DEADLINE_MS = 10 * 1000;
const CHECK_DEADLINE_MS = 20 * 1000;
// two seconds after the captured GTFS-RT feed header timestamp
const T0 = 1793949005 * 1000;
const HOUR_MS = 60 * 60 * 1000;
const CAPTURED_FIXTURES = readFixtures("tests/fixtures", { withEtd: true });

// test files and responses are ASCII, so one byte per character
const asciiBytes = (text) => Array.from(text, (c) => c.charCodeAt(0));
function bytesText(bytes) {
  let text = "";
  for (let i = 0; i < bytes.length; i += 8192) text += String.fromCharCode(...bytes.slice(i, i + 8192));
  return text;
}

class FakeData {
  constructor(bytes) {
    this.bytes = Array.from(bytes);
  }
  getBytes() {
    return this.bytes.slice();
  }
  // UTF-8, as Scriptable decodes
  toRawString() {
    return decodeURIComponent(escape(bytesText(this.bytes)));
  }
}

// sets globals while fn runs, restoring them once its promise settles
async function withGlobals(globals, fn) {
  const saved = Object.keys(globals).map((k) => [k, Object.getOwnPropertyDescriptor(globalThis, k)]);
  Object.assign(globalThis, globals);
  try {
    return await fn();
  } finally {
    for (const [k, d] of saved) {
      if (d) Object.defineProperty(globalThis, k, d);
      else delete globalThis[k];
    }
  }
}

function fakeEngine(version, { api = 1, run = "" } = {}) {
  return `module.exports = { api: ${api}, run: async () => { Script.setShortcutOutput("engine ${version}"); ${run} } };\n`;
}

const sha256Of = (body) =>
  createHash("sha256")
    .update(Buffer.from(typeof body === "string" ? asciiBytes(body) : body))
    .digest("hex");

const manifestFor = (version, source = fakeEngine(version), { api = 1, sha256 = sha256Of(source) } = {}) =>
  JSON.stringify({ version, api, sha256 });

// network responses of a release whose engine is source
function release(version, source = fakeEngine(version), manifest = {}) {
  return { [MANIFEST_URL]: manifestFor(version, source, manifest), [engineUrl(version)]: source };
}

// files of an installed engine whose last release check started at checked
function installed(version, { source = fakeEngine(version), checked = T0 - HOUR_MS } = {}) {
  return {
    [`${ENGINE_DIR}/engine-${version}.js`]: source,
    [STATE_FILE]: JSON.stringify({ version: version, checked }),
  };
}

// files after a run, in the form runLoader takes
const filesAfter = (r) => Object.fromEntries([...r.files].map(([path, bytes]) => [path, bytesText(bytes)]));
const stateAfter = (r) => (r.files.has(STATE_FILE) ? JSON.parse(bytesText(r.files.get(STATE_FILE))) : null);

/**
 * Runs the loader once.
 * @param {object} opts
 * @param {Object<string, string>} opts.files  file contents by path
 * @param {Object<string, string | { status: number, body: string | number[] } | null>} opts.network
 *   responses by URL; null fails the request as when offline, HANG never answers, and an unlisted
 *   release URL answers 404
 * @param {"widget"|"app"|"shortcuts"} opts.mode  how Scriptable launched the script
 * @param {string | null} opts.family  config.widgetFamily in widget mode
 * @param {boolean} opts.expire  whether timers fire, as soon as scheduled; otherwise they never do
 * @param {((store: Map<string, number[]>, from: string, to: string) => void) | null} opts.beforeMove
 *   changes the files just before each move, as a concurrent run would
 */
async function runLoader({
  files = {},
  network = {},
  now = T0,
  mode = "widget",
  family = "small",
  params = {},
  expire = false,
  beforeMove = null,
}) {
  const store = new Map(Object.entries(files).map(([path, text]) => [path, asciiBytes(text)]));
  const dirs = new Set(["/docs", ...[...store.keys()].map((p) => p.slice(0, p.lastIndexOf("/")))]);
  const result = {
    files: store,
    requests: [],
    warnings: [],
    output: null,
    widget: null,
    presented: null,
    completions: 0,
    stateAtCompletion: null,
    thrown: null,
    unserved: [],
    timers: [],
  };

  const fm = {
    documentsDirectory: () => "/docs",
    joinPath: (a, b) => `${a}/${b}`,
    fileExists: (p) => store.has(p) || dirs.has(p),
    createDirectory: (p) => dirs.add(p),
    read(p) {
      if (!store.has(p)) throw new Error(`no such file: ${p}`);
      return new FakeData(store.get(p));
    },
    readString: (p) => fm.read(p).toRawString(),
    write: (p, data) => store.set(p, data.getBytes()),
    writeString: (p, text) => store.set(p, asciiBytes(text)),
    remove: (p) => store.delete(p),
    move(from, to) {
      beforeMove?.(store, from, to);
      if (!store.has(from)) throw new Error(`no such file: ${from}`);
      if (store.has(to)) throw new Error(`file exists: ${to}`);
      store.set(to, store.get(from));
      store.delete(from);
    },
    listContents: (dir) =>
      [...store.keys()]
        .filter((p) => p.startsWith(`${dir}/`) && !p.slice(dir.length + 1).includes("/"))
        .map((p) => p.slice(dir.length + 1)),
  };

  class Request {
    constructor(url) {
      this.url = url;
      result.requests.push(url);
    }
    async load() {
      if (this.url === GTFS_RT_URL) {
        this.response = { statusCode: 200 };
        return new FakeData(CAPTURED_FIXTURES.feed);
      }
      if (this.url.startsWith(LEGACY_API_URL)) return new FakeData(asciiBytes(JSON.stringify(this.legacyFixture())));
      const answer = !this.url.startsWith(RELEASES)
        ? null
        : this.url in network
          ? network[this.url]
          : { status: 404, body: "Not Found" };
      if (answer === null) throw new Error(OFFLINE_MESSAGE);
      if (answer === HANG) return new Promise(() => {});
      const { status, body } = typeof answer === "string" ? { status: 200, body: answer } : answer;
      this.response = { statusCode: status };
      return new FakeData(typeof body === "string" ? asciiBytes(body) : body);
    }
    legacyFixture() {
      const plan = this.url.match(/[?&]cmd=depart&orig=(\w+)&dest=(\w+)&/);
      const m = plan || this.url.match(/[?&]orig=(\w+)&/);
      const table = plan
        ? CAPTURED_FIXTURES.depart
        : this.url.includes("/sched.aspx?")
          ? CAPTURED_FIXTURES.schedule
          : CAPTURED_FIXTURES.etd;
      const key = m && (plan ? `${m[1]}-${m[2]}` : m[1]);
      if (!m || !(key in table)) {
        result.unserved.push(this.url);
        throw new Error("missing fixture");
      }
      this.response = { statusCode: 200 };
      return JSON.parse(table[key]);
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

  const Timer = {
    schedule(ms, repeats, callback) {
      const timer = { ms, repeats, invalidated: false, invalidate: () => (timer.invalidated = true) };
      result.timers.push(timer);
      if (expire) Promise.resolve().then(() => timer.invalidated || callback());
      return timer;
    },
  };

  const globals = {
    Date: fakeDate(now),
    Timer,
    FileManager: { local: () => fm },
    Request,
    ListWidget,
    Script: {
      setWidget: (w) => (result.widget = w),
      setShortcutOutput: (v) => (result.output = v),
      complete() {
        result.completions++;
        result.stateAtCompletion = stateAfter(result);
      },
    },
    config: {
      runsInWidget: mode === "widget",
      runsInAccessoryWidget: mode === "widget" && String(family).startsWith("accessory"),
      widgetFamily: mode === "widget" ? family : null,
      runsWithSiri: false,
      runsInApp: mode === "app",
    },
    args: { widgetParameter: params.widget ?? null, shortcutParameter: params.shortcut ?? null, queryParameters: {} },
    console: { log() {}, warn: (m) => result.warnings.push(m), error: (m) => result.warnings.push(`error: ${m}`) },
    Color: class Color {
      constructor(hex) {
        this.hex = hex;
      }
      static white() {
        return new Color("#ffffff");
      }
      static dynamic(light, dark) {
        return dark;
      }
    },
    Size: class Size {
      constructor(width, height) {
        Object.assign(this, { width, height });
      }
    },
    Font: new Proxy({}, { get: (_, name) => (size) => `${String(name)}(${size})` }),
    DateFormatter: class DateFormatter {
      string(date) {
        return formatClock(date);
      }
    },
    URLScheme: { forRunningScript: () => "scriptable:///run/bart-commuter" },
    SFSymbol: { named: (name) => ({ image: name }) },
    Speech: { speak() {} },
  };

  try {
    await withGlobals(globals, () => runCurrentLoader());
  } catch (e) {
    result.thrown = e;
  }
  return result;
}

function assertNoThrow(r) {
  if (r.thrown) throw new Error(`loader threw: ${r.thrown.message}`);
}

describe.each([
  ["the loader", scriptFunction("bart-commuter-loader.js", [])],
  [
    "the README copy of the loader",
    new Function(`return (async () => {\n${python("tools/readme_script.py", "--print")}\n})();`),
  ],
])("%s", (_name, runLoaderScript) => {
  beforeAll(() => {
    runCurrentLoader = runLoaderScript;
  });

  test("a first run installs the latest engine and runs it", async () => {
    const r = await runLoader({ network: release("1.0.0") });
    assertNoThrow(r);
    expect(r.output, "output").toEqual("engine 1.0.0");
    expect(r.requests, "requests").toEqual([MANIFEST_URL, engineUrl("1.0.0")]);
    expect(stateAfter(r), "state").toEqual({ version: "1.0.0", checked: T0 });
    expect(r.completions, "completions").toEqual(1);
    expect(
      r.timers.map((t) => [t.ms, t.repeats, t.invalidated]),
      "install deadline",
    ).toEqual([[INSTALL_DEADLINE_MS, false, true]]);
  });

  test("a first run without a network shows the failure in a widget or throws it elsewhere", async () => {
    const widget = await runLoader({ network: { [MANIFEST_URL]: null } });
    assertNoThrow(widget);
    expect(texts(widget.widget), "widget").toEqual([OFFLINE_MESSAGE]);
    expect(widget.completions, "widget completions").toEqual(1);
    const app = await runLoader({ network: { [MANIFEST_URL]: null }, mode: "app" });
    expect(app.thrown?.message, "app").toEqual(OFFLINE_MESSAGE);
    expect(app.completions, "app completions").toEqual(1);
  });

  test("a run within the check interval makes no request", async () => {
    const r = await runLoader({ files: installed("1.0.0", { checked: T0 - HOUR_MS }), network: release("1.1.0") });
    assertNoThrow(r);
    expect(r.requests, "requests").toEqual([]);
    expect(r.output, "output").toEqual("engine 1.0.0");
  });

  test("a due check installs a newer engine for the next run and removes the older", async () => {
    const files = installed("1.0.0", { checked: T0 - 7 * HOUR_MS });
    const first = await runLoader({ files, network: release("1.1.0") });
    assertNoThrow(first);
    expect(first.output, "first output").toEqual("engine 1.0.0");
    expect(first.requests, "first requests").toEqual([MANIFEST_URL, engineUrl("1.1.0")]);
    expect([...first.files.keys()].sort(), "files").toEqual([`${ENGINE_DIR}/engine-1.1.0.js`, STATE_FILE]);
    const second = await runLoader({ files: filesAfter(first), now: T0 + HOUR_MS });
    expect(second.output, "second output").toEqual("engine 1.1.0");
    expect(second.requests, "second requests").toEqual([]);
  });

  test("the script completes once, after the release check", async () => {
    const r = await runLoader({ files: installed("1.0.0", { checked: 0 }), network: release("1.1.0") });
    assertNoThrow(r);
    expect(r.completions, "completions").toEqual(1);
    expect(r.stateAtCompletion.version, "version installed at completion").toEqual("1.1.0");
    expect(
      r.timers.map((t) => [t.ms, t.invalidated]),
      "check deadline",
    ).toEqual([[CHECK_DEADLINE_MS, true]]);
  });

  test("a check recorded in the future, as after the clock is set back, is due", async () => {
    const r = await runLoader({
      files: installed("1.0.0", { checked: T0 + 365 * 24 * HOUR_MS }),
      network: release("1.1.0"),
    });
    assertNoThrow(r);
    expect(r.requests, "requests").toEqual([MANIFEST_URL, engineUrl("1.1.0")]);
    expect(stateAfter(r), "state").toEqual({ version: "1.1.0", checked: T0 });
  });

  test("an engine whose run throws is still replaced by a newer release", async () => {
    const files = installed("1.0.0", {
      source: fakeEngine("1.0.0", { run: 'throw new Error("engine failed");' }),
      checked: 0,
    });
    const r = await runLoader({ files, network: release("1.1.0") });
    assertNoThrow(r);
    expect(texts(r.widget), "widget").toEqual(["engine failed"]);
    expect(stateAfter(r), "state").toEqual({ version: "1.1.0", checked: T0 });
    const next = await runLoader({ files: filesAfter(r), now: T0 + HOUR_MS });
    expect(next.output, "next output").toEqual("engine 1.1.0");
  });

  test("an install keeps the same engine a concurrent run moved into place first", async () => {
    const concurrent = (store, from, to) => store.set(to, store.get(from));
    const r = await runLoader({ network: release("1.0.0"), beforeMove: concurrent });
    assertNoThrow(r);
    expect(r.output, "output").toEqual("engine 1.0.0");
    expect(r.warnings, "warnings").toEqual([]);
    expect([...r.files.keys()].sort(), "files").toEqual([`${ENGINE_DIR}/engine-1.0.0.js`, STATE_FILE]);
    // a move that fails with nothing in place is an error
    const lost = await runLoader({ network: release("1.0.0"), beforeMove: (store, from) => store.delete(from) });
    expect(texts(lost.widget)[0].startsWith("no such file: "), "lost partial widget").toEqual(true);
    expect(stateAfter(lost).version, "lost partial installed").toEqual(null);
  });

  test("removing stale files keeps the partial engine of a concurrent run and removes an abandoned one", async () => {
    const inProgress = `${ENGINE_DIR}/engine-1.1.0.js.${T0 - 5 * 1000}-abc123`;
    const abandoned = `${ENGINE_DIR}/engine-1.1.0.js.${T0 - 2 * 60 * 1000}-def456`;
    const files = { ...installed("1.0.0", { checked: 0 }), [inProgress]: "partial", [abandoned]: "partial" };
    const r = await runLoader({ files, network: release("1.1.0") });
    assertNoThrow(r);
    expect([...r.files.keys()].sort(), "files").toEqual(
      [`${ENGINE_DIR}/engine-1.1.0.js`, inProgress, STATE_FILE].sort(),
    );
  });

  test("a run in the app checks within the interval", async () => {
    const r = await runLoader({
      files: installed("1.0.0", { checked: T0 - 60 * 1000 }),
      network: release("1.0.0"),
      mode: "app",
    });
    assertNoThrow(r);
    expect(r.requests, "requests").toEqual([MANIFEST_URL]);
    expect(
      r.timers.map((t) => [t.ms, t.invalidated]),
      "check deadline",
    ).toEqual([[CHECK_DEADLINE_MS, true]]);
  });

  test("a release no newer than the installed engine is not downloaded", async () => {
    for (const version of ["1.1.0", "1.0.9"]) {
      const r = await runLoader({ files: installed("1.1.0", { checked: 0 }), network: release(version) });
      assertNoThrow(r);
      expect(r.requests, `requests for ${version}`).toEqual([MANIFEST_URL]);
      expect(stateAfter(r).version, `installed for ${version}`).toEqual("1.1.0");
    }
  });

  test("a failed check keeps the engine running and waits for the interval", async () => {
    const cases = {
      "HTTP 404": { [MANIFEST_URL]: { status: 404, body: "Not Found" } },
      offline: { [MANIFEST_URL]: null },
      "bad manifest": { [MANIFEST_URL]: '{"version": "1.1"}' },
      "no checksum": { [MANIFEST_URL]: '{"version": "1.1.0", "api": 1}' },
      "upper-case checksum": release("1.1.0", fakeEngine("1.1.0"), {
        sha256: sha256Of(fakeEngine("1.1.0")).toUpperCase(),
      }),
      "wrong checksum": release("1.1.0", fakeEngine("1.1.0"), { sha256: WRONG_SHA256 }),
      "malformed engine": release("1.1.0", "module.exports = { api: 1 };\n"),
      "newer interface": release("1.1.0", fakeEngine("1.1.0", { api: 2 }), { api: 2 }),
      "missing engine": { [MANIFEST_URL]: manifestFor("1.1.0") },
    };
    for (const [name, network] of Object.entries(cases)) {
      const r = await runLoader({ files: installed("1.0.0", { checked: 0 }), network });
      assertNoThrow(r);
      expect(r.output, `${name} output`).toEqual("engine 1.0.0");
      expect(r.warnings.length, `${name} warnings`).toEqual(1);
      expect(stateAfter(r), `${name} state`).toEqual({ version: "1.0.0", checked: T0 });
      expect(
        [...r.files.keys()].filter((p) => p.includes("1.1.0")),
        `${name} files`,
      ).toEqual([]);
      const next = await runLoader({ files: filesAfter(r), network: release("1.1.0"), now: T0 + HOUR_MS });
      expect(next.requests, `${name} next requests`).toEqual([]);
    }
  });

  test("a malformed or missing engine is downloaded again", async () => {
    const malformed = installed("1.0.0", { source: "module.exports = { api: 1 };\n", checked: T0 });
    const missing = installed("1.0.0", { checked: T0 });
    delete missing[`${ENGINE_DIR}/engine-1.0.0.js`];
    for (const [name, files] of Object.entries({ malformed, missing })) {
      const r = await runLoader({ files, network: release("1.0.0") });
      assertNoThrow(r);
      expect(r.output, `${name} output`).toEqual("engine 1.0.0");
      expect(r.warnings.length, `${name} warnings`).toEqual(1);
      expect(r.requests, `${name} requests`).toEqual([MANIFEST_URL, engineUrl("1.0.0")]);
    }
  });

  test("a malformed engine is not installed", async () => {
    const source = "module.exports = { api: 1 };\n";
    const r = await runLoader({ network: release("1.0.0", source) });
    assertNoThrow(r);
    expect(texts(r.widget), "widget").toEqual(["engine 1.0.0 is malformed"]);
    expect(stateAfter(r).version, "installed").toEqual(null);
  });

  test("an engine that does not match its checksum is neither evaluated nor installed", async () => {
    const source = `Script.setShortcutOutput("evaluated");\n${fakeEngine("1.0.0")}`;
    const r = await runLoader({ network: release("1.0.0", source, { sha256: WRONG_SHA256 }) });
    assertNoThrow(r);
    expect(texts(r.widget), "widget").toEqual(["engine 1.0.0 does not match its checksum"]);
    expect(r.output, "output").toEqual(null);
    expect(stateAfter(r).version, "installed").toEqual(null);
    expect(
      [...r.files.keys()].filter((p) => p.includes("engine-")),
      "files",
    ).toEqual([]);
  });

  test("a manifest without a well-formed checksum is rejected before any engine download", async () => {
    for (const sha256 of [null, "abc", "A".repeat(64)]) {
      const r = await runLoader({ network: release("1.0.0", fakeEngine("1.0.0"), { sha256 }) });
      assertNoThrow(r);
      expect(r.requests, `requests for ${sha256}`).toEqual([MANIFEST_URL]);
      expect(texts(r.widget), `widget for ${sha256}`).toEqual(["invalid release manifest"]);
    }
  });

  test("a checksum matches across SHA-256 padding boundaries and is taken over raw bytes", async () => {
    for (const length of [119, 120, 121, 127, 128, 129, 183, 184]) {
      const r = await runLoader({ network: release("1.0.0", fakeEngine("1.0.0").padEnd(length)) });
      assertNoThrow(r);
      expect(r.output, `${length} bytes`).toEqual("engine 1.0.0");
    }
    const utf8 = Array.from(Buffer.from(`// café\n${fakeEngine("1.0.0")}`));
    const network = { [MANIFEST_URL]: manifestFor("1.0.0", utf8), [engineUrl("1.0.0")]: { status: 200, body: utf8 } };
    const r = await runLoader({ network });
    assertNoThrow(r);
    expect(r.output, "multi-byte engine").toEqual("engine 1.0.0");
  });

  test("a first install in a widget that outlasts its deadline shows it is installing", async () => {
    const network = { [MANIFEST_URL]: manifestFor("1.0.0"), [engineUrl("1.0.0")]: HANG };
    const r = await runLoader({ network, expire: true });
    assertNoThrow(r);
    expect(texts(r.widget), "widget").toEqual(["Installing BART"]);
    expect(r.widget.refreshAfterDate.getTime(), "refresh").toEqual(T0 + 60 * 1000);
    expect(
      r.timers.map((t) => t.ms),
      "deadline",
    ).toEqual([INSTALL_DEADLINE_MS]);
    expect(r.completions, "completions").toEqual(1);
    expect(stateAfter(r), "state").toEqual({ version: null, checked: T0 });
    const next = await runLoader({ files: filesAfter(r), network: release("1.0.0"), now: T0 + 60 * 1000 });
    expect(next.output, "next output").toEqual("engine 1.0.0");
  });

  test("a first install elsewhere that outlasts its deadline fails", async () => {
    const network = { [MANIFEST_URL]: manifestFor("1.0.0"), [engineUrl("1.0.0")]: HANG };
    for (const mode of ["app", "shortcuts"]) {
      const r = await runLoader({ network, mode, expire: true });
      expect(r.thrown, `${mode} thrown`).not.toBeNull();
      expect(r.completions, `${mode} completions`).toEqual(1);
    }
  });

  test("a run stops waiting for a release check at its deadline", async () => {
    const network = { [MANIFEST_URL]: manifestFor("1.1.0"), [engineUrl("1.1.0")]: HANG };
    for (const mode of ["widget", "app", "shortcuts"]) {
      const r = await runLoader({ files: installed("1.0.0", { checked: 0 }), network, mode, expire: true });
      assertNoThrow(r);
      expect(r.output, `${mode} output`).toEqual("engine 1.0.0");
      expect(r.warnings.length, `${mode} warnings`).toEqual(1);
      expect(
        r.timers.map((t) => t.ms),
        `${mode} deadline`,
      ).toEqual([CHECK_DEADLINE_MS]);
      expect(r.completions, `${mode} completions`).toEqual(1);
      expect(stateAfter(r), `${mode} state`).toEqual({ version: "1.0.0", checked: T0 });
    }
  });

  test("unreadable state starts over", async () => {
    const r = await runLoader({ files: { [STATE_FILE]: "{" }, network: release("1.0.0") });
    assertNoThrow(r);
    expect(r.warnings.length, "warnings").toEqual(1);
    expect(r.output, "output").toEqual("engine 1.0.0");
  });

  test("the engine run through the loader behaves as run by the harness", async () => {
    const { version } = JSON.parse(ENGINE_MANIFEST);
    const network = { [MANIFEST_URL]: ENGINE_MANIFEST, [engineUrl(version)]: { status: 200, body: ENGINE_BYTES } };
    const runs = [
      ...["small", "medium", "large", "accessoryRectangular", "accessoryCircular", "accessoryInline"].map((family) => ({
        mode: "widget",
        family,
      })),
      { mode: "shortcuts", family: null, params: { shortcut: "embr woak" } },
    ];
    for (const { mode, family, params = {} } of runs) {
      const what = family ?? mode;
      const direct = await runScript({ now: T0, mode, family, params, fixtures: CAPTURED_FIXTURES });
      const loaded = await runLoader({ network, mode, family, params });
      assertNoThrow(loaded);
      expect(loaded.unserved, `${what} unserved requests`).toEqual([]);
      expect(loaded.requests.slice(0, 2), `${what} release requests`).toEqual([MANIFEST_URL, engineUrl(version)]);
      expect(loaded.requests.slice(2), `${what} BART requests`).toEqual(direct.requests);
      expect(loaded.warnings, `${what} warnings`).toEqual(direct.warnings);
      expect(loaded.output, `${what} output`).toEqual(direct.output);
      expect(loaded.widget && texts(loaded.widget), `${what} widget`).toEqual(direct.widget && texts(direct.widget));
      expect(loaded.completions, `${what} completions`).toEqual(1);
    }
  });

  // deterministic pseudo-random numbers in [0, 1) from a 32-bit seed (mulberry32)
  function random(seed) {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  test(`a random sequence of ${SEQUENCE_RUNS} runs keeps a usable engine and moves only forward`, async () => {
    const rand = random(20261107);
    const pick = (options) => options[Math.floor(rand() * options.length)];
    const kinds = [
      "good",
      "good",
      "good",
      "malformed",
      "syntax error",
      "newer interface",
      "missing engine",
      "tampered",
    ];
    // releases by version, each with its kind; the newest is the latest
    const releases = [];
    // every good version ever published, withdrawn ones included
    const good = new Set();
    let minor = 0;
    const publish = () => {
      const release = { version: `1.${minor++}.0`, kind: pick(kinds) };
      releases.push(release);
      if (release.kind === "good") good.add(release.version);
    };
    publish();
    const sourceOf = ({ version, kind }) =>
      kind === "malformed"
        ? "module.exports = { api: 1 };\n"
        : kind === "syntax error"
          ? "module.exports = {\n"
          : fakeEngine(version, { api: kind === "newer interface" ? 2 : 1 });
    let files = {};
    let now = T0;
    let installed = null;
    for (let i = 0; i < SEQUENCE_RUNS; i++) {
      if (rand() < 0.15) publish();
      // a withdrawn release makes an older one the latest
      else if (releases.length > 1 && rand() < 0.05) releases.pop();
      // the clock mostly advances, and is sometimes set back
      now += pick([
        0,
        60 * 1000,
        15 * 60 * 1000,
        HOUR_MS,
        5 * HOUR_MS,
        7 * HOUR_MS,
        30 * HOUR_MS,
        -HOUR_MS,
        -30 * HOUR_MS,
      ]);
      const mode = pick(["widget", "widget", "widget", "app", "shortcuts"]);
      const latest = releases[releases.length - 1];
      const condition = pick(["ok", "ok", "ok", "offline", "manifest 404", "manifest 500", "stalled"]);
      const network = {};
      if (condition === "offline") network[MANIFEST_URL] = null;
      else if (condition === "manifest 404") network[MANIFEST_URL] = { status: 404, body: "Not Found" };
      else if (condition === "manifest 500") network[MANIFEST_URL] = { status: 500, body: "<html>" };
      else
        network[MANIFEST_URL] =
          condition === "stalled" && rand() < 0.5
            ? HANG
            : manifestFor(latest.version, sourceOf(latest), {
                api: latest.kind === "newer interface" ? 2 : 1,
                sha256: latest.kind === "tampered" ? WRONG_SHA256 : undefined,
              });
      if (latest.kind !== "missing engine")
        network[engineUrl(latest.version)] = condition === "stalled" ? HANG : sourceOf(latest);
      const state = files[STATE_FILE] ? JSON.parse(files[STATE_FILE]) : null;
      const due = !installed || mode === "app" || now - state.checked >= 6 * HOUR_MS || now < state.checked;
      const what = `run ${i} (${mode}, ${condition}, latest ${latest.version} ${latest.kind}, installed ${installed})`;

      const r = await runLoader({ files, network, now, mode, expire: condition === "stalled" });
      expect(r.completions, `${what} completions`).toEqual(1);
      expect(r.unserved, `${what} unserved`).toEqual([]);
      if (!due) expect(r.requests, `${what} requests within the interval`).toEqual([]);
      if (installed) {
        assertNoThrow(r);
        expect(r.output, `${what} runs the installed engine`).toEqual(`engine ${installed}`);
      } else if (r.thrown) {
        expect(mode === "widget", `${what} a widget throws`).toEqual(false);
      }
      const after = stateAfter(r);
      if (after !== null) {
        expect(Number.isFinite(after.checked) && after.checked <= now, `${what} checked`).toEqual(true);
        if (due) expect(after.checked, `${what} a due check is recorded`).toEqual(now);
      }
      const next = after?.version ?? null;
      if (installed)
        expect(
          next !== null && compareVersions(next, installed) >= 0,
          `${what} moves forward from ${installed} to ${next}`,
        ).toEqual(true);
      if (next !== null) {
        expect(good.has(next), `${what} installs only good engines, not ${next}`).toEqual(true);
        expect([...r.files.keys()].filter((p) => p.startsWith(`${ENGINE_DIR}/`)).sort(), `${what} files`).toEqual([
          `${ENGINE_DIR}/engine-${next}.js`,
          STATE_FILE,
        ]);
      }
      const newer = !installed || compareVersions(latest.version, installed) > 0;
      if (due && condition === "ok" && latest.kind === "good" && newer)
        expect(next, `${what} installs the latest release`).toEqual(latest.version);
      if (due && condition === "ok" && (!newer || latest.kind === "newer interface")) {
        expect(r.requests, `${what} reads only the manifest`).toEqual([MANIFEST_URL]);
      }
      if (!installed && next !== null) expect(r.output, `${what} runs a first install`).toEqual(`engine ${next}`);
      installed = next;
      files = filesAfter(r);
    }
    expect(installed !== null, "an engine is installed by the end").toEqual(true);
  });

  // negative, zero or positive as version a is older than, the same as, or newer than b
  function compareVersions(a, b) {
    const [x, y] = [a, b].map((v) => v.split(".").map(Number));
    return x[0] - y[0] || x[1] - y[1] || x[2] - y[2];
  }
});
