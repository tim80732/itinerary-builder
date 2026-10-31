"""JSON API for scripts and AI agents, mounted at /api/v1.

Auth: `Authorization: Bearer <ITINERARY_API_TOKEN>`. The editor's APP_PASSWORD does not work
here and this token does not work in the editor. No token set = the API answers 404.

Every write goes through the same rules as the editor (`main._form_to_card`) and takes a
history snapshot first, labelled with WHO ("<WHO>: …"), so any change can be reverted from the
project page's History panel. Deleting a trip first saves a full export zip to
DATA_DIR/deleted-trips/ — the delete is refused if that backup cannot be written.
"""
from __future__ import annotations

import hmac
import os
from datetime import datetime

from flask import Blueprint, abort, jsonify, request
from werkzeug.datastructures import MultiDict

from . import storage
from .triptypes import (CARD_FIELDS, LINK_TEXT_MAX, SECTION_LABELS, TYPES, card_meta, clean_link,
                        clean_links, kind_of, modes_of, trip_items)

bp = Blueprint("api", __name__, url_prefix="/api/v1")
WHO = "Assistim"


# ------------------------------------------------------------------ auth / errors
@bp.before_request
def require_token():
    token = os.environ.get("ITINERARY_API_TOKEN", "").strip()
    if not token:
        abort(404)
    sent = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(sent.encode(), token.encode()):
        return jsonify(error="unauthorized"), 401
    return None


@bp.errorhandler(404)
def _nf(e):
    return jsonify(error=getattr(e, "description", "not found")), 404


def _bad(msg: str, code: int = 400):
    return jsonify(error=msg), code


def _m():
    from . import main          # late import: main registers this blueprint at the end
    return main


def _trip(trip_id: str) -> dict:
    trip = storage.load_trip(trip_id)
    if trip is None:
        abort(404, description=f"no trip {trip_id!r}")
    return trip


def _card(trip: dict, card_id: str) -> dict:
    for c in trip.get("cards", []):
        if c.get("id") == card_id:
            return c
    abort(404, description=f"no card {card_id!r} in {trip.get('id')!r}")


def _doc_brief(d: dict) -> dict:
    return {"id": d.get("id"), "label": d.get("label") or d.get("name"), "name": d.get("name"),
            "mime": d.get("mime"), "pages": len(d.get("pages") or [])}


def _card_out(c: dict) -> dict:
    out = {k: v for k, v in c.items() if k != "docs"}
    out["docs"] = [_doc_brief(d) for d in c.get("docs", [])]
    return out


# ------------------------------------------------------------------ read
@bp.get("/meta")
def meta():
    return jsonify(card_types={k: v["label"] for k, v in TYPES.items()},
                   card_modes={k: {m: d["label"] for m, d in modes_of(k).items()} for k in TYPES if modes_of(k)},
                   sections={k or "(anytime)": v for k, v in SECTION_LABELS.items()},
                   card_fields=CARD_FIELDS + ["details", "price_ntd", "section", "links"])


@bp.get("/trips")
def trips():
    keep = ("id", "name", "title", "start_date", "end_date", "cards")
    return jsonify(trips=[{k: t.get(k) for k in keep} for t in storage.list_trips()])


