"""Itinerary Builder — web UI that produces a single-file HTML itinerary."""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import secrets
from datetime import date, timedelta
from pathlib import Path

from flask import (Flask, Response, abort, flash, redirect, render_template,
                   request, send_file, send_from_directory, url_for)

from . import storage
from .generator import build_card_preview, build_html, default_subtitle
from .triptypes import (CARD_FIELDS, PINNED_LABEL, SECTIONS, SECTION_LABELS,
                        LINK_TEXT_MAX, TYPES, card_meta, clean_link, clean_links, default_sort,
                        kind_of, modes_of, section_of, trip_items)

KINDS = {key: meta["kind"] for key, meta in TYPES.items()}
# {type: {mode: {label, icon, service}}} for the card form's Mode picker (only transit has modes)
TYPE_MODES = {key: meta["modes"] for key, meta in TYPES.items() if meta.get("modes")}

# Spelled out rather than taken from strftime: the container runs under C.UTF-8
# and these have to match the names the exported page prints.
MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]
MON3 = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAY3 = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]     # weekday() order

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "25"))

app = Flask(__name__)
# Signs the flash-message cookie. No fixed fallback: this source is public, so a
# built-in key would let anyone forge cookies. A random key per start only means
# a pending flash message is lost across a restart.
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.jinja_env.globals.update(type_modes=TYPE_MODES, card_meta=card_meta, trip_items=trip_items)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024 * 4  # several files per request


# ------------------------------------------------------------------ auth
@app.before_request
def require_password():
    if request.path.startswith("/api/"):
        return None             # the agent API checks its own bearer token (app/api.py)
    if request.path.startswith("/live/"):
        return None             # the live link's secret token is its own access check
    password = os.environ.get("APP_PASSWORD", "").strip()
    if not password:
        return None
    auth = request.authorization
    if auth and hmac.compare_digest((auth.password or "").encode(), password.encode()):
        return None
    return Response(
        "Authentication required.", 401,
        {"WWW-Authenticate": 'Basic realm="Itinerary Builder"'},
    )


# ------------------------------------------------------------------ helpers
def _trip_or_404(trip_id: str) -> dict:
    try:
        trip = storage.load_trip(trip_id)
    except ValueError:
        abort(404)
    if trip is None:
        abort(404)
    return trip


def _card_or_404(trip: dict, card_id: str) -> dict:
    for c in trip.get("cards", []):
        if c.get("id") == card_id:
            return c
    abort(404)


SECTION_ORDER = {key: i for i, (key, _label, _manual) in enumerate(SECTIONS)}


def _position(card: dict) -> float:
    """A card's place within its section — the clock until a drag says otherwise.

    Cards written before this existed carry no `sort` at all, so they fall back
    to their time and keep the order they have always had.
    """
    at = card.get("sort")
    return float(at) if isinstance(at, (int, float)) else default_sort(card)


def _sort_key(card: dict) -> tuple:
    """Where a card sits within its day: section first, then its own position.

    Mirrored by `orderOf` in the exported page.
    """
    return (SECTION_ORDER.get(section_of(card), len(SECTIONS)),
            _position(card),
            card.get("start_time") or "~",
            card.get("title") or "")


def _sorted_cards(trip: dict) -> list[dict]:
    return sorted(trip.get("cards", []),
                  key=lambda c: ((c.get("start_date") or ""),) + _sort_key(c))


def _as_date(value: str) -> date | None:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _occupied_days(card: dict) -> list[str]:
    """Which days a card spans. Mirrored by `datesOf` in the exported page."""
    start = _as_date(card.get("start_date", ""))
    if start is None:
        return []
    end = _as_date(card.get("end_date", "")) or start
    if end < start:
        end = start
    span = (end - start).days
    kind = kind_of(card.get("type", "custom"))

    if kind == "stay":                    # the check-out day is not a night
        days = [start + timedelta(days=i) for i in range(span)] or [start]
    elif kind == "hire":
        days = [start + timedelta(days=i) for i in range(span + 1)]
    elif kind == "move":
        days = [start] if end == start else [start, end]
    else:
        days = [start]
    return [d.isoformat() for d in days]


def _card_days(card: dict) -> list[str]:
    """The days a card shows on: its span minus the ones the editor hid.

    Mirrored by `shownDates` in the exported page — change one, change the
    other, or the project calendar and the export will disagree.
    """
    days = _occupied_days(card)
    hidden = set(card.get("hide_days") or [])
    return [d for d in days if d not in hidden] or days[:1]


def _names(card: dict) -> str:
    """How a card is referred to in the activity list."""
    title = (card.get("title") or "").strip()
    return f"“{title}”" if title else "an untitled card"


def _split_pinned(on_day: list[dict]) -> tuple[list[dict], list[dict]]:
    """Hotels ride above the day's sections — you sleep there, you don't do it."""
    pinned = [c for c in on_day if kind_of(c.get("type", "custom")) == "stay"]
    ids = {id(c) for c in pinned}
    return pinned, [c for c in on_day if id(c) not in ids]


