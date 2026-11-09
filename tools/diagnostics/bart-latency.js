// Variables used by Scriptable.
// These must be at the very top of the file. Do not edit.
// icon-color: deep-gray; icon-glyph: stopwatch;

// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

// Times requests to BART from this phone as the widget makes them: live departures (ETD) and the
// GTFS-RT feed together, then the trip planner.
// 1. Run it in the app and pick the network of the phone. It times ROUNDS rounds, then shows a
//    summary, which is copied to the clipboard.
// 2. Turn Wi-Fi off, run it again and pick Cellular.
// Run with a Run Script input or ?stations= query of two station codes to time another pair than
// "woak embr". In a widget, each run times one round.

const API_KEY = "MW9S-E7SL-26DU-VV8V";
const ETD_URL = "https://api.bart.gov/api/etd.aspx";
const SCHEDULE_URL = "https://api.bart.gov/api/sched.aspx";
const GTFS_RT_URL = "https://api.bart.gov/gtfsrt/tripupdate.aspx";
// longer than any timeout the widget would use, so slow answers are measured rather than cut off
const REQUEST_TIMEOUT_S = 20;
// iOS ends an overlong widget run with an error, so a run stops waiting this long after it starts
const WIDGET_LIMIT_MS = 25 * 1000;
const ROUNDS = 20;
const MAX_ENTRIES = 5000;

const fm = FileManager.local();
const DIR = fm.joinPath(fm.documentsDirectory(), "bart-latency-spike");
const LOG = fm.joinPath(DIR, "log.json");
const started = Date.now();
const stations = args.widgetParameter || args.shortcutParameter || args.queryParameters?.stations || "woak embr";
const [from, to] = stations.trim().toLowerCase().split(/\s+/);

// times one request; never throws
async function timed(url) {
  const t0 = Date.now();
  const req = new Request(url);
  req.timeoutInterval = REQUEST_TIMEOUT_S;
  try {
    const data = await req.load();
    return { ms: Date.now() - t0, status: req.response.statusCode, bytes: data.getBytes().length };
  } catch (e) {
    return { ms: Date.now() - t0, error: String(e.message ?? e) };
  }
}

const legacy = (url, params) => `${url}?${[...params, `key=${API_KEY}`, "json=y"].join("&")}`;

/** Times one round; with limitAt, requests still pending at that epoch ms are recorded as unfinished. */
async function measure(net, limitAt = Infinity) {
  const result = { at: new Date().toISOString(), ctx: config.runsInWidget ? "widget" : "app", net, from, to };
  const round = (async () => {
    const r1 = Date.now();
    [result.etd, result.feed] = await Promise.all([
      timed(legacy(ETD_URL, ["cmd=etd", `orig=${from}`])),
      timed(GTFS_RT_URL),
    ]);
    result.round1 = Date.now() - r1;
    result.plan = await timed(legacy(SCHEDULE_URL, ["cmd=depart", `orig=${from}`, `dest=${to}`, "b=1", "a=4"]));
    result.total = Date.now() - r1;
  })();
  if (limitAt === Infinity) {
    await round;
  } else {
    let timer;
    const limit = new Promise((resolve) => (timer = Timer.schedule(Math.max(0, limitAt - Date.now()), false, resolve)));
    await Promise.race([round, limit]);
    timer.invalidate();
    for (const name of ["etd", "feed", "plan"])
      result[name] ??= { ms: Date.now() - started, error: "unfinished at the run limit" };
  }
  return result;
}

function readLog() {
  if (!fm.fileExists(LOG)) return [];
  try {
    return JSON.parse(fm.readString(LOG));
  } catch {
    return [];
  }
}

function save(entries) {
  if (!fm.fileExists(DIR)) fm.createDirectory(DIR, true);
  fm.writeString(LOG, JSON.stringify([...readLog(), ...entries].slice(-MAX_ENTRIES)));
}

const fmt = (ms) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`);

function quantile(sorted, q) {
  return sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];
}

function summarize(entries) {
  const lines = [];
  const rows = { etd: "ETD", feed: "GTFS-RT", plan: "trip planner", total: "round (as the widget waits)" };
  for (const [key, label] of Object.entries(rows)) {
    const values = entries.map((e) => e[key]).filter((v) => v !== undefined);
    const ok = values
      .map((v) => (typeof v === "number" ? v : v.error ? null : v.ms))
      .filter((v) => v !== null)
      .sort((a, b) => a - b);
    const failures = values.filter((v) => typeof v === "object" && v.error);
    let line = `${label}: `;
    if (ok.length > 0)
      line += `median ${fmt(quantile(ok, 0.5))}, p90 ${fmt(quantile(ok, 0.9))}, max ${fmt(ok[ok.length - 1])}`;
    else if (failures.length === 0) line += "none finished";
    if (failures.length > 0)
      line += `, ${failures.length} failed (${[...new Set(failures.map((f) => f.error))].join("; ")})`;
    const over = ok.filter((v) => v > 5000).length;
    if (over > 0) line += `, ${over} over 5 s`;
    lines.push(line);
  }
  return lines.join("\n");
}

function summary(log) {
  const groups = {};
  for (const e of log) (groups[`${e.ctx}, ${e.net ?? "network not recorded"}, ${e.from}-${e.to}`] ??= []).push(e);
  return Object.entries(groups)
    .map(
      ([name, entries]) =>
        `${name}: ${entries.length} rounds, ${entries[0].at} to ${entries[entries.length - 1].at}\n${summarize(entries)}`,
    )
    .join("\n\n");
}

if (config.runsInWidget) {
  const entry = await measure(null, started + WIDGET_LIMIT_MS);
  save([entry]);
  const widget = new ListWidget();
  widget.refreshAfterDate = new Date(Date.now() + 60 * 1000);
  const shown = (r) => (r.error ? "failed" : fmt(r.ms));
  for (const text of [
    `latency, ${readLog().length} rounds`,
    `ETD ${shown(entry.etd)}`,
    `feed ${shown(entry.feed)}`,
    `plan ${shown(entry.plan)}`,
  ]) {
    widget.addText(text).font = Font.systemFont(13);
  }
  widget.addDate(new Date()).applyTimeStyle();
  Script.setWidget(widget);
} else {
  const ask = new Alert();
  ask.title = "BART latency";
  ask.message = `Times ${ROUNDS} rounds from ${from.toUpperCase()} to ${to.toUpperCase()}, about 30 s. Which network is the phone on?`;
  ask.addAction("Wi-Fi");
  ask.addAction("Cellular");
  ask.addAction("Show summary only");
  ask.addDestructiveAction("Clear log");
  const choice = await ask.present();
  if (choice === 3) {
    fm.writeString(LOG, "[]");
  } else {
    if (choice === 0 || choice === 1) {
      const net = choice === 0 ? "Wi-Fi" : "cellular";
      const entries = [];
      for (let i = 0; i < ROUNDS; i++) entries.push(await measure(net));
      save(entries);
    }
    const text = summary(readLog());
    Pasteboard.copy(text);
    const alert = new Alert();
    alert.title = "BART latency (copied)";
    alert.message = text;
    alert.addAction("OK");
    await alert.present();
  }
}
Script.complete();
