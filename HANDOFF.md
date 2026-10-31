# Handoff — Itinerary Builder

Orientation for whoever maintains this next (human or AI). `README.md` covers
*using* and *installing* it; this file covers *how it works and why*, plus the
traps that cost time the first time round.

**What it is:** a small self-hosted Flask app that edits travel itineraries card
by card and exports each one as a **single self-contained HTML file** — no
network, no assets, every booking document embedded. That exported file is the
product; the web app is scaffolding for producing it.

---

## 1. Repo map

| File | Lines | Owns |
|---|---|---|
| `app/generator.py` | ~1270 | **The exported page.** CSS + JS + HTML as string constants, and the live card preview. |
| `app/main.py` | ~530 | Flask routes, form→card translation, the project-page calendar, the live link. |
| `app/api.py` | | JSON API for scripts and AI agents at `/api/v1`, behind `ITINERARY_API_TOKEN`. |
| `app/storage.py` | ~480 | Disk layout, naming, document rasterising, zip import/export. |
| `app/triptypes.py` | ~110 | The card-type and part-of-day registries. Start here to understand the model. |
| `app/flags.py` | 155 | ~30 inline SVG country flags + name/emoji → code mapping. |
| `app/templates/*.html` | ~500 | Editor UI (Jinja). `card_form.html` is the big one. |
| `app/static/app.{css,js}` | ~1000 | Editor styling and seven small JS modules. |
| `tools/import_legacy_html.py` | 335 | One-off converter for the original hand-built page. |
| `unraid/` | | Unraid template and `deploy.sh`, the update script. |

No build step, no bundler, no framework on the client. Plain ES5-ish JS so the
exported file works on old phone browsers.

## 2. Data model

One JSON file per project, at
`data/projects/<project name>/project-files/trip.json`:

```json
{
  "id": "2027-北海道行",        // == folder name; derived from "name"
  "name": "2027 北海道行",      // shown in the app, names the folder and exports
  "title": "Hokkaido Winter Trip", // heading inside the exported HTML
  "eyebrow": "Winter Getaway",
  "subtitle": "", "start_date": "2027-02-03", "end_date": "2027-02-12",
  "tags": ["jp:Japan"],                    // subheading; `xx:` renders an SVG flag
  "notes": "", "day_notes": {"2027-02-08": "free day"},
  "cards": [ /* … */ ]
}
```

Every card carries the same flat key set (blank strings when unused) — the list
lives in `CARD_FIELDS` in `triptypes.py`, plus `id`, `details` (array of lines),
`price_ntd` (int), `hide_days` (array of ISO dates, see §3), `section` and
`sort` (§4) and `docs`. One of those flat fields is worth calling out:
**`alert`** is the red line under the title — it renders in the day window, the
schedule sheet, the full card and the editor's own card rows, and turns black
(still bold) on paper.

**Links** (2026-10-02) are lists of `{"text", "url"}` in display order, in two
places: a card's `links` (shown above its price, one per row, 🔗 + the text) and
the trip's `links` (the 🔗 Trip links bar fixed to the bottom of the live page's
calendar view — one centred line; a tap opens the links in a pop-up window, #linksModal). A blank `text`
shows the site's name; the address itself is never printed. Both are edited with
the same two-field editor (`templates/_links.html`, rows dragged by app.js) and
cleaned by `triptypes.clean_links`: http(s) only, `www.x.com` gets `https://`,
anything else (`javascript:`, `data:`) is dropped because these become hrefs.
**The editor posts a `links_form` marker** and `_form_links` only replaces the
links when it is present — the agent API also goes through `_form_to_card`, and
without the marker every API edit would wipe a card's links (same idea as
`day_toggles` for `hide_days`). The API sets card links only from an explicit `links` list in the request body (`api._apply_links`; a non-http url is a 400, not a silent drop).

