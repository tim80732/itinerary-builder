"""Import a hand-built itinerary page into Itinerary Builder.

Reads one of the original single-file HTML itineraries (the ones with static
`.entry` cards and a `const PDF_FILES = {...}` payload), converts every card
into the tool's card schema, extracts the embedded documents, and writes the
result into DATA_DIR. Also drops a portable .zip next to the script's output so
the same trip can be imported on another machine.

    python tools/import_legacy_html.py "path/to/page.html" --title "My Trip"
"""
from __future__ import annotations

import argparse
import base64
import html as html_mod
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import storage                      # noqa: E402
from app.flags import NAME_TO_CODE, split_tag  # noqa: E402
from app.triptypes import kind_of            # noqa: E402

# legacy entry class -> card type
TYPE_MAP = {
    "train": "transit",
    "plane": "plane",
    "car": "rental",
    "hotel": "hotel",
    "activity": "activity",
}
TIME_RE = re.compile(r"\b([0-2]?\d:[0-5]\d)\b")


# ----------------------------------------------------------------- helpers
def text_of(fragment: str) -> str:
    """Tags out, entities decoded, whitespace collapsed."""
    plain = re.sub(r"<[^>]+>", " ", fragment or "")
    plain = html_mod.unescape(plain).replace("\xa0", " ")
    return re.sub(r"\s+", " ", plain).strip()


def first(pattern: str, blob: str, group: int = 1) -> str:
    m = re.search(pattern, blob, re.S)
    return m.group(group) if m else ""


def strip_pin(value: str) -> str:
    return re.sub(r"^\U0001F4CD\s*", "", value).strip()


def slice_between(blob: str, start: str, stops: list[str]) -> str:
    """Text after `start` up to whichever of `stops` comes first.

    Safer than a lazy regex here: a point block ends with nested </div>s, and
    matching on those swallows the station's own closing tag.
    """
    i = blob.find(start)
    if i < 0:
        return ""
    i += len(start)
    ends = [e for e in (blob.find(s, i) for s in stops) if e >= 0]
    return blob[i:min(ends)] if ends else blob[i:]