def _calendar(trip: dict, cards: list[dict]) -> dict:
    """Month grids over the trip range, with the cards that land on each day."""
    by_day: dict[str, list[dict]] = {}
    for card in cards:
        for key in _card_days(card):
            by_day.setdefault(key, []).append(card)

    start, end = _as_date(trip.get("start_date", "")), _as_date(trip.get("end_date", ""))
    if start is None or end is None:
        return {"months": [], "selected": ""}
    if end < start:
        end = start

    months = []
    cursor = start.replace(day=1)
    while cursor <= end:
        month_end = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        first, last = max(start, cursor), min(end, month_end)
        days = []
        day = first
        while day <= last:
            key = day.isoformat()
            on_day = by_day.get(key, [])
            seen: list[str] = []
            for card in on_day:
                if card.get("type") not in seen:
                    seen.append(card.get("type"))
            pinned, rest = _split_pinned(on_day)
            days.append({
                "key": key,
                "num": day.day,
                "short": f"{day.day} {MON3[day.month - 1]}",
                "label": f"{DAY3[day.weekday()]} {day.day} {MON3[day.month - 1]}",
                "cards": on_day,
                "pinned": pinned,
                # every section, empty ones included: they are the drop targets
                # a drag needs, and the template only shows those while dragging
                "groups": [{"key": s, "label": SECTION_LABELS[s],
                            "cards": [c for c in rest if section_of(c) == s]}
                           for s in SECTION_ORDER],
                "types": seen,
            })
            day += timedelta(days=1)
        months.append({
            "label": f"{MONTHS[cursor.month - 1]} {cursor.year}",
            "lead": first.weekday(),           # Monday-first grid
            "trail": 6 - last.weekday(),
            "days": days,
        })
        cursor = month_end + timedelta(days=1)

    keys = [d["key"] for m in months for d in m["days"]]
    today = date.today().isoformat()
    if today in keys:
        selected = today
    else:
        selected = next((k for k in keys if by_day.get(k)), keys[0] if keys else "")
    return {"months": months, "selected": selected}


def _hidden_days(form, card: dict) -> list[str]:
    """Days the card spans but should not appear on.

    The editor posts one `show_day` per ticked day plus a `day_toggles` marker,
    so an absent marker (an older form, or a request that never had the control)
    leaves whatever was stored alone. A card is never hidden from every one of
    its days — the first one always survives, or it would vanish from the trip.
    """
    occupied = _occupied_days(card)
    if not occupied:
        return []
    if (form.get("day_toggles") or "") == "1":
        shown = set(form.getlist("show_day"))
        hidden = [d for d in occupied if d not in shown]
    else:
        kept = set(card.get("hide_days") or [])       # `card` starts as a copy of the stored one
        hidden = [d for d in occupied if d in kept]
    return hidden[1:] if len(hidden) == len(occupied) else hidden


def _form_links(form, current) -> list[dict]:
    """The link editor's rows, in their on-screen order: one `link_text` and one
    `link_url` per row, plus a `links_form` marker. A form without the marker (the
    agent API goes through _form_to_card too) leaves the stored links alone, the
    same way `day_toggles` guards hide_days — or every API edit would wipe them."""
    if (form.get("links_form") or "") != "1":
        return clean_links(current)
    pairs = zip(form.getlist("link_text"), form.getlist("link_url"))
    return clean_links({"text": text, "url": url} for text, url in pairs)


def _form_to_card(form, existing: dict | None = None) -> dict:
    card = dict(existing or {})
    card.setdefault("id", storage.new_id("c"))
    was_at = (existing or {}).get("start_time") or ""
    for field in CARD_FIELDS:
        card[field] = (form.get(field) or "").strip()
    card["type"] = card["type"] or "custom"
    if card["mode"] not in modes_of(card["type"]):        # only transit has modes; anything else is dropped
        card["mode"] = ""

    chosen = (form.get("section") or "").strip()
    card["section"] = chosen if chosen in SECTION_LABELS and chosen else ""

    # The clock seeds the order; a drag overrules it and is kept — until the
    # time itself changes, which is a fresh statement about when this happens.
    if existing is None or card["start_time"] != was_at or "sort" not in card:
        card["sort"] = default_sort(card)

    kind = kind_of(card["type"])
    if kind in ("stay", "hire") and not card["end_date"]:
        card["end_date"] = card["start_date"]
    if kind == "point":
        card["end_date"] = ""

    card["details"] = [ln.strip() for ln in (form.get("details") or "").splitlines() if ln.strip()]
    card["links"] = _form_links(form, (existing or {}).get("links"))
    card["hide_days"] = _hidden_days(form, card)

    raw_price = (form.get("price_ntd") or "").replace(",", "").replace("NT$", "").strip()
    try:
        card["price_ntd"] = int(round(float(raw_price))) if raw_price else 0
    except ValueError:
        card["price_ntd"] = 0

    card.setdefault("docs", [])
    # The editor's document rows post their ids in on-screen order (`doc_order`, plus the
    # `doc_order_form` marker): that order is the viewer's tab order, and a document whose
    # row was removed with ✕ leaves the card. Without the marker (the agent API) the
    # documents are left exactly as stored. New uploads are appended after this (save_card).
    if (form.get("doc_order_form") or "") == "1":
        by_id = {d.get("id"): d for d in card["docs"]}
        order = [i for i in form.getlist("doc_order") if i in by_id]
        card["docs"] = [by_id[i] for i in dict.fromkeys(order)]
    return card


