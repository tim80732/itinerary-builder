"""Turns a trip record into one self-contained HTML file.

The exported page is data-driven: the trip (and every document, base64-encoded)
is embedded as JSON and the page's own script renders the calendar, schedule
sheet, cards, modals and cost summary.
"""
from __future__ import annotations

import base64
import json
from datetime import date
from pathlib import Path

from .flags import flag_svg, split_tag
from .triptypes import (DAY_SPLITS, GROUPS, PINNED_LABEL, clean_link, clean_links,
                        meta_for_page, sections_for_page, trip_items)

PAGE_CSS = r"""
  /* Every colour on the page comes from here. Dark stays on :root so nothing
     that has never heard of themes can break; light is the same names with
     different values. Three states, but only two are ever visible: with no
     `data-theme` set the media query decides, which is what "Auto" means. */
  :root {
    --bg: #12141c;
    --card: #1c1f2b;
    --ink: #eceef4;
    --muted: #9aa0b0;
    --line: #363b4d;
    --divider: #2c3040;
    --accent: #e3b95e;
    --transit: #4ecca3;    --transit-soft: rgba(78, 204, 163, 0.12);
    --plane: #ff8a5c;      --plane-soft: rgba(255, 138, 92, 0.12);
    --rental: #7aa2f7;     --rental-soft: rgba(122, 162, 247, 0.12);
    --hotel: #c792ea;      --hotel-soft: rgba(199, 146, 234, 0.12);
    --activity: #ff7eb6;   --activity-soft: rgba(255, 126, 182, 0.12);
    --restaurant: #ff6b6b; --restaurant-soft: rgba(255, 107, 107, 0.12);
    --location: #56c8d8;   --location-soft: rgba(86, 200, 216, 0.12);
    --custom: #a0a8bd;     --custom-soft: rgba(160, 168, 189, 0.12);
    --alert: #ff6b6b;      /* the card's red heads-up line */
    /* Gold has to be two tokens. As TEXT it must darken on a light background
       to stay legible; as a FILL it must stay bright, or dark text on a dark
       gold is unreadable — which is exactly what happened the first time. */
    --accent-fill: #e3b95e;
    /* The same trap applies to the type colours: they are text on the schedule
       and a solid fill behind the cost badges. Darkening them for light fixes
       the text and breaks the badge, so the badge's own text flips instead. */
    --on-type: #12141c;
    --shadow: 0 6px 24px rgba(0, 0, 0, 0.35);
    /* surfaces that used to be written inline as rgba() over a dark base —
       they have to be tokens or they invert wrong */
    --hover: rgba(255, 255, 255, 0.05);
    --faint: #4a5068;                    /* "no time", "no map" — present but absent */
    --head: rgba(227, 185, 94, 0.10);    /* the schedule's date bar */
    --head-ink: #eceef4;
    --head-accent: #e3b95e;
    --band: #272b3a;                     /* the phone's section band */
    --band-ink: #e3b95e;
    --band-pin-ink: #c792ea;
    --sec-col: rgba(255, 255, 255, 0.022); /* the wide screen's merged column */
    /* The calendar's day cells get their own surface rather than --card. At
       --card they sat 1.12:1 off the page — the 1px border was the only thing
       separating them, so the whole grid read as one dark rectangle. These lift
       them to 1.48:1. They cannot just use a lighter --card: that token is also
       the modals, the panels and the day picker, and all of those would lift
       with it. */
    --cell: #2e3446;
    --cell-line: #454d66;
    --on-accent: #1f2430;                /* text sitting on a gold fill */
    --overlay: rgba(0, 0, 0, 0.62);
    --overlay-strong: rgba(0, 0, 0, 0.72);
  }

  /* Tell the browser which way round we are, so form controls, scrollbars and
     the address bar follow rather than staying dark on a light page. */
  :root[data-theme="light"] { color-scheme: light; }
  @media (prefers-color-scheme: light) {
    :root:not([data-theme]) { color-scheme: light; }
  }
  :root[data-theme="dark"] { color-scheme: dark; }
"""

# The light values, written once and applied to both the explicit choice and
# the "no choice at all" case. Keeping it in one Python string means the two
# can never drift — which they would, written out twice by hand.
_LIGHT_TOKENS = r"""
    --bg: #eef0f5;
    --card: #ffffff;
    --ink: #14171f;
    --muted: #5b6270;
    --line: #c2c8d6;
    --divider: #e3e6ee;
    --accent: #8a5f00;     /* gold as text: 5.6:1 on white */
    --accent-fill: #e3b95e;  /* gold as a fill: unchanged, so --on-accent still reads */
    --on-type: #ffffff;      /* the type colours are dark here, so their badges go white */
    --transit: #0b7a57;    --transit-soft: rgba(11, 122, 87, 0.10);
    --plane: #b4470f;      --plane-soft: rgba(180, 71, 15, 0.10);
    --rental: #2a55c8;     --rental-soft: rgba(42, 85, 200, 0.10);
    --hotel: #7a35c6;      --hotel-soft: rgba(122, 53, 198, 0.10);
    --activity: #c11c62;   --activity-soft: rgba(193, 28, 98, 0.10);
    --restaurant: #c32626; --restaurant-soft: rgba(195, 38, 38, 0.10);
    --location: #0b6f85;   --location-soft: rgba(11, 111, 133, 0.10);
    --custom: #4a5261;     --custom-soft: rgba(74, 82, 97, 0.10);
    --alert: #c32626;
    --shadow: 0 6px 20px rgba(20, 28, 55, 0.10);
    --hover: rgba(0, 0, 0, 0.05);
    --faint: #9aa3b4;
    --head: #1c1f2b;       /* stays dark on purpose: the date bar is the anchor */
    --head-ink: #ffffff;
    --head-accent: #f0c36a;
    --band: #d8dde8;
    --band-ink: #6b4a00;
    --band-pin-ink: #5b1aa8;
    --sec-col: #f3f5f9;
    /* Light needs no lift: the cell is already pure white, the brightest thing
       on the page, and there is nowhere above it to go. Same values as --card
       and --line, named separately so the calendar can be tuned on its own. */
    --cell: #ffffff;
    --cell-line: #c2c8d6;
    --on-accent: #1f2430;
    --overlay: rgba(20, 24, 38, 0.45);
    --overlay-strong: rgba(20, 24, 38, 0.62);
"""

PAGE_CSS += (
    "  :root[data-theme=\"light\"] {" + _LIGHT_TOKENS + "  }\n"
    "  @media (prefers-color-scheme: light) {\n"
    "    :root:not([data-theme]) {" + _LIGHT_TOKENS + "    }\n"
    "  }\n"
    # The schedule sheet is always light, whatever the rest of the page is.
    # It is the one view you read like a document rather than tap through, and
    # dark mode has no room to separate its layers: page, rows, date bar and
    # section band all sat within 1.22:1 of each other, the band and the date
    # bar within 1.05:1 — one continuous slab to the eye, and worse in daylight.
    # Re-declaring the tokens on #sheet rather than restyling anything means the
    # sheet's own rules are untouched; they just resolve to different values.
    # #sheet holds only the tables, so the toolbar above it stays with the page.
    # `color` has to be set as well as the tokens: anything that doesn't say
    # `var(--ink)` — the card title does not — inherits its colour from <body>,
    # which is outside this scope and still dark. Re-declaring the token alone
    # leaves white titles on the white sheet.
    "  #sheet {" + _LIGHT_TOKENS + "    color: var(--ink);\n  }\n"
)