**Trip documents and links** (2026-10-05) replace the trip-level `links`: the
trip's `items` is one list in display order of `{"id", "kind": "link", "text", "url"}`
and `{"id", "kind": "doc", "text", "docs": [doc records]}` — a document item holds
several files, exactly like a card's `docs`. `triptypes.trip_items(trip)` is the only
reader: a trip saved before this still has `links`, which read as link items until its
list is first saved (`save_trip_items` then drops `links`). Edited in the project page's
second drop-down ("Trip documents and links", `_links.html` `item_row`, app.js
`[data-items-editor]`): Add document / Add link on top, rows drag by ⠿, document rows
fold to "name · N files", each file has its own ✕ (it stops posting `keep_<key>`), new
files post as `files_<key>`. On the live page the 📎 Trip docs and links bar opens a
pop-up; a document opens the card documents viewer (`openDocs`). **The upload sweeper
must see these files**: `storage._all_docs` yields cards' and items' docs, and
`_referenced_uploads` (live trip + snapshot headers) goes through it — anything else
that decides which uploads are in use must too.
The agent API (2026-10-06; the owner calls these the trip's "home page"):
`GET /trips/<id>/items`, `POST …/items/link {text, url}`, `POST …/items/documents`
(multipart: `file` + `item_id` to add to an existing document item, or `text` to
start a new one; one file per call) and `PATCH …/items {items: [...]}` — the full
new list of EXISTING items (rename, reorder, drop items, keep-list of file ids);
PATCH can never create an item. The trip summary lists them with item/file ids.

A doc record:

```json
{"id": "h_spk1", "label": "Hotel voucher…", "name": "0203-0205.pdf",
 "mime": "application/pdf", "file": "h_spk1.pdf", "pages": ["h_spk1_p1.jpg"]}
```

`pages` is the important one: **PDFs are pre-rasterised to JPEG at upload time**
(`storage._render_pdf`, 150 dpi, capped 1800 px). The exported page shows those
images, never the PDF itself, because iOS blocks the pop-up-and-`document.write`
trick and Android won't render an in-page PDF. This was the single biggest
source of "it doesn't work on my phone" and is not negotiable without breaking
mobile.

**An empty `pages` on a PDF is the failure to look for** — the viewer falls back
to "No preview available" and the export carries no images at all. It used to
happen silently: `_render_pdf` caught every exception and returned `[]` with no
trace, so there was nothing to diagnose. Now:

- every failure prints `[pdf] <doc id>: …` to the container log with the reason;
- a page that won't render no longer discards the pages that did;
- `main()` prints `PDF page rendering: ready` (or why not) at startup, so a
  missing `pypdfium2` is obvious immediately rather than months later;
- the upload flashes a warning instead of attaching a preview-less PDF quietly;
- **⟳ Re-render** on the card's document row (`rerender_doc` →
  `storage.rerender_document`) rasterises the stored PDF again, so a document
  broken by an old renderer is repaired without re-uploading it.

## 3. The one concept that matters: card `kind`

Each type maps to a `kind` that decides which fields the editor shows, how the
times are worded, and **which days the card occupies**:

| kind | Types | Occupies |
|---|---|---|
| `move` | Train / Bus, Flight | departure day, plus arrival day if different |
| `hire` | Rental | every day, pick-up through drop-off inclusive |
| `stay` | Hotel | the **nights only** — check-out day excluded |
| `point` | Restaurant, Place, Activity, Custom | its one day |

⚠️ **This rule is implemented twice** and the two must agree:

