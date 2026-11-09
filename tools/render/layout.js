// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause

// Runs the widget script between West Oakland and Embarcadero, the busiest BART station pair, and between
// Rockridge and Fremont, which share no line, lays out its widgets, and prints them as JSON images:
// sections of widgets on Home Screen or Lock Screen wallpaper, each widget a list of positioned
// rectangles, symbols and text. The argument "readme" selects every family on a weekday morning commute,
// and "gallery" every family in each service state, each in both appearances. Run with Bun from the
// repository root.

import {
  METRICS_PATH,
  directPlan,
  etdResponse,
  feedOfTrips,
  naturalSize,
  planResponse,
  readText,
  runScript,
  shownText,
  texts,
} from "../../tests/js/support/harness.js";

const STATIONS = "woak embr";
// midnight starting Tuesday, November 3, 2026, in standard time
const DAY = Date.UTC(2026, 10, 3, 8);
const MIN = 60000;
const clockTime = (h, m) => DAY + (h * 60 + m) * MIN;
// direct trips through the Transbay Tube take 7 minutes
const tube = (route, from, to, times) => directPlan(route, from, to, times, 7);

// widget sizes in points for a 390x844 screen, from the Apple Human Interface Guidelines; an inline
// widget is as wide as its line
const SIZES = {
  small: { width: 158, height: 158 },
  medium: { width: 338, height: 158 },
  large: { width: 338, height: 354 },
  accessoryRectangular: { width: 160, height: 72 },
  accessoryCircular: { width: 72, height: 72 },
  accessoryInline: { width: null, height: 20 },
};
// unstyled text and images take the Lock Screen font size, approximated for inline widgets
const INLINE_FONT = "systemFont(14)";
const INLINE_SYMBOL_SIZE = 14;

const westbound = (lists) =>
  etdResponse(
    "WOAK",
    "West Oakland",
    lists.map(([color, dest, name, minutes]) => [color, dest, name, "South", minutes]),
  );
const eastbound = (lists) =>
  etdResponse(
    "EMBR",
    "Embarcadero",
    lists.map(([color, dest, name, minutes]) => [color, dest, name, "North", minutes]),
  );
const NO_SOURCE = { etd: {}, schedule: {}, depart: {}, feed: [] };
// a GTFS-RT feed at now that lists no train beyond the ETD lists
const emptyFeed = (now) => feedOfTrips([], { now });
// a trip changing trains at a station after wait minutes, its legs as [route, from, to, minutes]
const viaStation = (departs, [route1, from, at, run1], wait, [route2, to, run2]) => [
  [route1, from, at, departs, departs + run1 * MIN],
  [route2, at, to, departs + (run1 + wait) * MIN, departs + (run1 + wait + run2) * MIN],
];

