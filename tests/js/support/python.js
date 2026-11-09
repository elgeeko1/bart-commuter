// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

// stdout of a repository Python tool, named by a path relative to the repository root
export function python(...args) {
  const done = Bun.spawnSync(["python3", ...args]);
  if (done.exitCode !== 0) throw new Error(`python3 ${args.join(" ")}: ${done.stderr.toString()}`);
  return done.stdout.toString();
}
