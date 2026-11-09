---
name: operating-recorder
description: Checks, stops, and restarts the long-running BART corpus recorder and the periodic replay watcher, and maintains the recordings. Use when asked whether a recording is live, to restart the recorder, to apply a change in the widget's request settings to the recording, to read coverage gaps, or to run bart-corpus status, audit, prune, pack, verify, or watch.
---

# Operating the recorder

Both processes run detached on this Mac from `.agent-context/venv`, which `make deps` does not rebuild, so tool upgrades cannot disturb them.

Stop or restart a process only when the user's current request authorizes it for that process, because a gap in a recording cannot be recovered.

## Check it is live

Run `pgrep -fl "bart.corpus record"`, then read `tail corpus/tracker.log` and `bart-corpus status corpus`. A running process alone does not show that data is arriving.

The recorder idles between service days after closing a day, so hours without polls overnight are normal. Compare any gap against the service day, and say whether it was the host, the network, or the idle period.

## Restart

Send SIGTERM, never SIGKILL, so requests in flight finish, and wait for the process to exit. A restart resumes the same day's file:

```sh
nohup .agent-context/venv/bin/bart-corpus record corpus >> corpus/tracker.log 2>&1 &
```

Confirm a new poll appears in the log. Report the restart time when a restart picks up new request settings, since replays of that day must start after it.

## Replay watcher

Find it with `pgrep -fl "bart_corpus watch"`, and save its arguments with `ps -o command= -p PID`. Send SIGTERM only while it has no children (`pgrep -P PID` prints nothing), so no replay is cut off. Restart it with the same arguments, detached, with `.agent-context/venv/bin` and `.agent-context/bin` on PATH for Python and `bun`, logging to `periodic.log` in its `--out` directory.

Give `--script` a copy that nothing edits, so results stay comparable across days. Read `periodic.log` for failures before relying on `history.jsonl`.

## Maintain recordings

`bart-corpus audit corpus` finds recordings that no longer match the recorder's requests. `prune` lists the ones that failed and deletes them only with `--delete`, so show the list and get approval first. `pack` archives finished days, and `verify` checks the archive reads back whole.