PAGE_CSS += r"""
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: var(--bg);
    color: var(--ink);
    font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    line-height: 1.5;
    padding-bottom: 80px;
  }
  .hidden { display: none !important; }

  /* ---------- back to top ---------- */
  .top-btn {
    position: fixed; top: 18px; right: 18px; z-index: 10;
    background: var(--card); color: var(--ink);
    border: 1px solid var(--line); border-radius: 999px;
    padding: 8px 16px; font: 600 14px inherit; font-family: inherit;
    cursor: pointer; box-shadow: var(--shadow);
    opacity: 0; pointer-events: none; transition: opacity 0.25s;
  }
  .top-btn.show { opacity: 1; pointer-events: auto; }
  .top-btn:hover { border-color: var(--accent); }

  /* ---------- theme switch ----------
     Top left, because .top-btn owns the top right and fades in once the header
     has scrolled away — the corner does two jobs at two different times. The
     glyphs are inline SVG rather than emoji for the same reason the flags are:
     Windows has no glyph for some and you get a box. */
  .theme-btn {
    position: absolute; top: 14px; left: 14px; z-index: 3;
    display: inline-flex; align-items: center; justify-content: center;
    width: 30px; height: 30px; border-radius: 999px;
    background: var(--card); color: var(--ink);
    border: 1px solid var(--line); cursor: pointer; padding: 0;
  }
  /* the visible circle is 30px; the tap target is padded out to 44px, which is
     the smallest a thumb reliably hits */
  .theme-btn::after {
    content: ""; position: absolute; top: 50%; left: 50%;
    width: 44px; height: 44px; transform: translate(-50%, -50%);
  }
  .theme-btn:hover { border-color: var(--accent); }
  .theme-btn svg { display: block; }
  .theme-btn .sun { display: none; }
  /* the icon names the theme you are in, not the one you would get */
  :root[data-theme="light"] .theme-btn .sun { display: block; }
  :root[data-theme="light"] .theme-btn .moon { display: none; }
  @media (prefers-color-scheme: light) {
    :root:not([data-theme]) .theme-btn .sun { display: block; }
    :root:not([data-theme]) .theme-btn .moon { display: none; }
  }

  /* ---------- header ---------- */
  header { text-align: center; padding: 18px 20px 0; position: relative; }
  header .eyebrow {
    letter-spacing: 0.3em; text-transform: uppercase; font-size: 10px;
    color: var(--accent); font-weight: 700; margin-bottom: 6px;
  }
  header h1 { font-size: clamp(21px, 4.2vw, 34px); font-weight: 800; letter-spacing: -0.02em; }
  /* Dates and countries share one line — on a phone that is ~40px back, and the
     header as a whole drops from 335px to about 140px. It wraps rather than
     overflowing if a trip ever manages to outgrow it. */
  .trip-meta {
    display: flex; flex-wrap: wrap; justify-content: center; align-items: center;
    gap: 4px 9px; margin: 6px auto 0; max-width: 860px;
    font-size: 12.5px; color: var(--muted);
  }
  .trip-meta .sepdot { color: var(--line); }
  /* plain line, no pills — flags stay as inline SVG so Windows renders them */
  .tag-row {
    display: inline-flex; flex-wrap: wrap; justify-content: center; align-items: center;
    gap: 4px 11px; font-weight: 600; color: var(--ink);
  }
  .tag-row .chip { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
  .tag-row .flag {
    border-radius: 2px; flex: 0 0 auto;
    outline: 1px solid rgba(128, 128, 128, 0.35); outline-offset: -1px;
  }
  /* Past the name budget the flags carry it alone. Decided in Python, because
     the width depends on how long the names are, not how many there are —
     "Czechia" and "Czech Republic" are one country each and nearly double it. */
  .tag-row.flags-only .chip span { display: none; }
  .tag-row.flags-only { gap: 4px 8px; }

  /* Underline tabs, equal thirds. Equal thirds is the point: sized to their
     content the three pills wanted 345px and got 335px, so "Cost" wrapped onto
     a second line and cost 44px of a phone screen. */
  .view-tabs {
    display: flex; margin: 12px auto 0; max-width: 860px;
    border-bottom: 1px solid var(--line);
  }
  .view-tab {
    flex: 1; background: none; color: var(--muted); border: 0;
    border-bottom: 2px solid transparent; margin-bottom: -1px;
    padding: 10px 4px 9px; font-size: 13px; font-weight: 700;
    cursor: pointer; font-family: inherit; white-space: nowrap;
  }
  .view-tab:hover { color: var(--ink); }
  .view-tab.active { color: var(--accent); border-bottom-color: var(--accent); }

  /* ---------- schedule sheet ---------- */
  .sheet-wrap { max-width: 980px; margin: 26px auto 0; padding: 0 18px; }
  .sheet-actions {
    display: flex; justify-content: space-between; align-items: center;
    gap: 12px; margin-bottom: 12px;
  }
  .pdf-btn {
    font: inherit; font-size: 13.5px; font-weight: 700; cursor: pointer;
    background: var(--card); color: var(--ink);
    border: 1px solid var(--line); border-radius: 999px; padding: 9px 18px;
  }
  .sheet-actions.end { justify-content: flex-end; }   /* no day picker to sit opposite */
  /* the cost toolbar has to line up with the summary card under it, and that
     card is narrower than the schedule's sheet */
  #costView .sheet-wrap { max-width: 880px; padding: 0 20px; }
  .pdf-btn:hover { border-color: var(--accent); color: var(--accent); }

  /* Which days to show — a <details> rather than a <select>, because several
     days can be picked at once and a multiple-select is unusable on a phone. */
  .day-pick { position: relative; max-width: 66%; min-width: 0; }
  .day-pick > summary {
    list-style: none; cursor: pointer; position: relative;
    font-size: 13.5px; font-weight: 700;
    background: var(--card); color: var(--ink);
    border: 1px solid var(--line); border-radius: 999px;
    padding: 9px 36px 9px 16px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .day-pick > summary::-webkit-details-marker { display: none; }
  .day-pick > summary::after {
    content: "▾"; position: absolute; right: 15px; top: 9px; color: var(--muted);
  }
  .day-pick > summary:hover { border-color: var(--accent); }
  .day-pick[open] > summary { border-color: var(--accent); }
  .day-pick[open] > summary::after { content: "▴"; }
  .day-pick-panel {
    position: absolute; z-index: 30; left: 0; top: calc(100% + 6px);
    min-width: 270px; max-height: 330px; overflow-y: auto;
    background: var(--card); border: 1px solid var(--line);
    border-radius: 12px; box-shadow: var(--shadow); padding: 6px;
  }
  .day-opt {
    display: flex; align-items: center; gap: 10px; cursor: pointer;
    padding: 7px 11px; border-radius: 8px; font-size: 13px; white-space: nowrap;
  }
  .day-opt:hover { background: var(--hover); }
  .day-opt input { accent-color: var(--accent); cursor: pointer; }
  .day-opt.all {
    font-weight: 800; border-bottom: 1px solid var(--divider);
    border-radius: 8px 8px 0 0; margin-bottom: 4px;
  }

  .sheet-day {
    width: 100%; border-collapse: collapse; margin-bottom: 26px;
    background: var(--card); border: 1px solid var(--line); border-radius: 12px;
    overflow: hidden; table-layout: fixed;
  }
  /* Column widths belong on the colgroup: with table-layout:fixed the browser
     reads them from the first row, and that row is the date heading's single
     colspan cell, which would split the table into equal fifths. */
  .c-sec  { width: 124px; }
  .c-time { width: 82px; }
  .c-kind { width: 158px; }
  .c-map  { width: 62px; }
  /* .c-what takes whatever is left */

  /* a thead, not a caption: browsers repeat it when a day runs over a page
     break, so every printed page keeps its date heading */
  .date-row th {
    text-align: left; background: var(--head); color: var(--head-ink);
    font-size: 16px; font-weight: 800; letter-spacing: 0.04em;
    padding: 12px 16px; border-bottom: 1px solid var(--line);
  }
  .date-row .dayno { color: var(--head-accent); }
  /* dimmed from the bar's own ink rather than --muted: in light the bar is dark
     and --muted is a mid grey, which all but disappears on it */
  .date-row .sep { color: var(--head-ink); opacity: 0.6; font-weight: 600; margin: 0 7px; }

  .sheet-day td, .sheet-day th { border-top: 1px solid var(--divider); vertical-align: middle; }
  .sheet-day tbody tr:first-child td, .sheet-day tbody tr:first-child th { border-top: 0; }

  .sec {
    text-align: center; vertical-align: middle;
    color: var(--accent); font-size: 12.5px; font-weight: 800;
    letter-spacing: 0.14em; text-transform: uppercase;
    background: var(--sec-col); padding: 12px 8px;
    border-right: 1px solid var(--line); border-top: 1px solid var(--line) !important;
  }
  .stime {
    text-align: center; padding: 11px 8px; white-space: nowrap;
    font-size: 15px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--ink);
  }
  .stime.none { color: var(--faint); font-weight: 600; }
  .skind {
    padding: 11px 6px 11px 14px; white-space: nowrap;
    font-size: 11.5px; font-weight: 800; letter-spacing: 0.09em;
    text-transform: uppercase; color: var(--c, var(--muted));
  }
  .skind .state { color: var(--muted); }
  /* The phone's copies of the section heading and the type label. Both are in
     every row's markup and hidden here; the 620px breakpoint swaps them for the
     `.sec` and `.skind` columns. A rowspan can't be un-merged in CSS, and
     re-building the table on rotate would mean a second layout to keep in step
     with this one — so the page carries both shapes and picks one. */
  .sec-row { display: none; }
  .swhat .kindline { display: none; }
  .swhat { padding: 11px 14px; font-size: 16px; font-weight: 700; letter-spacing: -0.01em; }
  .smap { text-align: center; padding: 9px 8px; }
  .smap a {
    display: inline-block; text-decoration: none; font-size: 15px; line-height: 1;
    border: 1px solid var(--line); border-radius: 999px; padding: 7px 10px; color: var(--ink);
  }
  .smap a:hover { border-color: var(--accent); }
  .smap .off { color: var(--faint); font-size: 15px; }
  .sheet-note td { padding: 10px 16px; font-size: 13.5px; color: var(--muted); }

  /* ---------- calendar ---------- */
  .cal-wrap { max-width: 880px; margin: 18px auto 0; padding: 0 20px; }
  .cal-month-block + .cal-month-block { margin-top: 26px; }
  /* the hint sits on this line, pushed to the right edge, rather than above it */
  .cal-month {
    display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between;
    gap: 4px 14px; font-size: 20px; font-weight: 800; margin-bottom: 12px;
  }
  .cal-hint { font-size: 12.5px; font-weight: 600; color: var(--muted); }
  /* minmax(0, 1fr), not 1fr — the same shape app.css already uses for the
     builder's calendar, which is why the builder never had the bug below. */
  .cal-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 7px; }
  /* A grid item's automatic minimum size is its min-content, and for a box with
     an `aspect-ratio` that minimum is taken from the *content height* and then
     transferred through the ratio into the width. So each day cell refused to go
     below about 44px however narrow the screen got, and seven of them plus the
     gaps overflowed a 320px phone by 22px — sideways scrolling on the view you
     land on. `min-width: 0` lets the tracks do their job. Nothing above 344px
     ever showed it, which is why testing at 375 missed it for so long. */
  .cal-grid > * { min-width: 0; }
  .cal-dow {
    text-align: center; font-size: 11px; font-weight: 800; letter-spacing: 0.08em;
    text-transform: uppercase; color: var(--muted); padding-bottom: 4px;
    overflow: hidden;              /* "WED" clips rather than widening the track */
  }
  .cal-day {
    position: relative; aspect-ratio: 1 / 1;
    display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 5px;
    background: var(--cell); border: 1px solid var(--cell-line); border-radius: 12px;
    color: var(--ink); font-family: inherit; cursor: pointer; padding: 4px;
  }
  .cal-day.out { background: transparent; border-color: transparent; cursor: default; }
  .cal-day.free { border-style: dashed; }
  .cal-day:not(.out):hover { border-color: var(--accent); }
  .cal-day.today { outline: 2px solid var(--accent); outline-offset: 1px; }
  .cal-day.today .cal-num { color: var(--accent); }
  .cal-num { font-size: 20px; font-weight: 800; line-height: 1; }
  .cal-dots { display: flex; flex-wrap: wrap; justify-content: center; gap: 3px; min-height: 7px; }
  .dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; background: var(--c, var(--custom)); }
  .cal-legend {
    display: flex; flex-wrap: wrap; justify-content: center; gap: 8px 16px;
    margin-top: 18px; font-size: 12px; color: var(--muted);
  }
  .cal-legend span { display: inline-flex; align-items: center; gap: 6px; }

  /* ---------- the full card ----------
     Only ever seen inside the card window now that the timeline is gone, but
     the entry/node shape is kept because the editor's live preview renders it. */
  .entry { position: relative; padding-left: 84px; margin-bottom: 44px; --c: var(--custom); --c-soft: var(--custom-soft); }
  .entry[data-c="transit"]    { --c: var(--transit);    --c-soft: var(--transit-soft); }
  .entry[data-c="plane"]      { --c: var(--plane);      --c-soft: var(--plane-soft); }
  .entry[data-c="rental"]     { --c: var(--rental);     --c-soft: var(--rental-soft); }
  .entry[data-c="hotel"]      { --c: var(--hotel);      --c-soft: var(--hotel-soft); }
  .entry[data-c="activity"]   { --c: var(--activity);   --c-soft: var(--activity-soft); }
  .entry[data-c="restaurant"] { --c: var(--restaurant); --c-soft: var(--restaurant-soft); }
  .entry[data-c="location"]   { --c: var(--location);   --c-soft: var(--location-soft); }
  .entry .node {
    position: absolute; left: 12px; top: 6px; width: 56px; height: 56px;
    border-radius: 50%; display: flex; align-items: center; justify-content: center;
    font-size: 26px; background: var(--card); box-shadow: var(--shadow);
    border: 3px solid var(--c);
  }
  .card {
    background: var(--card); border-radius: 16px; box-shadow: var(--shadow);
    overflow: hidden; border: 1px solid var(--divider);
  }
  .card.clickable { cursor: pointer; transition: transform 0.15s, border-color 0.15s; }
  .card.clickable:hover { transform: translateY(-2px); border-color: var(--accent); }
  .card-top {
    display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
    gap: 8px; padding: 14px 22px; background: var(--c-soft);
    border-bottom: 1px solid var(--divider);
  }
  .card-top .date { font-weight: 800; font-size: 15px; letter-spacing: 0.02em; }
  .card-top .tag {
    font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.1em;
    color: #12141c; background: var(--c); border-radius: 999px; padding: 4px 12px;
  }
  .doc-chip {
    font-size: 12px; font-weight: 700; color: var(--ink); background: var(--card);
    border: 1px solid var(--line); border-radius: 999px; padding: 3px 11px; white-space: nowrap;
  }
  .place-name {
    display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
    gap: 8px 14px; padding: 18px 22px 0; font-size: 19px; font-weight: 800; letter-spacing: -0.01em;
  }
  .place-name .place-city { color: var(--muted); font-weight: 600; font-size: 15px; }
  .map-btn {
    display: inline-block; font-size: 12.5px; font-weight: 700; color: var(--c);
    text-decoration: none; border: 1.5px solid var(--c); border-radius: 999px;
    padding: 4px 13px; white-space: nowrap;
  }
  .map-btn:hover { background: var(--c-soft); }
  .journey {
    display: grid; grid-template-columns: 1fr auto 1fr; gap: 12px;
    align-items: center; padding: 22px 22px 8px;
  }
  .journey.single { grid-template-columns: 1fr; }
  .point .time { font-size: 32px; font-weight: 800; letter-spacing: -0.02em; line-height: 1.1; }
  .point .city { font-size: 17px; font-weight: 700; margin-top: 2px; }
  .point .station { font-size: 13.5px; color: var(--muted); margin-top: 1px; }
  .point.arrive { text-align: right; }
  a.stn-link {
    display: inline-block; margin-top: 4px; color: var(--ink); text-decoration: none;
    font-size: 12.5px; font-weight: 600; border: 1px solid var(--line);
    border-radius: 999px; padding: 3px 11px; background: var(--card);
  }
  a.stn-link:hover { border-color: var(--accent); color: var(--accent); }
  .mid { text-align: center; color: var(--muted); font-size: 12.5px; min-width: 120px; }
  .mid .duration { font-weight: 700; color: var(--ink); margin-bottom: 4px; }
  .mid .track { position: relative; height: 2px; background: var(--line); margin: 6px 8px; }
  .mid .track::after {
    content: ""; position: absolute; right: -1px; top: -4px;
    border: 5px solid transparent; border-left-color: var(--line);
  }
  .mid .service { font-weight: 600; }
  .details {
    display: flex; flex-wrap: wrap; gap: 8px 26px; padding: 14px 22px 6px;
    font-size: 13.5px; color: var(--muted);
  }
  .price-row {
    display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between;
    gap: 6px 16px; margin: 12px 22px 18px; padding-top: 12px; border-top: 1px dashed var(--line);
  }
  .price-row .label {
    font-size: 12px; text-transform: uppercase; letter-spacing: 0.12em;
    color: var(--muted); font-weight: 700;
  }
  .price-row .note { font-size: 12.5px; color: var(--muted); }
  .price-row .amount {
    font-size: 22px; font-weight: 800; color: var(--c); margin-left: auto; text-align: right;
  }
  .price-row .amount small {
    display: block; margin-top: 2px; font-size: 13px; font-weight: 600; color: var(--muted);
  }

  /* ---------- links: a card's own (above its price) and the trip's bar ----------
     One per row, 🔗 + the text. The address itself is never printed: the text is
     what the owner chose to call it, and a URL is noise on a phone. */
  .card-links { display: flex; flex-direction: column; gap: 7px; padding: 14px 22px 0; }
  .card-links:last-child { padding-bottom: 20px; }   /* no price row below to space it */
  .card-links .links-label {
    font-size: 12px; text-transform: uppercase; letter-spacing: 0.12em;
    color: var(--muted); font-weight: 700;
  }
  .link-item {               /* an <a> for a link, a <button> for a trip document */
    display: block; width: 100%; padding: 9px 13px; border: 1px solid var(--line); border-radius: 10px;
    background: var(--card); color: var(--ink); text-decoration: none; text-align: left;
    font: inherit; font-size: 14px; font-weight: 600; overflow-wrap: anywhere; cursor: pointer;
  }
  .link-item:hover { border-color: var(--accent); color: var(--accent); }

  /* The trip's links: a bar fixed to the bottom of the calendar view, one
     centred line; a tap opens the links in a window (#linksModal, the same
     modal shell as the day and card windows). Only the calendar shows the bar
     (body[data-view] comes from setView), and the body's bottom padding grows
     so the last week can always scroll clear of it. */
  .trip-links {
    position: fixed; left: 0; right: 0; bottom: 0; z-index: 60;
    display: flex; justify-content: center; pointer-events: none;
  }
  body:not([data-view="calendar"]) .trip-links { display: none; }
  .trip-links-box {
    pointer-events: auto; width: 100%; max-width: 880px;
    background: var(--card); border: 1px solid var(--line); border-bottom: 0;
    border-radius: 16px 16px 0 0; box-shadow: var(--shadow);
    padding-bottom: env(safe-area-inset-bottom, 0px);
  }
  .modal-body.trip-links-list { display: flex; flex-direction: column; gap: 8px; }
  .trip-links-list .link-item { background: var(--card); }
  .trip-links-bar {
    display: flex; align-items: center; justify-content: center; gap: 7px; width: 100%;
    padding: 14px 16px; background: none; border: 0; color: var(--ink);
    font: 700 14.5px inherit; font-family: inherit; cursor: pointer;
  }
  .trip-links-bar .chev { color: var(--muted); display: inline-flex; }
  body.has-trip-links[data-view="calendar"] { padding-bottom: calc(110px + env(safe-area-inset-bottom, 0px)); }

  /* ---------- cost summary ---------- */
  .summary { max-width: 880px; margin: 20px auto 0; padding: 0 20px; }
  .summary .card { padding: 26px 28px; }
  .summary h2 { font-size: 20px; font-weight: 800; margin-bottom: 16px; }
  .sum-tabs { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
  .sum-tab {
    background: none; border: 1px solid var(--line); color: var(--muted);
    border-radius: 999px; padding: 6px 18px; font-size: 13.5px; font-weight: 700;
    cursor: pointer; font-family: inherit;
  }
  .sum-tab:hover { border-color: var(--accent); color: var(--ink); }
  .sum-tab.active {
    background: var(--accent-fill); border-color: var(--accent-fill); color: var(--on-accent);
  }
  .summary table { width: 100%; border-collapse: collapse; font-size: 14.5px; }
  .summary td { padding: 9px 4px; border-bottom: 1px solid var(--divider); }
  .summary td:last-child { text-align: right; font-weight: 700; white-space: nowrap; }
  .summary td .orig { display: block; font-size: 12px; font-weight: 500; color: var(--muted); }
  .summary tr:last-child td { border-bottom: none; }
  .summary tr.subtotal td { font-weight: 600; }
  .summary tr.total td {
    border-top: 2px solid var(--line); font-size: 16px; font-weight: 800; padding-top: 14px;
  }
  .summary tr.total td:last-child { color: var(--accent); font-size: 19px; }
  .summary .foot { margin-top: 14px; font-size: 12.5px; color: var(--muted); }
  /* the per-category lists exist for the PDF only */
  #sumExtra { display: none; }
  .badge {
    display: inline-block; font-size: 11px; font-weight: 700; border-radius: 999px;
    padding: 2px 10px; margin-right: 8px; color: var(--on-type); vertical-align: 1px;
    background: var(--c, var(--custom));
  }

  /* ---------- modals ---------- */
  body.modal-lock { overflow: hidden; }
  .modal-overlay {
    position: fixed; inset: 0; z-index: 120; background: var(--overlay);
    display: none; align-items: center; justify-content: center; padding: 18px;
  }
  .modal-overlay.open { display: flex; }
  #cardModal { z-index: 140; }
  .modal-box {
    width: 100%; max-width: 520px; max-height: 86vh; display: flex; flex-direction: column;
    background: var(--bg); border: 1px solid var(--line); border-radius: 16px;
    box-shadow: 0 18px 50px rgba(0, 0, 0, 0.5); overflow: hidden;
  }
  .modal-box.wide { max-width: 780px; }
  .modal-bar {
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    padding: 14px 18px; background: var(--card); border-bottom: 1px solid var(--divider);
  }
  .modal-title { font-size: 15.5px; font-weight: 800; }
  .modal-close {
    background: none; border: 1px solid var(--line); color: var(--ink);
    border-radius: 999px; padding: 5px 13px; font-size: 13px; font-weight: 700;
    cursor: pointer; font-family: inherit; white-space: nowrap;
  }
  .modal-close:hover { border-color: var(--accent); }
  .modal-body { padding: 16px 18px 22px; overflow-y: auto; -webkit-overflow-scrolling: touch; }

  /* ---------- compact cards ---------- */
  .mini {
    display: flex; align-items: center; gap: 13px; width: 100%; text-align: left;
    background: var(--card); border: 1px solid var(--divider);
    border-left: 3px solid var(--c, var(--custom)); border-radius: 12px;
    padding: 12px 14px; margin-bottom: 10px; cursor: pointer; font: inherit; color: var(--ink);
  }
  .mini:hover { border-color: var(--accent); }
  .mini-ico {
    flex: 0 0 auto; width: 36px; height: 36px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 17px; line-height: 1; background: var(--c-soft, var(--custom-soft));
  }
  .mini-body { flex: 1 1 auto; min-width: 0; }
  .mini-meta {
    display: block; font-size: 10.5px; font-weight: 700; letter-spacing: 0.1em;
    text-transform: uppercase; line-height: 1.4; color: var(--c, var(--muted));
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .mini-name {
    display: block; font-size: 15px; font-weight: 700; line-height: 1.35;
    letter-spacing: -0.01em; margin-top: 1px;
  }
  /* the card's own heads-up, wherever it is listed */
  .mini-alert, .card-alert, .swhat .alert {
    display: block; color: var(--alert); font-weight: 700; line-height: 1.35;
  }
  .mini-alert { font-size: 13px; margin-top: 2px; }
  .swhat .alert { font-size: 13.5px; margin-top: 1px; }
  .card-alert { font-size: 15px; padding: 0 20px 2px; }
  .mini-map {
    flex: 0 0 auto; font-size: 13px; line-height: 1; text-decoration: none;
    border: 1px solid var(--line); border-radius: 999px; padding: 7px 10px; color: var(--ink);
  }
  .mini-map:hover { border-color: var(--accent); }
  .mini-chev { color: var(--muted); font-size: 18px; line-height: 1; flex: 0 0 auto; }
  .day-note { font-size: 13.5px; color: var(--muted); padding: 4px 2px; line-height: 1.6; }

  /* Where you are staying rides above the day and doesn't scroll away; the
     part-of-day headings then stick directly under it. --pin-h is measured and
     set by openDay, since the block is one row on most days and two on a
     change-over day. */
  /* no padding of its own: the sticky blocks below own the top edge, or a card
     would show through the gap above them as it scrolls past */
  #dayBody { padding-top: 0; }
  .day-pinned {
    position: sticky; top: 0; z-index: 3;
    background: var(--bg); padding: 14px 0 12px; margin-bottom: 4px;
    border-bottom: 1px solid var(--divider);
  }
  .day-pinned .mini { margin-bottom: 8px; }
  .day-pinned .mini:last-child { margin-bottom: 0; }
  .day-sec, .day-pin-title {
    color: var(--accent); text-align: center;
    font-size: 13px; font-weight: 800; letter-spacing: 0.16em; text-transform: uppercase;
  }
  /* the heading rides inside the pinned block, so it needs no stickiness of
     its own — and must not take the sections' offset, which sits below it */
  .day-pin-title { padding: 0 2px 10px; }
  .day-sec {
    position: sticky; top: var(--pin-h, 0px); z-index: 2;
    background: var(--bg); padding: 15px 2px 9px;
  }
  .entry.in-modal { padding-left: 0; margin-bottom: 0; }
  .entry.in-modal .node { display: none; }

  /* ---------- document viewer ---------- */
  .viewer-overlay {
    position: fixed; inset: 0; z-index: 200; background: var(--overlay-strong);
    display: none; overflow-y: auto; -webkit-overflow-scrolling: touch;
  }
  .viewer-overlay.open { display: block; }
  .viewer-bar {
    position: sticky; top: 0; z-index: 201; display: flex; align-items: center;
    justify-content: space-between; gap: 12px; background: var(--card);
    border-bottom: 1px solid var(--divider); padding: 12px 18px;
  }
  .viewer-shell { max-width: 920px; margin: 0 auto; padding: 20px 14px 60px; }
  .viewer-doc { margin-bottom: 30px; }
  .viewer-doc .doc-head {
    display: flex; justify-content: space-between; align-items: baseline;
    gap: 10px 14px; flex-wrap: wrap; margin-bottom: 10px;
  }
  .viewer-doc .doc-name {
    font-weight: 700; font-size: 14.5px; color: #fff; text-shadow: 0 1px 3px rgba(0, 0, 0, 0.6);
  }
  .viewer-doc a.dl {
    color: var(--accent); font-size: 13px; font-weight: 700; text-decoration: none;
    background: var(--card); border: 1px solid var(--line); border-radius: 999px; padding: 5px 14px;
  }
  .viewer-doc a.dl:hover { border-color: var(--accent); }
  .viewer-doc img.page {
    width: 100%; display: block; margin: 0 0 12px; border-radius: 8px;
    background: #fff; border: 1px solid var(--divider);
  }
  /* "page 3 / 24" on each page of a multi-page file */
  .viewer-doc .page-box { position: relative; }
  /* until it loads, a page holds an A4-shaped space, so the page labels don't pile
     up at the bottom and the scroll position doesn't jump (a loaded image keeps its
     own shape: `auto` prefers the picture's ratio) */
  .viewer-doc .page-box img.page { aspect-ratio: auto 210 / 297; }
  .viewer-doc .page-no {
    position: absolute; right: 8px; bottom: 20px; padding: 2px 9px; border-radius: 999px;
    background: rgba(0, 0, 0, 0.62); color: #fff; font-size: 12px; font-weight: 700;
  }
  /* Tabs for a document with several files: one row that scrolls sideways, each
     tab showing its file's FULL name (never cut off, by design), stuck under the
     title bar (its height is set by openDocs). The picked tab is scrolled into view. */
  .doc-tabs {
    position: sticky; z-index: 2; display: flex; flex-wrap: nowrap; gap: 7px;
    overflow-x: auto; overscroll-behavior-x: contain; -webkit-overflow-scrolling: touch;
    scrollbar-width: thin;
    margin: -20px -14px 16px; padding: 10px 14px; background: var(--bg);
    border-bottom: 1px solid var(--divider);
  }
  .doc-tab {
    flex: 0 0 auto; white-space: nowrap;
    font: inherit; font-size: 13px; font-weight: 600; color: var(--ink);
    background: var(--card); border: 1px solid var(--line); border-radius: 999px;
    padding: 6px 13px; cursor: pointer;
  }
  .doc-tab:hover { border-color: var(--accent); }
  .doc-tab.on { background: var(--accent-fill); border-color: var(--accent-fill); color: var(--on-accent); }

  @media (max-width: 620px) {
    .cal-wrap { padding: 0 12px; }
    .cal-grid { gap: 4px; }
    /* The date is the thing you read in this grid, so it gets the room. Two
       things paid for it: the dots stopped wrapping (at 6px with a 3px gap a
       busy day took a second row, costing 9px — more than the number needed),
       and the cell stopped being square, below. The number is limited by the
       cell's *width*, not its height: at 22px a two-digit date measures 23.8px
       against 32.9px of inner width on a 320px phone, which is the tightest
       this gets. 24px would still fit, with 7px to spare instead of 9. */
    .cal-num { font-size: 22px; }
    /* Not square on a phone. Seven columns fix the width at about 47px, so the
       square caps the height at 47 too and the grid finishes less than halfway
       down the screen — on a 375x667 phone the legend ended at 491 with 176px
       of nothing under it. A 4:5 cell spends that space on the thing you came
       to read. The ceiling is a six-row month, the tallest a calendar gets: at
       4:5 its legend lands at 662 of 667, so even that still fits unscrolled. */
    .cal-day { aspect-ratio: 4 / 5; border-radius: 9px; gap: 3px; padding: 3px; }
    .dot { width: 5px; height: 5px; }
    /* 1.5px, not 2: six dots at 5px plus five 2px gaps come to 40px and the
       cell's inner width is 39, so they wrapped by a single pixel. */
    .cal-dots { gap: 1.5px; }
    .entry { padding-left: 66px; }
    .entry .node { left: 8px; width: 46px; height: 46px; font-size: 21px; }
    .sheet-wrap { padding: 0 10px; }
    /* The five columns want 426px of fixed width before the title gets any, so
       on a phone the title — the one column you actually read — is squeezed to
       nothing and wraps a letter at a time. Nothing overflows, so it looks fine
       until you try to read it. Two columns earn their keep least: the section
       repeats one word down every row of a group, and the type is short text in
       a wide cell. Both move into the rows themselves here (see `sheetRows`),
       which leaves the title around 268px instead of 130px. */
    .c-sec { width: 0; }  .c-time { width: 48px; }
    .c-kind { width: 0; } .c-map  { width: 36px; }
    /* Collapse those two cells — never `display: none` them. A cell removed that
       way leaves its row, and every cell after it slides one column to the left,
       which quietly puts the title in the time column and the pin in the type's.
       (The print block gets away with `display: none` on .smap only because the
       map is the last column, with nothing behind it to shift.) With the col at
       width 0 and the padding gone, these take up nothing and show nothing. */
    /* Deliberately one class deep, no `.sheet-day` in front: the print block
       restates `.sec` and `.skind` at the same specificity and has to be able to
       win on order when a phone prints and both blocks apply. */
    .sec, .skind {
      width: 0; padding: 0; font-size: 0; line-height: 0;
      border-right: 0; background: none; overflow: hidden;
    }
    .sec-row { display: table-row; }
    .sec-row th {
      /* one shade off --card, so the band separates without becoming a slab.
         The label is the accent rather than --ink: at --ink it matched the card
         titles directly under it and the two stopped telling each other apart. */
      text-align: center; color: var(--band-ink);
      background: var(--band); padding: 7px 10px;
      font-size: 10.5px; font-weight: 800; letter-spacing: 0.22em; text-transform: uppercase;
      border-top: 1px solid var(--line) !important;
      border-bottom: 1px solid var(--line);
    }
    /* the hotel band is not a part of the day, so it keeps the hotel's colour */
    .sec-row.pin th { color: var(--band-pin-ink); }
    .swhat .kindline {
      display: block; margin-bottom: 2px; color: var(--c, var(--muted));
      font-size: 9px; font-weight: 800; letter-spacing: 0.06em; text-transform: uppercase;
    }
    .swhat .kindline .state { color: var(--muted); }
    .date-row th { font-size: 13.5px; padding: 9px 10px; }
    .date-row .sep { margin: 0 4px; }
    .stime { font-size: 12.5px; padding: 8px 3px; }
    .swhat { font-size: 13.5px; padding: 8px; }
    .smap { padding: 6px 2px; }
    .smap a { padding: 5px 6px; font-size: 13px; }
    .day-pick { max-width: 58%; font-size: 12.5px; }
    .journey { grid-template-columns: 1fr; }
    .point.arrive { text-align: left; }
    .mid { display: none; }
    .point .time { font-size: 26px; }
    .modal-overlay { padding: 10px; }
    .modal-body { padding: 14px 12px 20px; }
    .mini-name { font-size: 14px; }
    .summary .card { padding: 20px 16px; }
  }
  /* Below about 360px the cell is narrow enough that even 5px dots wrap again.
     The cell is deliberately taller than wide now so a second row no longer
     distorts it, but it eats the height the date wants. One step smaller keeps
     the dots on one line. Down at 280px (a folded phone's cover screen) the
     cell is 33px and the widest date, "24", measures 26.8 against 27.1px of
     inner width — 0.3px. Nothing clips if a font stack runs wider than that:
     the number is centred with no `overflow` on the cell, so it bleeds into
     2px of padding, and the track can't grow because it is minmax(0, 1fr). */
  @media (max-width: 360px) {
    .dot { width: 4px; height: 4px; }
    .cal-day { gap: 2px; padding: 2px; }
  }
  /* ---------- print / PDF ----------
     Paper wants no ink it can't use: white page, black rules, black text, no
     fills, no icons. Only the schedule goes to paper — the calendar and the
     card windows are things you tap, not things you read on a sheet.

     This block is theme-blind by construction: every rule here is a literal
     black or white, never a token, so the printed sheet is identical whether
     the screen was light, dark or following the phone. */
  @media print {
    @page { margin: 13mm; }
    body { background: #fff !important; color: #000 !important; padding: 0; }
    .top-btn, .theme-btn, .view-tabs, #calendarView, .modal-overlay,
    .viewer-overlay, .sheet-actions, .trip-links { display: none !important; }
    /* print what is on screen: the schedule, unless you are on the cost view */
    #scheduleView { display: block !important; }
    #costView { display: none !important; }
    body[data-view="cost"] #scheduleView { display: none !important; }
    body[data-view="cost"] #costView { display: block !important; }
    .sheet-wrap { max-width: none; margin: 0; padding: 0; }
    header { padding: 0 0 10px; }
    header .eyebrow, header h1, header .trip-meta, header .tag-row,
    header .chip, header .sepdot { color: #000 !important; }
    header .eyebrow { letter-spacing: 0.22em; font-size: 10px; }
    header h1 { font-size: 21px; }
    header .trip-meta, header .chip { font-size: 11.5px; }
    header .chip svg { display: none; }     /* flags are ink, not information */
    /* paper has the width, so the names come back even past the screen budget */
    header .tag-row.flags-only .chip span { display: inline !important; }

    .sheet-day {
      background: #fff !important; border: 1px solid #000 !important;
      border-radius: 0 !important; margin-bottom: 13px; page-break-inside: auto;
    }
    .date-row th {
      background: #fff !important; color: #000 !important;
      border-bottom: 1px solid #000 !important; font-size: 12.5px; padding: 6px 9px;
    }
    .date-row .dayno, .date-row .sep { color: #000 !important; }
    .sheet-day td, .sheet-day th { border-top: 1px solid #000 !important; color: #000 !important; }
    /* Paper is wide whatever it is printed from, so put the section and the type
       back in their columns. Printing from a phone matches the 620px block too,
       and this comes after it. */
    .sheet-day th.sec, .sheet-day td.skind {
      width: auto; line-height: inherit; overflow: visible;
    }                                    /* .sec / .skind below restore the rest */
    .sec-row, .swhat .kindline { display: none !important; }
    .c-sec { width: 88px; } .c-time { width: 58px; } .c-kind { width: 118px; }
    .c-map { width: 0; }                 /* collapse the column, not just its cells */
    .sec {
      background: #fff !important; color: #000 !important;
      border-right: 1px solid #000 !important; border-top: 1px solid #000 !important;
      font-size: 9.5px; letter-spacing: 0.1em; padding: 5px;
    }
    .stime, .stime.none { color: #000 !important; font-size: 11px; padding: 5px; }
    .skind, .skind .state {
      color: #000 !important; font-size: 9.5px; letter-spacing: 0.06em; padding: 5px 5px 5px 9px;
    }
    .swhat { font-size: 11.5px; padding: 5px 9px; }
    /* the heads-up keeps its weight without spending red ink */
    .swhat .alert { color: #000 !important; font-size: 10.5px; font-weight: 800; }
    .smap { display: none !important; }  /* a tappable pin is no use on paper */
    .sheet-note td { color: #000 !important; font-size: 10.5px; padding: 5px 9px; }
    tr { page-break-inside: avoid; }

    /* the cost summary, same ink-free treatment */
    .summary { max-width: none; margin: 0; padding: 0; }
    /* The PDF is one list per category and a grand total. The screen table is
       dropped entirely: the categories already account for every priced card,
       and that table carries whatever the tabs happen to be filtering. */
    #sumTable { display: none !important; }
    #sumExtra { display: block !important; }
    .summary h3 {
      color: #000 !important; font-size: 12px; font-weight: 800;
      letter-spacing: 0.1em; text-transform: uppercase;
      margin: 14px 0 5px; padding-top: 8px; border-top: 1px solid #000;
    }
    .sum-section:first-child h3 { margin-top: 4px; padding-top: 0; border-top: 0; }
    .sum-section { page-break-inside: auto; }
    .sum-grand { margin-top: 10px; border-top: 2px solid #000; }
    .sum-grand td { border-bottom: 0 !important; padding-top: 7px !important; }
    .summary .card {
      background: #fff !important; border: 1px solid #000 !important;
      border-radius: 0 !important; box-shadow: none !important; padding: 10px 12px;
    }
    .summary h2, .summary h2 span { color: #000 !important; font-size: 14px; }
    .summary .sum-tabs { display: none !important; }   /* filters need a pointer */
    /* the type badge is a filled colour chip — the cheapest thing to print is
       its text alone */
    .summary .badge {
      background: none !important; color: #000 !important;
      border: 0 !important; padding: 0 6px 0 0 !important;
      font-size: 9.5px; letter-spacing: 0.06em;
    }
    .summary td, .summary td .orig { color: #000 !important; font-size: 11.5px; padding: 4px 3px; }
    .summary td { border-bottom: 1px solid #000 !important; }
    .summary tr.total td, .summary tr.total td:last-child {
      color: #000 !important; font-size: 13px; font-weight: 800;
    }
    .summary .foot { color: #000 !important; font-size: 10px; }
  }
"""