- `generator.py` → `datesOf(c)` in `PAGE_JS` (runs in the exported page)
- `main.py` → `_occupied_days(card)` (drives the project page's calendar)

If you change occupancy, change both, or the editor's calendar and the exported
page will quietly disagree. There is no test guarding this.

**A card can be kept off some of the days it spans.** `hide_days` lists them, so
a ten-day rental can appear on its pick-up and drop-off days only. The span
itself is untouched — the dates, the duration and the price all still read from
`start_date`/`end_date`, and only the day placement changes. That filter is the
*second* pair that has to agree: `shownDates(c)` in `PAGE_JS` and `_card_days()`
in `main.py`. Two rules follow from splitting them:

- the wording ("pickup", "ongoing", "drop-off", "staying") reads from the **full**
  span (`c._all` / `miniMeta`), never the filtered list, so a day that survives
  in the middle still says "ongoing";
- a card is never hidden from *every* day — `_hidden_days()` keeps the first one,
  or the card would vanish from the trip with no way back to it.

The editor builds the day tick-boxes in JS (a third copy of the occupancy rule,
in `app.js`) and posts one `show_day` per ticked day. The `day_toggles` marker
that tells the server to recompute is set by that same script, so a page whose
JS never ran posts no marker and the stored list is left alone.

## 4. How a day is laid out

A day view — the modal in the exported page, and the day panel in the builder —
is three things stacked:

1. **the hotel**, pinned above everything under a heading of its own
   (`PINNED_LABEL`) with a rule below. Any card of the `stay` kind; you sleep
   there, you don't *do* it at a time. Its heading rides *inside* the pinned
   block (`.day-pin-title`) rather than being a section heading, so it holds
   still with the block and takes none of the sections' behaviour.
2. **parts of the day**, each a heading over the cards under it — bare gold caps
   in the exported page, a solid grey bar in the editor, where they double as
   the blocks you drag between and want more weight. The list
   lives in `SECTIONS` in `triptypes.py`, in the order they are shown:
   Breakfast, Morning, Lunch, Afternoon, Dinner, Evening, and a trailing
   **Anytime** (key `""`) for cards with no time and no section of their own.
3. **the cards**, in `sort` order within each heading.

The two views treat those headings differently, on purpose:

- **Exported page** — each part of the day is wrapped in its own
  `.day-sec-block`, and the heading is `sticky` *inside that wrapper*. A sticky
  element is bounded by its containing block, so the heading holds the top while
  its cards pass and is then pushed out. Put the headings back as bare siblings
  and they each stick forever, stacking up and covering the next part's cards.

  The **last card of each part is deliberately left outside its wrapper**, so
  the wrapper ends above that card and the heading is pushed off as it *arrives*
  rather than hovering over it while it slides away. That also means a part with
  a single card has a heading that never sticks — there is nothing left for it
  to label. Move that card back inside and you get a heading hanging over a
  half-hidden lone card, which is what it looked like before.
- **Builder** — nothing sticks except the day's own header. The hotel block and
  the section bars are things you drag between, and anything parked at the top
  is just in the way while you edit. The hotel is still *pinned* in the sense
  that it sits above the sections and can't be dragged; it simply scrolls with
  everything else.

Two fields drive it, and both have a deliberate "unset" meaning:

- **`section`** — blank means *work it out from `start_time`*: before
  `DAY_SPLITS[0]` is morning, before `DAY_SPLITS[1]` is afternoon, after that
  evening, and no time at all means Anytime. The three **meal sections are never
  chosen automatically** — 12:30 is no way to tell lunch from a museum — so they
  only ever come from the editor's *Listed under* select, or from dragging a
  card under that heading.
- **`sort`** — a position on the same scale as a clock (minutes past midnight),
  seeded from `start_time` so an untouched day reads chronologically. A drag
  rewrites it and that sticks; **changing the card's time re-seeds it**, which
  is the rule that makes "time gives the default order, a drag overrules it"
  work. `_form_to_card` decides by comparing the posted time with the stored
  one. A card written before this feature has no `sort` at all and falls back
  to its time (`_position` / `orderOf`), so old trips keep the order they had.

⚠️ **A third pair that has to agree** (after occupancy and hidden days, §3):
`section_of` / `_sort_key` in Python against `sectionOf` / `orderOf` in
`PAGE_JS`. `SECTIONS` and `DAY_SPLITS` are embedded in the exported page rather
than restated, so only the two functions can drift.

Dropping a card calls `POST /trips/<id>/cards/<card>/place` with the section it
landed in and the ids either side of it. The server sets `section` and puts
`sort` halfway between its two new neighbours, so **no other card has to move**.
When that gap gets too narrow to halve again `_respread` gives the whole section
whole numbers back — rare, but a day rearranged fifty times in the same spot
would otherwise run out of room between two floats.

## 5. How export works

`build_html(trip, uploads_dir)` assembles: `PAGE_CSS` + `const TRIP = {…}` (the
trip as JSON, with every document base64-inlined by `collect_docs`) + `PAGE_JS`
+ `PAGE_HTML`. Everything visible — calendar, schedule sheet, day windows, detail
cards, cost summary — is built **client-side from `TRIP`** by the page's own
script. Nothing is server-rendered into the markup.

Consequences worth knowing:

- Adding a field to the page usually means editing `PAGE_JS`, not a template.
- The **live preview in the card editor calls the same renderer**
  (`build_card_preview` → `POST /trips/<id>/card-preview`), so it cannot drift
  from the export. Keep it that way; don't reimplement card markup in the editor.
- Export size tracks your uploads. A 23-document trip lands around 20 MB.
- **Two ways the same page carries documents** (2026-10-06). The downloaded
  file and the builder's preview embed every document (`build_html` with no
  `doc_base`) — self-contained, works offline; a document-heavy trip is ~48 MB. The
  **live link** passes `doc_base=/live/<token>/doc/`: `collect_docs(embed=False)`
  sends only `{name, label, mime, n, file, v}` per document (the page is ~0.17 MB,
  ~40 KB gzipped) and the viewer fetches `…/doc/<id>/p/<n>` and `…/doc/<id>/file`
  when a document is opened (`live_doc_page` / `live_doc_file`, which look the
  doc up inside the token's own trip and answer the fake 404 otherwise). Those
  URLs carry `?v=<file mtime>`, so they are served `private, max-age=1y,
  immutable`; the page itself is `private, no-cache` with an ETag, so a reload
  of an unchanged trip is a bodiless 304. Before this, every open of the live
  link downloaded the whole ~32 MB (gzipped) — painful on roaming data.

### The header, and why it is small

It used to be 335 px — 41% of a 375×812 phone — with the first date cell at
468 px, so 58% of the screen came before the trip did. It is now about 140 px.
Three things got it there, and each is load-bearing:

- **the dates and the countries share one line** (`.trip-meta`), which alone was
  ~40 px, and the top padding went 60 → 18;
- **the view tabs are equal thirds.** Sized to their content the three pills
  wanted 345 px and the row had 335, so "Cost" wrapped and cost another 44 px.
  Equal thirds cannot wrap, whatever the labels say;
- **the "tap a day" hint rides on the month title**, pushed to the right edge,
  rather than taking a 32 px row of its own. `buildMonth(..., first)` appends it
  to the first month only.

**The countries are a width budget, not a count** — `CHIP_COST * chips + spelled
<= META_BUDGET` in `build_html`. Measured on a 375 px phone: the row has 335 px,
the dates take 151 px, and the rest costs about 25 px per chip (an 18 px flag
and its gaps) plus 6.5 px per character. Dividing through, a chip costs what
four characters cost. A plain character count is not enough, and a country count
is not either — "Czechia" and "Czech Republic" are one country each and differ by
seven characters. Past the budget the names drop and the flags carry the line,
each keeping a `title`; paper puts the names back, because paper has the width.
`.trip-meta` also wraps, so a case nobody predicted costs one 18 px line rather
than running off the screen.

**The theme switch is top left** because `.top-btn` ("↑ Back to top") is fixed
top right and fades in past `scrollY > 400`, by which point the header has gone.
The corner does two jobs at two different times and they never overlap. The
visible circle is 30 px with the tap target padded to 44 px by a `::after`.

### The three views, and what prints

`#calendarView`, `#scheduleView` and `#costView`, one visible at a time via
`setView`. **Calendar** taps through to a day's window. **Schedule** is a
spreadsheet of the whole trip, one `<table class="sheet-day">` per day: the part
of the day merged down the left with `rowspan`, then time, type, title and map
pin — on a phone the section becomes a band across the row and the type moves
into the title cell, see below. It replaced an older scrolling "Timeline" view
and its date-pill filter;
the pills are now `#dayPick` beside the PDF button — a `<details>` holding a
checkbox per day, not a `<select>`, because several days can be picked at once
and a multiple-select is unusable on a phone. Ticking none means the whole trip
rather than an empty sheet, so the page can't end up looking broken. **Cost**
holds the summary, which used to sit under every view.

### The sheet has two shapes

Five columns don't fit a phone. Four of the five have fixed pixel widths —
section, time, kind and map, 426 px together — and the title takes what is left,
which below about 500 px is nothing: it collapses to `width: 0` and wraps a
letter at a time. Nothing overflows, so the page looks fine until you read it.

So `sheetRows()` writes **every group twice**: once as the merged `rowspan`
column a wide screen and paper use, and once as a full-width `<tr class="sec-row">`
band plus a `.kindline` inside each title cell. The 620 px breakpoint shows one
set and collapses the other, which gets the title from 130 px to 268 px. Both
shapes are always in the DOM — about 40 bytes a card — because CSS cannot
un-merge a `rowspan` and rebuilding the table on rotate would mean a second
layout to keep in step with this one.

The band is centred on `#272b3a`, one shade up from `--card`, and its label is
`--accent` (the hotel's is `--hotel`). The label is deliberately *not* `--ink`:
at `--ink` it was the same colour as the card titles sitting directly under it
and the heading stopped reading as a heading.

Four traps in that arrangement, all of which fail quietly:

- **never `display: none` a cell that has columns after it.** A removed cell
  leaves the row and everything behind it slides one column left, so the title
  lands in the time column at 48 px. Collapse them instead — `width: 0`,
  `padding: 0`, `font-size: 0` — as the 620 px block does. The print block gets
  away with `display: none` on `.smap` only because the map is last;
- **the mobile rules for `.sec` / `.skind` are deliberately one class deep.**
  The print block restates them at the same specificity and has to win on order,
  because a phone printing matches *both* blocks. Write `.sheet-day th.sec` there
  and the section column prints 88 px wide with `font-size: 0` — a blank column;
- the date heading is a **`<thead>`, not a `<caption>`** — browsers repeat a
  thead on each page when a table breaks, so a long day keeps its date on every
  printed page;
- column widths live on a **`<colgroup>`**, because `table-layout: fixed` reads
  them from the first row and that row is the heading's single `colspan` cell —
  put them on the cells and the table splits into equal fifths.

`sheetTime()` / `sheetState()` give the *day's* view of a card, so a hire reads
`pick-up 10:00` on one day and `drop-off` on another, in whichever shape.

**Save as PDF is `window.print()`**, not a library. The export is one offline
file: a PDF library would add 300 KB+ to every export and still need an embedded
font to set CJK, which is megabytes. Printing gives selectable text, real CJK
and proper pagination for nothing. The `@media print` block turns the page into
something worth putting on paper — white, black rules, black text, no fills, no
flags, the map column collapsed and the cost summary's colour badges reduced to
their text, since all of that is ink you can't use. Every rule in that block is
a literal black or white rather than a token, which is what makes the printed
sheet identical whether the screen was light or dark (§11).

**Printing follows the view you are on.** `setView` writes `data-view` onto
`<body>` and the print block reads it: the schedule prints by default, the cost
summary prints when that view is showing, and the calendar never does. Both
views carry their own *Save as PDF* button, so printing is never a keyboard-only
trick.

`#dayPick` **does** narrow the printed schedule — printing a day, or a couple of
days, is the point of the control.

The **cost summary prints something different from what is on screen**. On
screen it is one table plus category tabs that filter it. On paper it is one
list per category, each with its subtotal, then a grand total — the categories
account for every priced card between them (verified: the subtotals sum to the
grand total), so a combined list would only repeat them. Those per-category
lists are a second, print-only copy built in `buildSummary` and parked in
`#sumExtra`, because the screen table can't be in five places at once; `#sumTable`
is hidden outright in print, which also means whatever the tabs were filtering
can't leak into the PDF. `priceRow()` and `subtotalRow()` build both copies so
they can't drift.

## 6. Storage and naming

```
data/projects/<project name>/
    project-files/trip.json     the live record
    documents/                  uploads + rendered PDF pages
    history/                    the last 10 undo points (see §7)
    export-html/                <name>-YYYYMMDDHHMM.html
    export-json/                <name>-YYYYMMDDHHMM.json
```

- `safe_id()` / `slugify()` / `safe_filename()` deliberately **keep unicode**
  (`re.UNICODE` `\w`) so a project called 台南行 gets a readable folder, while
  stripping anything path-ish and the Windows-illegal set (so names survive an
  SMB round-trip).
- Renaming a project **moves the folder** (`rename_project`) and returns a new
  id; the route redirects to the new URL. Documents and past exports come along.
- Every download also saves a timestamped copy (`save_export`); same-minute
  collisions get a `-2` suffix.
- Zip entry names always go through `PurePosixPath` / `as_posix()` so archives
  written on Windows unpack correctly on Linux.
- `storage.init()` migrates the pre-projects layout (`trips/*.json` +
  `uploads/<id>/`) on startup. Leave that in place; real data went through it.

## 7. History and undo

Every mutating route calls `storage.snapshot(trip_id, label)` **before** it
changes anything, so a snapshot holds the state the label describes moving
*away* from — that is what makes "Revert" on "Deleted X" mean "put X back".
A snapshot file is **two JSON documents, one per line**: a small header
(`at`, `label`, `cards`, and `files` — the uploads that copy references) and
then the whole trip. Listing the history and sweeping uploads need only the
header, so a page load reads ten 80-byte lines rather than parsing ten whole
trips. Files written before the header existed parse as a single line and still
work — `list_history` falls back to counting cards from the payload, and the
sweeper falls back to reading it in full. Newest `HISTORY_KEEP` (10) are kept. The project page lists them under
*Activity history* and `POST /trips/<id>/history/<snap>/revert` writes one back.
A revert snapshots first, so undoing a revert is just the next entry.

⚠️ **Deleting a card or a document no longer unlinks its files.** It can't: a
snapshot still points at them, and a revert that restored a card full of missing
documents would be worse than no revert at all. Instead
`_collect_orphan_uploads` runs after each snapshot and removes uploads that
neither the live trip nor any surviving snapshot references — so the bytes go
once the delete is ten changes old and genuinely unreachable. Two things keep
that safe: it reads the live trip first and gives up entirely if it or any
snapshot won't parse (a partial picture would delete live files), and it only
ever considers files inside that project's `documents/`.

Everything is one file per snapshot on disk, so the cost is ~10 × the size of
`trip.json` per project — text, trivial next to the uploads.

Two things worth knowing:

- a drag-reorder counts as a change, so ten drags in a row push older entries
  out. Raise `HISTORY_KEEP` if that bites;
- reverting after a **rename** restores the old name into the record but leaves
  the folder on the new id (revert doesn't call `rename_project`). Saving
  *Project details* again re-syncs them. Cosmetic, but surprising if you hit it.

## 8. Layout contracts

Both working pages turn into an app shell at **≥1101 px** and flow normally
below it. The shape is the same in each: one wrapper gets an explicit pixel
height from JS, every column inside is `height: 100%` with `min-height: 0`, and
exactly the panes that should move get `overflow-y: auto`. `min-height: 0` is
the load-bearing part — without it a flex item refuses to shrink below its
content and the inner scrollbars never appear.

### The project page

`#projectLayout` is a `1fr / 2fr` grid sized by `sizeShell()` in `app.js`, so
the page itself never scrolls. Left third: project details, saved exports and
the delete button, scrolling inside `#projectSide`. Right two thirds: the
calendar in `#calRegion`, which holds its place and only scrolls internally once
it would pass `--cal-max` (half the panel, but never less than `--cal-floor`,
four week rows) — and under it the selected day's cards in `#dayPanels`, which
is the pane meant to scroll. `.day-panel-head` is `sticky` inside it so the day
and its *Add card* button stay reachable; nothing else in that pane sticks (§4).

`#calSplitter` divides the two and is the drag handle for the split, the same
shape as the editor's `#splitter`. A drag pins `flex/max-height/height` as
inline styles on `#calRegion`, which is what overrides the two variables, and
stores the pixel height under `ib-cal-h:<trip id>` — per trip, because a
fortnight and a three-month trip want different splits. A double-click removes
both and the stylesheet takes over again. Two clamps that matter: it never goes
taller than the months themselves (`calContent`, measured once *before* the
stored height is applied, or a short trip would get blank space under its last
row), and it always leaves `CARDS_MIN` for the cards. A pin is ignored below
1101 px but kept, so switching to a narrow window and back doesn't lose it.

The splitter's `margin-top` is also what keeps the rule clear of the last row of
date cells — `#calRegion` sits at exactly its content height, so a divider
drawn as a plain `border-top` lands flush against the cells.

⚠️ The scrolling panes are `flex: 1 1 0`, **not** `1 1 auto`, and that is not
cosmetic. With `auto` a pane's own content counts towards the column's
hypothetical height, so a day with a dozen cards overflows the column and the
browser shares that overflow between the panes by shrink-factor × base size —
which quietly steals rows off the *calendar* no matter how much room the panel
really has. A basis of `0` means only `#calRegion` is content-sized, the card
list simply takes what is left, and the calendar keeps its four weeks.

Below 1101 px, `.project-side` becomes `display: contents` and `order` puts the
cards panel back between *Project details* and *Saved exports* — the DOM order
is details / exports / delete / cards, which is **not** the reading order on a
phone. If you add a panel to the left column, give it an `order`.

### The card editor

At **≥1101 px** the card editor is an app shell, not a scrolling page:
`#editorLayout` gets an explicit height from JS (`sizePane()`), `#editorMain`
(left column) is the only thing that scrolls, and `.preview-pane` is
`position: static` at `height: 100%` so the preview and its Save button never
move. Below 1101 px, `.editor-main` becomes `display: contents` and everything
flows normally with the preview on top.

Three details that look arbitrary but aren't:

- `.grid label > input/select/textarea { margin-top: auto }` — labels in a row
  are stretched to equal height, and only some have hint lines. Without this,
  inputs in the same row sit at different heights.
- The pane's Save button lives *outside* the form and uses
  `form="cardForm"` to submit it.
- `sticky` was tried for the preview pane and abandoned: it releases at the end
  of its container, so the pane drifted on long forms.

## 9. The project page remembers where it was

Saving a card used to land you back at the top of the project page with the
calendar reset, which made editing a long trip miserable. Now the cards panel
keeps `{view, day, tops, off}` in `sessionStorage` under `ib-proj:<trip id>` —
updated on every day click, view switch, scroll (throttled) and on `pagehide` —
and the routes that land back on it — card save/delete/copy, project details,
deleting an export — redirect with **`?restore=1`**. Only that flag replays the
stored state; a fresh visit still gets the server's own choice of day. Three
details worth keeping:

- `tops` holds the `scrollTop` of each pane of the wide-screen shell (§6). That
  is what actually moves there; the window doesn't scroll at all.
- `off` is the fallback for narrow screens, and is measured **relative to the
  cards panel** rather than the document: a save adds a flash message above it,
  and an absolute offset would land you that much too high.
- the flag is stripped with `history.replaceState` after it is used, so a reload
  doesn't replay a stale position.

If you add another route that returns to the project page from the editor, give
it `restore=1` too.

## 10. Copying a card

`POST /trips/<id>/cards/<card>/copy` puts a copy on another date, in this
project or another one. The whole span shifts by the same number of days, so a
five-night hotel stays five nights, and `hide_days` shifts with it. Documents
are **physically copied** (`storage.copy_document`, fresh document id and fresh
filenames) rather than shared: `delete_document` unlinks whatever a card points
at, so two cards sharing one file on disk would take each other's documents
down. The dialog lives in `trip.html`; one copy of it serves every row.

Not to be confused with the older **⧉ duplicate** button, which clones a card
inside the project, drops its documents and opens the copy for editing.

## 11. Conventions and gotchas

- **Restart after editing a template.** Jinja templates are cached in the
  running process; CSS/JS are not. Much confusion saved by knowing this.
- **UTF-8 in the container.** The Dockerfile sets `LANG`/`LC_ALL=C.UTF-8` and
  `PYTHONUTF8=1` because project folders and export names are routinely CJK.
  Don't drop those. Note a comment cannot sit inside an `ENV \` continuation —
  that's why there are two `ENV` blocks.
- **`requirements.txt` uses `>=`, not pins,** because a pinned Pillow had no
  wheel for the newest Python and tried to compile from source.
- **Local dev on Windows:** use a real Python install, not the Microsoft Store
  stub on `PATH` (it can't install packages). `run-local.ps1` finds the right
  one; run it with `powershell -ExecutionPolicy Bypass -File .\run-local.ps1`.
- There is **no test suite.** The smoke path that catches most regressions:
  create a project → add a card and watch the live preview → attach a PDF (proves
  the rasteriser works) → Download HTML → open it offline on a phone → confirm
  the dated copy appears under *Saved exports*. Worth adding since: save a card
  from a scrolled calendar and check you come back to the same day and offset;
  untick some days of a multi-day card and confirm the project calendar and the
  exported page light up the **same** dates; copy a card with a document into
  another project and check the file landed in that project's `documents/`;
  open the export's **Schedule at phone width** and confirm the section bands
  are showing and the title is readable, then print it from that width and
  confirm the section is back in its column *with its text* — a squeezed table
  has no overflow to notice, so nothing else in the smoke path catches either;
  **flip the theme on every view and in the builder**, looking hardest at the
  cost badges and any gold-filled control — a light palette breaks there first,
  by leaving dark text on a fill that darkened with it; and **check for sideways
  scroll at 320 px and 280 px**, not just at 375 px. The calendar grid overflowed
  a 320 px phone by 22 px for a long time and no check at 375 could see it —
  narrow is a direction, and the bottom of the range has to be tested on purpose.
- **After editing either stylesheet, confirm the rule you wrote is the rule that
  applies** — read it back off the element with `getComputedStyle`, not off the
  file. CSS fails silently and locally: a stray `*/` left four lines of prose
  loose inside a media block, and the browser did what the spec says and skipped
  to the next `}`, dropping exactly one declaration. The rule on the line after
  it kept working, so the page looked *almost* right, and nothing was logged.
  Comment-and-brace balance is cheap to check mechanically; eyes are not.

## 12. Known limitations (deliberate, not bugs)

- **No concurrency control.** Every mutating route re-reads `trip.json`, edits
  it, and writes the whole file back via a `.tmp` + atomic `replace`, so data
  can't corrupt and two people editing *different* cards both succeed. But two
  people editing the **same** card, or both submitting *Project details*, means
  **last save wins silently** — no warning, no live refresh. The fix, if it ever
  matters, is an optimistic lock: a revision counter in `trip.json`, echoed in a
  hidden form field, rejected on mismatch.
- **One shared password.** `APP_PASSWORD` + HTTP Basic, any username. There is
  no per-user identity and no audit trail. Treat it as a single-editor tool.
- **Light and dark, in both the export and the builder.** Colour lives in tokens
  on `:root`; dark stays there and light is the same names with different values,
  selected either by `[data-theme="light"]` or — when nothing is stored — by
  `prefers-color-scheme`. Two things to know before touching the palette:
  - **gold and the type colours each have two lives.** As *text* they darken for
    a light background; as a *fill* they must stay bright, or the dark text on
    them becomes unreadable. Hence `--accent` vs `--accent-fill`, and `--on-type`
    flipping the badge's own text. Both bugs happened here before being caught.
  - **`@media print` never reads a token.** Every rule in it is a literal black
    or white, so paper is identical whichever theme the screen was on. That is
    verified, not assumed — the check compares a print snapshot taken in each
    theme and asserts they are the same.
  - **the calendar's day cells have their own token**, `--cell` / `--cell-line`,
    not `--card` / `--sunken`. At the shared surface they sat 1.12:1 off the page
    in the export and 1.08:1 off the panel in the builder — the 1px border was
    the only thing separating them and the grid read as one slab. They are now
    1.48:1 and 1.47:1. The two files hold *different* values for the same ratio
    because the export's cells sit on `--bg` and the builder's on `--panel`.
    Lifting `--card` instead would have lifted every modal and panel with it.
    Light is deliberately unchanged in both: the cell is already white and there
    is nowhere above it to go.
  - **the day cell is square on a wide screen and 4:5 on a phone.** Seven
    columns fix its *width*, so a square caps its height at the same ~47px and
    the grid stops less than halfway down the screen — 176px of nothing under
    the legend on a 375×667 phone. The extra height goes to the date, which is
    22px there (20px in the builder, whose cells are 5px narrower). Two numbers
    bound this: a six-row month is the tallest a calendar gets and its legend
    lands at 662 of 667, so even that still fits unscrolled; and the date is
    limited by the cell's *width*, not its height — at 280px the widest date
    clears the content box by 0.3px. It bleeds into padding rather than
    clipping, because the cell sets no `overflow` and the track is
    `minmax(0, 1fr)`, so it cannot grow and push the grid sideways.
  - **the schedule sheet is always light**, dark mode included. `#sheet`
    re-declares the light tokens for its own subtree, so the sheet's rules are
    untouched and simply resolve to different values; the toolbar above it lives
    outside `#sheet` and stays with the page. It also sets `color`, because
    anything that doesn't say `var(--ink)` — the card title doesn't — inherits
    its colour from `<body>`, which is outside the scope: redeclaring the token
    alone leaves white titles on a white sheet. The reason it is worth the
    special case is in §5: dark mode's layers sat within 1.22:1 of each other,
    and the section band within 1.05:1 of the date bar.
- **Sensitive by nature:** the data includes full names, birth dates, booking
  references, door PINs and scanned tickets. Keep the password set and prefer
  LAN/Tailscale over exposing it.

## 13. Deploy and update

The server runs a clone of the GitHub repo at
`/mnt/user/appdata/itinerary-builder/src`, with the data beside it in
`/mnt/user/appdata/itinerary-builder/data`. **Change the code here, commit, push,
then on the server run `bash /mnt/user/appdata/itinerary-builder/src/unraid/deploy.sh`.**
Never edit the server's copy: the next `git pull --ff-only` refuses to run over
local changes, and they were never in the repo to begin with.

What the script handles, and why each step is there:

1. The image is built **locally** on the server (`docker build -t
   itinerary-builder:latest .`), so Unraid's update check always says "not
   available" — expected, not an error. Avoid *Force Update*: it tries to
   `docker pull` an image no registry has.
2. The container has to be **recreated**, not restarted — a running container is
   bound to the old image's ID, so Stop/Start keeps serving old code. The script
   calls Unraid's own `rebuild_container`, which is what Edit → Apply does.
3. `rebuild_container` leaves the container **stopped** unless autostart is on.
   The script starts it again if it was running before.
4. Each build leaves the previous image untagged, which Unraid lists as an
   *orphan image*; `docker image prune -f` clears them.

## 14. Moving the whole thing

Source alone is enough to continue development. To carry the **trips** as well,
use the app's own **Download full backup (.zip)** on the start page and
**Import .zip** on the new install — that's a tested round-trip and far smaller
than copying `data/`, which is mostly rendered PDF pages.