// service states; warnings marks a state whose sources fail by design
const STATES = [
  {
    name: "commute",
    title: "Weekday morning, 8:04 AM",
    now: clockTime(8, 4),
    // every line runs every 20 minutes through the Transbay Tube
    fixtures: {
      ...NO_SOURCE,
      feed: emptyFeed(clockTime(8, 4)),
      etd: {
        WOAK: westbound([
          ["YELLOW", "SFIA", "SF Airport", [5, 25]],
          ["RED", "MLBR", "Millbrae", [10, 30]],
          ["GREEN", "DALY", "Daly City", [15, 35]],
          ["BLUE", "DALY", "Daly City", [19, 39]],
        ]),
        EMBR: eastbound([
          ["RED", "RICH", "Richmond", [3, 23]],
          ["BLUE", "DUBL", "Dublin/Pleasanton", [8, 28]],
          ["YELLOW", "ANTC", "Antioch", [12, 32]],
          ["GREEN", "BERY", "Berryessa", [17, 37]],
        ]),
      },
      depart: {
        "WOAK-EMBR": tube(
          1,
          "WOAK",
          "EMBR",
          [9, 14, 19, 23].map((m) => clockTime(8, m)),
        ),
        "EMBR-WOAK": tube(
          8,
          "EMBR",
          "WOAK",
          [7, 12, 16, 21].map((m) => clockTime(8, m)),
        ),
      },
    },
  },
  {
    name: "transfer",
    title: "Rockridge to Fremont, changing trains at MacArthur and 19th St",
    stations: "rock frmt",
    now: clockTime(8, 4),
    fixtures: {
      ...NO_SOURCE,
      feed: emptyFeed(clockTime(8, 4)),
      etd: {
        ROCK: etdResponse("ROCK", "Rockridge", [["YELLOW", "MLBR", "Millbrae", "South", [5, 25, 45]]]),
        MCAR: etdResponse("MCAR", "MacArthur", [["ORANGE", "BERY", "Berryessa", "South", [11, 31, 51]]]),
        FRMT: etdResponse("FRMT", "Fremont", [["ORANGE", "RICH", "Richmond", "North", [8, 28, 48]]]),
        "19TH": etdResponse("19TH", "19th St Oakland", [["YELLOW", "ANTC", "Antioch", "North", [46, 66, 86]]]),
      },
      depart: {
        "ROCK-FRMT": planResponse(
          [9, 29, 49, 69].map((m) => viaStation(clockTime(8, m), [1, "ROCK", "MCAR", 3], 3, [4, "FRMT", 42])),
        ),
        "FRMT-ROCK": planResponse(
          [12, 32, 52, 72].map((m) => viaStation(clockTime(8, m), [3, "FRMT", "19TH", 37], 1, [2, "ROCK", 6])),
        ),
      },
    },
  },
  {
    name: "disruption",
    title: "Disruption: the next train in 75 minutes, only timetable times the other way",
    now: clockTime(8, 30),
    fixtures: {
      ...NO_SOURCE,
      feed: emptyFeed(clockTime(8, 30)),
      etd: { WOAK: westbound([["BLUE", "DALY", "Daly City", [75]]]), EMBR: eastbound([]) },
      // the timetable has later trains, so the sole predicted train is not the last
      depart: {
        "WOAK-EMBR": tube(
          1,
          "WOAK",
          "EMBR",
          [38, 58, 78, 98].map((m) => clockTime(8, m)),
        ),
        "EMBR-WOAK": tube(
          2,
          "EMBR",
          "WOAK",
          [36, 56, 76, 96].map((m) => clockTime(8, m)),
        ),
      },
    },
  },
  {
    name: "timetable",
    title: "Live times down at night: timetable times and the last train",
    now: clockTime(23, 50),
    etdFailure: "not-json",
    warnings: true,
    fixtures: {
      ...NO_SOURCE,
      depart: {
        "WOAK-EMBR": tube(
          1,
          "WOAK",
          "EMBR",
          [23 * 60 + 58, 24 * 60 + 18, 24 * 60 + 38, 29 * 60 + 15].map((m) => clockTime(0, m)),
        ),
        "EMBR-WOAK": tube(
          2,
          "EMBR",
          "WOAK",
          [24 * 60 + 5, 29 * 60 + 10].map((m) => clockTime(0, m)),
        ),
      },
    },
  },
  {
    name: "transfer-last",
    title: "Last trains with transfers: on a later train, and on the next departure",
    stations: "rock frmt",
    now: clockTime(23, 50),
    etdFailure: "not-json",
    warnings: true,
    fixtures: {
      ...NO_SOURCE,
      depart: {
        "ROCK-FRMT": planResponse([
          ...[23 * 60 + 58, 24 * 60 + 18, 24 * 60 + 38].map((m) =>
            viaStation(clockTime(0, m), [1, "ROCK", "MCAR", 3], 3, [4, "FRMT", 42]),
          ),
          viaStation(clockTime(0, 29 * 60 + 15), [1, "ROCK", "MCAR", 3], 3, [4, "FRMT", 42]),
        ]),
        "FRMT-ROCK": planResponse([
          viaStation(clockTime(0, 24 * 60 + 5), [3, "FRMT", "19TH", 37], 1, [2, "ROCK", 6]),
          viaStation(clockTime(0, 29 * 60 + 10), [3, "FRMT", "19TH", 37], 1, [2, "ROCK", 6]),
        ]),
      },
    },
  },
  {
    name: "mixed-last",
    title: "Last trains mixing transfers and direct trips, one each way",
    stations: "dbrk embr",
    now: clockTime(23, 50),
    etdFailure: "not-json",
    warnings: true,
    fixtures: {
      ...NO_SOURCE,
      depart: {
        "DBRK-EMBR": planResponse([
          viaStation(clockTime(0, 23 * 60 + 52), [4, "DBRK", "MCAR", 5], 1, [1, "EMBR", 15]),
          [[7, "DBRK", "EMBR", clockTime(0, 24 * 60 + 5), clockTime(0, 24 * 60 + 30)]],
          [[7, "DBRK", "EMBR", clockTime(0, 29 * 60 + 15), clockTime(0, 29 * 60 + 40)]],
        ]),
        "EMBR-DBRK": planResponse([
          [[8, "EMBR", "DBRK", clockTime(0, 23 * 60 + 55), clockTime(0, 24 * 60 + 20)]],
          viaStation(clockTime(0, 24 * 60 + 10), [2, "EMBR", "MCAR", 15], 1, [3, "DBRK", 5]),
          [[8, "EMBR", "DBRK", clockTime(0, 29 * 60 + 10), clockTime(0, 29 * 60 + 35)]],
        ]),
      },
    },
  },
  {
    name: "no-data",
    title: "Every source down",
    now: clockTime(9, 0),
    etdFailure: "not-json",
    scheduleFailure: "not-json",
    warnings: true,
    fixtures: NO_SOURCE,
  },
];

