"""Inline SVG country flags for the tag pills.

Emoji flags are not an option: Windows ships no glyphs for regional-indicator
pairs, so a desktop browser shows "CZ" instead of a flag. These are drawn as
tiny SVGs (viewBox 0 0 6 4) that render identically everywhere and keep the
exported file self-contained.

A tag is written as "cz:Czechia" or with an emoji flag ("\U0001F1E8\U0001F1FF Czechia");
anything without a known code simply renders as text.
"""
from __future__ import annotations

import re

_H = (0, 1.3333, 2.6667)          # horizontal band offsets
_V = (0, 2, 4)                    # vertical band offsets


def _bands(colors: tuple[str, ...], vertical: bool = False) -> str:
    out = []
    if vertical:
        for x, c in zip(_V, colors):
            out.append(f'<rect x="{x}" width="2" height="4" fill="{c}"/>')
    else:
        for y, c in zip(_H, colors):
            out.append(f'<rect y="{y}" width="6" height="1.3334" fill="{c}"/>')
    return "".join(out)


def _halves(top: str, bottom: str) -> str:
    return (f'<rect width="6" height="2" fill="{top}"/>'
            f'<rect y="2" width="6" height="2" fill="{bottom}"/>')


def _nordic(bg: str, cross: str = "#fff") -> str:
    return (f'<rect width="6" height="4" fill="{bg}"/>'
            f'<rect x="1.7" width="0.85" height="4" fill="{cross}"/>'
            f'<rect y="1.6" width="6" height="0.85" fill="{cross}"/>')


# code -> svg inner markup
FLAGS: dict[str, str] = {
    # horizontal bands
    "at": _bands(("#ed2939", "#fff", "#ed2939")),
    "hu": _bands(("#ce2939", "#fff", "#477050")),
    "de": _bands(("#000", "#dd0000", "#ffce00")),
    "nl": _bands(("#ae1c28", "#fff", "#21468b")),
    "lu": _bands(("#ed2939", "#fff", "#00a1de")),
    "lt": _bands(("#fdb913", "#006a44", "#c1272d")),
    "ee": _bands(("#4891d9", "#000", "#fff")),
    "bg": _bands(("#fff", "#00966e", "#d62612")),
    "hr": _bands(("#ff0000", "#fff", "#171796")),
    # vertical bands
    "fr": _bands(("#002395", "#fff", "#ed2939"), vertical=True),
    "it": _bands(("#009246", "#fff", "#ce2b37"), vertical=True),
    "ie": _bands(("#169b62", "#fff", "#ff883e"), vertical=True),
    "be": _bands(("#000", "#fae042", "#ed2939"), vertical=True),
    "ro": _bands(("#002b7f", "#fcd116", "#ce1126"), vertical=True),
    # two bands
    "pl": _halves("#fff", "#dc143c"),
    "id": _halves("#ce1126", "#fff"),
    "ua": _halves("#005bbb", "#ffd500"),
    # nordic crosses
    "dk": _nordic("#c8102e"),
    "se": _nordic("#006aa7", "#fecc00"),
    "no": ('<rect width="6" height="4" fill="#ba0c2f"/>'
           '<rect x="1.55" width="1.15" height="4" fill="#fff"/>'
           '<rect y="1.45" width="6" height="1.15" fill="#fff"/>'
           '<rect x="1.8" width="0.65" height="4" fill="#00205b"/>'
           '<rect y="1.7" width="6" height="0.65" fill="#00205b"/>'),
    "fi": _nordic("#fff", "#003580"),
    "is": ('<rect width="6" height="4" fill="#02529c"/>'
           '<rect x="1.55" width="1.15" height="4" fill="#fff"/>'
           '<rect y="1.45" width="6" height="1.15" fill="#fff"/>'
           '<rect x="1.8" width="0.65" height="4" fill="#dc1e35"/>'
           '<rect y="1.7" width="6" height="0.65" fill="#dc1e35"/>'),
    # one-offs
    "cz": ('<rect width="6" height="2" fill="#fff"/>'
           '<rect y="2" width="6" height="2" fill="#d7141a"/>'
           '<path d="M0 0l3 2-3 2z" fill="#11457e"/>'),
    "ch": ('<rect width="6" height="4" fill="#d52b1e"/>'
           '<rect x="2.55" y="0.8" width="0.9" height="2.4" fill="#fff"/>'
           '<rect x="1.8" y="1.55" width="2.4" height="0.9" fill="#fff"/>'),
    "jp": ('<rect width="6" height="4" fill="#fff"/>'
           '<circle cx="3" cy="2" r="1.2" fill="#bc002d"/>'),
    "es": ('<rect width="6" height="4" fill="#c60b1e"/>'
           '<rect y="1" width="6" height="2" fill="#ffc400"/>'),
    "pt": ('<rect width="6" height="4" fill="#da291c"/>'
           '<rect width="2.4" height="4" fill="#046a38"/>'),
    "gr": ('<rect width="6" height="4" fill="#fff"/>'
           '<rect width="6" height="0.444" y="0" fill="#0d5eaf"/>'
           '<rect width="6" height="0.444" y="0.889" fill="#0d5eaf"/>'
           '<rect width="6" height="0.444" y="1.778" fill="#0d5eaf"/>'
           '<rect width="6" height="0.444" y="2.667" fill="#0d5eaf"/>'
           '<rect width="6" height="0.444" y="3.556" fill="#0d5eaf"/>'
           '<rect width="2.222" height="2.222" fill="#0d5eaf"/>'
           '<rect x="0.889" width="0.444" height="2.222" fill="#fff"/>'
           '<rect y="0.889" width="2.222" height="0.444" fill="#fff"/>'),
    "tw": ('<rect width="6" height="4" fill="#fe0000"/>'
           '<rect width="3" height="2" fill="#000095"/>'
           '<circle cx="1.5" cy="1" r="0.5" fill="#fff"/>'),
    "kr": ('<rect width="6" height="4" fill="#fff"/>'
           '<circle cx="3" cy="2" r="0.85" fill="#cd2e3a"/>'
           '<path d="M3 1.15a0.425 0.425 0 000 0.85 0.425 0.425 0 010 0.85z" fill="#0047a0"/>'),
    "th": ('<rect width="6" height="4" fill="#a51931"/>'
           '<rect y="0.667" width="6" height="2.667" fill="#f4f5f8"/>'
           '<rect y="1.333" width="6" height="1.333" fill="#2d2a4a"/>'),
    "vn": ('<rect width="6" height="4" fill="#da251d"/>'
           '<circle cx="3" cy="2" r="0.9" fill="#ff0"/>'),
    "sg": ('<rect width="6" height="4" fill="#fff"/>'
           '<rect width="6" height="2" fill="#ed2939"/>'
           '<circle cx="1.3" cy="1" r="0.62" fill="#fff"/>'
           '<circle cx="1.55" cy="1" r="0.62" fill="#ed2939"/>'),
}

