// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause
//
// Draws widget images, JSON sections of widgets on wallpaper where each widget is a list of positioned
// rectangles, symbols and text, with SF Pro and SF Pro Rounded on this Mac at 3x, into DIR/NAME.png.
// Usage: osascript -l JavaScript tools/render/draw.js IMAGES_JSON DIR

ObjC.import("AppKit");

const PIXELS_PER_POINT = 3;
// wallpaper around and between widgets, in points
const MARGIN = 14;
const TITLE_SIZE = 13;
// Home Screen widget corner radius on a 6.1-inch iPhone
const CORNER_RADIUS = 22;
// the Lock Screen draws widgets in one tint, with secondary text partly transparent
const SECONDARY_ALPHA = 0.6;
const PRIMARY = "#ffffff";
// translucent backdrop the Lock Screen draws behind a circular widget
const ACCESSORY_BACKGROUND_ALPHA = 0.18;
const WALLPAPER = { home: ["#2b3a67", "#7a4a8c"], lock: ["#0f1c3f", "#3b2d5c"] };

// Scriptable font constructors as SF weights and designs
const CONSTRUCTORS = {
  systemFont: { weight: () => $.NSFontWeightRegular, rounded: false },
  mediumSystemFont: { weight: () => $.NSFontWeightMedium, rounded: false },
  semiboldSystemFont: { weight: () => $.NSFontWeightSemibold, rounded: false },
  boldSystemFont: { weight: () => $.NSFontWeightBold, rounded: false },
  boldRoundedSystemFont: { weight: () => $.NSFontWeightBold, rounded: true },
  italicSystemFont: { weight: () => $.NSFontWeightRegular, rounded: false, italic: true },
};

function font(spec, scale) {
  const m = /^(\w+)\((\d+(?:\.\d+)?)\)$/.exec(spec);
  if (!m || !CONSTRUCTORS[m[1]]) throw new Error(`unknown font: ${spec}`);
  const { weight, rounded, italic } = CONSTRUCTORS[m[1]];
  const size = Number(m[2]) * scale;
  const system = $.NSFont.systemFontOfSizeWeight(size, weight());
  if (italic)
    return $.NSFont.fontWithDescriptorSize(
      system.fontDescriptor.fontDescriptorWithSymbolicTraits($.NSFontDescriptorTraitItalic),
      size,
    );
  if (!rounded) return system;
  const descriptor = system.fontDescriptor.fontDescriptorWithDesign($.NSFontDescriptorSystemDesignRounded);
  if (descriptor.isNil()) throw new Error(`no rounded design for ${spec}`);
  return $.NSFont.fontWithDescriptorSize(descriptor, size);
}

function color(hex, alpha = 1) {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (!m) throw new Error(`unknown color: ${hex}`);
  const [r, g, b] = m.slice(1).map((c) => parseInt(c, 16) / 255);
  return $.NSColor.colorWithSRGBRedGreenBlueAlpha(r, g, b, alpha);
}

// AppKit draws with y up, so frames are flipped against the canvas height
function canvas(width, height) {
  const rep =
    $.NSBitmapImageRep.alloc.initWithBitmapDataPlanesPixelsWidePixelsHighBitsPerSampleSamplesPerPixelHasAlphaIsPlanarColorSpaceNameBytesPerRowBitsPerPixel(
      null,
      Math.round(width * PIXELS_PER_POINT),
      Math.round(height * PIXELS_PER_POINT),
      8,
      4,
      true,
      false,
      $.NSDeviceRGBColorSpace,
      0,
      0,
    );
  rep.setSize($.NSMakeSize(width, height));
  const rect = (x, y, w, h) => $.NSMakeRect(x, height - y - h, w, h);
  return { rep, rect };
}

function fillRounded(rect, radius, fill) {
  fill.setFill;
  $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius(rect, radius, radius).fill;
}

function drawText(rect, text, nsFont, fill) {
  const attrs = $.NSMutableDictionary.dictionary;
  attrs.setObjectForKey(nsFont, $.NSFontAttributeName);
  attrs.setObjectForKey(fill, $.NSForegroundColorAttributeName);
  $(text).drawInRectWithAttributes(rect, attrs);
}

// width of text in a font, in points
function textWidth(text, nsFont) {
  return $(text).sizeWithAttributes($.NSDictionary.dictionaryWithObjectForKey(nsFont, $.NSFontAttributeName)).width;
}