PAGE_JS = r"""
(function () {
  var T = TRIP, TM = TYPE_META;
  var MONTHS = ['January','February','March','April','May','June','July',
                'August','September','October','November','December'];
  var MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  var DAYS = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
  var DAY3 = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  var DOW = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];

  /* ---------- helpers ---------- */
  function pad(n) { return n < 10 ? '0' + n : '' + n; }
  function parseD(s) { var p = String(s).split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function ymd(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function addDays(d, n) { var x = new Date(d.getTime()); x.setDate(x.getDate() + n); return x; }
  function dayDiff(a, b) { return Math.round((parseD(b) - parseD(a)) / 86400000); }
  function fmtLong(s) { var d = parseD(s); return DAYS[d.getDay()] + ', ' + d.getDate() + ' ' + MONTHS[d.getMonth()] + ' ' + d.getFullYear(); }
  function fmtMed(s) { var d = parseD(s); return DAY3[d.getDay()] + ' ' + d.getDate() + ' ' + MON[d.getMonth()]; }
  function toMin(t) { var p = String(t).split(':'); return (+p[0]) * 60 + (+p[1] || 0); }
  function money(n) { return 'NT$' + Number(n).toLocaleString('en-US'); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function link(href, cls, text) {
    var a = el('a', cls, text);
    a.href = href; a.target = '_blank'; a.rel = 'noopener';
    return a;
  }
  /* [{text, url}] -> only real web addresses, each with something to show:
     a blank text becomes the site's name. The server cleans these too. */
  function hostOf(u) {
    try { return new URL(u).hostname.replace(/^www\./, ''); } catch (e) { return u; }
  }
  function linksOf(list) {
    return (list || []).filter(function (l) { return l && /^https?:\/\//i.test(l.url || ''); })
      .map(function (l) { return { url: l.url, text: String(l.text || '').trim() || hostOf(l.url) }; });
  }
  function linkItem(l) { return link(l.url, 'link-item', '🔗 ' + l.text); }
  /* A transit card's mode (train, bus, ferry, car) replaces the type's label and icon.
     Mirrors `card_meta` in triptypes.py — change one, change the other. */
  function metaOf(c) {
    var m = TM[c.type] || TM.custom, md = m.modes && c.mode && m.modes[c.mode];
    if (!md) return m;
    var out = {}, k;
    for (k in m) out[k] = m[k];
    out.label = md.label; out.icon = md.icon;
    return out;
  }
  function typeLabel(c) { return c.type_label || metaOf(c).label; }

  /* ---------- prepare cards ---------- */
  /* Every day a card spans. Mirrored by _occupied_days() in main.py. */
  function datesOf(c) {
    var kind = metaOf(c).kind, out = [], d;
    var s = parseD(c.start_date);
    var e = parseD(c.end_date || c.start_date);
    if (kind === 'stay') {
      for (d = new Date(s); d < e; d = addDays(d, 1)) out.push(ymd(d));
      if (!out.length) out.push(ymd(s));
    } else if (kind === 'hire') {
      for (d = new Date(s); d <= e; d = addDays(d, 1)) out.push(ymd(d));
    } else if (kind === 'move') {
      out.push(ymd(s));
      if (c.end_date && c.end_date !== c.start_date) out.push(ymd(e));
    } else {
      out.push(ymd(s));
    }
    return out;
  }

  /* The days it is actually listed on: the span minus the ones the editor hid,
     so a 10-day rental can show only its pick-up and drop-off. The full span
     stays on _all, because the wording ("ongoing", "drop-off") reads from it. */
  function shownDates(c) {
    var hidden = c.hide_days || [];
    var out = c._all.filter(function (k) { return hidden.indexOf(k) === -1; });
    return out.length ? out : c._all.slice(0, 1);
  }

  /* ---------- parts of a day ---------- */
  var NO_TIME_SORT = 24 * 60;
  var SECTION_AT = {};
  SECTION_ORDER.forEach(function (s, i) { SECTION_AT[s[0]] = i; });

  /* Mirrors section_of() in triptypes.py. Meals are only ever chosen by hand. */
  function sectionOf(c) {
    var chosen = c.section || '';
    if (chosen && SECTION_AT[chosen] !== undefined) return chosen;
    if (!c.start_time) return '';
    var at = toMin(c.start_time);
    if (at < DAY_SPLITS[0]) return 'morning';
    return at < DAY_SPLITS[1] ? 'afternoon' : 'evening';
  }

  /* The clock decides the order until someone drags a card; `sort` then holds
     the position they put it in. Mirrors _sort_key() in main.py. */
  function orderOf(c) {
    var at = typeof c.sort === 'number' ? c.sort
           : (c.start_time ? toMin(c.start_time) : NO_TIME_SORT);
    return [SECTION_AT[sectionOf(c)], at, c.start_time || '~', c.title || ''];
  }

  function byOrder(a, b) {
    var x = a._order, y = b._order;
    for (var i = 0; i < x.length; i++) {
      if (x[i] !== y[i]) return x[i] < y[i] ? -1 : 1;
    }
    return 0;
  }

  var cards = (T.cards || []).slice();
  cards.forEach(function (c) { c._order = orderOf(c); });
  cards.sort(function (a, b) {
    if (a.start_date !== b.start_date) return a.start_date < b.start_date ? -1 : 1;
    return byOrder(a, b);
  });
  cards.forEach(function (c, i) { c._i = i; c._all = datesOf(c); c._dates = shownDates(c); });

  var tripDates = [];
  if (T.start_date && T.end_date) {
    for (var d = parseD(T.start_date), e = parseD(T.end_date); d <= e; d = addDays(d, 1)) {
      tripDates.push(ymd(d));
    }
  }
  function cardsOn(key) {
    return cards.filter(function (c) { return c._dates.indexOf(key) !== -1; });
  }

  /* ---------- wording ---------- */
  function durationOf(c) {
    var m = metaOf(c);
    if (m.kind === 'stay') {
      var n = Math.max(1, dayDiff(c.start_date, c.end_date || c.start_date));
      return n + (n === 1 ? ' night' : ' nights');
    }
    if (m.kind === 'hire') {
      var days = dayDiff(c.start_date, c.end_date || c.start_date) + 1;
      return days + (days === 1 ? ' day' : ' days');
    }
    if (m.kind === 'move' && c.start_time && c.end_time) {
      var diff = toMin(c.end_time) - toMin(c.start_time);
      if (c.end_date && c.end_date !== c.start_date) diff += dayDiff(c.start_date, c.end_date) * 1440;
      if (diff < 0) diff += 1440;
      var h = Math.floor(diff / 60), mm = diff % 60;
      return (h ? h + ' h ' : '') + (mm ? mm + ' m' : '') || '0 m';
    }
    return '';
  }

  function headerDate(c) {
    var m = metaOf(c);
    if (m.kind === 'stay' || m.kind === 'hire') {
      var dur = durationOf(c);
      return fmtMed(c.start_date) + ' – ' + fmtMed(c.end_date || c.start_date) +
             (dur ? ' · ' + dur : '');
    }
    if (m.kind === 'move' && c.end_date && c.end_date !== c.start_date) {
      return fmtLong(c.start_date) + ' → ' + fmtMed(c.end_date);
    }
    return fmtLong(c.start_date);
  }

  function miniMeta(c, key) {
    var m = metaOf(c), label = typeLabel(c);
    /* against the full span: a shown middle day is still "ongoing" */
    var first = c._all[0] === key, last = c._all[c._all.length - 1] === key;
    if (m.kind === 'move') {
      if (first && c.start_time && c.end_time) {
        var over = c.end_date && c.end_date !== c.start_date;
        return label + ' · ' + c.start_time + ' → ' + c.end_time + (over ? ' +1d' : '');
      }
      if (!first && c.end_time) return label + ' · arrives ' + c.end_time;
      return label + (c.start_time ? ' · ' + c.start_time : '');
    }
    if (m.kind === 'hire') {
      if (first) return label + ' · pickup' + (c.start_time ? ' ' + c.start_time : '');
      if (last) return label + ' · drop-off' + (c.end_time ? ' ' + c.end_time : '');
      return label + ' · ongoing';
    }
    if (m.kind === 'stay') {
      if (first) return label + ' · check-in' + (c.start_time ? ' ' + c.start_time : '');
      return label + ' · staying';
    }
    return label + (c.start_time ? ' · ' + c.start_time : '');
  }

  function primaryMap(c) { return c.map_link || c.from_map || c.to_map || ''; }

  /* ---------- full card ---------- */
  function point(cls, time, city, sub, mapHref) {
    var p = el('div', 'point ' + cls);
    p.appendChild(el('div', 'time', time || '—'));
    if (city) p.appendChild(el('div', 'city', city));
    if (sub || mapHref) {
      var s = el('div', 'station');
      if (mapHref) s.appendChild(link(mapHref, 'stn-link', '📍 ' + (sub || 'Map')));
      else s.appendChild(document.createTextNode(sub));
      p.appendChild(s);
    }
    return p;
  }

  function buildJourney(c) {
    var m = metaOf(c), j = el('div', 'journey');
    if (m.kind === 'move' || m.kind === 'hire') {
      var isHire = m.kind === 'hire';
      j.appendChild(point('depart', c.start_time, c.from_label || (isHire ? 'Pick-up' : 'Departure'),
                          c.from_sub, c.from_map));
      var mid = el('div', 'mid');
      var dur = durationOf(c);
      if (dur) mid.appendChild(el('div', 'duration', dur));
      mid.appendChild(el('div', 'track'));
      if (c.service) mid.appendChild(el('div', 'service', c.service));
      j.appendChild(mid);
      j.appendChild(point('arrive', c.end_time, c.to_label || (isHire ? 'Drop-off' : 'Arrival'),
                          c.to_sub, c.to_map));
      return j;
    }
    if (m.kind === 'stay') {
      j.appendChild(point('depart', c.start_time, 'Check-in · ' + fmtMed(c.start_date)));
      var mid2 = el('div', 'mid');
      mid2.appendChild(el('div', 'duration', durationOf(c)));
      mid2.appendChild(el('div', 'track'));
      if (c.service) mid2.appendChild(el('div', 'service', c.service));
      j.appendChild(mid2);
      j.appendChild(point('arrive', c.end_time, 'Check-out · ' + fmtMed(c.end_date || c.start_date)));
      return j;
    }
    j.classList.add('single');
    j.appendChild(point('depart', c.start_time, c.place || fmtMed(c.start_date),
                        c.end_time ? 'until ' + c.end_time : ''));
    return j;
  }

  function buildEntry(c) {
    var m = metaOf(c);
    var entry = el('div', 'entry');
    entry.setAttribute('data-c', c.type);
    entry.setAttribute('data-dates', c._dates.join(' '));
    entry.setAttribute('data-idx', c._i);
    entry.appendChild(el('div', 'node', c.icon || m.icon));

    var card = el('div', 'card');
    var top = el('div', 'card-top');
    top.appendChild(el('span', 'date', headerDate(c)));
    top.appendChild(el('span', 'tag', typeLabel(c) + (c.service ? ' · ' + c.service : '')));
    var docs = c.docs || [];
    if (docs.length) {
      top.appendChild(el('span', 'doc-chip', '📎 ' + docs.length + (docs.length === 1 ? ' doc' : ' docs')));
      card.classList.add('clickable');
      card.title = 'Click to open the documents for this booking';
    }
    card.appendChild(top);

    if (m.kind === 'stay' || m.kind === 'point') {
      var nameLine = el('div', 'place-name');
      var left = el('span');
      left.appendChild(document.createTextNode(c.title || typeLabel(c)));
      if (c.place) left.appendChild(el('span', 'place-city', ' · ' + c.place));
      nameLine.appendChild(left);
      if (c.map_link) nameLine.appendChild(link(c.map_link, 'map-btn', '📍 Google Maps'));
      card.appendChild(nameLine);
    }
    if (c.alert) card.appendChild(el('div', 'card-alert', c.alert));

    card.appendChild(buildJourney(c));

    var lines = [];
    if (c.address) lines.push(c.address);
    (c.details || []).forEach(function (d) { if (d) lines.push(d); });
    if (lines.length) {
      var det = el('div', 'details');
      lines.forEach(function (t) { det.appendChild(el('span', null, t)); });
      card.appendChild(det);
    }

    var links = linksOf(c.links);
    if (links.length) {
      var lb = el('div', 'card-links');
      lb.appendChild(el('div', 'links-label', 'Links'));
      links.forEach(function (l) { lb.appendChild(linkItem(l)); });
      card.appendChild(lb);
    }

    if (c.price_ntd || c.price_original || c.price_note) {
      var pr = el('div', 'price-row');
      pr.appendChild(el('span', 'label', c.price_label || 'Price'));
      if (c.price_note) pr.appendChild(el('span', 'note', c.price_note));
      var amt = el('span', 'amount', c.price_ntd ? money(c.price_ntd) : '—');
      if (c.price_original) amt.appendChild(el('small', null, '(' + c.price_original + ')'));
      pr.appendChild(amt);
      card.appendChild(pr);
    }

    entry.appendChild(card);
    if (docs.length) {
      card.addEventListener('click', function (ev) {
        if (ev.target.closest('.map-btn, .stn-link, .link-item')) return;
        openDocs(c);
      });
    }
    return entry;
  }

  /* ---------- schedule sheet ----------
     One table per day: the part of the day merged down the left, then the
     time, what kind of thing it is, its name and its map pin. */

  function mdY(key) {
    var d = parseD(key);
    return pad(d.getMonth() + 1) + '/' + pad(d.getDate()) + '/' + d.getFullYear();
  }

  /* the time that matters on *this* day — a hire's drop-off, a stay's check-in */
  function sheetTime(c, key) {
    var m = metaOf(c), first = c._all[0] === key, last = c._all[c._all.length - 1] === key;
    if (m.kind === 'move' || m.kind === 'hire') {
      if (first) return c.start_time || '';
      return last ? (c.end_time || '') : '';
    }
    if (m.kind === 'stay') return first ? (c.start_time || '') : '';
    return c.start_time || '';
  }

  /* and what it is doing on this day, when that isn't simply "happening" */
  function sheetState(c, key) {
    var m = metaOf(c), first = c._all[0] === key, last = c._all[c._all.length - 1] === key;
    if (m.kind === 'hire') return first ? 'pick-up' : (last ? 'drop-off' : 'ongoing');
    if (m.kind === 'stay') return first ? 'check-in' : 'staying';
    if (m.kind === 'move' && !first) return 'arrives';
    return '';
  }

  /* Every group is written twice over: once as the merged left-hand column that
     a wide screen and paper use, and once as a full-width band plus a type line
     inside each title cell, which is what a phone gets. The CSS picks one — see
     the note by `.sec-row` in the stylesheet for why it isn't done by rebuilding
     the table. `pinned` only marks the hotel band so it can keep its own colour. */
  function sheetRows(tb, label, list, key, pinned) {
    var band = el('tr', 'sec-row' + (pinned ? ' pin' : ''));
    var bandCell = el('th', null, label);
    bandCell.colSpan = 5;
    band.appendChild(bandCell);
    tb.appendChild(band);

    list.forEach(function (c, i) {
      var tr = el('tr');
      if (i === 0) {
        var sec = el('th', 'sec', label);
        sec.rowSpan = list.length;
        tr.appendChild(sec);
      }

      var at = sheetTime(c, key);
      tr.appendChild(el('td', 'stime' + (at ? '' : ' none'), at || '—'));

      var state = sheetState(c, key);
      var kind = el('td', 'skind', typeLabel(c));
      kind.style.setProperty('--c', 'var(--' + c.type + ')');
      if (state) kind.appendChild(el('span', 'state', ' · ' + state));
      tr.appendChild(kind);

      var what = el('td', 'swhat');
      var line = el('span', 'kindline', typeLabel(c));
      line.style.setProperty('--c', 'var(--' + c.type + ')');
      if (state) line.appendChild(el('span', 'state', ' · ' + state));
      what.appendChild(line);
      what.appendChild(document.createTextNode(c.title || typeLabel(c)));
      if (c.alert) what.appendChild(el('span', 'alert', c.alert));
      tr.appendChild(what);

      var cell = el('td', 'smap');
      var mp = primaryMap(c);
      if (mp) {
        var pin = link(mp, null, '📍');
        pin.title = 'Open in Google Maps';
        cell.appendChild(pin);
      } else {
        cell.appendChild(el('span', 'off', '·'));
      }
      tr.appendChild(cell);
      tb.appendChild(tr);
    });
  }

  function dayTable(key, dayNo) {
    var items = cardsOn(key).slice().sort(byOrder);
    var note = (T.day_notes || {})[key] || '';
    if (!items.length && !note) return null;

    var t = el('table', 'sheet-day');
    t.setAttribute('data-day', key);
    var cg = el('colgroup');
    ['c-sec', 'c-time', 'c-kind', 'c-what', 'c-map'].forEach(function (c) {
      cg.appendChild(el('col', c));
    });
    t.appendChild(cg);

    var th = el('th');
    th.colSpan = 5;
    th.appendChild(el('span', 'dayno', 'Day ' + dayNo));
    th.appendChild(el('span', 'sep', '·'));
    th.appendChild(document.createTextNode(mdY(key)));
    th.appendChild(el('span', 'sep', '·'));
    th.appendChild(document.createTextNode(DAYS[parseD(key).getDay()]));
    var hr = el('tr', 'date-row');
    hr.appendChild(th);
    var thead = el('thead');
    thead.appendChild(hr);
    t.appendChild(thead);

    var tb = el('tbody');
    var stays = items.filter(function (c) { return metaOf(c).kind === 'stay'; });
    var rest = items.filter(function (c) { return metaOf(c).kind !== 'stay'; });
    if (stays.length) sheetRows(tb, PINNED_LABEL, stays, key, true);
    SECTION_ORDER.forEach(function (s) {
      var mine = rest.filter(function (c) { return sectionOf(c) === s[0]; });
      if (mine.length) sheetRows(tb, s[1], mine, key);
    });
    if (note) {
      var nr = el('tr', 'sheet-note');
      var nc = el('td', null, note);
      nc.colSpan = 5;
      nr.appendChild(nc);
      tb.appendChild(nr);
    }
    t.appendChild(tb);
    return t;
  }

  function buildSchedule() {
    var host = document.getElementById('sheet');
    var pick = document.getElementById('dayPick');
    var panel = document.getElementById('dayPickPanel');
    var label = document.getElementById('dayPickLabel');
    var names = {}, shown = 0;

    function option(value, text, checked) {
      var row = el('label', 'day-opt' + (value === 'all' ? ' all' : ''));
      var box = el('input');
      box.type = 'checkbox';
      box.value = value;
      box.checked = checked;
      row.appendChild(box);
      row.appendChild(document.createTextNode(text));
      panel.appendChild(row);
      return box;
    }

    tripDates.forEach(function (key, i) {
      var t = dayTable(key, i + 1);
      if (!t) return;
      host.appendChild(t);
      shown++;
      names[key] = 'Day ' + (i + 1) + ' · ' + mdY(key) + ' · ' + DAYS[parseD(key).getDay()];
    });
    if (!shown) {
      host.appendChild(el('p', 'day-note', 'No bookings yet.'));
      return;
    }

    var everyLabel = 'All dates — ' + shown + (shown === 1 ? ' day' : ' days');
    var all = option('all', everyLabel, true);
    var boxes = [];
    tripDates.forEach(function (key) {
      if (names[key]) boxes.push(option(key, names[key], false));
    });

    function picked() {
      var out = [];
      boxes.forEach(function (b) { if (b.checked) out.push(b.value); });
      return out;
    }

    /* No day ticked means the whole trip rather than an empty sheet, so there
       is never a state where the page looks broken. */
    function apply() {
      var want = picked();
      var every = all.checked || !want.length;
      Array.prototype.forEach.call(host.querySelectorAll('.sheet-day'), function (t) {
        t.classList.toggle('hidden', !every && want.indexOf(t.getAttribute('data-day')) === -1);
      });
      label.textContent = every ? everyLabel
        : (want.length === 1 ? names[want[0]]
                             : want.length + ' days selected');
    }

    all.addEventListener('change', function () {
      if (all.checked) boxes.forEach(function (b) { b.checked = false; });
      apply();
    });
    boxes.forEach(function (b) {
      b.addEventListener('change', function () {
        all.checked = !picked().length;
        apply();
      });
    });

    /* <details> has no idea about clicks landing outside it */
    document.addEventListener('click', function (ev) {
      if (pick.open && !pick.contains(ev.target)) pick.removeAttribute('open');
    });
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && pick.open) pick.removeAttribute('open');
    });
    apply();
  }

  /* ---------- calendar ---------- */
  function buildCalendar() {
    var host = document.getElementById('calMonths');
    if (!tripDates.length) return;
    var start = parseD(T.start_date), end = parseD(T.end_date);
    var cur = new Date(start.getFullYear(), start.getMonth(), 1);
    var firstMonth = true;
    while (cur <= end) {
      host.appendChild(buildMonth(cur.getFullYear(), cur.getMonth(), start, end, firstMonth));
      firstMonth = false;
      cur = new Date(cur.getFullYear(), cur.getMonth() + 1, 1);
    }
  }

  /* `first` carries the "tap a day" hint, which rides on the month title rather
     than taking a row of its own — 32px of a phone screen for six words. */
  function buildMonth(year, month, start, end, first) {
    var wrap = el('div', 'cal-month-block');
    var title = el('div', 'cal-month');
    title.appendChild(el('span', null, MONTHS[month] + ' ' + year));
    if (first) title.appendChild(el('span', 'cal-hint', 'Tap a day to see its plan'));
    wrap.appendChild(title);
    var grid = el('div', 'cal-grid');
    DOW.forEach(function (d) { grid.appendChild(el('div', 'cal-dow', d)); });

    var mStart = new Date(year, month, 1), mEnd = new Date(year, month + 1, 0);
    var first = start > mStart ? start : mStart;
    var last = end < mEnd ? end : mEnd;
    var lead = (first.getDay() + 6) % 7;
    var trail = 6 - ((last.getDay() + 6) % 7);
    var i;
    for (i = 0; i < lead; i++) grid.appendChild(blankCell());
    for (var d = new Date(first); d <= last; d = addDays(d, 1)) grid.appendChild(dayCell(d));
    for (i = 0; i < trail; i++) grid.appendChild(blankCell());

    wrap.appendChild(grid);
    return wrap;
  }

  function blankCell() {
    var b = el('button', 'cal-day out');
    b.type = 'button';
    b.disabled = true;
    return b;
  }

  function dayCell(dateObj) {
    var key = ymd(dateObj);
    var cell = el('button', 'cal-day');
    cell.type = 'button';
    cell.appendChild(el('span', 'cal-num', dateObj.getDate()));

    var types = [], dots = el('span', 'cal-dots');
    cardsOn(key).forEach(function (c) { if (types.indexOf(c.type) === -1) types.push(c.type); });
    types.forEach(function (t) {
      var i = el('i', 'dot');
      i.style.setProperty('--c', 'var(--' + t + ')');
      dots.appendChild(i);
    });
    cell.appendChild(dots);

    if (!types.length) cell.classList.add('free');
    var now = new Date();
    if (ymd(now) === key) cell.classList.add('today');
    cell.title = 'Open the plan for ' + fmtMed(key);
    cell.addEventListener('click', function () { openDay(key); });
    return cell;
  }

  /* ---------- cost summary ---------- */
  function buildSummary() {
    var groups = GROUP_ORDER.filter(function (g) {
      return cards.some(function (c) { return metaOf(c).group === g[0] && c.price_ntd; });
    });
    var tabsHost = document.getElementById('sumTabs');
    var table = document.getElementById('sumTable');
    var priced = cards.filter(function (c) { return c.price_ntd; });
    if (!priced.length) {
      document.getElementById('summary').classList.add('hidden');
      return;
    }

    var all = el('button', 'sum-tab active', 'All');
    all.setAttribute('data-filter', 'all');
    tabsHost.appendChild(all);
    groups.forEach(function (g) {
      var b = el('button', 'sum-tab', g[2] + ' ' + g[1]);
      b.setAttribute('data-filter', g[0]);
      tabsHost.appendChild(b);
    });

    function priceRow(c) {
      var tr = el('tr');
      tr.setAttribute('data-cat', metaOf(c).group);
      var td = el('td');
      var badge = el('span', 'badge', typeLabel(c).toUpperCase());
      badge.style.setProperty('--c', 'var(--' + c.type + ')');
      td.appendChild(badge);
      td.appendChild(document.createTextNode(
        (c.title || typeLabel(c)) + ' (' + fmtMed(c.start_date) + ')'
      ));
      tr.appendChild(td);
      var td2 = el('td', null, money(c.price_ntd));
      if (c.price_original) td2.appendChild(el('span', 'orig', c.price_original));
      tr.appendChild(td2);
      return tr;
    }

    function inGroup(key) {
      return priced.filter(function (c) { return metaOf(c).group === key; });
    }
    function totalOf(list) {
      return list.reduce(function (a, c) { return a + (Number(c.price_ntd) || 0); }, 0);
    }
    function subtotalRow(g, list) {
      var n = list.length;
      var tr = el('tr', 'subtotal');
      tr.setAttribute('data-cat', g[0] + '-sum');
      tr.appendChild(el('td', null, g[1] + ' subtotal (' + n + (n === 1 ? ' item)' : ' items)')));
      tr.appendChild(el('td', null, money(totalOf(list))));
      return tr;
    }

    var grand = totalOf(priced);
    priced.forEach(function (c) { table.appendChild(priceRow(c)); });
    groups.forEach(function (g) { table.appendChild(subtotalRow(g, inGroup(g[0]))); });

    /* Printed only: one list per category, which between them account for every
       priced card — so the PDF needs no combined list. The tabs filter by
       hiding rows in the table above, and a row can't be in two places at
       once, hence this second copy. */
    var extra = document.getElementById('sumExtra');
    groups.forEach(function (g) {
      var mine = inGroup(g[0]);
      if (!mine.length) return;
      var block = el('div', 'sum-section');
      block.appendChild(el('h3', null, g[1]));   /* no icon — this is for paper */
      var t = el('table');
      mine.forEach(function (c) { t.appendChild(priceRow(c)); });
      t.appendChild(subtotalRow(g, mine));
      block.appendChild(t);
      extra.appendChild(block);
    });
    /* the grand total lives in the screen table, which the PDF drops */
    var grandTable = el('table', 'sum-grand');
    var grandRow = el('tr', 'total');
    grandRow.appendChild(el('td', null, 'Grand total'));
    grandRow.appendChild(el('td', null, '≈ ' + money(grand)));
    grandTable.appendChild(grandRow);
    extra.appendChild(grandTable);

    var total = el('tr', 'total');
    total.setAttribute('data-cat', 'grand');
    total.appendChild(el('td', null, 'Grand total'));
    total.appendChild(el('td', null, '≈ ' + money(grand)));
    table.appendChild(total);

    tabsHost.addEventListener('click', function (ev) {
      var btn = ev.target.closest('.sum-tab');
      if (!btn) return;
      Array.prototype.forEach.call(tabsHost.querySelectorAll('.sum-tab'),
        function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      var mode = btn.getAttribute('data-filter');
      Array.prototype.forEach.call(table.querySelectorAll('tr'), function (tr) {
        var cat = tr.getAttribute('data-cat');
        tr.style.display = (mode === 'all' || cat === mode || cat === mode + '-sum') ? '' : 'none';
      });
    });
  }

  /* ---------- modals ---------- */
  function lockBody() { document.body.classList.add('modal-lock'); }
  function unlockIfIdle() {
    if (!document.querySelector('.modal-overlay.open')) document.body.classList.remove('modal-lock');
  }
  window.closeDay = function () {
    document.getElementById('dayModal').classList.remove('open');
    unlockIfIdle();
  };
  window.closeCard = function () {
    document.getElementById('cardModal').classList.remove('open');
    unlockIfIdle();
  };
  window.openDay = function (key) { openDay(key); };
  window.openCard = function (c) { openCard(c); };     /* the editor preview uses this */

  function openDay(key) {
    var d = parseD(key);
    document.getElementById('dayTitle').textContent =
      DAYS[d.getDay()] + ', ' + d.getDate() + ' ' + MON[d.getMonth()] + ' ' + d.getFullYear();

    var items = cardsOn(key).slice().sort(byOrder);
    var body = document.getElementById('dayBody');
    body.innerHTML = '';

    function miniRow(c) {
      var row = el('button', 'mini');
      row.type = 'button';
      row.style.setProperty('--c', 'var(--' + c.type + ')');
      row.style.setProperty('--c-soft', 'var(--' + c.type + '-soft)');

      row.appendChild(el('span', 'mini-ico', c.icon || metaOf(c).icon));
      var mid = el('span', 'mini-body');
      mid.appendChild(el('span', 'mini-meta', miniMeta(c, key)));
      mid.appendChild(el('span', 'mini-name', c.title || typeLabel(c)));
      if (c.alert) mid.appendChild(el('span', 'mini-alert', c.alert));
      row.appendChild(mid);

      var mp = primaryMap(c);
      if (mp) {
        var pin = link(mp, 'mini-map', '📍');
        pin.title = 'Open in Google Maps';
        pin.addEventListener('click', function (ev) { ev.stopPropagation(); });
        row.appendChild(pin);
      }
      row.appendChild(el('span', 'mini-chev', '›'));
      row.addEventListener('click', function (ev) {
        if (ev.target.closest('.mini-map')) return;
        openCard(c);
      });
      return row;
    }

    /* where you are staying sits above the day and stays there while it scrolls */
    var stays = items.filter(function (c) { return metaOf(c).kind === 'stay'; });
    var rest = items.filter(function (c) { return metaOf(c).kind !== 'stay'; });
    var pinned = null;
    if (stays.length) {
      pinned = el('div', 'day-pinned');
      pinned.appendChild(el('div', 'day-pin-title', PINNED_LABEL));
      stays.forEach(function (c) { pinned.appendChild(miniRow(c)); });
      body.appendChild(pinned);
    }

    /* A heading is sticky only within its own .day-sec-block, so it can't hang
       around over the next part of the day. The last card of each part is left
       *outside* that block on purpose: a sticky element is pushed out by the
       bottom of its container, so ending the container above the final card
       means the heading leaves as that card arrives rather than hovering over
       it while it slides away. A part with a single card therefore has a
       heading that never sticks at all — there is nothing left to label. */
    SECTION_ORDER.forEach(function (s) {
      var mine = rest.filter(function (c) { return sectionOf(c) === s[0]; });
      if (!mine.length) return;
      var block = el('div', 'day-sec-block');
      block.appendChild(el('div', 'day-sec', s[1]));
      mine.slice(0, -1).forEach(function (c) { block.appendChild(miniRow(c)); });
      body.appendChild(block);
      body.appendChild(miniRow(mine[mine.length - 1]));
    });

    if (!items.length || (T.day_notes || {})[key]) {
      body.appendChild(el('p', 'day-note',
        (T.day_notes || {})[key] || 'Nothing booked on this day.'));
    }

    document.getElementById('dayModal').classList.add('open');
    body.scrollTop = 0;
    /* the section headings stick just under the pinned block, whatever its
       height turns out to be — measured after it is in the document */
    body.style.setProperty('--pin-h', (pinned ? pinned.offsetHeight : 0) + 'px');
    lockBody();
  }

  function openCard(c) {
    var body = document.getElementById('cardBody');
    body.innerHTML = '';
    var entry = buildEntry(c);
    entry.classList.add('in-modal');
    body.appendChild(entry);
    document.getElementById('cardModalTitle').textContent = c.title || typeLabel(c);
    document.getElementById('cardModal').classList.add('open');
    body.scrollTop = 0;
    lockBody();
  }

  /* ---------- document viewer ---------- */
  var blobUrls = [];
  function b64ToBlob(b64, mime) {
    var bin = atob(b64), arr = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return new Blob([arr], { type: mime });
  }

  /* One file's section: its name, the download button, and its pages (addresses
     parked in data-src until showSection, then left to `loading=lazy`). */
  function docSection(id, doc) {
    var sec = el('div', 'viewer-doc');
    var head = el('div', 'doc-head');
    /* one name per document: its file name (the owner renames the file itself; a stored
       `label` — the API used to write descriptive ones — is no longer shown) */
    head.appendChild(el('span', 'doc-name', doc.name || doc.label || id));
    var srcs = [];
    if (T.doc_base) {          /* the live link: fetched on demand, see build_html */
      var base = T.doc_base + encodeURIComponent(id) + '/';
      var stamp = '?v=' + encodeURIComponent(doc.v || '0');
      if (doc.file) {
        var live = el('a', 'dl', '⬇ ' + (doc.name || 'download'));
        live.href = base + 'file' + stamp;
        live.setAttribute('download', doc.name || 'document');
        head.appendChild(live);
      }
      for (var n = 0; n < (doc.n || 0); n++) srcs.push(base + 'p/' + n + stamp);
    } else {
      var payload = doc.orig || (doc.pages || [])[0];
      var mime = doc.orig ? doc.mime : 'image/jpeg';
      if (payload) {
        var dl = el('a', 'dl', '⬇ ' + (doc.name || 'download'));
        dl.setAttribute('download', doc.name || 'document');
        try {
          var u = URL.createObjectURL(b64ToBlob(payload, mime));
          blobUrls.push(u);
          dl.href = u;
        } catch (e) {
          dl.href = 'data:' + mime + ';base64,' + payload;
        }
        head.appendChild(dl);
      }
      var pmime = doc.mime === 'image/png' ? 'image/png' : 'image/jpeg';
      (doc.pages || []).forEach(function (p) { srcs.push('data:' + pmime + ';base64,' + p); });
    }
    sec.appendChild(head);
    srcs.forEach(function (src, i) {
      var box = el('div', 'page-box');
      var img = el('img', 'page');
      img.loading = 'lazy';
      img.setAttribute('data-src', src);
      box.appendChild(img);
      if (srcs.length > 1) box.appendChild(el('span', 'page-no', 'page ' + (i + 1) + ' / ' + srcs.length));
      sec.appendChild(box);
    });
    if (!srcs.length) sec.appendChild(el('p', 'day-note', 'No preview available — use the download button above.'));
    sec._pages = srcs.length;
    return sec;
  }

  function showSection(sec) {
    sec.classList.remove('hidden');
    Array.prototype.forEach.call(sec.querySelectorAll('img[data-src]'), function (img) {
      img.src = img.getAttribute('data-src');
      img.removeAttribute('data-src');
    });
  }

  /* `icon` is for the trip's own documents (📄); a card's documents keep 🎫.
     Two or more files get a sideways-scrolling row of tabs under the title bar —
     full names, never cut off. All files stay in one scroll; a tab jumps to its
     file and the tab of the file on screen lights up as you scroll, so the second
     file is one tap away however long the first is (say a 2-page and a 24-page PDF). */
  function openDocs(c, icon) {
    var ids = (c.docs || []).filter(function (id) { return (T.docs || {})[id]; });
    var body = document.getElementById('viewerBody');
    var viewer = document.getElementById('viewer');
    body.innerHTML = '';
    blobUrls.forEach(function (u) { URL.revokeObjectURL(u); });
    blobUrls = [];
    document.getElementById('viewerTitle').textContent = (icon || '🎫') + ' ' + (c.title || typeLabel(c));

    var secs = ids.map(function (id) { return docSection(id, T.docs[id]); });
    var tabs = null, buttons = [];
    if (secs.length > 1) {
      tabs = el('div', 'doc-tabs');
      buttons = ids.map(function (id, i) {
        var doc = T.docs[id];
        var b = el('button', 'doc-tab', (doc.name || doc.label || id) + ' · ' +
                   (secs[i]._pages === 1 ? '1 page' : secs[i]._pages + ' pages'));
        b.type = 'button';
        b.addEventListener('click', function () { jumpTo(i); });
        tabs.appendChild(b);
        return b;
      });
      body.appendChild(tabs);
    }
    /* every file stays in one long scroll; pages are `loading=lazy` with an A4
       placeholder, so on the live link only the pages near the screen download */
    secs.forEach(function (s) { body.appendChild(s); showSection(s); });

    viewer.classList.add('open');
    viewer.scrollTop = 0;
    var barH = viewer.querySelector('.viewer-bar').offsetHeight;
    if (tabs) tabs.style.top = barH + 'px';          /* stuck just under the title bar */

    /* the space the sticky bar + tabs cover at the top of the scroll */
    function covered() { return barH + (tabs ? tabs.offsetHeight : 0) + 8; }
    function topOf(sec) {
      return sec.getBoundingClientRect().top - viewer.getBoundingClientRect().top + viewer.scrollTop;
    }
    function mark(k) {                                 /* highlight tab k, slide it into view */
      if (!buttons.length || buttons[k].classList.contains('on')) return;
      buttons.forEach(function (b, i) {
        b.classList.toggle('on', i === k);
        b.setAttribute('aria-current', i === k ? 'true' : 'false');
      });
      var t = buttons[k];
      tabs.scrollLeft = Math.max(0, t.offsetLeft - (tabs.clientWidth - t.offsetWidth) / 2);
    }
    function jumpTo(k) {
      viewer.scrollTop = k === 0 ? 0 : topOf(secs[k]) - covered();
      mark(k);
    }
    /* as you scroll, the tab of the file you are reading lights up */
    viewerSpy = buttons.length ? function () {
      var line = covered() + 40, k = 0;
      secs.forEach(function (s, i) { if (topOf(s) - viewer.scrollTop <= line) k = i; });
      /* a short last file can't reach the top: at the very bottom, it is the one */
      if (viewer.scrollTop + viewer.clientHeight >= viewer.scrollHeight - 2) k = secs.length - 1;
      mark(k);
    } : null;
    mark(0);
    lockBody();
  }
  var viewerSpy = null;
  document.getElementById('viewer').addEventListener('scroll', function () {
    if (viewerSpy) viewerSpy();
  }, { passive: true });

  window.closeViewer = function () {
    document.getElementById('viewer').classList.remove('open');
    unlockIfIdle();
  };

  ['dayModal', 'cardModal'].forEach(function (id) {
    document.getElementById(id).addEventListener('click', function (ev) {
      if (ev.target === ev.currentTarget) (id === 'dayModal' ? closeDay : closeCard)();
    });
  });
  document.getElementById('viewer').addEventListener('click', function (ev) {
    if (ev.target === ev.currentTarget) closeViewer();
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key !== 'Escape') return;
    if (document.getElementById('viewer').classList.contains('open')) { closeViewer(); return; }
    if (document.getElementById('cardModal').classList.contains('open')) { closeCard(); return; }
    if (document.getElementById('dayModal').classList.contains('open')) closeDay();
  });

  /* ---------- views ---------- */
  function setView(next) {
    document.getElementById('calendarView').classList.toggle('hidden', next !== 'calendar');
    document.getElementById('scheduleView').classList.toggle('hidden', next !== 'schedule');
    document.getElementById('costView').classList.toggle('hidden', next !== 'cost');
    Array.prototype.forEach.call(document.querySelectorAll('.view-tab'), function (b) {
      b.classList.toggle('active', b.getAttribute('data-view') === next);
    });
    /* printing follows the view you are looking at — see the @media print block */
    document.body.setAttribute('data-view', next);
  }
  document.getElementById('viewTabs').addEventListener('click', function (ev) {
    var btn = ev.target.closest('.view-tab');
    if (!btn) return;
    setView(btn.getAttribute('data-view'));
    window.scrollTo({ top: 0, behavior: 'instant' });
  });

  var topBtn = document.getElementById('topBtn');
  window.addEventListener('scroll', function () {
    topBtn.classList.toggle('show', window.scrollY > 400);
  }, { passive: true });

  /* ---------- light / dark ----------
     Two visible states, three real ones. Untouched means no attribute, which
     lets the stylesheet's media query follow the phone — so a file you send
     someone opens the way *their* phone is set. The first tap has to read what
     is actually on screen rather than assume dark, or it would appear to do
     nothing for anyone whose phone is already light. */
  var themeBtn = document.getElementById('themeBtn');
  themeBtn.addEventListener('click', function () {
    var set = document.documentElement.getAttribute('data-theme');
    var now = set || (window.matchMedia &&
      window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
    var next = now === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('trip-theme', next); } catch (e) {}
  });

  /* ---------- trip documents and links: the bar at the bottom of the calendar ----------
     One centred "📎 Trip docs and links ⌃" line fixed to the bottom; a tap opens
     them in a window shaped like the day and card windows (an upward-growing panel
     animated badly on phones). A document opens the same viewer a card's documents
     use; a link opens in a new tab. ✕, a tap outside the box, Escape, or picking
     an item closes it. Nothing to show, no bar. */
  function buildTripLinks() {
    var items = (T.items || []).filter(function (it) {
      return it.kind === 'doc' ? (it.docs || []).some(function (id) { return (T.docs || {})[id]; })
                               : linksOf([it]).length;
    });
    if (!items.length) return;
    var LABEL = '📎 Trip docs and links';

    var modal = el('div', 'modal-overlay');
    modal.id = 'linksModal';
    var mbox = el('div', 'modal-box');
    var mbar = el('div', 'modal-bar');
    mbar.appendChild(el('span', 'modal-title', LABEL));
    var close = el('button', 'modal-close', '✕ Close');
    close.type = 'button';
    mbar.appendChild(close);
    var mbody = el('div', 'modal-body trip-links-list');
    mbox.appendChild(mbar);
    mbox.appendChild(mbody);
    modal.appendChild(mbox);

    function show(open) {
      modal.classList.toggle('open', open);
      bar.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) lockBody(); else unlockIfIdle();
    }
    items.forEach(function (it) {
      var row;
      if (it.kind === 'doc') {
        row = el('button', 'link-item', '📄 ' + it.text);
        row.type = 'button';
        /* the pop-up stays open under the viewer (z-index 200 over 120), so closing
           the viewer comes back to it — the same as a card's documents */
        row.addEventListener('click', function () {
          openDocs({ title: it.text, docs: it.docs }, '📄');
        });
      } else {
        row = linkItem(linksOf([it])[0]);
        row.addEventListener('click', function () { show(false); });
      }
      mbody.appendChild(row);
    });
    close.addEventListener('click', function () { show(false); });
    modal.addEventListener('click', function (ev) { if (ev.target === modal) show(false); });
    /* Capture phase, so this runs before the page's own Escape handler: while a
       document is open, Escape closes only the viewer and leaves the pop-up. */
    document.addEventListener('keydown', function (ev) {
      if (ev.key !== 'Escape' || !modal.classList.contains('open')) return;
      if (document.getElementById('viewer').classList.contains('open')) return;
      show(false);
    }, true);

    var wrap = el('div', 'trip-links');
    var box = el('div', 'trip-links-box');
    var bar = el('button', 'trip-links-bar');
    bar.type = 'button';
    bar.setAttribute('aria-haspopup', 'dialog');
    bar.setAttribute('aria-expanded', 'false');
    bar.appendChild(el('span', null, LABEL));
    var chev = el('span', 'chev');
    chev.innerHTML = '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" ' +
      'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M3.5 10l4.5-4.5 4.5 4.5"/></svg>';
    bar.appendChild(chev);
    bar.addEventListener('click', function () { show(true); });

    box.appendChild(bar);
    wrap.appendChild(box);
    document.body.appendChild(wrap);
    document.body.appendChild(modal);
    document.body.classList.add('has-trip-links');
  }

  buildCalendar();
  buildSchedule();
  buildSummary();
  buildTripLinks();
  setView('calendar');
})();
"""

PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__</title>
<script>
/* Before the body paints, or a light-mode phone flashes dark for a frame every
   time the file is opened. No stored value is left alone on purpose: that is
   what "Auto" is, and the media query in the stylesheet decides it. */
try {
  var _t = localStorage.getItem('trip-theme');
  if (_t === 'light' || _t === 'dark') document.documentElement.setAttribute('data-theme', _t);
} catch (e) {}
</script>
<style>__CSS__</style>
</head>
<body>

<button class="top-btn" id="topBtn" onclick="window.scrollTo({top:0,behavior:'instant'})">↑ Back to top</button>

<header>
  <button class="theme-btn" id="themeBtn" type="button" title="Light or dark" aria-label="Switch between light and dark">
    <svg class="moon" viewBox="0 0 16 16" width="15" height="15" fill="currentColor" aria-hidden="true"><path d="M13.6 10.3A5.8 5.8 0 0 1 5.7 2.4 6 6 0 1 0 13.6 10.3z"/></svg>
    <svg class="sun" viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><circle cx="8" cy="8" r="3"/><path d="M8 1.4v1.5M8 13.1v1.5M1.4 8h1.5M13.1 8h1.5M3.3 3.3l1 1M11.7 11.7l1 1M12.7 3.3l-1 1M4.3 11.7l-1 1"/></svg>
  </button>
  __EYEBROW__
  <h1>__HEADING__</h1>
  <div class="trip-meta">
    <span>__SUBTITLE__</span>__TAGS__
  </div>
  <div class="view-tabs" id="viewTabs">
    <button class="view-tab active" data-view="calendar">📅 Calendar</button>
    <button class="view-tab" data-view="schedule">🗓 Schedule</button>
    <button class="view-tab" data-view="cost">💶 Cost</button>
  </div>