# ------------------------------------------------------------------ trips
@app.get("/")
def index():
    return render_template("trips.html", trips=storage.list_trips())


@app.post("/trips")
def create_trip():
    name = (request.form.get("name") or "").strip() or "New project"
    title = (request.form.get("title") or "").strip() or name
    start = (request.form.get("start_date") or "").strip() or date.today().isoformat()
    end = (request.form.get("end_date") or "").strip() or start
    trip = {
        "id": storage.unique_trip_id(name),
        "name": name,
        "title": title,
        "eyebrow": (request.form.get("eyebrow") or "").strip(),
        "subtitle": "",
        "start_date": start,
        "end_date": end,
        "tags": [],
        "notes": "",
        "day_notes": {},
        "cards": [],
    }
    storage.save_trip(trip)
    return redirect(url_for("edit_trip", trip_id=trip["id"]))


@app.get("/trips/<trip_id>")
def edit_trip(trip_id: str):
    trip = _trip_or_404(trip_id)
    exports = []
    for kind in storage.EXPORT_KINDS:
        exports.extend(storage.list_exports(trip_id, kind))
    exports.sort(key=lambda e: e["sort"], reverse=True)
    cards = _sorted_cards(trip)
    cal = _calendar(trip, cards)
    return render_template(
        "trip.html",
        trip=trip,
        cards=cards,
        types=TYPES,
        auto_subtitle=default_subtitle(trip),
        exports=exports,
        project_path=storage.project_dir(trip_id),
        months=cal["months"],
        selected_day=cal["selected"],
        dow=DAY3,
        today=date.today().isoformat(),
        all_trips=storage.list_trips(),     # targets for "copy card to…"
        pinned_label=PINNED_LABEL,
        history=storage.list_history(trip_id),
    )


