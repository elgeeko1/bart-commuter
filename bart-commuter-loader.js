// Variables used by Scriptable.
// These must be at the very top of the file. Do not edit.
// icon-color: yellow; icon-glyph: subway;

// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

/*
 * BART departure boards for Scriptable widgets, Siri, and Shortcuts.
 *
 * Set the widget Parameter, the Shortcuts Run Script input, or a ?stations= URL query to one or two
 * station codes, such as "woak embr". Station codes and setup: https://github.com/elgeeko1/bart-commuter
 *
 * This script runs the widget engine, which it downloads from the latest release of the project on its
 * first run. It checks for a newer release every few hours and whenever it runs in the app, and runs a
 * newer engine from the next run on.
 */

const RELEASES_URL = "https://github.com/elgeeko1/bart-commuter/releases";
const MANIFEST_ASSET = "manifest.json";
const ENGINE_ASSET = "bart-commuter.js";
// version of the engine run() interface this script calls
const ENGINE_API = 1;
// minimum time between the starts of two release checks
const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;

// A run bounds its waits by time since it started, because iOS shows a widget only once its run ends and
// ends an overlong run with an error

// time a first install may take, leaving the rest of the run to the engine; past it a widget shows
// INSTALLING_TEXT
const INSTALL_DEADLINE_MS = 10 * 1000;
// a run stops waiting for a release check this long after it started
const CHECK_DEADLINE_MS = 20 * 1000;
// a partial engine file this old belongs to a run that has ended, since iOS ends every run sooner
const PARTIAL_MAX_AGE_MS = 60 * 1000;
const INSTALLING_TEXT = "Installing BART";
const VERSION_PATTERN = /^\d+\.\d+\.\d+$/;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;

const STARTED = Date.now();
const TIMED_OUT = Symbol("timed out");
const files = FileManager.local();
// folder of the installed engines and the loader state
const DIR = files.joinPath(files.documentsDirectory(), "bart-commuter");
const STATE_PATH = files.joinPath(DIR, "state.json");

/**
 * @typedef {{ version: string, api: number, sha256: string }} Manifest  the engine published by a release
 * @typedef {{ version: string | null, checked: number }} State
 *   version is that of the installed engine, and checked the epoch ms at which the last release check started
 */

// FIPS 180-4 section 4.2.2 constants
const SHA256_K = [
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98,
  0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
  0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8,
  0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819,
  0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
  0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
  0xc67178f2,
];

/**
 * SHA-256 digest as lower-case hex, computed here because Scriptable has no crypto API.
 * @param {number[]} bytes  values 0 to 255
 */
