# Developer notes

`bart-commuter.js` is a single Scriptable script with no dependencies. For each run it parses one or two station codes into two boards, loads departures for both in parallel, then renders a widget for the requesting widget family, speaks a summary under Siri, or presents a medium widget in the app. Scriptable APIs used are documented at [docs.scriptable.app](https://docs.scriptable.app). Rider-facing design follows the [product guidelines](PRODUCT_GUIDELINES.md).

## Boards

- Two stations, Home and Work, make a commute: board Home to Work, then board Work to Home. A board holds direct trains, those that stop at the origin and later at the destination, and trips with one transfer from the trip planner, as described in [Transfers](#transfers).
- Each source classifies a train as serving a commute board, not serving it, or unsure. A board shows confirmed trains only, and a source fails for that board when an unsure train departs no later than the last train it would show, so a train that might be the next one to the destination is never left out silently. The source order then moves on to the next source.
- One station makes a single board of every train leaving it, on every line and in both directions.
- Widgets name only the stations the user entered, never a train's own destination.

## Network tables

The script needs BART's stations, their platforms, and each route's station order. These are generated into the block between `BEGIN NETWORK` and `END NETWORK` from BART's [static GTFS](https://www.bart.gov/schedules/developers/gtfs):

```sh
curl -fsSL -o google_transit.zip https://www.bart.gov/dev/schedules/google_transit.zip
python3 tools/generate_network.py google_transit.zip
```

- `STATIONS` maps each station code to a display name and its platform stop ids. Display names are maintained in the generator, which fails if the feed's stations differ from them.
- `ROUTES` lists each rail route with its line, its direction, its full station order, taken from the route's longest trip pattern, and `skips`, the stations some of its trips pass without stopping. The generator fails if any trip leaves that order. In the 2026-08-10 schedule, the only skip is a few late-night Yellow trips that run from San Bruno straight to Millbrae, bypassing SFO.
- Route ids in the static GTFS equal the Legacy API route numbers, and route names such as `Yellow-S` give the line and direction.

Regenerate when BART opens a station or changes a route.

## Data sources

The script reads BART's Legacy API (ETD and two schedule commands) and GTFS-RT. ETD and GTFS-RT together give a board's live trains, as described in [Source selection](#source-selection). All are served from `api.bart.gov`, so the fallbacks cover failures of one endpoint, not an outage of the whole host. Requests are shared, so two boards at the same station make one request per source.

### ETD (live, with a key)

[BART Legacy API](https://api.bart.gov/docs/etd/etd.aspx), `etd.aspx?cmd=etd&orig=STATION&key=KEY&json=y`. One request per origin station.

- Requires an API key; an invalid key returns HTTP 400 with `root.message.error.text` set to `Invalid key`.
- Each estimate has a line `color`, a `direction` of `North` or `South`, and the destination `abbreviation` of its group.
- A commute board keeps an estimate when the route of that line and direction visits the origin, then the destination, then the train's own destination, so trains turning back early are excluded. The estimate is unsure when some trips of the route skip the destination and the train does not end there. A one-station board keeps every estimate.
- `minutes` is a whole-minute string, or `Leaving` for a train at the platform. Estimates with `cancelflag` set to `1` are left off the board, and keep the feed's prediction of that train off it too.
- Departure times are computed as fetch time plus `minutes`, so they carry up to a minute of rounding.
- Each destination lists at most 3 estimates (observed across all stations on 2026-11-05), so a later train to that destination may be missing. ETD also omits trains far ahead: on 2026-11-06, most lists of fewer than 3 estimates ended within 40 minutes. A full destination list covers trains through its last estimate, and a shorter one through its last estimate or the GTFS-RT horizon, whichever is later; without the feed a shorter list is taken as complete. A board's ETD trains end where the first of its destination lists stops covering, which keeps an unreported train from being skipped over. Large widgets then show fewer than 7 trains.
- ETD also skips trains near at hand: on 2026-11-06, while listing a later train of the same line, it omitted a train that ran 145 of 5,084 times, most often from 6 to 11 PM, and GTFS-RT listed all but 11 of them.

### GTFS-RT (live, every board)

[BART GTFS Realtime](https://www.bart.gov/schedules/developers/gtfs-realtime) trip updates, `gtfsrt/tripupdate.aspx`. One request per run, shared by all boards. No key required.

- Protocol buffers per [gtfs-realtime.proto](https://github.com/google/transit/blob/master/gtfs-realtime/proto/gtfs-realtime.proto). The script includes a minimal decoder because Scriptable has no protobuf support.
- Stop time events carry absolute POSIX times, so departure times are exact to the second.
- Stops are platform ids such as `M30-2`, mapped to stations through `STATIONS`. An unknown platform rejects the feed.
- Feeds whose header timestamp is more than 5 minutes old are rejected.
- Trips that have not started appear ahead of their first departure; on 2026-11-05 the feed held every timetabled trip starting within the next 43 minutes. Every departure within 43 minutes (`GTFS_RT_HORIZON_MIN`) is therefore listed, but a later one may be missing, so the feed alone covers only trains within that horizon. A board with no live train falls through to the schedule, keeping only timetable trains after the horizon, since the feed shows that earlier ones are not running. When the schedule is a trip plan that ends before the horizon, later trains are unknown and the board has no data.
- Trips whose `schedule_relationship` is CANCELED or DELETED are dropped, as are stops marked SKIPPED. Any other value outside the specification's scheduled, added and unscheduled ones rejects the feed.

Each trip lists its remaining departures in order, which is enough to match direct trains: a commute board keeps a trip that departs the origin and lists the destination later. BART's feed leaves out the rest:

- `TripDescriptor` carries only `trip_id` and `schedule_relationship`, without `route_id` or `direction_id`. Joining `trip_id` against the static GTFS is not reliable: on 2026-11-05 the published static feed was missing 10 of 57 live trip ids.
- A trip omits its final stop, so a Daly City train ends at Balboa Park in the feed.

The script infers these from the candidate routes whose station order contains the trip's stations and continues past the last one. Where all candidates agree, they give the trip's line. The possible omitted final stops are the next station on each candidate, plus the station after it when the next one is skipped by some trips.

- A commute board is confirmed when the trip lists the destination after the origin, or when the destination is the only possible final stop. It is unsure when the destination is one of several possible final stops.
- Where lines share track the line is often ambiguous, for example a Blue trip west of Bay Fair also fits Green. That departure gets a gray marker; it does not affect matching.
- A trip that ends at the last station of every route it fits means the feed has started listing final stops, and the feed is rejected, since the inference would then be wrong.

### Schedule (last resort)

Two Legacy API schedule commands, both with the same key as ETD, so they do not help when the key is rejected, only when ETD itself fails and GTFS-RT fails or lists no train for the board. Each item's `@line` is `ROUTE n`, a Legacy API route number, which gives the line and direction.

- Commute boards use the [trip planner](https://api.bart.gov/docs/sched/depart.aspx), `sched.aspx?cmd=depart&orig=HOME&dest=WORK&b=1&a=4`, one request per board: `b` is the number of trips timetabled to leave before now (`TRIPS_BEFORE`), whose first train may still be at the platform running late, and `a` the number after (`TRIPS_PLANNED`). BART's planner decides which trains reach the destination. Each leg carries its `ROUTE n` line, origin and destination, and departure and arrival times with their calendar dates (`@origTimeDate`, `@destTimeDate`), so times after midnight need no service-day inference. Trips of one leg are direct trains, trips of two legs have a transfer, and trips of three legs are dropped, so a board can show fewer than three trains. The planner returns fewer trips when `a` exceeds 4, so a Large widget shows at most 4 timetable trips per commute board.
- One-station boards use the [station schedule](https://api.bart.gov/docs/sched/stnsched.aspx), `sched.aspx?cmd=stnsched&orig=STATION&date=MM/DD/YYYY`, the station's full timetable for the service day in progress. The date is explicit because after midnight the API defaults to the next service day.
- A board marks its final listed train as the last only when every remaining train is listed and the schedule confirms it: the station schedule is the whole day, and a trip plan counts when its trips reach the next service day. A live train is the last when the schedule has no departure, direct or with a transfer, a minute or more after it, since a prediction and the timetable minute of the same train differ by less than a minute. Without a key, or when the schedule fails, the board shows no label.
- Station schedule times are "h:mm AM" readings of BART's wall clock on the service day's date; times before 3 AM fall on the next calendar date, at the end of the service day that began the previous morning. A time repeated when daylight time ends is its first occurrence, as in GTFS, and a time skipped when daylight time starts reads as standard time. The service day itself changes at 3 AM on the wall clock.
- Siri says "scheduled" for a scheduled departure; widgets show it like a live one.

### Lines without live data

Neither ETD nor GTFS-RT covers the Grey line (Oakland Airport to Coliseum): the feed has no trips at its platforms, and ETD returns no estimates for it while trains run. Boards the Grey line serves skip both live sources and come from the schedule. `UNTRACKED_LINES` lists such lines.

### Known data limits

BART's data describes some trains differently from how they run, and the widget follows the data:

- After about 9 PM, ETD labels Yellow line trains that end at SFO with the destination Millbrae (for example trip 1965215 on weekdays), and riders change to a shuttle there. Boards to Millbrae show them as direct; GTFS-RT and the trip planner do not.
- GTFS, ETD and the trip planner run Antioch trains through Pittsburg/Bay Point as one trip, while riders change to the eBART train across the platform there. Boards across Pittsburg/Bay Point show them as direct.
- Unscheduled trains appear in ETD with the color `WHITE`. They follow no route, so commute boards drop them; one-station boards show them with a white dot and the letter U. Any other color outside the network's lines fails ETD for that station.

### Source selection

Each board uses the first source in this order that returns data:

| Source | Used when | User-visible signal |
| -- | -- | -- |
| Live trains: ETD merged with GTFS-RT | either source alone serves | gray markers where a GTFS-RT train's line is ambiguous |
| Schedule | no live source has a train for the board | Siri says "scheduled" |

The live trains are ETD's, plus each GTFS-RT train that no ETD train of its line runs. An ETD estimate and the feed's departure for one train are taken as the same train when at most 5 minutes apart (`SAME_TRAIN_MIN`): on 2026-11-06 and 2026-11-07 over 90% were within a minute, and almost none 4 to 8 minutes apart. Each ETD train stands for at most one, the nearest. With both sources the list reaches the later of where ETD's coverage ends and the GTFS-RT horizon. A timetable train within the horizon that the live trains lack is not running only when the feed loaded, since ETD alone can skip a train.

This order applies to a board's direct trains. A commute board also loads its trip plan for [transfers](#transfers), whatever source served its direct trains, and a commute whose stations share no line skips the direct sources.

A board with no usable source, including one where every source is unsure of a train, has no data rather than an error: Home Screen widgets show the SF Symbol `icloud.slash` where the departure time would be, the rectangular Lock Screen widget "No data", and the circular and inline ones `--`. Siri says so for that board, and the other board renders normally. A board with no trains shows the SF Symbol `nosign` in place of its departure time instead. Source failures go to `console.warn`. Only invalid station lists and unexpected exceptions render an error widget or throw.

## Transfers

A commute board merges its direct trains with the trip planner's trips of two legs. BART publishes no live data on connections, so each trip with a transfer is checked against live trains, and boards without a key, or whose trip plan fails, show direct trains alone. A commute whose stations share no line has no direct trains, and without the trip plan it has no data.

### Matching legs to live trains

Each leg is matched to the live trains at its origin that stop later at the leg's destination, as for a board between those stations. Requests are shared, so a transfer station costs one ETD request per run. A leg's train is the first train of its line leaving from 1 minute before to 10 minutes after its timetable departure (`LEG_MATCH_MIN`). Then:

- A leg is unknown when its line is untracked, no live source has data, the first train in the window has an unknown line (GTFS-RT on shared track), or the window ends after the live list may miss trains: the last listed train of a full ETD destination list, or the GTFS-RT horizon.
- A leg whose window the live list covers without a train is not running, and its trip is dropped. The list covers the window only when the window ends within the GTFS-RT horizon of a feed that loaded, and the list has a train leaving after it. ETD alone never rules a train out, since it skips trains: on 2026-11-06 one connection dropped as not running from ETD alone held. A leg from the first station of its line is never judged not running, since ETD there can omit a train waiting to leave: on 2026-11-06, Dublin/Pleasanton's list often lacked a train leaving within 10 minutes.
- A trip timetabled to leave before now, which the planner returns for `TRIPS_BEFORE`, is kept only when a live train yet to depart runs its first leg.

### Connection rules

The first train's delay is its live departure minus its timetable departure, and it reaches the transfer station at its timetable arrival plus that delay. The connecting train's live departure counts the same way. Delays round to whole minutes, the resolution of both ETD and the trip planner, since a live departure also carries the seconds of the run.

- **The Grand Meet** (`GRAND_MEET`): the last trains of the night meet at MacArthur and hold for each other, so a transfer there whose connecting train is timetabled at 12:51 or 12:52 AM always holds. Those are the connecting departures the trip planner gave for the 2026-11-06 timetable.
- **Every other transfer** holds when the connecting train's departure, live or else timetabled, is no earlier than the first train reaches the station (`TRANSFER_SLACK_MIN`, 0 minutes; ETD and the trip planner give whole minutes, so seconds would not be meaningful). The transfer stations are the trip planner's.
- BART's timed transfers (MacArthur southbound, 19th St Oakland northbound, and Bay Fair between the Dublin/Pleasanton and Berryessa lines) get no hold of their own. Replaying 183 timed connections from 2026-11-06 and 2026-11-07, a connection being made when its train left no more than 30 seconds before the first train arrived: holding them whenever the first train was at most 2 minutes late missed 10 and hid 19 that a rider made, and keeping every one missed 23, while comparing live times as above missed 10 and hid 10. A 1-minute slack missed 2 but hid 61.
- With neither leg known, the trip keeps the planner's connection.

A trip that holds is shown at its first train's departure, live or timetabled. Siri calls it scheduled unless live trains run both legs. Its arrival is the connecting train's departure, or the first train's arrival when that is later, as at a held connection, plus the connecting leg's timetable run time.

### Departure times

- A live train that starts its run at the board's origin shows at the earlier of its live and timetable departures, since BART predicts departures there minutes late while trains leave close to the timetable. Across the first stations of every line from 2026-11-07 13:13 to 2026-11-08 08:50, in the 10 minutes before each train left, live predictions showed a departure over a minute after the train left in 124 of 2,551 views, at Millbrae, Dublin/Pleasanton and Daly City, and the earlier of the two in none, while views more than 2 minutes before the train left rose from 317 to 672. The train runs the latest planned departure of its line within the leg matching window, so it never moves before a later departure it may run. Only the shown departure moves: connections and arrivals use the live time. A single-station board has no planned departures and shows live times.
- BART's GTFS-RT dates a departure just after midnight on the previous day when its arrival is before midnight, as for the southbound Orange train at Ashby at about 12:00 AM on 2026-11-06 and 2026-11-07, for about an hour beforehand. The script and the replay move a departure more than 12 hours before its arrival forward a day.

### Fastest trips

A direct train's arrival is its departure plus the shortest direct run in the trip plan; trains between two stations share track, so one run time serves every line. A trip plan that has trips with a transfer but no direct trip gives no run time, so the board logs it and shows its direct trains alone.

- Trips are sorted by departure, and a trip is dropped when another leaving no earlier arrives sooner, or as soon while leaving later or without a transfer. Arrival times therefore increase down the list, and a direct train wins a tie.
- Direct trains end at the earlier of the direct source's last complete time and the trip plan's last trip, since a trip the plan omits could beat a train after that. A trip from the plan stays when its first train runs late past the plan's last departure, up to the direct source's last complete time. A commute whose trip plan has no transfers keeps its direct list unchanged.

### Display

- Small: the line after the next departure time starts under the time and has the transfer symbol (SF Symbol `arrow.triangle.branch`), the connecting line's dot, and the transfer station; a direct trip has no transfer line. A 1pt rule divides the two trips. Small has no updated line. Text grows into the rows a widget does not need: the departure time, later trains, and heading are largest with no transfer lines and smaller with one or two, and a one-station widget has the largest departure time (`SMALL_TWO_TRIPS`, `SMALL_ONE_STATION`). Both trips share one set of sizes. A larger heading applies only when every trip shows its full station names unshrunk.
- Medium: a "BART Commuter" header and a rule top the widget, with the updated line at the right of the header, then one column per trip, with the same transfer line after the next departure time. Rows stay level across columns: a direct trip leaves a blank line when the other trip has a transfer, and the later trains or last-train words share a fixed-height row (`COLUMN_LATER_HEIGHT`).
- Large: trips stack one above the other, divided by the same rule. The same header and rule top the widget, with the updated line at the right of the header. Each trip's route spans one line, the arrow and destination smaller and gray as on Small. Below it, the left column has the next departure, and the same transfer line, starting under the time. The right column starts at the center, a little below the top of the departure, with the later trains, indented. Type runs about 30 percent larger than on Medium (`STACKED_SIZES`). Later trains have a gap between them, a later train's transfer comes directly below it, and the list stops at `LIST_ROWS` rows.
- Later trains and their transfer lines are italic, with line dots at reduced strength (`LATER_DOT_STRENGTH`), mixed with the widget background.
- Last train (SF Symbol `flag.checkered`): a next departure that is the last has the flag and the words on a line below it, the flag under the departure time. A later train that is the last has the flag after its time. On the rectangular Lock Screen widget the flag and the words follow a last next departure, and the circular widget shows the flag alone on a line below. The flag and words are gray and regular weight, since a color would read as a line color, at the size of the later trains.
- Circular Lock Screen: the first trip before 1 PM BART time, then the second (`RETURN_TRIP_MIN`).
- Later trains set am and pm in lower case, with no space after the time except on Large.
- A transfer station shows its name when the line, with its text shrunk to 80%, fits the measured width, and its code otherwise.
- Lock Screen widgets show the next trip unchanged. Siri names the next trip's transfer station.

## Refresh rates

BART documents no update interval or rate limit for either API. Polling both every 10 seconds for two minutes on 2026-11-05 showed:

| Source | Observed behavior |
| -- | -- |
| ETD | Regenerated about every 15 seconds; minute values change once per minute |
| GTFS-RT | Generated on each request; predictions change every 10 to 30 seconds |

The widget, not the APIs, is the limiting factor. The script sets `refreshAfterDate` one minute ahead, but WidgetKit treats it as the earliest reload and enforces a daily budget, typically 40 to 70 reloads for a frequently viewed widget; see [Keeping a widget up to date](https://developer.apple.com/documentation/widgetkit/keeping-a-widget-up-to-date). Every widget renders static clock times, which stay correct between reloads; a train that has left stays on screen until the next one. The age in the "updated ... ago" line on Home Screen and rectangular Lock Screen widgets is a `WidgetDate` with `applyOffsetStyle()`, which iOS keeps current without a reload. It fills the width it is offered, so it sits in a box as wide as the widest age under an hour (`AGE_WIDTH`), and longer ages shrink to fit.

Runs in the app, from Shortcuts, or through Siri always fetch fresh data. A widget sets no `url`, because one overrides the widget's configured When Interacting behavior. Run Script opens Scriptable and shows a fresh preview; Scriptable offers no way to reload the widget from a tap.

Home Screen colors use `Color.dynamic`, because `Device.isUsingDarkAppearance()` is not supported in widgets. Lock Screen colors are static, since the system tints that surface itself.

### Request time limits

Time limits follow from the widget's budget, not from measured latency. iOS shows a widget only once its run ends, and ends a run still going about 30 s after it starts with an error in place of the widget. Of that budget, a first install may take 10 s and rendering with a safety margin 5 s, which leaves the engine 15 s: every request of a run, however Scriptable launched it, races a `Timer` set 15 s after `run()` starts (`RUN_DEADLINE_MS`), and a request still pending then fails with `timed out`. A request has no limit of its own. The sources a board falls back to are requested alongside each other, not after a failure, so an earlier limit would only discard an answer that could still arrive in time; Scriptable's `timeoutInterval` would in any case bound only how long a request waits for data, and a request cannot be cancelled. A commute requests its trip plan together with its live trains, since it always uses the plan, which leaves the transfer stations' ETD as the only request that waits on another; when the deadline leaves it no time, a trip's legs keep their timetable times.

Measured latency checks the design rather than setting it. On the corpus of 2026-11-06 and 2026-11-07, recorded from a Mac, a widget run's requests took a median 1.2 s, 2.0 s at the 99th percentile and at most 6.2 s. On an iPhone on 2026-11-07, 20 rounds over Wi-Fi took a median 0.6 s and at most 0.9 s, and 20 over cellular a median 1.3 s and at most 9.0 s, when ETD and GTFS-RT stalled together for 8.3 s: an answer a per-request limit of 5 s would have discarded, and that the deadline keeps.

## API key

ETD and the trip planner reject requests without a key. `API_KEY` is the shared public key published on the [BART developer page](https://www.bart.gov/schedules/developers/api), so the widget needs no setup. BART may rotate that key; the shipped key then fails like any rejected key until a release with the new public key reaches each widget through [updates](#updates). Until then, widgets show times from GTFS-RT, with fewer line colors and no trips that need a transfer.

- The key is sent only to the Legacy API endpoints, as the `key` query parameter.
- Fixture tests make no network requests. Where a test needs GTFS-RT alone, it simulates a rejected key with the harness's `etdFailure` and `scheduleFailure` options.
- Live checks call ETD and the trip planner with the shipped key, so a rotated public key fails them. They include Rockridge to Fremont, which share no line, with ETD wherever its trips' legs start.

## Tests

`make check` runs everything CI runs: `make lint`, described in [Linting](#linting), and `make test`, the JavaScript and Python tests. Install Python 3.12 or later, then install Bun, the tools, and the pre-commit hooks with `make config`. The project needs no Node.js: Bun runs the JavaScript tests and the npm-packaged linters.

### JavaScript

JavaScript tests use `bun test`, whose engine is JavaScriptCore, the one Scriptable runs. `tests/js/support/harness.js` stubs the Scriptable APIs, serves BART responses from files, evaluates the script as a module and calls its `run()`, then records the rendered widget tree, spoken text, Shortcuts output, warnings, requests, and timers. Time is simulated: each request answers after the test's `latency` for its URL, 0 by default or never for `Infinity`, `Date` and `Timer` follow a clock that advances to each answer and timer in turn once the script is idle, and `elapsedMs` gives the clock when the run ended.

| Path | Covers |
| -- | -- |
| `tests/js/engine/` | The engine against captured and synthetic data: boards, source selection and deadlines, rendering, launch parameters, the timetable, GTFS-RT, a disrupted morning step by step, and transfers |
| `tests/js/loader.test.js` | The loader and its compact copy in `README.md`, on an in-memory file system and network, including a seeded random sequence of runs |
| `tests/js/layout.test.js` | Every single station and ordered station pair, in each Home Screen family and the rectangular Lock Screen widget, from a synthetic timetable with the widest clock times, against SF Pro metrics |
| `tests/js/render.test.js` | The layout of the [widget images](#widget-images) |

For a longer random sequence in the loader tests, set `BART_LOADER_RUNS`:

```sh
BART_LOADER_RUNS=100000 bun test tests/js/loader.test.js
```

The script cannot measure text, so `tests/fixtures/font-metrics.json` gives each station's heading width in the `TEXT WIDTHS` block. Small and rectangular Lock Screen headings use full names when the line, shrunk to 80%, fits that iPhone's widget; otherwise the destination's code, then both codes. After changing a font, or when the network tables change, remeasure on macOS from the repository root, which rewrites both files:

```sh
osascript -l JavaScript tools/measure_fonts.js
```

Recapture the fixtures with the command below, then update `NOW` in `tests/js/support/engine.js` and the expected values in `tests/js/engine/`, which derive from the captured data:

```sh
bart-corpus capture tests/fixtures
```

Tests assert what a rider sees and which source failed, not the wording of logs. A warning's board and source are part of the contract; its reason text is not.

### Python

The Python tools and their tests need Python 3.12 or later. Tests live in `tests/python` and make requests only to a local server.

```sh
make test-py
```

### Live checks

The live checks capture current BART data with the script's `API_KEY`, render it, check that ETD and the trip planner accept the key, and verify that ETD and GTFS-RT agree within two minutes. They run only by hand, since they depend on BART's service at that moment:

```sh
make test-live
```

### Linting

[pre-commit](https://pre-commit.com) runs every formatter and linter, on the files of each commit and on all files with `make lint`, which is what CI runs. [`.pre-commit-config.yaml`](../.pre-commit-config.yaml) lists the tools and their versions.

A hook that formats a file fails the run, so commit again with its changes. After a change to the loader, regenerate the copy in `README.md`:

```sh
python3 tools/readme_script.py
```

The workspace's [recommended extensions](../.vscode/extensions.json) run the same tools at the same versions: ruff, mypy, ShellCheck, and actionlint from `.venv`, Prettier and ESLint from `node_modules`, and mdformat, shfmt, and mbake through their pre-commit hooks on save. Pyright and markdownlint come from their extensions, whose versions the hooks match, so update those hooks when the extensions update.

### CI

[CI](../.github/workflows/ci.yml) runs the tests and checks on Ubuntu on every push and pull request, and uploads test coverage to Codecov. `make coverage-js` and `make coverage-py` write the same LCOV reports to `coverage/`. [Release](../.github/workflows/release.yml) runs on `v` tags, as described in [Releasing](#releasing).

## Live data corpus

`bart-corpus record` records BART's live data over days or weeks, for testing hypotheses about the sources and replaying real service in the test harness. It uses only the Python standard library. Start it with a corpus directory:

```sh
bart-corpus record corpus
```

It runs until interrupted by Ctrl-C or SIGTERM, then finishes the requests in flight. A restart resumes the same day's file. Run it on an always-on machine; polls missed while the host sleeps or is offline show as gaps in the coverage report.

Each service day gets its own file. The recorder starts 90 minutes before the timetable's first departure, about 30 minutes ahead of the first estimates, and no earlier than the 3 AM service day change. It closes the day once the timetable's last arrival has passed and the board for every station has not changed for 15 minutes; BART's board freezes on its last estimate overnight instead of emptying. It then [archives](#archive) the day, removes the uncompressed file, and idles until the next day's start. Without a timetable it records until the 3 AM change.

A refresh makes the requests the widget makes when it loads, at the widget's interval, `REFRESH_MS`, with the longest the widget waits for a request, its run deadline `RUN_DEADLINE_MS`, as each request's timeout, so a replay sees what the widget would have seen. These and the trip planner's trip counts are read from `bart-commuter.js` at startup, so a change to them takes effect when the tracker restarts.

| Source | Request | Cadence |
| -- | -- | -- |
| `etd` | ETD for every station, `orig=ALL`, and for each station of `--pairs` and `--stations` on its own, as the widget requests it; then, on its own, for each station where a leg of the refresh's trip plans starts, as the widget requests it to check a [transfer](#transfers) | Every refresh |
| `tripupdate` | GTFS-RT trip updates | Every refresh |
| `depart` | Trip planner for each `--pairs` commute, for the widget's `TRIPS_BEFORE` and `TRIPS_PLANNED` trips. The default commutes cover direct trains, timed transfers, direct trains against timed transfers, untimed transfers at Bay Fair and MacArthur, and the Oakland Airport line | Every refresh |
| `alerts` | GTFS-RT service alerts, `gtfsrt/alerts.aspx` | 60 seconds |
| `bsa`, `count` | Service advisories and train count, `bsa.aspx?cmd=bsa` and `cmd=count` | 60 seconds |
| `elev` | Elevator status, `bsa.aspx?cmd=elev` | 5 minutes |
| `stns`, `stnsched` | Station list, then each station's schedule for the service day | Once per service day |
| `gtfs-static` | [Static GTFS](https://www.bart.gov/schedules/developers/gtfs) zip | Once per service day |

- Requests start on wall-clock multiples of their cadence, so the responses of one refresh describe the same moment. `--interval` overrides the refresh interval.
- The widget reloads no more often than `REFRESH_MS`, and iOS reloads it less often still, as described in [Refresh rates](#refresh-rates); a refresh is the most frequent load the widget can make.
- ETD for every station is generated separately from single-station ETD and does not match it: on 2026-11-06 it lagged by up to 16 seconds, and about 1 estimate in 25 differed by a minute even at the same generation time. It covers the network; replays of a station need its own response.
- Station schedules do not change during a service day, so the daily request gives what the widget would load at any time that day.
- The daily requests run when a service day's recording starts, and are retried every 10 minutes until all succeed.
- Legacy API requests use the script's `API_KEY`, or `--key`; with an empty key only GTFS-RT and static GTFS are recorded. The key is not stored.
- ETD for every station omits Oakland Airport, as described in [Lines without live data](#lines-without-live-data).

### Storage

The corpus holds one SQLite file for the service day being recorded, `YYYY-MM-DD.sqlite`, and earlier days in its [archive](#archive). Table `poll` has a row for every request, failed ones included: `source`, `subject` (station, station pair, or `ALL`), `tick` (the scheduled epoch ms shared by requests of one run), `requested_at` (epoch ms), `elapsed_ms`, the HTTP `status` or a transport `error`, and the `sha256` of the response. Table `body` holds each distinct response once, byte for byte as served, compressed with zlib. Weekday afternoon service with the default commutes and interval takes up to about 2 MB per hour.

### Auditing

A recording is complete when it runs from the recorder's start for the day to at least the last arrival of its own recorded timetable, has no gap over 5 minutes between refreshes, and made every query of the current recorder with the trip planner's current `TRIPS_BEFORE` and `TRIPS_PLANNED`, which each trip plan response echoes as `before` and `after`.

```sh
bart-corpus audit corpus
bart-corpus prune corpus --delete
```

Both cover archived days as well as the file being recorded. `prune` without `--delete` only lists what it would remove. A service day still in progress is never pruned.

### Archive

The recorder compresses each day as it closes it into `corpus/archive/DAY.sqlite.xz`, tracked in git, after checking that the compressed copy holds every poll and body. The file is a SQLite database with raw bodies, and xz compresses what consecutive polls share, which the per-body zlib of a recording cannot. A full Saturday recording of 50 MB compresses to 3.5 MB. A file over 50 MB, where GitHub starts warning, is refused and the recording kept.

`pack` archives and removes any recording of a finished day the recorder has not, such as after a crash. `verify` checks that archived days read back whole.

```sh
bart-corpus pack corpus
bart-corpus verify corpus
```

The replay and export commands read uncompressed files, so restore a day first:

```sh
bart-corpus unpack corpus DAY /tmp/restored
```

### Coverage

Report polls, successes, distinct responses, the longest gap between successful ticks, and the first and last poll, per day and source:

```sh
bart-corpus status corpus
```

### Fixtures from the corpus

Export the last refresh at or before a time, BART time unless the time has an offset, into a new directory:

```sh
bart-corpus export corpus 2026-11-06T17:45 /tmp/bart-snapshot
```

The directory uses the layout `bart-corpus capture` writes, for every station: `tripupdate.pb`, `etd-STATION.json`, `sched-STATION.json`, and `depart-ORIG-DEST.json`, along with `alerts.pb`, `bsa.json`, `count.json`, `elev.json`, `stns.json` and `google_transit.zip`. A station's ETD is its own response; stations without one get their part of the all-station response, and a station whose own request failed gets no ETD file. Refresh responses come only from that refresh; other sources contribute their latest response at or before it. `snapshot.json` gives `now`, the ETD request time from which the script dates estimates, to pass to `runScript`, lists in `etd_split` the stations whose ETD comes from the all-station response, and lists the responses that are missing or failed at that refresh, apart from a transfer station's own ETD, which a refresh requests only when its trip plans change trains there. Load the directory with `readFixtures` in `tests/js/support/harness.js`, passing the stations to load.

### Replay

`bart-corpus replay` runs the script on every refresh of a corpus file, for each commute whose trip plans were recorded both ways, and checks what each board lists against the trains that ran. To replay a day still being recorded, copy its file first with SQLite's backup API, which includes writes not yet checkpointed:

```sh
python3 -c 'import sqlite3, sys; sqlite3.connect(sys.argv[1]).backup(sqlite3.connect(sys.argv[2]))' \
  corpus/DAY.sqlite /tmp/DAY.sqlite
bart-corpus replay /tmp/DAY.sqlite --report /tmp/replay.json
```

`tools/replay/widget.js` runs the script in the test harness with a step after its `run()` that records each board's trips as a Large widget lists them, and each trip with a transfer in the trip plan with the live trains matched to its legs and whether the widget kept it. The trains that ran come from GTFS-RT: a stop leaves a trip's updates once the train leaves it, so its last prediction is its actual time, to within a refresh. Where a line starts, the feed can stop updating a waiting train's departure, so a stop's time is always before the first refresh that lists its trip without it: on 2026-11-06, Dublin/Pleasanton trains several minutes late had passed West Dublin while their last prediction still had them waiting. The timetable in the file's static GTFS gives each trip's line and stations. The report covers:

- the rider experience, each trip and connection counted once as of the last refresh before its first train left, each count with its share of the trips, connections or views it could have happened to: the rider was not shown the fastest trip; took a listed trip, but one leaving at the same time or later got there over 1 minute sooner; missed a listed connection; was not shown a connection they could have made; or arrived at the time shown and the train had already left, over a minute earlier. Board views are counted when the board said no trains while a trip left within the hour, or had no data
- each of those failures with its fault and why, in the same counts and shares. Ours when the data the widget had at that refresh, GTFS-RT where it lists a train and the timetable otherwise, showed the better outcome, with the widget rule that chose otherwise as the why. A schedule exception when that data did not show it, or when the trip exists only because trains ran off the timetable, which is never ours: the widget offers only what the timetable makes. BART data wrong when every source failed or gave a later departure than the train's
- errors, harness errors and warnings, by message
- each shown trip's arrival for a rider who takes it, changing to the first train that serves the destination, against the best arrival of any trip leaving no earlier, with at most one transfer. A rider makes a connection whose train left no more than 30 seconds before the first train's recorded arrival, since the recorded times are no finer
- trips that no later trip beat and that a list omits, before its last trip
- each planned transfer's connection as it ran, matched to the planned trains as the widget matches live trains: held, tight (the connecting train left up to 30 seconds before the first arrived), or missed; those dropped although a rider made the connection, with the rule that dropped them, and those kept although a rider missed it.
- arrival estimates against actual arrivals

Only trips that arrive before the file's last refresh are checked, and the Oakland Airport line, which GTFS-RT omits, is not.

To compare an earlier version of the script on the same data, pass it with `--script`; it must define the internals `tools/replay/widget.js` calls. When it requested fewer trips timetabled before now from the trip planner than the corpus recorded, the planner's `b` parameter, `--drop-first-trips` drops that many trips from the start of each recorded trip plan, since each trip `b` adds comes first. The planner's own idea of now lags the request by up to several minutes, so the earlier trips cannot be told apart by departure time:

```sh
bart-corpus replay /tmp/DAY.sqlite --script /tmp/old-bart-commuter.js --drop-first-trips 1
```

Compare the rider experience of replay reports side by side, summing several under one label:

```sh
bart-corpus compare old=/tmp/old.json new=/tmp/new.json
```

`bart-corpus watch` repeats this on a schedule, for each day whose final comparison is not yet written, copying the day from its recording or archive, replaying each `--script` and appending each comparison to `history.jsonl` in `--out`.

### Timed transfer waits

`bart-corpus meets` measures how long connecting trains wait at BART's timed transfers, listed in the script's `TIMED_TRANSFERS`, to check whether they hold for a late first train. A meet is a train arriving at one of those stations and a train of another line leaving in the same direction within 4 minutes after it in the timetable, where changing trains goes somewhere the first train does not, without doubling back. Every meet that ran, by the trains' actual times as the replay reads them, is grouped by station and by the wait it needed: how far past the connecting train's timetable departure the first train arrived. Each group gives the meets whose connection held, left at most 30 seconds before the first train's recorded arrival (tight, which the recorded times cannot tell from held), or left earlier (missed), and how late the connecting train left. Pass every corpus file to measure; a meet in more than one file, such as a snapshot and its day's file, counts once per service day:

```sh
bart-corpus meets corpus/*.sqlite --report /tmp/meets.json
```

## Updates

Users install `bart-commuter-loader.js`, which runs `bart-commuter.js` as its engine and keeps it current from GitHub releases.

- The engine is a module: evaluating it defines everything and runs nothing. It exports `run()`, which renders the boards for however Scriptable launched the script, and `api`, the version of `run()`'s interface (`ENGINE_API`). The loader alone calls `Script.complete()`.
- Each release carries the engine unchanged, the loader, and `manifest.json`, which `tools/build_manifest.py` writes from the engine: its version, `api`, and SHA-256. `tests/python/test_build_manifest.py` covers it. `SHA256SUMS` lists the SHA-256 of those three files for checking them by hand with `sha256sum -c`; the loader does not read it.
- The loader keeps the installed engine and its state in a `bart-commuter` folder in `FileManager.local()`'s documents directory. While the installed engine runs, it reads `releases/latest/download/manifest.json`, at most every 6 hours (`CHECK_INTERVAL_MS`) and on every run in the app; a check recorded in the future, as after the clock is set back, is due. It installs a release only when its `api` equals the loader's `ENGINE_API` and its version is newer, downloads the engine from that release's tag, checks its bytes against the manifest's SHA-256, and only then evaluates it once before saving it as installed, rejecting an engine that fails the checksum, fails to evaluate, or lacks `run()` or the matching `api`. A newly installed engine runs from the next run on.
- Widgets may run at once. An install writes the engine under a unique partial name and moves it into place, and keeps the copy another run installing the same release moved there first. Cleanup after an install keeps partial engines less than a minute old (`PARTIAL_MAX_AGE_MS`), since their runs may still be installing.
- With no usable engine, as on a first run, the loader installs one before running it; a failure then shows in the widget, or throws in the app, Siri and Shortcuts. Other failures are logged and retried after the interval.
- iOS shows a widget only when its run ends, and ends a run still going about 30 s after it starts with "Received timeout when running script" in place of the widget (measured on an iPhone on 2026-11-07). Every run therefore bounds its waits by time since it started, with a `Timer` race, since `Request.timeoutInterval` limits only how long a request waits for data, and `Request` cannot be cancelled. A first install has until 10 s (`INSTALL_DEADLINE_MS`), leaving the rest to the engine; past it a widget shows "Installing BART" and asks to run again a minute later, when the install starts over, and other runs throw `engine install timed out`. Once the engine has run, the loader waits for a release check until 20 s (`CHECK_DEADLINE_MS`), then ends and logs `release check: timed out`; that check is retried after the interval.
- Releases only move forward: a bad release is fixed by a newer one.
- `ENGINE_API` changes, in the engine and the loader together, only when `run()`'s contract does. A loader with the older value keeps its installed engine and logs the mismatch, so users must paste the new loader.
- The checksum catches a corrupted download or an engine asset replaced on its own. It does not authenticate a release, because the manifest comes from the same release: anyone who can publish or edit a release can change every installed widget. Scriptable has no crypto API, so the loader computes SHA-256 in JavaScript, only when it installs an engine.
- GitHub serves `releases/latest/download/` through redirects, outside the REST API's rate limit, and the loader makes no `api.github.com` requests.

## README script

Users install by copying the loader from a code block in `README.md`, because GitHub Markdown cannot include a file. `tools/readme_script.py` writes that block from `bart-commuter-loader.js`, dropping comments and repeated blank lines but keeping Scriptable's header, the license lines, and the code as written. Regenerate it after changing the loader:

```sh
python3 tools/readme_script.py
```

CI fails when the block is out of date (`python3 tools/readme_script.py --check`).

## Widget images

`README.md` shows every widget family on a weekday commute in both appearances, `doc/widgets-dark.png` and `doc/widgets-light.png`. Regenerate them on macOS from the repository root when the layout changes:

```sh
tools/render_widgets.sh
```

To review a layout change, render every family, Home Screen and Lock Screen, in each service state and both appearances (a weekday commute, last trains with transfers, a disruption with a train 75 minutes away and only timetable times the other way, timetable times with the last train at night, and every source down) into a directory of your choice:

```sh
tools/render_widgets.sh --gallery /tmp/bart-gallery
```

`tools/render/layout.js` runs the script in the test harness between West Oakland and Embarcadero, BART's busiest station pair in its October 2026 average weekday ridership, with synthetic data for each state, and lays out each widget at 6.1-inch iPhone sizes. `tools/render/draw.js` draws the result with the Mac's SF Pro and SF Pro Rounded at 3x. The layout approximates WidgetKit's: flexible spacers and stacks that hold them share leftover space, text shrinks to its minimum scale factor to fit, and the Lock Screen is drawn in a single white tint, and the inline widget's font is approximated. Check a layout change on a phone before relying on a render.

## Releasing

The release version is the `// Version:` line in the header of `bart-commuter.js`. To release, set that line, regenerate the README script, commit, and push a matching `v`-prefixed tag:

```sh
git tag v0.0.1
git push origin v0.0.1
```

The [Release workflow](../.github/workflows/release.yml) reruns CI for the tag, checks that the tag is on `main` and matches the script version, and builds the assets without write access. A job in the `release` environment then publishes a GitHub release with the engine `bart-commuter.js`, its `manifest.json`, the loader, and `SHA256SUMS` attached, and notes generated from the commits since the previous release. The release stays a draft until every asset is uploaded, since loaders read the latest release's manifest. A failed test, a stale README script, or a tag not on `main` or not matching the version publishes nothing.