def _summary(trip: dict, day_from: str, day_to: str) -> str:
    m = _m()
    cards = m._sorted_cards(trip)
    by_day: dict[str, list] = {}
    for c in cards:
        for d in m._card_days(c):
            by_day.setdefault(d, []).append(c)
    lines = [f"{trip.get('name')} — “{trip.get('title')}” {trip.get('start_date')} → {trip.get('end_date')}, "
             f"{len(cards)} cards (id {trip.get('id')})"]
    if trip.get("tags"):
        lines.append("Tags: " + ", ".join(trip["tags"]))
    if trip.get("notes"):
        lines.append("Notes: " + trip["notes"])
    home = _items_out(trip)
    if home:
        lines.append("Home page (📎 trip documents and links, in order):")
        for i in home:
            if i["kind"] == "link":
                lines.append(f"  - 🔗 {i['text'] or i['url']} — {i['url']} [item {i['id']}]")
            else:
                files = ", ".join(f"{f['name']} [file {f['id']}]" for f in i["files"]) or "no files"
                lines.append(f"  - 📄 {i['text']}: {files} [item {i['id']}]")
    for day in sorted(by_day):
        if (day_from and day < day_from) or (day_to and day > day_to):
            continue
        d = m._as_date(day)
        head = f"\n== {m.DAY3[d.weekday()]} {d.day} {m.MON3[d.month - 1]} ({day})"
        if trip.get("day_notes", {}).get(day):
            head += f" — {trip['day_notes'][day]}"
        lines.append(head)
        for c in by_day[day]:
            t = c.get("start_time") or ""
            if c.get("end_time"):
                t += f"–{c['end_time']}"
            where = c.get("place") or ""
            if c.get("from_label") or c.get("to_label"):
                where = f"{c.get('from_label', '')} → {c.get('to_label', '')}".strip()
            bits = [t, c.get("icon") or card_meta(c).get("icon", ""),
                    c.get("title") or "(untitled)"]
            if kind_of(c.get("type", "custom")) == "stay":
                bits.insert(0, "[stay]")
            text = " ".join(b for b in bits if b)
            if where:
                text += f" · {where}"
            if c.get("alert"):
                text += f" ⚠ {c['alert']}"
            if c.get("price_ntd"):
                text += f" · NT${c['price_ntd']:,}"
            if c.get("docs"):
                text += f" · {len(c['docs'])} doc(s)"
            if c.get("links"):
                text += f" · {len(c['links'])} link(s)"
            lines.append(f"  - {text} [card {c.get('id')}]")
    return "\n".join(lines)


@bp.get("/trips/<trip_id>")
def trip_get(trip_id: str):
    trip = _trip(trip_id)
    if request.args.get("format") == "full":
        out = {k: v for k, v in trip.items() if k != "cards"}
        out["cards"] = [_card_out(c) for c in _m()._sorted_cards(trip)]
        return jsonify(out)
    return jsonify(id=trip["id"], summary=_summary(trip, request.args.get("from", ""), request.args.get("to", "")))


@bp.get("/trips/<trip_id>/cards/<card_id>")
def card_get(trip_id: str, card_id: str):
    return jsonify(_card_out(_card(_trip(trip_id), card_id)))


# ------------------------------------------------------------------ home page: trip documents and links
# The trip's own 📎 documents and links (trip["items"], the bar on the live calendar; the owner calls
# it the "home page"). New items are only ever appended; PATCH reorders, renames and removes
# existing ones but never creates any, so every new link or file arrives through a call that
# names exactly what it adds.
def _items_out(trip: dict) -> list[dict]:
    out = []
    for i in trip_items(trip):
        if i["kind"] == "link":
            out.append({"id": i["id"], "kind": "link", "text": i.get("text", ""), "url": i.get("url", "")})
        else:
            out.append({"id": i["id"], "kind": "doc", "text": i.get("text", ""),
                        "files": [_doc_brief(d) for d in i.get("docs") or []]})
    return out


def _save_items(trip: dict, items: list[dict]) -> None:
    trip["items"] = items
    trip.pop("links", None)                  # folded into items (see triptypes.trip_items)
    storage.save_trip(trip)


@bp.get("/trips/<trip_id>/items")
def items_get(trip_id: str):
    return jsonify(items=_items_out(_trip(trip_id)))


@bp.post("/trips/<trip_id>/items/link")
def items_add_link(trip_id: str):
    trip = _trip(trip_id)
    body = request.get_json(silent=True) or {}
    url = clean_link(str(body.get("url") or ""))
    if not url:
        return _bad("url must be an http(s) address")
    text = str(body.get("text") or "").strip()[:LINK_TEXT_MAX]
    storage.snapshot(trip_id, f"{WHO}: added the link “{text or url}” to the trip documents and links")
    items = trip_items(trip) + [{"id": storage.new_id("t"), "kind": "link", "text": text, "url": url}]
    _save_items(trip, items)
    return jsonify(items=_items_out(trip)), 201