const metricsFile = JSON.parse(readText(METRICS_PATH));
const errors = [];
const APPEARANCES = ["dark", "light"];
let appearance = "dark";

// a color as drawn in the current appearance
const resolved = (c) => (appearance === "light" && c.lightHex ? c.lightHex : c.hex);

const isVertical = (node) => node.vertical ?? node.kind === "widget";

function natural(node) {
  if (node.kind === "image") return node.imageSize ?? { width: INLINE_SYMBOL_SIZE, height: INLINE_SYMBOL_SIZE };
  if (node.kind === "widget" && !isVertical(node)) {
    const sizes = node.children.map(natural);
    return { width: sizes.reduce((sum, s) => sum + s.width, 0), height: Math.max(...sizes.map((s) => s.height)) };
  }
  return naturalSize(node, errors);
}

// a stack without a fixed size along an axis grows along it when the stack or a descendant holds a flexible spacer there
function grows(node, vertical) {
  if (node.kind !== "stack" && node.kind !== "widget") return false;
  if ((vertical ? node.size?.height : node.size?.width) > 0) return false;
  const own = isVertical(node) === vertical;
  return node.children.some((c) => (own && c.kind === "spacer" && c.value === null) || grows(c, vertical));
}

// text shrinks to fit its frame, down to its minimum scale factor
function placeText(node, { x, y, w, h }, out) {
  const width = natural(node).width;
  const scale = width > w ? Math.max(w / width, node.minimumScaleFactor ?? 1) : 1;
  out.push({
    kind: "text",
    text: shownText(node),
    font: node.font,
    scale,
    lineHeight: metricsFile[node.font].lineHeight,
    color: resolved(node.textColor),
    x: node.align === "right" ? x + w - width * scale : x,
    y,
    h,
  });
}

/**
 * Places a node in its frame. Flexible spacers and growing stacks share the space left along the
 * axis, content shrinks evenly when it overflows, a vertical stack aligns children at its leading
 * edge unless it centers them, and a horizontal stack aligns them by its content alignment, top by
 * default. Right-aligned text fills its stack. A widget centers content that does not fill it.
 */
function place(node, frame, out) {
  if (node.kind === "text" || node.kind === "date") return placeText(node, frame, out);
  const { x, y, w, h } = frame;
  if (node.kind === "image") {
    const size = natural(node);
    const color = node.tintColor ? resolved(node.tintColor) : "#ffffff";
    out.push({
      kind: "symbol",
      name: node.value,
      color,
      x,
      y: y + (h - size.height) / 2,
      w: size.width,
      h: size.height,
    });
    return;
  }
  if (node.kind === "stack" && node.backgroundColor) {
    out.push({ kind: "rect", x, y, w, h, color: resolved(node.backgroundColor), radius: node.cornerRadius ?? 0 });
  }
  const vertical = isVertical(node);
  const [main, cross] = vertical ? [h, w] : [w, h];
  const items = node.children.map((c) => {
    if (c.kind === "spacer") return { c, size: c.value ?? 0, cross: 0, flex: c.value === null };
    const s = natural(c);
    return {
      c,
      size: vertical ? s.height : s.width,
      cross: vertical ? s.width : s.height,
      flex: grows(c, vertical) || (!vertical && c.align === "right"),
    };
  });
  const total = items.reduce((sum, i) => sum + i.size, 0);
  const flexible = items.filter((i) => i.flex);
  let offset = 0;
  // SwiftUI offers flexible children equal shares of what fixed children leave
  const fixedTotal = items.filter((i) => !i.flex).reduce((sum, i) => sum + i.size, 0);
  if (total > main) {
    const content = items.filter((i) => i.c.kind !== "spacer");
    const fixed = total - content.reduce((sum, i) => sum + i.size, 0);
    const k = Math.max(0, main - fixed) / (total - fixed);
    for (const i of content) i.size *= k;
  } else if (flexible.length > 0) {
    let left = main - fixedTotal;
    const bySize = [...flexible].sort((a, b) => b.size - a.size);
    bySize.forEach((i, n) => {
      i.size = Math.max(i.size, left / (bySize.length - n));
      left -= i.size;
    });
  } else if (node.kind === "widget") {
    offset = (main - total) / 2;
  }
  for (const i of items) {
    if (i.c.kind !== "spacer") {
      const size = grows(i.c, !vertical) ? cross : Math.min(i.cross, cross);
      const align = vertical
        ? node.centered
          ? "center"
          : "top"
        : (node.contentAlign ?? (node.kind === "widget" ? "center" : "top"));
      const at = align === "center" ? (cross - size) / 2 : align === "bottom" ? cross - size : 0;
      const f = vertical
        ? { x: x + at, y: y + offset, w: size, h: i.size }
        : { x: x + offset, y: y + at, w: i.size, h: size };
      place(i.c, f, out);
    }
    offset += i.size;
  }
}

