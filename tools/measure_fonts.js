// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause
//
// Measures SF Pro on this Mac, then writes the character advances and line heights that the layout
// tests use to tests/fixtures/font-metrics.json and the heading and transfer widths into bart-commuter.js.
// Run from the repository root: osascript -l JavaScript tools/measure_fonts.js

ObjC.import("AppKit");

const SCRIPT_PATH = "bart-commuter.js";
const METRICS_PATH = "tests/fixtures/font-metrics.json";
const BLOCK_START = "// BEGIN TEXT WIDTHS";
const BLOCK_END = "// END TEXT WIDTHS";

// Scriptable font constructors as SF weights and designs
const CONSTRUCTORS = {
  systemFont: { weight: () => $.NSFontWeightRegular, rounded: false },
  mediumSystemFont: { weight: () => $.NSFontWeightMedium, rounded: false },
  semiboldSystemFont: { weight: () => $.NSFontWeightSemibold, rounded: false },
  boldSystemFont: { weight: () => $.NSFontWeightBold, rounded: false },
  boldRoundedSystemFont: { weight: () => $.NSFontWeightBold, rounded: true },
  italicSystemFont: { weight: () => $.NSFontWeightRegular, rounded: false, italic: true },
};

// every font the widget script uses
const FONTS = {
  systemFont: [9, 10, 11, 12, 13, 14, 18],
  mediumSystemFont: [12, 14],
  semiboldSystemFont: [10, 11, 14, 17],
  boldSystemFont: [11, 12, 13, 16, 18, 22],
  boldRoundedSystemFont: [9, 11, 12, 13, 14, 15, 16, 17, 21, 24, 26, 30, 33],
  italicSystemFont: [10, 11, 12, 14],
};

// heading fonts: a bold origin, then a regular destination after an arrow
const HEADINGS = {
  compact: { origin: 11, destination: 9 },
  large: { origin: 22, destination: 18 },
  comfortable: { origin: 12, destination: 10 },
  spacious: { origin: 13, destination: 11 },
};
const HEADING_ARROW = " \u2192 ";
// medium departure time font size
const DEPARTURE_SIZE = 26;
// transfer line font sizes on a small widget and in columns
const TRANSFER_SIZES = { small: 10, columns: 12, large: 14, list: 14 };

const CHARACTERS = [...Array.from({ length: 95 }, (_, i) => String.fromCharCode(32 + i)), "\u2192"];

const rounded = (value) => Math.round(value * 1000) / 1000;

function font(name, size) {
  const { weight, rounded: isRounded, italic } = CONSTRUCTORS[name];
  const system = $.NSFont.systemFontOfSizeWeight(size, weight());
  if (italic)
    return $.NSFont.fontWithDescriptorSize(
      system.fontDescriptor.fontDescriptorWithSymbolicTraits($.NSFontDescriptorTraitItalic),
      size,
    );
  if (!isRounded) return system;
  const descriptor = system.fontDescriptor.fontDescriptorWithDesign($.NSFontDescriptorSystemDesignRounded);
  if (descriptor.isNil()) throw new Error(`no rounded design for ${name}`);
  return $.NSFont.fontWithDescriptorSize(descriptor, size);
}

function measureFonts() {
  const metrics = {};
  for (const [name, sizes] of Object.entries(FONTS)) {
    for (const size of sizes) {
      const f = font(name, size);
      const attrs = $.NSDictionary.dictionaryWithObjectForKey(f, $.NSFontAttributeName);
      const advances = {};
      for (const c of CHARACTERS)
        advances[c] = rounded($.NSString.alloc.initWithUTF8String(c).sizeWithAttributes(attrs).width);
      metrics[`${name}(${size})`] = { lineHeight: rounded(f.ascender - f.descender + f.leading), advances };
    }
  }
  return metrics;
}

const path = (relative) => `${$.NSFileManager.defaultManager.currentDirectoryPath.js}/${relative}`;

function readText(relative) {
  const text = $.NSString.stringWithContentsOfFileEncodingError(path(relative), $.NSUTF8StringEncoding, null);
  if (text.isNil()) throw new Error(`cannot read ${relative}`);
  return text.js;
}

function writeText(relative, text) {
  if (!$(text).writeToFileAtomicallyEncodingError(path(relative), true, $.NSUTF8StringEncoding, null)) {
    throw new Error(`cannot write ${relative}`);
  }
}

// widths are sums of character advances, as the layout tests estimate them, rounded up to 0.1pt
function width(advances, text) {
  let sum = 0;
  for (const c of text) {
    if (!(c in advances)) throw new Error(`unmeasured character: ${JSON.stringify(c)}`);
    sum += advances[c];
  }
  return Math.ceil(sum * 10) / 10;
}

// size of the line saying how long ago the data was loaded
const UPDATED_SIZE = 9;

function widthsBlock(source, metrics) {
  const fonts = Object.values(HEADINGS).map(({ origin, destination }) => ({
    origin: metrics[`boldSystemFont(${origin})`].advances,
    destination: metrics[`systemFont(${destination})`].advances,
  }));
  const columns = (text) => fonts.flatMap((f) => [width(f.origin, text), width(f.destination, text)]).join(", ");
  const headings = Object.entries(HEADINGS).map(
    ([key, { origin, destination }], i) =>
      `${key}: { origin: ${origin}, destination: ${destination}, arrow: ${width(fonts[i].destination, HEADING_ARROW)}, column: ${i * 2} }`,
  );
  const ages = Array.from({ length: 59 }, (_, i) => `+${i + 1} minutes`);
  const ageWidth = Math.max(...ages.map((t) => width(metrics[`systemFont(${UPDATED_SIZE})`].advances, t)));
  const stations = [...source.matchAll(/^ {2}"?(\w+)"?: \{ name: "([^"]+)"/gm)].map((m) => ({
    code: m[1],
    name: m[2],
  }));
  if (stations.length === 0) throw new Error("no stations found");
  const rows = stations.map(({ code, name }) => `  "${code}": { name: [${columns(name)}], code: [${columns(code)}] },`);
  return [
    `${BLOCK_START}: generated by tools/measure_fonts.js from SF Pro; do not edit`,
    `const HEADINGS = { ${headings.join(", ")} };`,
    "// heading widths in points of each station's name and code, as origin and as destination, per heading kind",
    "const HEADING_WIDTHS = {",
    ...rows,
    "};",
    `const DEPARTURE_SIZE = ${DEPARTURE_SIZE};`,
    `const TRANSFER_SIZES = { small: ${TRANSFER_SIZES.small}, columns: ${TRANSFER_SIZES.columns}, large: ${TRANSFER_SIZES.large}, list: ${TRANSFER_SIZES.list} };`,
    `const UPDATED_SIZE = ${UPDATED_SIZE};`,
    "// widest age under an hour in points at the updated line's size",
    `const AGE_WIDTH = ${ageWidth};`,
    BLOCK_END,
  ].join("\n");
}

function run() {
  const metrics = measureFonts();
  writeText(METRICS_PATH, `${JSON.stringify(metrics)}\n`);
  const source = readText(SCRIPT_PATH);
  const start = source.indexOf(BLOCK_START);
  const end = source.indexOf(BLOCK_END);
  if (start < 0 || end < start) throw new Error(`${SCRIPT_PATH} has no text widths block`);
  writeText(SCRIPT_PATH, source.slice(0, start) + widthsBlock(source, metrics) + source.slice(end + BLOCK_END.length));
  return "";
}
