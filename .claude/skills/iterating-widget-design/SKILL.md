---
name: iterating-widget-design
description: Iterates on the look and layout of the BART Scriptable widgets, renders every size and service state for review, and proposes design candidates. Use when changing text, symbols, colors, spacing, or alignment in a Small, Medium, Large, or Lock Screen widget, when asked to show renders or the gallery, or when reviewing the widget's visual design.
---

# Iterating on widget design

## Loop

1. Change the engine, then run `make gallery`, which writes every family in each service state, in light and dark, to `.agent-context/gallery/`.
1. Run `bun test tests/js/layout.test.js tests/js/render.test.js`. A new character or font fails as unmeasured until `osascript -l JavaScript tools/measure_fonts.js` remeasures it.
1. View the images yourself, then give the user the full paths. Render again before every review, because a stale image costs the user a round.

The user reviews images in the main checkout's `.agent-context/`. Copy them there when working anywhere else.

## Apply a change everywhere it belongs

Check each change against the product guidelines in every family and state. A pattern that changes in one size usually changes in all of them, so when the request names one size, check whether the others now differ and say so.

Rules the user has settled:

- An indented line describes the train above it. Lines about later trains are not indented under the departure time.
- A last-train flag aligns with the departure time of its train.
- Color means a train line. Other information stays neutral, so it cannot be read as a line.
- Lock Screen widgets draw in one tint, so a letter stands in for a line color.
- Use SF Symbols, not emojis.
- Departure times use a 12-hour clock with AM or PM.
- Use the words outbound and return, not morning and evening.

## Candidates

For a choice between designs, render each candidate as the real widget in `.agent-context/` under a letter or number, so the user can pick by name. Keep any options table matching the current renders. Delete each candidate once it is decided.

## Scriptable limits

Check a proposal against these before offering it:

- Running the script in the app does not update a widget; iOS decides when it refreshes. Riders use only the widget, so an in-widget refresh control does nothing.
- A timer or offset date is drawn by iOS, so nothing else can change when it rolls over.
- Tapping a widget opens a URL. It cannot run code in the widget.

Renders approximate WidgetKit. Treat a tight fit as unconfirmed until it is seen on a phone, and when a phone screenshot and a render disagree, trust the phone.

Run `make render` to update the README images only when the user asks. It needs macOS, and the project's GitHub workflows stay on Ubuntu.