@bp.post("/trips/<trip_id>/items/documents")
def items_add_document(trip_id: str):
    """One file per call: into document item `item_id`, or a new item named `text`."""
    trip = _trip(trip_id)
    file = request.files.get("file")
    if not file or not file.filename:
        return _bad("multipart field 'file' is required")
    items = trip_items(trip)
    item_id = (request.form.get("item_id") or "").strip()
    text = (request.form.get("text") or "").strip()[:LINK_TEXT_MAX]
    item = None
    if item_id:
        item = next((i for i in items if i.get("id") == item_id and i["kind"] == "doc"), None)
        if item is None:
            return _bad(f"no document item {item_id!r} in this trip's documents and links")
    elif not text:
        return _bad("give item_id (add to an existing document item) or text (the name of a new one)")
    blob = file.read()
    limit = _m().MAX_UPLOAD_MB
    if not blob:
        return _bad("the file is empty")
    if len(blob) > limit * 1024 * 1024:
        return _bad(f"{file.filename} is larger than {limit} MB")
    # snapshot BEFORE writing the upload: a later snapshot's sweeper would delete it (see save_card)
    storage.snapshot(trip_id, f"{WHO}: added {file.filename} to “{item['text'] if item else text}” "
                              "in the trip documents and links")
    try:
        doc = storage.store_document(trip_id, file.filename, file.mimetype, blob,
                                     (request.form.get("label") or "").strip())
    except ValueError as exc:
        return _bad(f"{file.filename}: {exc}")
    if item is None:
        item = {"id": storage.new_id("t"), "kind": "doc", "text": text, "docs": []}
        items.append(item)
    item.setdefault("docs", []).append(doc)
    _save_items(trip, items)
    warning = ""
    if doc.get("mime") == storage.PDF_MIME and not doc.get("pages"):
        warning = f"attached, but no page previews: {storage.renderer_status() or 'rasterising failed'}"
    return jsonify(item_id=item["id"], doc=_doc_brief(doc), warning=warning, items=_items_out(trip)), 201


@bp.patch("/trips/<trip_id>/items")
def items_update(trip_id: str):
    """`items` is the full new list of EXISTING items, in order: [{id, text?, url? (links),
    files? (documents: the file ids to keep, in order)}]. An item left out is removed."""
    trip = _trip(trip_id)
    want = (request.get_json(silent=True) or {}).get("items")
    if not isinstance(want, list):
        return _bad("items must be the full new list of existing items")
    have = {i["id"]: i for i in trip_items(trip)}
    out, seen = [], set()
    for w in want:
        iid = w.get("id") if isinstance(w, dict) else None
        if iid not in have or iid in seen:
            return _bad(f"unknown or repeated item id {iid!r}; new items are added with "
                        "items/link or items/documents")
        seen.add(iid)
        cur = dict(have[iid])
        if "text" in w:
            cur["text"] = str(w.get("text") or "").strip()[:LINK_TEXT_MAX]
        if cur["kind"] == "link" and "url" in w:
            url = clean_link(str(w.get("url") or ""))
            if not url:
                return _bad(f"item {iid}: url must be an http(s) address")
            cur["url"] = url
        if cur["kind"] == "doc" and "files" in w:
            by_id = {d.get("id"): d for d in cur.get("docs") or []}
            files = w.get("files")
            if not isinstance(files, list) or any(f not in by_id for f in files):
                return _bad(f"item {iid}: files must be ids of files already in it ({sorted(by_id)})")
            cur["docs"] = [by_id[f] for f in files]
        out.append(cur)
    storage.snapshot(trip_id, f"{WHO}: edited the trip documents and links")
    _save_items(trip, out)
    return jsonify(items=_items_out(trip))


