# AGENTS.md

This file provides guidance to coding agents when working with code in this repository.

An iPhone widget for BART departures that runs in [Scriptable](https://docs.scriptable.app). This project is Scriptable-only: within a Scriptable limitation, state the limit and the best Scriptable workaround, and do not propose a native app or Swift widget. The Python and shell tools are for development and never ship.

Anything a rider sees follows @doc/PRODUCT_GUIDELINES.md.

[doc/DEVELOPMENT.md](doc/DEVELOPMENT.md) specifies each behavior and the measurements behind it. Read the relevant section before changing source selection, transfers, deadlines, or the updater.

## Commands

Run from the repository root. Install Python 3.12 or later, then run `make config`, which installs Bun, the Python and JavaScript tools, and the pre-commit hooks. JavaScript tests run under `bun test`, whose JavaScriptCore engine is the one Scriptable uses.

CI runs these:

```sh
make lint
make test
```

Run one JavaScript test file, or tests matching a name:

```sh
bun test tests/js/engine/transfers.test.js
bun test -t "Grand Meet"
```

Run one Python test:

```sh
python3 -m pytest tests/python/corpus/test_replay.py -k NAME
```

Check the script against live BART data, by hand only:

```sh
make test-live
```

Render every widget family in each service state (macOS):

```sh
make gallery
```

`bart-corpus` records and works with the corpus; `bart-corpus COMMAND --help` documents each command.

| Command | Purpose |
| -- | -- |
| `record` | Record each service day to its own compressed file, idle between days, until interrupted |
| `status` | Report coverage per day and source |
| `audit` | Check recordings against the current recorder's queries |
| `prune` | List recordings that failed the audit, or delete them with `--delete` |
| `pack`, `unpack`, `verify` | Archive leftover recordings of finished days, restore an archived day, and check the archive |
| `capture` | Download live responses as test fixtures |
| `export` | Write fixtures for a moment in the corpus |
| `replay`, `compare`, `meets` | Score the widget on recorded service, and compare scripts |
| `watch` | Replay and compare scripts periodically |

Replay a day still being recorded from a copy made with SQLite's backup API, so unflushed writes are included. Pass an earlier script with `--script` to compare versions.

```sh
python3 -c 'import sqlite3, sys; sqlite3.connect(sys.argv[1]).backup(sqlite3.connect(sys.argv[2]))' corpus/DAY.sqlite .agent-context/replay/DAY.sqlite
bart-corpus replay .agent-context/replay/DAY.sqlite --report .agent-context/replay/DAY.json
```

## Architecture

- `bart-commuter-loader.js` is what users paste into Scriptable. It installs `bart-commuter.js`, the engine, from GitHub releases and keeps it current.
- The engine is a dependency-free module: evaluating it runs nothing. It exports `run()` and `api`, and only the loader calls `Script.complete()`.
- One run parses one or two station codes into boards, loads them in parallel, then renders for the widget family or speaks under Siri. All requests of a run race one deadline (`RUN_DEADLINE_MS`) because iOS kills a widget run after about 30 seconds.
- A board's live trains are ETD merged with GTFS-RT; the timetable is the last resort. A commute board also merges transfer trips from the trip planner, checked against live trains.
- A train that might be the next one but cannot be confirmed fails the source for that board, so the widget shows no data rather than silently omit a train.
- `tests/js/support/harness.js` stubs the Scriptable APIs and simulates time. `tools/render/layout.js` and `tools/replay/widget.js` run the engine through this same harness, so a harness change affects tests, renders, and replays.
- `bart-corpus record` (`src/bart_corpus/recorder.py`) records each service day to one SQLite file, compresses it into `corpus/archive/` once the last train has left the boards, and reads its request parameters (`REFRESH_MS`, `RUN_DEADLINE_MS`, trip counts, `API_KEY`) from the engine at startup. Restart it for a change to those to take effect.
- `bart-corpus replay` runs the engine over each recorded refresh and scores the trips shown against the trains that ran, taken from later GTFS-RT.

## Generated content

Do not edit these by hand. Regenerate them when their source changes.

| Content | Regenerate with | When |
| -- | -- | -- |
| `NETWORK` block in the engine | `python3 tools/generate_network.py google_transit.zip` | BART opens a station or changes a route |
| `TEXT WIDTHS` block in the engine and `tests/fixtures/font-metrics.json` | `osascript -l JavaScript tools/measure_fonts.js` (macOS) | A font or the network tables change |
| Script block in `README.md` | `python3 tools/readme_script.py` | The loader changes |
| `doc/widgets-*.png` | `make render` (macOS) | The layout changes |

## Pitfalls

- Renders and `tests/js/layout.test.js` approximate WidgetKit from SF Pro metrics on a 390x844 iPhone. A layout is confirmed only on a phone.
- After recapturing `tests/fixtures`, update `NOW` in `tests/js/support/engine.js` and the expected values in `tests/js/engine/`, which derive from the captured data.
- Change `ENGINE_API` in the engine and the loader together, and only when the contract of `run()` changes.
- A script passed to `bart-corpus replay --script` must be a module exporting `run()` and defining the internals `tools/replay/widget.js` calls. If it requested fewer trips before now than the corpus recorded, add `--drop-first-trips N`.
- Report replay results in rider terms, from the rider view that leads the report: trips a rider would have taken, connections made and missed. Define any API parameter or internal term when citing it.
- A recorder may already be running on `corpus/`. Check with `pgrep -fl "bart.corpus record"` before starting one.
- BART times are `America/Los_Angeles`, and a service day changes at 3 AM wall-clock time.
- Pushing a `v` tag publishes a release that every installed widget downloads, and nothing authenticates a release. Tag only on explicit request.

## Conventions

- Edit the main checkout in place; use a worktree only when asked.
- Put generated working output such as galleries, reports, and scratch files in `.agent-context/`, which is gitignored along with `corpus/` apart from `corpus/archive/`.
- Start new source files with the two SPDX lines used throughout the repository.