</header>

<div id="calendarView" class="view">
  <div class="cal-wrap">
    <div id="calMonths"></div>
    <div class="cal-legend">__LEGEND__</div>
  </div>
</div>

<div id="scheduleView" class="view hidden">
  <div class="sheet-wrap">
    <div class="sheet-actions">
      <details class="day-pick" id="dayPick">
        <summary id="dayPickLabel">All dates</summary>
        <div class="day-pick-panel" id="dayPickPanel"></div>
      </details>
      <button class="pdf-btn" onclick="window.print()">⬇ Save as PDF</button>
    </div>
    <div id="sheet"></div>
  </div>
</div>

<div id="costView" class="view hidden">
  <div class="sheet-wrap">
    <div class="sheet-actions end">
      <button class="pdf-btn" onclick="window.print()">⬇ Save as PDF</button>
    </div>
  </div>
  <div class="summary" id="summary">
    <div class="card">
      <h2>💶 Cost Summary <span style="font-weight:500; font-size:13px; color:var(--muted);">(as billed, in NT$)</span></h2>
      <div class="sum-tabs" id="sumTabs"></div>
      <table id="sumTable"></table>
      <div id="sumExtra"></div>
      __FOOT__
    </div>
  </div>
</div>

<div class="modal-overlay" id="dayModal">
  <div class="modal-box">
    <div class="modal-bar">
      <span class="modal-title" id="dayTitle"></span>
      <button class="modal-close" onclick="closeDay()">✕ Close</button>
    </div>
    <div class="modal-body" id="dayBody"></div>
  </div>