function drawSymbol(rect, name, fill) {
  const symbol = $.NSImage.imageWithSystemSymbolNameAccessibilityDescription(name, name);
  if (symbol.isNil()) throw new Error(`unknown symbol: ${name}`);
  const palette = $.NSImageSymbolConfiguration.configurationWithPaletteColors($.NSArray.arrayWithObject(fill));
  const image = symbol.imageWithSymbolConfiguration(palette);
  // the symbol keeps its aspect ratio, centered in the rect
  const { width, height } = image.size;
  const k = Math.min(rect.size.width / width, rect.size.height / height);
  const [w, h] = [width * k, height * k];
  image.drawInRect(
    $.NSMakeRect(rect.origin.x + (rect.size.width - w) / 2, rect.origin.y + (rect.size.height - h) / 2, w, h),
  );
}

function drawWidget(widget, origin, rect) {
  if (widget.accessoryBackground) {
    const r = Math.min(widget.width, widget.height) / 2;
    fillRounded(rect(origin.x, origin.y, widget.width, widget.height), r, color(PRIMARY, ACCESSORY_BACKGROUND_ALPHA));
  } else if (!widget.accessory) {
    fillRounded(rect(origin.x, origin.y, widget.width, widget.height), CORNER_RADIUS, color(widget.background));
  }
  for (const item of widget.items) {
    const x = origin.x + item.x;
    const y = origin.y + item.y;
    const alpha = widget.accessory && item.color !== PRIMARY ? SECONDARY_ALPHA : 1;
    const hex = widget.accessory ? PRIMARY : (item.color ?? PRIMARY);
    if (item.kind === "rect") {
      fillRounded(rect(x, y, item.w, item.h), item.radius, color(hex, alpha));
      continue;
    }
    if (item.kind === "symbol") {
      drawSymbol(rect(x, y, item.w, item.h), item.name, color(hex));
      continue;
    }
    // shrunk text keeps its line centered in the frame
    const lineHeight = item.lineHeight * item.scale;
    const nsFont = font(item.font, item.scale);
    const top = y + (item.h - lineHeight) / 2;
    // a rect a point wider than the text keeps it on one line
    drawText(rect(x, top, textWidth(item.text, nsFont) + 1, lineHeight), item.text, nsFont, color(hex, alpha));
  }
}

function wallpaper(rect, [from, to]) {
  const gradient = $.NSGradient.alloc.initWithStartingColorEndingColor(color(from), color(to));
  gradient.drawInRectAngle(rect, -45);
}

const TITLE_HEIGHT = TITLE_SIZE * 1.4;

// sizes of the rows of widgets in a section, laid out left to right with MARGIN around and between them
function measure(section, titled) {
  const rows = section.rows.map((row) => ({
    width: row.reduce((sum, w) => sum + w.width, 0) + MARGIN * (row.length + 1),
    height: Math.max(...row.map((w) => w.height)),
  }));
  const top = titled ? TITLE_HEIGHT + MARGIN : 0;
  return {
    rows,
    top,
    width: Math.max(...rows.map((r) => r.width)),
    height: top + rows.reduce((sum, r) => sum + r.height + MARGIN, MARGIN),
  };
}

function render(image) {
  const sizes = image.sections.map((section, i) => measure(section, i === 0 && image.title));
  const width = Math.max(...sizes.map((s) => s.width));
  const height = sizes.reduce((sum, s) => sum + s.height, 0);
  const { rep, rect } = canvas(width, height);
  $.NSGraphicsContext.saveGraphicsState;
  $.NSGraphicsContext.setCurrentContext($.NSGraphicsContext.graphicsContextWithBitmapImageRep(rep));
  let y = 0;
  image.sections.forEach((section, i) => {
    const size = sizes[i];
    wallpaper(rect(0, y, width, size.height), WALLPAPER[section.wallpaper]);
    let top = y;
    if (i === 0 && image.title) {
      const f = font(`semiboldSystemFont(${TITLE_SIZE})`, 1);
      drawText(rect(MARGIN, y + MARGIN, textWidth(image.title, f) + 1, TITLE_HEIGHT), image.title, f, color(PRIMARY));
      top += TITLE_HEIGHT + MARGIN;
    }
    let rowTop = top + MARGIN;
    section.rows.forEach((row, r) => {
      let x = MARGIN;
      for (const widget of row) {
        drawWidget(widget, { x, y: rowTop }, rect);
        x += widget.width + MARGIN;
      }
      rowTop += size.rows[r].height + MARGIN;
    });
    y += size.height;
  });
  $.NSGraphicsContext.restoreGraphicsState;
  return rep;
}

function readText(path) {
  const text = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
  if (text.isNil()) throw new Error(`cannot read ${path}`);
  return text.js;
}

function run(argv) {
  if (argv.length !== 2) throw new Error("usage: draw.js IMAGES_JSON DIR");
  const [imagesPath, dir] = argv;
  for (const image of JSON.parse(readText(imagesPath))) {
    const png = render(image).representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $());
    const path = `${dir}/${image.name}.png`;
    if (!png.writeToFileAtomically(path, true)) throw new Error(`cannot write ${path}`);
  }
  return "";
}