# ------------------------------------------------------------------ trips: write
@bp.post("/trips")
def trip_create():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip() or "New project"
    start = (body.get("start_date") or "").strip() or datetime.now().date().isoformat()
    trip = {"id": storage.unique_trip_id(name), "name": name,
            "title": (body.get("title") or "").strip() or name,
            "eyebrow": (body.get("eyebrow") or "").strip(), "subtitle": (body.get("subtitle") or "").strip(),
            "start_date": start, "end_date": (body.get("end_date") or "").strip() or start,
            "tags": [t.strip() for t in body.get("tags") or [] if str(t).strip()],
            "notes": (body.get("notes") or "").strip(), "day_notes": dict(body.get("day_notes") or {}),
            "cards": []}
    storage.save_trip(trip)
    return jsonify(id=trip["id"], created=True), 201


@bp.patch("/trips/<trip_id>")
def trip_update(trip_id: str):
    trip = _trip(trip_id)
    body = request.get_json(silent=True) or {}
    storage.snapshot(trip_id, f"{WHO}: edited project details")
    for key in ("title", "eyebrow", "subtitle", "start_date", "end_date", "notes"):
        if key in body:
            trip[key] = str(body[key] or "").strip()
    if "tags" in body:
        trip["tags"] = [t.strip() for t in body["tags"] or [] if str(t).strip()]
    if "day_notes" in body:                       # merge; an empty value removes that day's note
        notes = dict(trip.get("day_notes") or {})
        for k, v in (body["day_notes"] or {}).items():
            if str(v or "").strip():
                notes[k] = str(v).strip()
            else:
                notes.pop(k, None)
        trip["day_notes"] = notes
    new_id = trip_id
    if (body.get("name") or "").strip():
        trip["name"] = body["name"].strip()
        storage.save_trip(trip)
        new_id = storage.rename_project(trip_id, trip["name"])     # the folder follows the name
    else:
        storage.save_trip(trip)
    return jsonify(id=new_id, updated=True)


@bp.delete("/trips/<trip_id>")
def trip_delete(trip_id: str):
    _trip(trip_id)
    try:
        blob = storage.export_zip(trip_id)
        folder = storage.DATA_DIR / "deleted-trips"
        folder.mkdir(parents=True, exist_ok=True)
        backup = folder / f"{trip_id}-{datetime.now():%Y%m%d-%H%M%S}.zip"
        backup.write_bytes(blob)
    except Exception as exc:                       # no backup, no delete
        return _bad(f"backup failed, trip NOT deleted: {exc}", 500)
    storage.delete_trip(trip_id)
    return jsonify(id=trip_id, deleted=True, backup=str(backup),
                   restore_hint="Import this zip on the start page (Import) to restore the trip.")


# ------------------------------------------------------------------ cards: write
def _to_form(values: dict) -> MultiDict:
    form = MultiDict()
    for k, v in values.items():
        if k == "details":
            v = "\n".join(v) if isinstance(v, list) else str(v or "")
        form[k] = "" if v is None else str(v)
    return form


def _apply_links(card: dict, body: dict) -> list[str]:
    """`links` in a request body replaces the card's links ([{text, url}], display order).
    The form path (_form_to_card) never touches them for the API — it has no link editor —
    so they are set here, and refused rather than silently dropped when one isn't a web address."""
    if "links" not in body:
        return []
    raw = body.get("links") or []
    if not isinstance(raw, list) or not all(isinstance(x, dict) for x in raw):
        return ["links must be a list of {\"text\", \"url\"} objects"]
    links = clean_links(raw)
    if len(links) != len(raw):
        return ["every link needs an http(s) url"]
    card["links"] = links
    return []