</div>

<div class="modal-overlay" id="cardModal">
  <div class="modal-box wide">
    <div class="modal-bar">
      <span class="modal-title" id="cardModalTitle">Booking details</span>
      <button class="modal-close" onclick="closeCard()">✕ Close</button>
    </div>
    <div class="modal-body" id="cardBody"></div>
  </div>
</div>

<div class="viewer-overlay" id="viewer">
  <div class="viewer-bar">
    <span class="modal-title" id="viewerTitle"></span>
    <button class="modal-close" onclick="closeViewer()">✕ Close</button>
  </div>
  <div class="viewer-shell" id="viewerBody"></div>
</div>

<script>
const TRIP = __TRIP_DATA__;
const TYPE_META = __TYPE_META__;
const GROUP_ORDER = __GROUP_ORDER__;
const SECTION_ORDER = __SECTION_ORDER__;
const DAY_SPLITS = __DAY_SPLITS__;
const PINNED_LABEL = __PINNED_LABEL__;
__JS__
</script>

</body>
</html>
"""


# Strips the generated page down to one card, shown the two ways a reader meets
# it: the compact row in a day's window, and the full card.
PREVIEW_CSS = r"""
  header, #calendarView, #scheduleView, #costView, .top-btn, .view-tabs,
  .viewer-overlay, .trip-links { display: none !important; }
  body { padding: 10px 0 14px; }
  body.modal-lock { overflow: visible !important; }
  .entry { padding-left: 0; margin-bottom: 0; }
  .entry .node { display: none; }
  /* both windows, flattened into the page */
  #dayModal, #cardModal {
    position: static !important; inset: auto !important; z-index: auto !important;
    display: block !important; background: none !important; padding: 0 !important;
  }
  #dayModal .modal-box, #cardModal .modal-box {
    max-width: none; max-height: none; border: 0; border-radius: 0;
    background: none; box-shadow: none;
  }
  #dayModal .modal-bar, #cardModal .modal-bar { display: none !important; }
  #dayModal .modal-body, #cardModal .modal-body {
    padding: 0 12px !important; overflow: visible !important; pointer-events: none;
  }
  #dayModal .mini { margin-bottom: 0; }
  .pv-label {
    font-size: 9.5px; font-weight: 800; letter-spacing: 0.14em; text-transform: uppercase;
    color: var(--muted); padding: 0 12px 6px;
  }
  .pv-label + .pv-label, .pv-gap { margin-top: 14px; }