@app.post("/trips/<trip_id>")
def update_trip(trip_id: str):
    trip = _trip_or_404(trip_id)
    storage.snapshot(trip_id, "Edited project details")
    new_name = (request.form.get("name") or "").strip() or trip.get("name", trip_id)
    trip["name"] = new_name
    trip["title"] = (request.form.get("title") or trip["title"]).strip() or new_name
    trip["eyebrow"] = (request.form.get("eyebrow") or "").strip()
    trip["subtitle"] = (request.form.get("subtitle") or "").strip()
    trip["start_date"] = (request.form.get("start_date") or "").strip()
    trip["end_date"] = (request.form.get("end_date") or "").strip()
    trip["notes"] = (request.form.get("notes") or "").strip()
    trip["tags"] = [t.strip() for t in (request.form.get("tags") or "").split(",") if t.strip()]
    day_notes = {}
    for line in (request.form.get("day_notes") or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k and v:
                day_notes[k] = v
    trip["day_notes"] = day_notes
    storage.save_trip(trip)

    # the folder follows the project name
    new_id = storage.rename_project(trip_id, new_name)
    if new_id != trip_id:
        flash(f"Project details saved — folder renamed to “{new_id}”.")
    else:
        flash("Project details saved.")
    return redirect(url_for("edit_trip", trip_id=new_id, restore=1))


ITEM_KEY = re.compile(r"[A-Za-z0-9_-]{1,40}")


@app.post("/trips/<trip_id>/items")
def save_trip_items(trip_id: str):
    """Save the trip's documents and links (the 📎 bar on the live calendar) in on-screen order.

    Every row posts `item_key`, `item_kind`, `item_text` and `item_url`, in the same order, so they
    zip into rows. A document row also posts `keep_<key>` once per file it still has, in on-screen
    order (a file whose ✕ was clicked simply stops being posted) and its new uploads as `files_<key>`. The key is the
    item's id for a saved item and a throwaway one for a new row; new items get their id here.
    """
    trip = _trip_or_404(trip_id)
    old = {i.get("id"): i for i in trip_items(trip)}
    form = request.form
    rows = list(zip(form.getlist("item_key"), form.getlist("item_kind"),
                    form.getlist("item_text"), form.getlist("item_url")))
    # Snapshot BEFORE any upload is written (see save_card): the sweeper inside it would
    # otherwise see files that no trip.json mentions yet.
    storage.snapshot(trip_id, "Edited trip documents and links")
    items, errors = [], []
    for key, kind, text, url in rows:
        if not ITEM_KEY.fullmatch(key or ""):
            continue
        text = (text or "").strip()[:LINK_TEXT_MAX]
        if kind == "link":
            address = clean_link(url)
            if address:
                items.append({"id": key if key in old else storage.new_id("t"), "kind": "link",
                              "text": text, "url": address})
            elif (url or "").strip():
                errors.append(f"“{text or url.strip()}” is not a web address, so it was left out.")
            continue
        if kind != "doc":
            continue
        prev = old.get(key) if (old.get(key) or {}).get("kind") == "doc" else {}
        # kept files in the order their rows are on screen (the ⠿ file grips), new uploads after
        by_id = {d.get("id"): d for d in prev.get("docs", [])}
        docs = [by_id[i] for i in dict.fromkeys(form.getlist(f"keep_{key}")) if i in by_id]
        for file in request.files.getlist(f"files_{key}"):
            if not file or not file.filename:
                continue
            blob = file.read()
            if not blob:
                continue
            if len(blob) > MAX_UPLOAD_MB * 1024 * 1024:
                errors.append(f"{file.filename} is larger than {MAX_UPLOAD_MB} MB.")
                continue
            try:
                doc = storage.store_document(trip["id"], file.filename, file.mimetype, blob)
            except ValueError as exc:
                errors.append(f"{file.filename}: {exc}")
                continue
            if doc.get("mime") == storage.PDF_MIME and not doc.get("pages"):
                why = storage.renderer_status() or "the file could not be rasterised"
                flash(f"{file.filename} was attached, but its pages could not be rendered — {why}.")
            docs.append(doc)
        if not docs and not text:
            continue                           # an empty new row
        items.append({"id": prev.get("id") or storage.new_id("t"), "kind": "doc",
                      "text": text or "Document", "docs": docs})
    trip["items"] = items
    trip.pop("links", None)                    # folded into items (trip_items reads old trips)
    storage.save_trip(trip)
    for e in errors:
        flash(e)
    flash("Trip documents and links saved.")
    return redirect(url_for("edit_trip", trip_id=trip_id, restore=1))


@app.post("/trips/<trip_id>/delete")
def remove_trip(trip_id: str):
    _trip_or_404(trip_id)
    storage.delete_trip(trip_id)
    flash("Trip deleted.")
    return redirect(url_for("index"))


# ------------------------------------------------------------------ cards
@app.get("/trips/<trip_id>/cards/new")
def new_card(trip_id: str):
    trip = _trip_or_404(trip_id)
    # ?date= comes from the calendar view, so a day's + button lands pre-filled
    picked = _as_date(request.args.get("date", ""))
    blank = {"id": "", "type": request.args.get("type", "hotel"), "details": [],
             "docs": [], "price_ntd": 0, "section": request.args.get("section", ""),
             "start_date": picked.isoformat() if picked else trip.get("start_date", ""),
             "end_date": ""}
    return render_template("card_form.html", trip=trip, card=blank, types=TYPES,
                           kinds=KINDS, is_new=True, sections=SECTIONS)


@app.get("/trips/<trip_id>/cards/<card_id>")
def edit_card(trip_id: str, card_id: str):
    trip = _trip_or_404(trip_id)
    card = _card_or_404(trip, card_id)
    return render_template("card_form.html", trip=trip, card=card, types=TYPES,
                           kinds=KINDS, is_new=False, sections=SECTIONS)


@app.post("/trips/<trip_id>/cards")
def save_card(trip_id: str):
    trip = _trip_or_404(trip_id)
    card_id = (request.form.get("id") or "").strip()
    existing = None
    if card_id:
        existing = _card_or_404(trip, card_id)

    card = _form_to_card(request.form, existing)

    errors = []
    if not card["start_date"]:
        errors.append("A start date is required.")
    if kind_of(card["type"]) in ("stay", "hire") and card["end_date"] < card["start_date"]:
        errors.append("The end date cannot be before the start date.")

    if not errors:
        # Snapshot BEFORE any upload is written. Taking it afterwards means the
        # sweeper inside it runs against a trip.json that doesn't mention the
        # new card yet, and deletes the file that was just uploaded.
        storage.snapshot(trip_id, f"{'Edited' if existing else 'Added'} {_names(card)}")
        for file in request.files.getlist("documents"):
            if not file or not file.filename:
                continue
            blob = file.read()
            if not blob:
                continue
            if len(blob) > MAX_UPLOAD_MB * 1024 * 1024:
                errors.append(f"{file.filename} is larger than {MAX_UPLOAD_MB} MB.")
                continue
            try:
                doc = storage.store_document(trip["id"], file.filename, file.mimetype, blob)
            except ValueError as exc:
                errors.append(f"{file.filename}: {exc}")
                continue
            # a PDF that rendered no pages shows as "no preview" in the export,
            # so say so now rather than letting it be discovered on a phone
            if doc.get("mime") == storage.PDF_MIME and not doc.get("pages"):
                why = storage.renderer_status() or "the file could not be rasterised"
                flash(f"{file.filename} was attached, but its pages could not be "
                      f"rendered — {why}. The container log has the detail; "
                      f"“⟳ Re-render” on the card retries it.")
            card["docs"].append(doc)

    if errors:
        for e in errors:
            flash(e)
        return render_template("card_form.html", trip=trip, card=card, types=TYPES,
                               kinds=KINDS, is_new=existing is None, sections=SECTIONS)

    if existing is None:
        trip.setdefault("cards", []).append(card)
    else:
        trip["cards"] = [card if c.get("id") == card["id"] else c for c in trip["cards"]]

    storage.save_trip(trip)
    flash("Card saved.")
    # `restore` tells the project page to put the calendar and the scroll
    # position back where they were when the editor was opened
    return redirect(url_for("edit_trip", trip_id=trip_id, restore=1))


@app.post("/trips/<trip_id>/card-preview")
def card_preview(trip_id: str):
    """Render the card being edited, exactly as the export would."""
    _trip_or_404(trip_id)
    card = _form_to_card(request.form)
    try:
        count = max(0, min(20, int(request.form.get("doc_count") or 0)))
    except ValueError:
        count = 0
    card["docs"] = [{"id": f"p{i}", "pages": []} for i in range(count)]
    return Response(build_card_preview(card), mimetype="text/html")


@app.post("/trips/<trip_id>/cards/<card_id>/delete")
def remove_card(trip_id: str, card_id: str):
    trip = _trip_or_404(trip_id)
    card = _card_or_404(trip, card_id)
    storage.snapshot(trip_id, f"Deleted {_names(card)}")
    # the documents stay on disk until no snapshot wants them either
    trip["cards"] = [c for c in trip["cards"] if c.get("id") != card_id]
    storage.save_trip(trip)
    flash("Card deleted.")
    return redirect(url_for("edit_trip", trip_id=trip_id, restore=1))


@app.post("/trips/<trip_id>/cards/<card_id>/duplicate")
def duplicate_card(trip_id: str, card_id: str):
    trip = _trip_or_404(trip_id)
    card = _card_or_404(trip, card_id)
    storage.snapshot(trip_id, f"Duplicated {_names(card)}")
    clone = json.loads(json.dumps(card))
    clone["id"] = storage.new_id("c")
    clone["docs"] = []          # documents stay with the original
    clone["title"] = (clone.get("title") or "") + " (copy)"
    trip["cards"].append(clone)
    storage.save_trip(trip)
    return redirect(url_for("edit_card", trip_id=trip_id, card_id=clone["id"]))


@app.post("/trips/<trip_id>/cards/<card_id>/place")
def place_card(trip_id: str, card_id: str):
    """Drop a dragged card into a section, between two of its neighbours.

    The card keeps a `sort` value on the same scale as a clock time (minutes
    past midnight), so it lands halfway between the two cards it was dropped
    between and nothing else in the day has to move. When that gap gets too
    small to halve again the whole section is respread — rare, but a day
    rearranged fifty times would otherwise run out of room between two values.
    """
    trip = _trip_or_404(trip_id)
    card = _card_or_404(trip, card_id)
    storage.snapshot(trip_id, f"Moved {_names(card)}")

    wanted = (request.form.get("section") or "").strip()
    card["section"] = wanted if wanted in SECTION_LABELS and wanted else ""

    by_id = {c.get("id"): c for c in trip.get("cards", [])}

    def sort_of(other_id: str):
        other = by_id.get((other_id or "").strip())
        return _position(other) if other else None

    above = sort_of(request.form.get("after", ""))     # the card it now sits below
    below = sort_of(request.form.get("before", ""))    # and the one it sits above

    if above is None and below is None:
        card["sort"] = default_sort(card)
    elif above is None:
        card["sort"] = below - 1
    elif below is None:
        card["sort"] = above + 1
    else:
        card["sort"] = (above + below) / 2

    day = (request.form.get("day") or "").strip()
    if above is not None and below is not None and abs(below - above) < 0.002:
        _respread(trip, day, card["section"])

    storage.save_trip(trip)
    return Response(status=204)


def _respread(trip: dict, day: str, section: str) -> None:
    """Give one section's cards whole-number positions again, order unchanged."""
    members = [c for c in trip.get("cards", [])
               if section_of(c) == section and (not day or day in _card_days(c))]
    members.sort(key=_sort_key)
    if not members:
        return
    base = float(int(min(_position(c) for c in members)))
    for i, c in enumerate(members):
        c["sort"] = base + i


@app.post("/trips/<trip_id>/cards/<card_id>/copy")
def copy_card(trip_id: str, card_id: str):
    """Copy a card onto another date, in this project or another one.

    The whole span moves together, so a 5-night hotel stays 5 nights, and the
    per-day hide list moves with it. Documents are physically copied (never
    shared) — deleting either card has to leave the other one intact.
    """
    trip = _trip_or_404(trip_id)
    card = _card_or_404(trip, card_id)

    target_id = (request.form.get("target_trip") or "").strip() or trip_id
    target = trip if target_id == trip_id else _trip_or_404(target_id)
    storage.snapshot(target_id, f"Copy of {_names(card)} added")

    old_start = _as_date(card.get("start_date", ""))
    new_start = _as_date(request.form.get("start_date", "")) or old_start
    shift = (new_start - old_start).days if (new_start and old_start) else 0

    def moved(value: str) -> str:
        d = _as_date(value)
        return (d + timedelta(days=shift)).isoformat() if d else value

    clone = json.loads(json.dumps(card))
    clone["id"] = storage.new_id("c")
    clone["start_date"] = moved(card.get("start_date", ""))
    clone["end_date"] = moved(card.get("end_date", ""))
    clone["hide_days"] = [moved(d) for d in card.get("hide_days") or []]

    clone["docs"] = []
    if request.form.get("copy_docs"):
        for doc in card.get("docs", []):
            copied = storage.copy_document(trip_id, target_id, doc)
            if copied:
                clone["docs"].append(copied)

    target.setdefault("cards", []).append(clone)
    storage.save_trip(target)

    where = "this project" if target is trip else f"“{target.get('name', target_id)}”"
    flash(f"Copied to {where} on {clone['start_date']}.")
    start, end = _as_date(target.get("start_date", "")), _as_date(target.get("end_date", ""))
    days = [_as_date(d) for d in _card_days(clone)]
    if start and end and days and not (start <= days[0] and days[-1] <= end):
        flash(f"Some of its days now fall outside the project’s own range "
              f"({target.get('start_date')} → {target.get('end_date')}), so they have no "
              f"cell on the calendar — widen the project dates to see them.")
    return redirect(url_for("edit_trip", trip_id=trip_id, restore=1))


@app.post("/trips/<trip_id>/cards/<card_id>/docs/<doc_id>/delete")
def remove_doc(trip_id: str, card_id: str, doc_id: str):
    trip = _trip_or_404(trip_id)
    card = _card_or_404(trip, card_id)
    storage.snapshot(trip_id, f"Removed a document from {_names(card)}")
    keep = []
    for doc in card.get("docs", []):
        if doc.get("id") == doc_id:
            pass                     # the file goes when no snapshot wants it
        else:
            keep.append(doc)
    card["docs"] = keep
    storage.save_trip(trip)
    return redirect(url_for("edit_card", trip_id=trip_id, card_id=card_id))


@app.post("/trips/<trip_id>/cards/<card_id>/docs/<doc_id>/rerender")
def rerender_doc(trip_id: str, card_id: str, doc_id: str):
    """Retry rasterising a PDF that is already attached."""
    trip = _trip_or_404(trip_id)
    card = _card_or_404(trip, card_id)
    doc = next((d for d in card.get("docs", []) if d.get("id") == doc_id), None)
    if doc is None:
        abort(404)

    storage.snapshot(trip_id, f"Re-rendered a document on {_names(card)}")
    pages = storage.rerender_document(trip["id"], doc)
    doc["pages"] = pages
    storage.save_trip(trip)
    if pages:
        flash(f"Rendered {len(pages)} page{'' if len(pages) == 1 else 's'}.")
    else:
        why = storage.renderer_status() or "the file could not be rasterised"
        flash(f"Still no pages — {why}. See the container log for the reason.")
    return redirect(url_for("edit_card", trip_id=trip_id, card_id=card_id))


@app.post("/trips/<trip_id>/history/<snap_id>/revert")
def revert_trip(trip_id: str, snap_id: str):
    """Put the trip back the way that history entry describes it being before.

    The revert is itself a change, so it takes its own snapshot first — undoing
    a revert is just another entry in the list.
    """
    _trip_or_404(trip_id)
    try:
        was = storage.restore_snapshot(trip_id, snap_id)
    except ValueError:
        flash("That restore point is no longer there.")
        return redirect(url_for("edit_trip", trip_id=trip_id, restore=1))

    entry = next((h for h in storage.list_history(trip_id) if h["id"] == snap_id), None)
    # the label is left as it was written — lower-casing it mangles card titles
    storage.snapshot(trip_id, f"Reverted — {entry['label']}" if entry else "Reverted")
    storage.save_trip(was)
    flash(f"Reverted to {entry['when']}." if entry else "Reverted.")
    return redirect(url_for("edit_trip", trip_id=trip_id, restore=1))


@app.get("/uploads/<trip_id>/<path:name>")
def serve_upload(trip_id: str, name: str):
    folder = storage.uploads_for(trip_id)
    return send_from_directory(folder, name)


# ------------------------------------------------------------------ output
def _render(trip_id: str) -> tuple[dict, str]:
    trip = _trip_or_404(trip_id)
    html = build_html(trip, storage.uploads_for(trip["id"]))
    return trip, html


@app.get("/trips/<trip_id>/preview")
def preview(trip_id: str):
    _, html = _render(trip_id)
    return Response(html, mimetype="text/html")


# ------------------------------------------------------------------ live link
# A per-trip secret address (/live/<token>) for sharing the live page read-only. The proxy
# lets /live/ through without a password; the token is the access check. It survives renames
# (see storage.LIVE_TOKEN_FILE) and "New link" revokes it.
# A wrong or revoked token gets a byte-identical copy of the proxy's own (NPM/openresty) 404
# page, so a guessed /live/ address looks like nothing is there at all.
PROXY_404 = (b"<html>\r\n<head><title>404 Not Found</title></head>\r\n<body>\r\n"
             b"<center><h1>404 Not Found</h1></center>\r\n<hr><center>openresty</center>\r\n"
             b"</body>\r\n</html>\r\n")


def _not_found() -> Response:
    return Response(PROXY_404, 404, content_type="text/html")


def _live_headers(resp: Response) -> Response:
    resp.headers["Referrer-Policy"] = "no-referrer"      # don't leak the token to Maps etc.
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@app.get("/live/", defaults={"token": ""})
@app.get("/live/<path:token>")
def live_page(token: str):
    """The live page carries only the itinerary (~0.2 MB); documents are fetched one at a
    time when opened (live_doc_page / live_doc_file). It used to embed all of them —
    ~32 MB on every open, which matters on roaming data. The downloaded file still
    embeds everything (download), for offline use.

    `private, no-cache` + an ETag: the phone may keep the page but asks each time,
    and an unchanged trip answers 304 with no body."""
    trip_id = storage.trip_for_live_token(token)
    if trip_id is None:
        return _not_found()
    trip = _trip_or_404(trip_id)
    base = url_for("live_page", token=token).rstrip("/") + "/doc/"
    html = build_html(trip, storage.uploads_for(trip["id"]), doc_base=base)
    resp = Response(html, mimetype="text/html")
    resp.set_etag(hashlib.sha256(html.encode("utf-8")).hexdigest()[:32])
    resp.headers["Cache-Control"] = "private, no-cache"
    return _live_headers(resp.make_conditional(request))


def _live_doc(token: str, doc_id: str):
    """(trip id, doc record) for a document on the trip this token opens, else (None, None)."""
    trip_id = storage.trip_for_live_token(token)
    trip = storage.load_trip(trip_id) if trip_id else None
    if trip is None:
        return None, None
    doc = next((d for d in storage.all_docs(trip) if d.get("id") == doc_id), None)
    return (trip_id, doc) if doc else (None, None)


def _send_upload(trip_id: str, name: str, mime: str, download_name: str | None = None) -> Response:
    path = storage.uploads_for(trip_id) / Path(str(name)).name
    if not name or not path.is_file():
        return _not_found()
    resp = send_file(path, mimetype=mime, conditional=True,
                     as_attachment=download_name is not None, download_name=download_name)
    # The URL carries ?v=<file stamp> (collect_docs), so a changed file has a new address
    # and this one never changes: the phone can keep it.
    resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return _live_headers(resp)


@app.get("/live/<token>/doc/<doc_id>/p/<int:n>")
def live_doc_page(token: str, doc_id: str, n: int):
    """One page image of a document (a PDF's rendered page, or the image itself)."""
    trip_id, doc = _live_doc(token, doc_id)
    pages = (doc or {}).get("pages") or []
    if doc is None or not 0 <= n < len(pages):
        return _not_found()
    mime = "image/jpeg" if doc.get("mime") == storage.PDF_MIME else (doc.get("mime") or "image/jpeg")
    return _send_upload(trip_id, pages[n], mime)


@app.get("/live/<token>/doc/<doc_id>/file")
def live_doc_file(token: str, doc_id: str):
    """The document to download: a PDF's original, or the image itself."""
    trip_id, doc = _live_doc(token, doc_id)
    if doc is None:
        return _not_found()
    if doc.get("file"):
        return _send_upload(trip_id, doc["file"], doc.get("mime") or "application/octet-stream",
                            download_name=doc.get("name") or Path(str(doc["file"])).name)
    pages = doc.get("pages") or []
    if not pages:
        return _not_found()
    return _send_upload(trip_id, pages[0], doc.get("mime") or "image/jpeg",
                        download_name=doc.get("name") or Path(str(pages[0])).name)


@app.post("/trips/<trip_id>/live-link/reset")
def reset_live_link(trip_id: str):
    trip = _trip_or_404(trip_id)
    storage.reset_live_token(trip["id"])
    flash("New live link made. The old one no longer works.")
    return redirect(url_for("edit_trip", trip_id=trip["id"]))


app.jinja_env.globals["live_url"] = lambda trip_id: url_for("live_page", token=storage.live_token(trip_id))


@app.get("/trips/<trip_id>/download")
def download(trip_id: str):
    """Send the page and keep a timestamped copy in the project folder."""
    trip, html = _render(trip_id)
    blob = html.encode("utf-8")
    saved = storage.save_export(trip, "html", blob)
    return send_file(io.BytesIO(blob), mimetype="text/html",
                     as_attachment=True, download_name=saved.name)


@app.get("/trips/<trip_id>/export.json")
def export_json(trip_id: str):
    trip = _trip_or_404(trip_id)
    blob = json.dumps(trip, ensure_ascii=False, indent=2).encode("utf-8")
    saved = storage.save_export(trip, "json", blob)
    return send_file(io.BytesIO(blob), mimetype="application/json",
                     as_attachment=True, download_name=saved.name)


@app.get("/trips/<trip_id>/exports/<kind>/<path:name>")
def download_export(trip_id: str, kind: str, name: str):
    _trip_or_404(trip_id)
    try:
        path = storage.safe_export_path(trip_id, kind, name)
    except ValueError:
        abort(404)
    mime = storage.EXPORT_KINDS[kind][2]
    return send_file(path, mimetype=mime, as_attachment=True, download_name=path.name)


@app.post("/trips/<trip_id>/exports/<kind>/<path:name>/delete")
def remove_export(trip_id: str, kind: str, name: str):
    _trip_or_404(trip_id)
    label = Path(name).name
    try:
        storage.delete_export(trip_id, kind, name)
        flash(f"Deleted {label}.")
    except ValueError:
        flash(f"{label} is no longer there.")
    except OSError as exc:
        # e.g. Windows refuses while the file is still being served
        flash(f"Could not delete {label}: {exc.strerror or exc}")
    return redirect(url_for("edit_trip", trip_id=trip_id, restore=1))


@app.get("/trips/<trip_id>/export.zip")
def export_zip(trip_id: str):
    _trip_or_404(trip_id)
    blob = storage.export_zip(trip_id)
    return send_file(io.BytesIO(blob), mimetype="application/zip",
                     as_attachment=True, download_name=f"{storage.safe_id(trip_id)}.zip")


@app.get("/backup.zip")
def backup_all():
    blob = storage.backup_all_zip()
    stamp = date.today().isoformat()
    return send_file(io.BytesIO(blob), mimetype="application/zip",
                     as_attachment=True, download_name=f"itineraries-backup-{stamp}.zip")


@app.post("/trips/<trip_id>/duplicate")
def duplicate_trip(trip_id: str):
    _trip_or_404(trip_id)
    new_id = storage.duplicate_trip(trip_id)
    flash("Trip duplicated.")
    return redirect(url_for("edit_trip", trip_id=new_id))


@app.post("/import")
def import_trip():
    file = request.files.get("payload")
    if not file or not file.filename:
        flash("Choose a .zip or .json file to import.")
        return redirect(url_for("index"))
    blob = file.read()

    if file.filename.lower().endswith(".zip") or blob[:2] == b"PK":
        try:
            ids, mode = storage.import_zip(blob)
        except (ValueError, OSError) as exc:
            flash(f"Could not import that archive: {exc}")
            return redirect(url_for("index"))
        if mode == "library":
            flash(f"Restored {len(ids)} trip(s) with their documents.")
            return redirect(url_for("index"))
        flash("Imported with documents.")
        return redirect(url_for("edit_trip", trip_id=ids[0]))

    try:
        trip = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        flash(f"That file is neither a zip nor valid JSON: {exc}")
        return redirect(url_for("index"))

    trip["id"] = storage.unique_trip_id(trip.get("id") or trip.get("title") or "trip")
    trip.setdefault("cards", [])
    trip.setdefault("tags", [])
    trip.setdefault("day_notes", {})
    folder = storage.uploads_for(trip["id"])
    for card in trip["cards"]:     # JSON alone carries no files — drop dead references
        card["docs"] = [d for d in card.get("docs", [])
                        if d.get("file") and (folder / str(d["file"])).exists()]
    for item in trip.get("items") or []:
        if isinstance(item, dict) and item.get("kind") == "doc":
            item["docs"] = [d for d in item.get("docs", [])
                            if d.get("file") and (folder / str(d["file"])).exists()]
    storage.save_trip(trip)
    flash("Imported. A .json file carries no documents — use .zip to keep them.")
    return redirect(url_for("edit_trip", trip_id=trip["id"]))


# ------------------------------------------------------------------ agent API
from .api import bp as api_bp  # noqa: E402  (after the helpers it reuses)
app.register_blueprint(api_bp)


def main() -> None:
    storage.init()
    port = int(os.environ.get("PORT", "8080"))
    from waitress import serve
    print(f"Itinerary Builder listening on http://0.0.0.0:{port}  (data: {storage.DATA_DIR})")
    # a missing rasteriser only shows up much later, as PDFs with no preview
    why = storage.renderer_status()
    print(f"PDF page rendering: {'UNAVAILABLE — ' + why if why else 'ready'}")
    serve(app, host="0.0.0.0", port=port, threads=8)


if __name__ == "__main__":
    main()
