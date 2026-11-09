---
name: replaying-corpus
description: Replays recorded BART service through the widget script, compares script versions, and reports the results in rider terms. Use when evaluating the widget on recorded data, running bart-corpus replay, compare, or meets, testing whether a routing or transfer change helps riders, or asked about missed connections, fastest trips, or timed-transfer holds.
---

# Replaying the corpus

Run from the repository root with `PATH="$PWD/.venv/bin:$PATH"`, since replay starts `bun` from PATH.

## Choose the data

1. Run `bart-corpus status corpus` and `bart-corpus audit corpus`. Replay only recordings whose requests match the script under test, because a recording that asked the trip planner for fewer trips cannot show what the script would have seen. When the recorder restarted with new request parameters during a day, pass `--since` with the restart time.
1. Work on a copy in `.agent-context/replay/`. Snapshot a day still being recorded with the command in `AGENTS.md`. Restore an archived day with `bart-corpus unpack corpus DAY .agent-context/replay`.

## Replay and compare

Replay every script on the same copy, and keep the report JSON:

```sh
bart-corpus replay .agent-context/replay/DAY.sqlite --script .agent-context/replay/old.js --report .agent-context/replay/DAY-old.json
```

Get an earlier script with `git show REV:bart-commuter.js`. Then compare, summing several days under one label with commas:

```sh
bart-corpus compare old=.agent-context/replay/DAY-old.json new=.agent-context/replay/DAY-new.json
```

Rerun both sides after any change to the scoring. Reports and `history.jsonl` entries from older scoring are not comparable, so a difference between them may be the scoring rather than the widget.

For timed-transfer holds, run `bart-corpus meets` on every recorded day, unpacking archived days first.

## Report

Use this structure, since the rider outcome is what decides a change and the fault and cause are only useful after it:

```markdown
**Data:** service days, refreshes, trains known until TIME, scripts compared.

| Rider experience | Failed | Our fault | BART data wrong | Schedule exception |
| -- | -- | -- | -- | -- |
| Rider missed a listed connection | 3 of 120 (2.5%) | 1 of 120 (0.8%) | 0 of 120 (0%) | 2 of 120 (1.7%) |

**Our fault:** each why, with its count and percent, one example board and time, and the proposed fix.

**Errors and warnings:** anything the replay itself reported.
```

- Give every count as absolute and percent, including counts in prose.
- Write each metric so a rider could understand it without knowing the code. Reject wording such as "arrived over a minute after one leaving no earlier"; say what happened to the rider.
- Define every API parameter and internal term where it is first cited, for example "the trip planner's `b`, how many trips before now it returns". A bare symbol tells the reader nothing.
- Count a failure as ours only when the data the widget had at that refresh showed the better outcome. A delay after the last refresh is unpredictable.
- Judge connections as a passenger would. Recorded times are about 30 seconds coarse, so a tight connection cannot be told from one that held; do not draw conclusions from differences that small.
- Say when a result rests on one day or a handful of trips.

Recommend changes and stop. Implement them, or replay again, only when asked.