# Names the importer may meet in an existing page.
NAME_TO_CODE = {
    "czechia": "cz", "czech republic": "cz", "austria": "at", "hungary": "hu",
    "italy": "it", "germany": "de", "netherlands": "nl", "luxembourg": "lu",
    "lithuania": "lt", "estonia": "ee", "bulgaria": "bg", "croatia": "hr",
    "france": "fr", "ireland": "ie", "belgium": "be", "romania": "ro",
    "poland": "pl", "indonesia": "id", "ukraine": "ua", "denmark": "dk",
    "sweden": "se", "norway": "no", "finland": "fi", "iceland": "is",
    "switzerland": "ch", "japan": "jp", "spain": "es", "portugal": "pt",
    "greece": "gr", "taiwan": "tw", "south korea": "kr", "korea": "kr",
    "thailand": "th", "vietnam": "vn", "singapore": "sg",
}

_EMOJI_FLAG = re.compile("^[\U0001F1E6-\U0001F1FF]{2}")


def split_tag(tag: str) -> tuple[str, str]:
    """('cz:Czechia'|'<flag emoji> Czechia'|'Czechia') -> (code, label)."""
    tag = (tag or "").strip()

    m = re.match(r"^([A-Za-z]{2})\s*:\s*(.+)$", tag)
    if m and m.group(1).lower() in FLAGS:
        return m.group(1).lower(), m.group(2).strip()

    m = _EMOJI_FLAG.match(tag)
    if m:
        code = "".join(chr(ord(ch) - 0x1F1E6 + ord("a")) for ch in m.group(0))
        label = tag[m.end():].strip()
        if code in FLAGS:
            return code, (label or code.upper())

    return "", tag


def flag_svg(code: str) -> str:
    inner = FLAGS.get((code or "").lower())
    if not inner:
        return ""
    return (f'<svg class="flag" width="18" height="12" viewBox="0 0 6 4" '
            f'aria-hidden="true">{inner}</svg>')