"""

PREVIEW_BOOTSTRAP = """
<script>
(function () {
  try {
    var dm = document.getElementById('dayModal');
    var cm = document.getElementById('cardModal');
    openDay(TRIP.cards[0].start_date);
    openCard(TRIP.cards[0]);
    function label(text, gap) {
      var d = document.createElement('div');
      d.className = 'pv-label' + (gap ? ' pv-gap' : '');
      d.textContent = text;
      return d;
    }
    /* day row above the full card, both where the timeline used to sit */
    var host = cm.parentNode;
    host.insertBefore(label('Day list', false), dm);
    host.insertBefore(dm, cm);
    host.insertBefore(label('Full card', true), cm);
  } catch (e) { /* a half-filled form is fine — show what renders */ }
})();
</script>
"""


def build_card_preview(card: dict) -> str:
    """One-card page for the editor preview pane.

    Runs the real generator, so the preview cannot drift from the export.
    Documents are referenced but not embedded (the chip shows, the bytes don't
    travel on every keystroke).
    """
    start = card.get("start_date") or date.today().isoformat()
    end = card.get("end_date") or start
    card = dict(card, hide_days=[])   # the pane previews the card, not its placement
    stub = {
        "title": "", "eyebrow": "", "subtitle": " ",
        "start_date": min(start, end), "end_date": max(start, end),
        "tags": [], "notes": "", "day_notes": {},
        "cards": [card],
    }
    html = build_html(stub, Path("__no_uploads__"))
    html = html.replace("</style>", PREVIEW_CSS + "</style>", 1)
    return html.replace("</body>", PREVIEW_BOOTSTRAP + "</body>", 1)


def _esc(text: str) -> str:
    return (str(text or "")
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _json_for_html(obj) -> str:
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _month_name(m: int) -> str:
    return ["January", "February", "March", "April", "May", "June", "July",
            "August", "September", "October", "November", "December"][m - 1]


def default_subtitle(trip: dict) -> str:
    s, e = trip.get("start_date", ""), trip.get("end_date", "")
    if not s:
        return ""
    if not e or e == s:
        return s.replace("-", ".")
    return f"{s.replace('-', '.')} - {e.replace('-', '.')}"


def collect_docs(trip: dict, uploads_dir: Path, embed: bool = True) -> dict:
    """Every referenced upload — the cards' and the trip's own documents — as
    {doc_id: payload}.

    embed=True (the downloaded file, the builder's preview): the page images and
    PDF originals go in as base64, so the file works offline on its own.
    embed=False (the live link): only what the viewer needs to ask for them —
    `n` pages, whether there is a `file`, and `v`, a stamp that changes when the
    files do, so a phone can keep them for good and still see a re-render."""
    out: dict[str, dict] = {}
    card_docs = [d for card in trip.get("cards", []) for d in card.get("docs", [])]
    trip_docs = [d for i in trip_items(trip) if i["kind"] == "doc" for d in i.get("docs") or []]
    for doc in card_docs + trip_docs:
        did = doc.get("id")
        if not did or did in out:
            continue
        if not embed:
            pages = [uploads_dir / Path(str(n)).name for n in doc.get("pages", [])]
            pages = [p for p in pages if p.exists()]
            orig = uploads_dir / Path(str(doc.get("file") or "")).name if doc.get("file") else None
            stamps = [p.stat().st_mtime_ns for p in pages + ([orig] if orig and orig.exists() else [])]
            out[did] = {
                "name": doc.get("name") or doc.get("file") or did,
                "label": doc.get("label") or doc.get("name") or did,
                "mime": doc.get("mime", "image/jpeg"),
                "n": len(pages),
                "file": bool(orig and orig.exists()) or bool(pages),
                "v": format(max(stamps), "x") if stamps else "0",
            }
            continue
        pages = []
        for name in doc.get("pages", []):
            p = uploads_dir / Path(name).name
            if p.exists():
                pages.append(_b64(p))
        entry = {
            "name": doc.get("name") or doc.get("file") or did,
            "label": doc.get("label") or doc.get("name") or did,
            "mime": doc.get("mime", "image/jpeg"),
            "pages": pages,
        }
        if doc.get("mime") == "application/pdf" and doc.get("file"):
            p = uploads_dir / Path(doc["file"]).name
            if p.exists():
                entry["orig"] = _b64(p)
        out[did] = entry
    return out


# What fits on the header's one meta line before the names give way to bare
# flags. Measured on a 375px phone, where the row has 335px and the dates alone
# take 151px of it. Fitting the rest: about 25px per chip (the 18px flag plus
# its gaps) and about 6.5px per character. Dividing through, a chip costs what
# four characters cost — which is why a plain character count is not enough, and
# why a country count is not either: "Czechia" and "Czech Republic" are one
# country each and differ by seven characters.
META_BUDGET = 28
CHIP_COST = 4


def _page_items(trip: dict) -> list[dict]:
    """The trip's documents and links for the 📎 pop-up: links keep only a web address
    (they become hrefs), documents carry their doc ids (payloads are in data.docs)."""
    out = []
    for item in trip_items(trip):
        text = str(item.get("text") or "").strip()
        if item["kind"] == "link":
            url = clean_link(str(item.get("url") or ""))
            if url:
                out.append({"kind": "link", "text": text, "url": url})
        else:
            ids = [d.get("id") for d in item.get("docs") or [] if d.get("id")]
            if ids:
                out.append({"kind": "doc", "text": text or "Document", "docs": ids})
    return out


def build_html(trip: dict, uploads_dir: Path, doc_base: str | None = None) -> str:
    """The trip as one HTML page. Without `doc_base` every document is embedded (the
    self-contained file you download). With it (the live link) the page carries only
    the itinerary and the viewer fetches a document's pages from
    `<doc_base><doc id>/p/<n>` and its file from `<doc_base><doc id>/file` when opened."""
    cards = []
    for card in trip.get("cards", []):
        c = dict(card)
        c["docs"] = [d.get("id") for d in card.get("docs", []) if d.get("id")]
        c["price_ntd"] = int(c.get("price_ntd") or 0)
        c["links"] = clean_links(card.get("links"))      # web addresses only: they become hrefs
        cards.append(c)

    data = {
        "title": trip.get("title", "Itinerary"),
        "start_date": trip.get("start_date", ""),
        "end_date": trip.get("end_date", ""),
        "cards": cards,
        "docs": collect_docs(trip, uploads_dir, embed=doc_base is None),
        "doc_base": doc_base or "",
        "day_notes": trip.get("day_notes", {}),
        "items": _page_items(trip),
    }

    tags = trip.get("tags") or []
    tags_html = ""
    if tags:
        chips, spelled = [], 0
        for tag in tags:
            code, label = split_tag(tag)
            spelled += len(label)
            chips.append(f'<span class="chip" title="{_esc(label)}">'
                         f'{flag_svg(code)}<span>{_esc(label)}</span></span>')
        # Past the budget the flags carry the line alone. Each keeps a title, so
        # a long-press still names the country, and the printed sheet puts the
        # names back because paper has the width. The budget is set for the
        # narrowest phone worth designing for; anything wider simply has room to
        # spare, and `.trip-meta` wraps as a backstop either way.
        cost = CHIP_COST * len(chips) + spelled
        only = " flags-only" if cost > META_BUDGET else ""
        tags_html = (f'<span class="sepdot">·</span>'
                     f'<span class="tag-row{only}">{"".join(chips)}</span>')

    eyebrow = trip.get("eyebrow", "")
    eyebrow_html = f'<div class="eyebrow">{_esc(eyebrow)}</div>' if eyebrow else ""

    notes = trip.get("notes", "")
    foot_html = f'<div class="foot">{_esc(notes)}</div>' if notes else ""

    used = []
    for card in trip.get("cards", []):
        t = card.get("type", "custom")
        if t not in used:
            used.append(t)
    meta = meta_for_page()
    legend = "".join(
        f'<span><i class="dot" style="--c: var(--{t})"></i>'
        f'{_esc(meta.get(t, meta["custom"])["label"])}</span>'
        for t in used
    )

    html = PAGE_HTML
    html = html.replace("__CSS__", PAGE_CSS)
    html = html.replace("__JS__", PAGE_JS)
    html = html.replace("__TRIP_DATA__", _json_for_html(data))
    html = html.replace("__TYPE_META__", _json_for_html(meta))
    html = html.replace("__GROUP_ORDER__", _json_for_html([list(g) for g in GROUPS]))
    html = html.replace("__SECTION_ORDER__", _json_for_html(sections_for_page()))
    html = html.replace("__DAY_SPLITS__", _json_for_html(list(DAY_SPLITS)))
    html = html.replace("__PINNED_LABEL__", _json_for_html(PINNED_LABEL))
    html = html.replace("__TITLE__", _esc(trip.get("title", "Itinerary")))
    html = html.replace("__HEADING__", _esc(trip.get("title", "Itinerary")))
    html = html.replace("__SUBTITLE__", _esc(trip.get("subtitle") or default_subtitle(trip)))
    html = html.replace("__EYEBROW__", eyebrow_html)
    html = html.replace("__TAGS__", tags_html)
    html = html.replace("__FOOT__", foot_html)
    html = html.replace("__LEGEND__", legend)
    return html
