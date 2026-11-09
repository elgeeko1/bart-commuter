---
name: checking-on-device
description: Prepares Scriptable scripts for the user to paste and run on an iPhone, with handoff steps and a log to send back. Use when a behavior can only be confirmed on a phone, when measuring device timing or Scriptable API behavior, when writing a probe or diagnostic script, or when testing an unreleased engine or loader in Scriptable.
---

# Checking on a device

The user runs these by hand, so an unclear handoff or a broken script costs a round trip.

## Write the script

- Put it in `.agent-context/`, as one self-contained file. Scriptable has no imports, Node APIs, or test harness, so a helper from `tests/` throws a ReferenceError on the phone.
- Start it with Scriptable's three header lines, as in `tools/diagnostics/bart-latency.js`, and use a distinct icon.
- Check every Scriptable API against [its documentation](https://docs.scriptable.app) before using it.
- Keep what it stores out of the loader's `bart-commuter` folder, so a test cannot shadow a real install. An unreleased engine needs a single-file build with the engine inlined, because the loader otherwise fetches it from GitHub.

## Log for pasting back

Print one line per check, with the result, its name, the observed value, and milliseconds. In the app, show the log in an `Alert` and copy it with `Pasteboard.copy`.

A widget run has no visible console, and iOS ends it after about 30 seconds with "Received timeout when running script". Have it write results to a file within that limit, and show them on the next app run.

## Hand off

State all of this without being asked:

1. How to get it on the phone, and the script name to use. Choose a name other than BART Commuter, so it cannot overwrite the installed script.
1. Each run in order: app, widget, Siri, or Shortcuts. For a widget, name the size, the Script setting, and the Parameter.
1. How long to wait. Running in the app does not update a widget, because iOS decides when it refreshes.
1. What to paste back.

When the results arrive, compare them with what the harness assumes. Record any new device fact in `doc/DEVELOPMENT.md` with its date. Delete scripts that are no longer needed.