function styleUnstyled(node) {
  for (const c of node.children) {
    if (c.kind === "text") {
      c.font ??= INLINE_FONT;
      c.textColor ??= { hex: "#ffffff" };
    }
    styleUnstyled(c);
  }
}

async function scene(state, family) {
  const r = await runScript({
    now: state.now,
    family,
    params: { widget: state.stations ?? STATIONS },
    etdFailure: state.etdFailure ?? null,
    scheduleFailure: state.scheduleFailure ?? null,
    fixtures: state.fixtures,
  });
  if (r.thrown) throw r.thrown;
  if (r.harnessErrors.length > 0) throw new Error(`${state.name} ${family}: ${r.harnessErrors.join("; ")}`);
  if (!state.warnings && r.warnings.length > 0) throw new Error(`${state.name} ${family}: ${r.warnings.join("; ")}`);
  const widget = r.widget;
  // the script shows an error as a widget of one 12pt line
  if (widget.children.length === 1 && widget.children[0].font === "systemFont(12)") {
    throw new Error(`${state.name} ${family}: ${texts(widget).join(" ")}`);
  }
  styleUnstyled(widget);
  // an inline widget is one line, and a circular one centers its lines
  if (family === "accessoryInline") widget.vertical = false;
  if (family === "accessoryCircular") widget.centered = true;
  const size = SIZES[family];
  const width = size.width ?? natural(widget).width;
  const pad = widget.padding ?? { top: 0, leading: 0, bottom: 0, trailing: 0 };
  const items = [];
  const frame = {
    x: pad.leading,
    y: pad.top,
    w: width - pad.leading - pad.trailing,
    h: size.height - pad.top - pad.bottom,
  };
  place(widget, frame, items);
  if (errors.length > 0) throw new Error(`${state.name} ${family}: ${errors.join("; ")}`);
  return {
    accessory: family.startsWith("accessory"),
    width,
    height: size.height,
    background: widget.backgroundColor ? resolved(widget.backgroundColor) : null,
    accessoryBackground: widget.addAccessoryWidgetBackground === true,
    items,
  };
}

// every widget family of a state, in the current appearance
async function allFamilies(state) {
  const s = (family) => scene(state, family);
  return [
    { wallpaper: "home", rows: [[await s("small"), await s("medium")], [await s("large")]] },
    {
      wallpaper: "lock",
      rows: [[await s("accessoryInline")], [await s("accessoryCircular"), await s("accessoryRectangular")]],
    },
  ];
}

export async function readmeImages() {
  const [commute] = STATES;
  const images = [];
  for (appearance of APPEARANCES) {
    images.push({ name: `widgets-${appearance}`, sections: await allFamilies(commute) });
  }
  return images;
}

export async function galleryImages() {
  const images = [];
  for (const state of STATES) {
    for (appearance of APPEARANCES) {
      images.push({
        name: `gallery-${state.name}-${appearance}`,
        title: `${state.title} (${appearance})`,
        sections: await allFamilies(state),
      });
    }
  }
  return images;
}

if (import.meta.main) {
  const [mode] = process.argv.slice(2);
  if (mode !== "readme" && mode !== "gallery") throw new Error("expected readme or gallery");
  console.log(JSON.stringify(await (mode === "readme" ? readmeImages() : galleryImages())));
}
