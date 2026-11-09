// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

// Runs the widget script on recorded refreshes and prints, for each refresh and commute, a JSON line
// with the Siri answer, the warnings and errors, and the trips of each board as a Large widget lists them,
// along with every trip with a transfer in the trip plan and the live trains matched to its legs.
// The argument is a file of JSON lines, one per refresh: tick, now, commutes as "ORIG DEST", ETD
// responses by station, trip plans by pair, and the GTFS-RT feed in hex. Run with Bun from the repository
// root. An optional second argument is the script to run in place of bart-commuter.js.

import { ENGINE_PATH, readText, runScript } from "../../tests/js/support/harness.js";

const CAPTURE_MARK = "replay capture";

// runs after run() of the script, in its scope
const CAPTURE = `
{
  const loadedBoards = await (async () => {
    console.warn(${JSON.stringify(CAPTURE_MARK)});
    return loadBoards(boardsFor(parseStations(args.shortcutParameter), TRAINS_PER_LIST));
  })();
  const src = sources();
  globalThis.replayBoards = [];
  // a leg in the record: its live train, the not-running status, or null when unknown
  const recordedLeg = (run) => (run.status === NOT_RUNNING ? run.status : run.train);
  for (const loaded of loadedBoards) {
    const { board } = loaded;
    let plan;
    try {
      plan = await loadTripPlan(board.from, board.to);
    } catch (e) {
      plan = { error: e.message };
    }
    const transfers = [];
    for (const legs of plan.transfers ?? []) {
      const [firstRun, connectingRun] = await Promise.all(legs.map((leg) => runningLeg(leg, src)));
      const train = transferTrip(legs, firstRun, connectingRun);
      transfers.push({ legs, firstTrain: recordedLeg(firstRun), connectingTrain: recordedLeg(connectingRun), train, grandMeet: isGrandMeet(legs[1]) });
    }
    replayBoards.push({ board: board.id, trains: loaded.trips, last: loaded.last, planned: plan.departures ?? null, transfers, planError: plan.error ?? null });
  }
}
`;

const [input, script = ENGINE_PATH] = process.argv.slice(2);

function hexBytes(hex) {
  const bytes = new Array(hex.length / 2);
  for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(hex.substr(i * 2, 2), 16);
  return bytes;
}

// dates as epoch ms
function replacer(key, value) {
  return this[key] instanceof Date ? this[key].getTime() : value;
}

async function replay(line) {
  const refresh = JSON.parse(line);
  const fixtures = {
    etd: refresh.etd,
    schedule: {},
    depart: refresh.depart,
    feed: refresh.feed === null ? null : hexBytes(refresh.feed),
  };
  for (const commute of refresh.commutes) {
    globalThis.replayBoards = null;
    const r = await runScript({
      script,
      now: refresh.now,
      fixtures,
      mode: "shortcuts",
      params: { shortcut: commute },
      epilogue: CAPTURE,
    });
    const mark = r.warnings.indexOf(CAPTURE_MARK);
    const record = {
      tick: refresh.tick,
      now: refresh.now,
      commute,
      output: r.output,
      warnings: mark < 0 ? r.warnings : r.warnings.slice(0, mark),
      harnessErrors: r.harnessErrors,
      thrown: r.thrown ? String(r.thrown) : null,
      boards: globalThis.replayBoards,
    };
    console.log(JSON.stringify(record, replacer));
  }
}

try {
  for (const line of readText(input).split("\n")) if (line) await replay(line);
} catch (e) {
  console.log(JSON.stringify({ fatal: `${e}\n${e.stack}` }));
}