def parse_js_object(source: str, marker: str) -> str:
    """Return the `{...}` that follows `marker`, brace-balanced."""
    start = source.index(marker) + len(marker)
    start = source.index("{", start)
    depth, i = 0, start
    in_str, quote, esc = False, "", False
    while i < len(source):
        ch = source[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
        elif ch in "\"'":
            in_str, quote = True, ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
        i += 1
    raise ValueError(f"unbalanced object after {marker!r}")


def parse_leg_docs(source: str) -> dict[str, list[str]]:
    blob = parse_js_object(source, "const LEG_DOCS =")
    out: dict[str, list[str]] = {}
    for key, body in re.findall(r"(\w+)\s*:\s*\[([^\]]*)\]", blob):
        out[key] = re.findall(r"['\"]([^'\"]+)['\"]", body)
    return out


def key_to_date(key: str, year: int) -> date:
    """'0203' -> 2027-02-03 (legacy keys are MMDD)."""
    return date(year, int(key[:2]), int(key[2:]))


# ----------------------------------------------------------------- parsing
def parse_entry(chunk: str, leg_docs: dict, year: int) -> dict | None:
    css_type = first(r'^(\w+)"', chunk)
    card_type = TYPE_MAP.get(css_type)
    if not card_type:
        return None
    kind = kind_of(card_type)

    dates = first(r'data-dates="([^"]*)"', chunk).split()
    if not dates:
        return None
    start = key_to_date(dates[0], year)
    end = key_to_date(dates[-1], year)
    if kind == "stay":                       # legacy stored nights only
        end = end + timedelta(days=1)

    leg = first(r'data-leg="([^"]*)"', chunk)
    full_title = html_mod.unescape(first(r'data-title="([^"]*)"', chunk))
    title = full_title.split(" · ")[0].strip()

    icon = text_of(first(r'<div class="node">(.*?)</div>', chunk))

    # tag: "Hotel · Booking.com" -> service fallback
    tag = text_of(first(r'<span class="tag">(.*?)</span>', chunk))
    tag_service = tag.split(" · ", 1)[1].strip() if " · " in tag else ""
    mid_service = text_of(first(r'<div class="service">(.*?)</div>', chunk))
    service = mid_service or tag_service

    # journey points
    dep = slice_between(chunk, '<div class="point depart">',
                        ['<div class="mid"', '<div class="point arrive"',
                         '<div class="details"', '<div class="price-row"'])
    arr = slice_between(chunk, '<div class="point arrive">',
                        ['<div class="details"', '<div class="price-row"'])

    def point_bits(block: str) -> tuple[str, str, str, str]:
        raw_time = text_of(first(r'<div class="time">(.*?)</div>', block))
        m = TIME_RE.search(raw_time)
        clock = m.group(1) if m else ""
        city = text_of(first(r'<div class="city">(.*?)</div>', block))
        station_html = first(r'<div class="station">(.*?)</div>', block)
        # attribute text is HTML-escaped in the source: &amp; must become &
        href = html_mod.unescape(first(r'href="([^"]*)"', station_html))
        sub = strip_pin(text_of(station_html))
        return clock, city, sub, href

    dep_time, dep_city, dep_sub, dep_map = point_bits(dep)
    arr_time, arr_city, arr_sub, arr_map = point_bits(arr)

    # place + map button (hotels, activities)
    place = text_of(first(r'<span class="hotel-city">(.*?)</span>', chunk)).lstrip("· ").strip()
    map_link = html_mod.unescape(first(r'<a class="map-btn" href="([^"]*)"', chunk))

    # details
    details_block = first(r'<div class="details">(.*?)</div>\s*<div class="price-row"', chunk) or \
        first(r'<div class="details">(.*?)</div>', chunk)
    details = [text_of(s) for s in re.findall(r"<span>(.*?)</span>", details_block, re.S)]
    details = [d for d in details if d]

    address = ""
    if kind in ("stay", "point"):
        if dep_sub and not dep_map:          # activity kept its address under the time
            address = dep_sub
        elif details:                        # hotels kept it as the first detail line
            address = details.pop(0)

    # price
    price_row = first(r'<div class="price-row">(.*?)</div>\s*</div>', chunk) or \
        first(r'<div class="price-row">(.*?)$', chunk)
    price_label = text_of(first(r'<span class="label">(.*?)</span>', price_row))
    price_note = text_of(first(r'<span class="note">(.*?)</span>', price_row))
    amount_html = first(r'<span class="amount">(.*?)</span>\s*</div>', price_row) or \
        first(r'<span class="amount">(.*?)$', price_row)
    original = text_of(first(r"<small>(.*?)</small>", amount_html)).strip("()")
    amount_text = text_of(re.sub(r"<small>.*?</small>", "", amount_html, flags=re.S))
    digits = re.sub(r"[^\d]", "", amount_text.replace("NT$", ""))
    price_ntd = int(digits) if digits else 0

    card = {
        "id": storage.new_id("c"),
        "type": card_type,
        "type_label": "",
        "icon": icon,
        "title": title,
        "place": place,
        "service": service,
        "start_date": start.isoformat(),
        "start_time": dep_time,
        "end_date": "" if kind == "point" else end.isoformat(),
        "end_time": arr_time,
        "from_label": dep_city if kind in ("move", "hire") else "",
        "from_sub": dep_sub if kind in ("move", "hire") else "",
        "from_map": dep_map if kind in ("move", "hire") else "",
        "to_label": arr_city if kind in ("move", "hire") else "",
        "to_sub": arr_sub if kind in ("move", "hire") else "",
        "to_map": arr_map if kind in ("move", "hire") else "",
        "address": address,
        "map_link": map_link,
        "details": details,
        "price_ntd": price_ntd,
        "price_original": original,
        "price_label": price_label,
        "price_note": price_note,
        "docs": [],
        "_leg": leg,
    }
    return card


def tags_from(source: str) -> list[str]:
    """Header pills back into tags, re-attaching a flag code where we know it.

    The old pages drew flags as inline SVG, so the country is only recoverable
    from the label text next to it.
    """
    out = []
    for raw in re.findall(r'<span class="city">(.*?)</span>', source, re.S):
        label = text_of(raw)
        if not label:
            continue
        code, plain = split_tag(label)
        if not code:
            code = NAME_TO_CODE.get(plain.lower(), "")
        out.append(f"{code}:{plain}" if code else plain)
    return out


def extract_documents(source: str, trip_id: str) -> dict[str, dict]:
    """Write every embedded document to the trip's uploads folder."""
    payload = json.loads(parse_js_object(source, "const PDF_FILES ="))
    folder = storage.uploads_for(trip_id)
    out: dict[str, dict] = {}

    for doc_id, entry in payload.items():
        name = entry.get("name") or f"{doc_id}.pdf"
        label = entry.get("label") or name
        data = entry.get("data") or ""
        pages_b64 = entry.get("pages") or []
        is_pdf = name.lower().endswith(".pdf")

        stored_pages: list[str] = []
        if is_pdf:
            main = f"{doc_id}.pdf"
            (folder / main).write_bytes(base64.b64decode(data))
            for n, page in enumerate(pages_b64, 1):
                page_name = f"{doc_id}_p{n}.jpg"
                (folder / page_name).write_bytes(base64.b64decode(page))
                stored_pages.append(page_name)
            mime = "application/pdf"
        else:
            ext = ".png" if name.lower().endswith(".png") else ".jpg"
            main = f"{doc_id}{ext}"
            (folder / main).write_bytes(base64.b64decode(data))
            stored_pages = [main]
            mime = "image/png" if ext == ".png" else "image/jpeg"

        out[doc_id] = {"id": doc_id, "label": label, "name": name,
                       "mime": mime, "file": main, "pages": stored_pages}
    return out


# ----------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("html", help="the legacy single-file itinerary")
    ap.add_argument("--title", default="", help="page title (default: the page's <title>)")
    ap.add_argument("--name", default="", help="project name (default: same as the title)")
    ap.add_argument("--year", type=int, default=2026, help="year the MMDD keys belong to")
    ap.add_argument("--zip", dest="zip_out", default="", help="also write a portable .zip here")
    args = ap.parse_args()

    source = Path(args.html).read_text("utf-8")
    storage.init()

    title = args.title or text_of(first(r"<title>(.*?)</title>", source)) or "Imported itinerary"
    name = args.name or title
    trip_id = storage.unique_trip_id(name)

    leg_docs = parse_leg_docs(source)
    docs = extract_documents(source, trip_id)

    timeline = source[source.index('<div class="timeline"'):source.index("<!-- ---------- Cost summary")]
    chunks = timeline.split('<div class="entry ')[1:]
    cards = []
    for chunk in chunks:
        card = parse_entry(chunk, leg_docs, args.year)
        if card:
            leg = card.pop("_leg", "")
            card["docs"] = [docs[d] for d in leg_docs.get(leg, []) if d in docs]
            cards.append(card)

    cards.sort(key=lambda c: (c["start_date"], c["start_time"] or "00:00"))
    starts = [c["start_date"] for c in cards]
    ends = [c["end_date"] or c["start_date"] for c in cards]

    trip = {
        "id": trip_id,
        "name": name,
        "title": title,
        "eyebrow": text_of(first(r'<div class="eyebrow">(.*?)</div>', source)),
        "subtitle": text_of(first(r'<div class="sub">(.*?)</div>', source)),
        "start_date": min(starts) if starts else date.today().isoformat(),
        "end_date": max(ends) if ends else date.today().isoformat(),
        "tags": tags_from(source),
        "notes": text_of(first(r'<div class="foot">(.*?)</div>', source)),
        "day_notes": {},
        "cards": cards,
    }
    storage.save_trip(trip)

    if args.zip_out:
        Path(args.zip_out).write_bytes(storage.export_zip(trip_id))

    doc_count = sum(len(c["docs"]) for c in cards)
    print(f"imported '{title}' as {trip_id}")
    print(f"  {len(cards)} cards, {doc_count} document links, {len(docs)} files extracted")
    print(f"  {trip['start_date']} -> {trip['end_date']}")
    print(f"  data dir: {storage.DATA_DIR}")
    if args.zip_out:
        print(f"  portable zip: {args.zip_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