function sha256Hex(bytes) {
  const n = bytes.length;
  // the message, a 1 bit, zeros, and the 64-bit big-endian bit length fill whole 64-byte blocks
  const padded = new Uint8Array(Math.ceil((n + 9) / 64) * 64);
  padded.set(bytes);
  padded[n] = 0x80;
  const view = new DataView(padded.buffer);
  view.setUint32(padded.length - 8, Math.floor(n / 0x20000000));
  view.setUint32(padded.length - 4, (n * 8) >>> 0);
  const h = Uint32Array.of(
    0x6a09e667,
    0xbb67ae85,
    0x3c6ef372,
    0xa54ff53a,
    0x510e527f,
    0x9b05688c,
    0x1f83d9ab,
    0x5be0cd19,
  );
  const w = new Uint32Array(64);
  const rotr = (x, r) => (x >>> r) | (x << (32 - r));
  for (let off = 0; off < padded.length; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(off + i * 4);
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      // a Uint32Array element stores the sum modulo 2^32
      w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    let [a, b, c, d, e, f, g, k] = h;
    for (let i = 0; i < 64; i++) {
      const t1 = (k + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + SHA256_K[i] + w[i]) >>> 0;
      const t2 = ((rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) >>> 0;
      [k, g, f, e, d, c, b, a] = [g, f, e, (d + t1) >>> 0, c, b, a, (t1 + t2) >>> 0];
    }
    [a, b, c, d, e, f, g, k].forEach((x, i) => (h[i] += x));
  }
  return Array.from(h, (x) => x.toString(16).padStart(8, "0")).join("");
}

// negative, zero or positive as version a is older than, the same as, or newer than b
function compareVersions(a, b) {
  const [x, y] = [a, b].map((v) => v.split(".").map(Number));
  return x[0] - y[0] || x[1] - y[1] || x[2] - y[2];
}

/** @returns {State} the saved state, or an empty one when none is saved or it is unreadable */
function readState() {
  if (files.fileExists(STATE_PATH)) {
    try {
      const s = JSON.parse(files.readString(STATE_PATH));
      if ((s.version === null || VERSION_PATTERN.test(s.version)) && Number.isFinite(s.checked)) return s;
    } catch {}
    console.warn("unreadable loader state");
  }
  return { version: null, checked: 0 };
}

/** @param {State} state */
function writeState(state) {
  if (!files.fileExists(DIR)) files.createDirectory(DIR, true);
  files.writeString(STATE_PATH, JSON.stringify(state));
}

const enginePath = (version) => files.joinPath(DIR, `engine-${version}.js`);

async function download(url) {
  const req = new Request(url);
  const data = await req.load();
  if (req.response.statusCode !== 200) throw new Error(`HTTP ${req.response.statusCode} for ${url}`);
  return data;
}

/** @returns {Promise<Manifest>} */
async function latestRelease() {
  const m = JSON.parse((await download(`${RELEASES_URL}/latest/download/${MANIFEST_ASSET}`)).toRawString());
  if (!VERSION_PATTERN.test(m?.version) || !Number.isInteger(m.api) || !SHA256_PATTERN.test(m.sha256)) {
    throw new Error("invalid release manifest");
  }
  return m;
}

/**
 * Evaluates the source of an engine, rejecting one without run() or of another interface.
 * @param {string} source
 * @param {string} version
 * @returns {{ api: number, run: () => Promise<void> }}
 */
function evaluateEngine(source, version) {
  const module = { exports: {} };
  new Function("module", "exports", source)(module, module.exports);
  const engine = module.exports;
  if (engine.api !== ENGINE_API || typeof engine.run !== "function") throw new Error(`engine ${version} is malformed`);
  return engine;
}

/** @param {string} version */
function loadEngine(version) {
  const path = enginePath(version);
  if (!files.fileExists(path)) throw new Error(`engine ${version} is missing`);
  return evaluateEngine(files.readString(path), version);
}

/**
 * Downloads the engine of a release and stores it once it matches the manifest checksum and evaluates.
 * @param {Manifest} manifest
 */
async function installEngine({ version, sha256 }) {
  const data = await download(`${RELEASES_URL}/download/v${version}/${ENGINE_ASSET}`);
  // checked first, since evaluating an engine runs its code
  if (sha256Hex(data.getBytes()) !== sha256) throw new Error(`engine ${version} does not match its checksum`);
  evaluateEngine(data.toRawString(), version);
  const path = enginePath(version);
  // written under a unique name and moved into place, so a concurrent run reads a whole engine or none
  const partial = `${path}.${Date.now()}-${Math.random().toString(36).slice(2)}`;
  files.write(partial, data);
  if (files.fileExists(path)) files.remove(path);
  try {
    files.move(partial, path);
  } catch (e) {
    // move fails onto an existing file, which a concurrent run installing this release has moved there
    if (!files.fileExists(path)) throw e;
    files.remove(partial);
  }
}

// whether a file is a partial engine of a run that may still be installing it
function isPartialInProgress(name) {
  const m = /^engine-\d+\.\d+\.\d+\.js\.(\d+)-[a-z0-9]*$/.exec(name);
  const age = m ? Date.now() - Number(m[1]) : NaN;
  return age >= 0 && age < PARTIAL_MAX_AGE_MS;
}

/** Deletes every file in the folder but the state, the installed engine, and partial engines in progress. */
function removeStaleFiles(version) {
  const keep = [STATE_PATH, enginePath(version)];
  for (const name of files.listContents(DIR)) {
    const path = files.joinPath(DIR, name);
    if (!keep.includes(path) && !isPartialInProgress(name)) files.remove(path);
  }
}

/**
 * Installs the engine of the latest release when it is newer than the installed one.
 * @param {State} state
 * @returns {Promise<State>} the state after the check
 */
async function checkRelease(state) {
  // saved first, so that concurrent runs skip the check
  const claimed = { ...state, checked: Date.now() };
  writeState(claimed);
  const manifest = await latestRelease();
  if (manifest.api !== ENGINE_API) {
    throw new Error(`release ${manifest.version} has engine interface ${manifest.api}, not ${ENGINE_API}`);
  }
  if (state.version && compareVersions(manifest.version, state.version) <= 0) return claimed;
  await installEngine(manifest);
  const next = { ...claimed, version: manifest.version };
  writeState(next);
  removeStaleFiles(next.version);
  return next;
}

/**
 * Settles as promise does, or with TIMED_OUT once ms have passed since the script started.
 * @template T
 * @param {Promise<T>} promise
 * @param {number} ms
 * @returns {Promise<T | typeof TIMED_OUT>}
 */
function untilDeadline(promise, ms) {
  let timer = null;
  const expired = new Promise((resolve) => {
    timer = Timer.schedule(Math.max(0, STARTED + ms - Date.now()), false, () => resolve(TIMED_OUT));
  });
  return Promise.race([promise, expired]).finally(() => timer.invalidate());
}

/** A widget showing text, which asks to run again a minute later. */
function textWidget(text) {
  const widget = new ListWidget();
  widget.refreshAfterDate = new Date(Date.now() + 60 * 1000);
  widget.addText(text);
  return widget;
}

/** @returns the installed engine, or null when none is installed or it does not load */
function loadInstalledEngine(version) {
  if (!version) return null;
  try {
    return loadEngine(version);
  } catch (e) {
    console.warn(e.message);
    return null;
  }
}

/** Runs the installed engine, installing one first when there is none, while checking for a newer release. */
async function main() {
  const state = readState();
  const engine = loadInstalledEngine(state.version);
  if (!engine) {
    const installed = await untilDeadline(checkRelease({ ...state, version: null }), INSTALL_DEADLINE_MS);
    if (installed === TIMED_OUT) {
      if (!config.runsInWidget) throw new Error("engine install timed out");
      Script.setWidget(textWidget(INSTALLING_TEXT));
      return;
    }
    await loadEngine(installed.version).run();
    return;
  }
  // a check recorded in the future, as after the clock is set back, is due
  const sinceCheck = Date.now() - state.checked;
  if (!config.runsInApp && sinceCheck >= 0 && sinceCheck < CHECK_INTERVAL_MS) {
    await engine.run();
    return;
  }
  // an engine installed while this one runs is used from the next run
  const checking = checkRelease(state).catch((e) => console.warn(`release check: ${e.message}`));
  try {
    await engine.run();
  } finally {
    const checked = await untilDeadline(checking, CHECK_DEADLINE_MS);
    if (checked === TIMED_OUT) console.warn("release check: timed out");
  }
}

// completes the script only once main() is done, since the release check may outlast the engine run
try {
  await main();
} catch (e) {
  if (!config.runsInWidget) throw e;
  Script.setWidget(textWidget(e.message));
} finally {
  Script.complete();
}