def _validate(card: dict, body: dict | None = None) -> list[str]:
    errors = []
    asked = str((body or {}).get("mode") or "").strip()
    if asked and card.get("mode") != asked:                  # _form_to_card dropped a mode the type lacks
        modes = sorted(modes_of(card["type"]))
        errors.append(f"mode must be one of {modes} for type {card['type']}" if modes
                      else f"type {card['type']} has no modes (only transit does)")
    if not card["start_date"]:
        errors.append("start_date is required (YYYY-MM-DD)")
    if card["type"] not in TYPES:
        errors.append(f"type must be one of {sorted(TYPES)}")
    if kind_of(card["type"]) in ("stay", "hire") and card["end_date"] < card["start_date"]:
        errors.append("end_date cannot be before start_date")
    return errors


@bp.post("/trips/<trip_id>/cards")
def card_create(trip_id: str):
    trip = _trip(trip_id)
    body = request.get_json(silent=True) or {}
    card = _m()._form_to_card(_to_form({k: v for k, v in body.items() if k != "links"}))
    if errors := _validate(card, body) + _apply_links(card, body):
        return _bad("; ".join(errors))
    storage.snapshot(trip_id, f"{WHO}: added {_m()._names(card)}")
    trip.setdefault("cards", []).append(card)
    storage.save_trip(trip)
    return jsonify(_card_out(card)), 201


@bp.patch("/trips/<trip_id>/cards/<card_id>")
def card_update(trip_id: str, card_id: str):
    trip = _trip(trip_id)
    existing = _card(trip, card_id)
    body = request.get_json(silent=True) or {}
    current = {f: existing.get(f, "") for f in CARD_FIELDS}
    current.update(section=existing.get("section", ""), details=existing.get("details", []),
                   price_ntd=existing.get("price_ntd") or "")
    current.update({k: v for k, v in body.items() if k in current})
    card = _m()._form_to_card(_to_form(current), existing)
    if errors := _validate(card, body) + _apply_links(card, body):
        return _bad("; ".join(errors))
    storage.snapshot(trip_id, f"{WHO}: edited {_m()._names(card)}")
    trip["cards"] = [card if c.get("id") == card_id else c for c in trip["cards"]]
    storage.save_trip(trip)
    return jsonify(_card_out(card))


@bp.delete("/trips/<trip_id>/cards/<card_id>")
def card_delete(trip_id: str, card_id: str):
    trip = _trip(trip_id)
    card = _card(trip, card_id)
    storage.snapshot(trip_id, f"{WHO}: deleted {_m()._names(card)}")
    trip["cards"] = [c for c in trip["cards"] if c.get("id") != card_id]
    storage.save_trip(trip)
    return jsonify(id=card_id, deleted=True, undo="History panel on the project page")


@bp.post("/trips/<trip_id>/cards/<card_id>/documents")
def card_add_document(trip_id: str, card_id: str):
    trip = _trip(trip_id)
    card = _card(trip, card_id)
    file = request.files.get("file")
    if not file or not file.filename:
        return _bad("multipart field 'file' is required")
    blob = file.read()
    limit = _m().MAX_UPLOAD_MB
    if not blob:
        return _bad("the file is empty")
    if len(blob) > limit * 1024 * 1024:
        return _bad(f"{file.filename} is larger than {limit} MB")
    # snapshot BEFORE writing the upload: a later snapshot's sweeper would delete it (see save_card)
    storage.snapshot(trip_id, f"{WHO}: attached {file.filename} to {_m()._names(card)}")
    try:
        doc = storage.store_document(trip_id, file.filename, file.mimetype, blob,
                                     (request.form.get("label") or "").strip())
    except ValueError as exc:
        return _bad(f"{file.filename}: {exc}")
    card.setdefault("docs", []).append(doc)
    storage.save_trip(trip)
    warning = ""
    if doc.get("mime") == storage.PDF_MIME and not doc.get("pages"):
        warning = f"attached, but no page previews: {storage.renderer_status() or 'rasterising failed'}"
    return jsonify(card_id=card_id, doc=_doc_brief(doc), warning=warning), 201
