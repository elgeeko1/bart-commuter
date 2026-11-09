// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

import js from "@eslint/js";

function readonly(names) {
  return Object.fromEntries(names.map((name) => [name, "readonly"]));
}

const SCRIPTABLE = [
  "Alert",
  "args",
  "Color",
  "config",
  "DateFormatter",
  "FileManager",
  "Font",
  "ListWidget",
  "module",
  "Pasteboard",
  "Request",
  "Script",
  "SFSymbol",
  "Size",
  "Speech",
  "Timer",
];

export default [
  { ignores: [".agent-context/**", ".claude/**", "node_modules/**", "tests/fixtures/**"] },
  js.configs.recommended,
  {
    rules: { "no-empty": ["error", { allowEmptyCatch: true }] },
    languageOptions: { ecmaVersion: "latest", sourceType: "module", globals: readonly(["console"]) },
  },
  {
    files: ["bart-commuter.js", "bart-commuter-loader.js", "tools/diagnostics/*.js"],
    languageOptions: { globals: readonly(SCRIPTABLE) },
  },
  {
    files: ["tests/**/*.js", "tools/render/*.js", "tools/replay/*.js"],
    languageOptions: { globals: readonly(["Bun", "process", "setImmediate", "setTimeout"]) },
  },
  {
    // run by osascript as JavaScript for Automation
    files: ["tools/measure_fonts.js", "tools/render/draw.js"],
    languageOptions: { globals: readonly(["$", "ObjC"]) },
    // osascript calls run
    rules: { "no-unused-vars": ["error", { varsIgnorePattern: "^run$" }] },
  },
];
