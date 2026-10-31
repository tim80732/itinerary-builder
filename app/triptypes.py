"""Card types.

`kind` decides which fields the editor shows, how many days a card occupies
and how its time is worded:

  move   two endpoints, same day (or overnight)   depart -> arrive
  hire   two endpoints, spans whole days          pickup / ongoing / drop-off
  stay   spans nights (check-out day excluded)    check-in / staying
  point  one moment                               single time
"""

from urllib.parse import urlsplit

TYPES = {
    # A transit card can say how it travels (`mode`): the page then shows that mode's label and icon.
    # No mode -> "Transit" with the train icon. The card's own `icon` still beats both.
    "transit":    {"label": "Transit",     "icon": "\U0001F683", "kind": "move",  "group": "travel",
                   "modes": {"train": {"label": "Train", "icon": "\U0001F683", "service": "Train / operator"},
                             "bus":   {"label": "Bus",   "icon": "\U0001F68C", "service": "Bus line / operator"},
                             "ferry": {"label": "Ferry", "icon": "\U0001F6A2", "service": "Ferry / operator"},
                             "car":   {"label": "Car",   "icon": "\U0001F697", "service": "Car / rental company"}}},
    "plane":      {"label": "Flight",      "icon": "✈️", "kind": "move",  "group": "travel"},
    "rental":     {"label": "Rental",      "icon": "\U0001F697", "kind": "hire",  "group": "travel"},
    "hotel":      {"label": "Hotel",       "icon": "\U0001F3E8", "kind": "stay",  "group": "hotel"},
    "restaurant": {"label": "Restaurant",  "icon": "\U0001F37D️", "kind": "point", "group": "food"},
    "location":   {"label": "Place",       "icon": "\U0001F4CD", "kind": "point", "group": "activity"},
    "activity":   {"label": "Activity",    "icon": "\U0001F3AD", "kind": "point", "group": "activity"},
    "custom":     {"label": "Custom",      "icon": "⭐",     "kind": "point", "group": "activity"},
}

# Parts of a day, in the order they are listed under. The meal sections are
# never chosen automatically — a card only lands in one if the editor says so,
# because "12:30" is no way to tell lunch from a museum. The rest follow the
# clock: before DAY_SPLITS[0] is morning, before DAY_SPLITS[1] is afternoon,
# after that evening. "" is the trailing bucket for a card with no time and no
# section of its own.
SECTIONS = [
    ("breakfast", "Breakfast", True),
    ("morning",   "Morning",   False),
    ("lunch",     "Lunch",     True),
    ("afternoon", "Afternoon", False),
    ("dinner",    "Dinner",    True),
    ("evening",   "Evening",   False),
    ("",          "Anytime",   False),
]
# Heading over the pinned block that sits above those sections.
PINNED_LABEL = "Hotel"
SECTION_LABELS = {key: label for key, label, _manual in SECTIONS}
DAY_SPLITS = (12 * 60, 18 * 60)         # noon, 18:00

# A card sorts after every real time when it has none at all.
NO_TIME_SORT = 24 * 60


def minutes_of(clock: str) -> int | None:
    """"14:05" -> 845. None when it isn't a time."""
    parts = (clock or "").split(":")
    if len(parts) != 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return h * 60 + m if 0 <= h < 24 and 0 <= m < 60 else None


def section_of(card: dict) -> str:
    """Which part of the day a card is listed under.

    Mirrored by `sectionOf` in the exported page — change one, change the other.
    """
    chosen = card.get("section") or ""
    if chosen in SECTION_LABELS and chosen:
        return chosen
    when = minutes_of(card.get("start_time", ""))
    if when is None:
        return ""
    if when < DAY_SPLITS[0]:
        return "morning"
    return "afternoon" if when < DAY_SPLITS[1] else "evening"


def default_sort(card: dict) -> float:
    """Where a card sits before anyone has dragged it: by the clock."""
    when = minutes_of(card.get("start_time", ""))
    return float(NO_TIME_SORT if when is None else when)


# Order used by the cost summary tabs / subtotals.
GROUPS = [
    ("travel",   "Travel",   "\U0001F683✈️"),
    ("hotel",    "Hotel",    "\U0001F3E8"),
    ("food",     "Food",     "\U0001F37D️"),
    ("activity", "Activity", "\U0001F3AD"),
]

# Fields kept on every card (blank string when unused).
CARD_FIELDS = [
    "type", "mode", "type_label", "icon", "title", "alert", "place", "service",
    "start_date", "start_time", "end_date", "end_time",
    "from_label", "from_sub", "from_map",
    "to_label", "to_sub", "to_map",
    "address", "map_link",
    "price_original", "price_label", "price_note",
]


# Links: a card's own (shown above its price) and the trip's (the bar at the
# bottom of the calendar). Both are lists of {"text", "url"} in display order;
# an empty text shows the site's name instead.
LINK_TEXT_MAX = 120


def clean_link(url: str) -> str:
    """An http(s) address, or "" — a bare "www.x.com" gets https:// in front, and
    anything that isn't a web page (javascript:, data:, mailto:) is dropped, because
    the page puts these straight into hrefs."""
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url and not url.lower().startswith(("javascript:", "data:", "mailto:", "vbscript:")):
        url = "https://" + url.lstrip("/")
    parts = urlsplit(url)
    return url if parts.scheme.lower() in ("http", "https") and parts.netloc else ""


def clean_links(links) -> list[dict]:
    """Stored/posted links, keeping only the ones with a usable address."""
    out = []
    for item in links or []:
        if not isinstance(item, dict):
            continue
        url = clean_link(str(item.get("url") or ""))
        if url:
            out.append({"text": str(item.get("text") or "").strip()[:LINK_TEXT_MAX], "url": url})
    return out


def trip_items(trip: dict) -> list[dict]:
    """The trip's documents and links, in display order (the 📎 bar on the live calendar):
      {"id", "kind": "link", "text", "url"}  or  {"id", "kind": "doc", "text", "docs": [doc records]}
    A trip saved before documents existed has only `links`; those read as link items."""
    items = trip.get("items")
    if isinstance(items, list):
        return [i for i in items if isinstance(i, dict) and i.get("kind") in ("doc", "link")]
    return [{"id": f"l{n}", "kind": "link", **link} for n, link in enumerate(clean_links(trip.get("links")))]


def kind_of(card_type: str) -> str:
    return TYPES.get(card_type, TYPES["custom"])["kind"]


def modes_of(card_type: str) -> dict:
    """{mode: {label, icon, service}} for types that have modes (transit), else {}."""
    return TYPES.get(card_type, {}).get("modes") or {}


def card_meta(card: dict) -> dict:
    """Label and icon a card shows: its mode's when it has one, else its type's.
    Mirrored by `metaOf` in the exported page — change one, change the other."""
    meta = TYPES.get(card.get("type"), TYPES["custom"])
    mode = modes_of(card.get("type")).get(card.get("mode") or "")
    return {**meta, "label": mode["label"], "icon": mode["icon"]} if mode else meta


def meta_for_page() -> dict:
    """Trimmed registry embedded in the generated HTML."""
    out = {}
    for k, v in TYPES.items():
        out[k] = {"label": v["label"], "icon": v["icon"], "kind": v["kind"], "group": v["group"]}
        if v.get("modes"):
            out[k]["modes"] = {m: {"label": d["label"], "icon": d["icon"]} for m, d in v["modes"].items()}
    return out


def sections_for_page() -> list:
    """[key, label] pairs in order, embedded in the generated HTML."""
    return [[key, label] for key, label, _manual in SECTIONS]
