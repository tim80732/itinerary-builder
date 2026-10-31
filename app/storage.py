"""File storage for projects, documents and saved exports.

One folder per project inside DATA_DIR:

    projects/<project>/project-files/trip.json      the live record
    projects/<project>/documents/                   uploads + rendered PDF pages
    projects/<project>/export-html/                 <title>-<YYYYMMDDHHMM>.html
    projects/<project>/export-json/                 <title>-<YYYYMMDDHHMM>.json

Anything written from an earlier layout (trips/*.json + uploads/<id>/) is moved
into place on startup.
"""
from __future__ import annotations

import hmac
import io
import json
import os
import re
import secrets
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data")).resolve()
PROJECTS_DIR = DATA_DIR / "projects"

# folders inside a project
FILES_SUB = "project-files"
DOCS_SUB = "documents"
HISTORY_SUB = "history"
TRIP_FILE = "trip.json"
# secret for the trip's shareable live link (/live/<token>). Kept beside the project, NOT in
# trip.json: it follows renames (the folder moves), but duplicates, exports and snapshots never
# carry it, so a copy or an exported file can't reopen the original's live page.
LIVE_TOKEN_FILE = "live-link-token"

# how many undo points a project keeps
HISTORY_KEEP = 10
# an upload isn't referenced by anything until its card is saved, so the orphan
# sweeper leaves anything this new alone
UPLOAD_GRACE = 120

# kind -> (folder, extension, mimetype)
EXPORT_KINDS = {
    "html": ("export-html", ".html", "text/html"),
    "json": ("export-json", ".json", "application/json"),
}

IMAGE_MIMES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
PDF_MIME = "application/pdf"

PDF_DPI = 150
JPEG_QUALITY = 82
MAX_PAGE_PX = 1800


# ---------------------------------------------------------------- ids & names

def safe_id(trip_id: str) -> str:
    """Guard against path traversal in ids coming from the URL.

    Unicode letters are allowed so a CJK title keeps a readable folder name;
    \\w excludes separators, dots and control characters, which is what matters.
    """
    s = re.sub(r"[^\w-]+", "", trip_id or "", flags=re.UNICODE)
    if not s or s in (".", ".."):
        raise ValueError("invalid id")
    return s


def slugify(text: str, fallback: str = "trip") -> str:
    s = re.sub(r"[^\w-]+", "-", (text or "").strip().lower(), flags=re.UNICODE).strip("-")
    return (s or fallback)[:60]


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:10]


def safe_filename(text: str, fallback: str = "itinerary") -> str:
    """Keep unicode (CJK titles are the point) but drop anything path-ish.

    Linux only forbids "/" and NUL, but the Windows-illegal set is stripped too
    so the same names survive a trip through an SMB share.
    """
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t\x00]+', "", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    return cleaned[:80] or fallback


def stamp(when: datetime | None = None) -> str:
    return (when or datetime.now()).strftime("%Y%m%d%H%M")


# ---------------------------------------------------------------- layout

def project_dir(trip_id: str) -> Path:
    return PROJECTS_DIR / safe_id(trip_id)


def trip_path(trip_id: str) -> Path:
    return project_dir(trip_id) / FILES_SUB / TRIP_FILE


def uploads_for(trip_id: str) -> Path:
    folder = project_dir(trip_id) / DOCS_SUB
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def exports_dir(trip_id: str, kind: str) -> Path:
    if kind not in EXPORT_KINDS:
        raise ValueError(f"unknown export kind: {kind}")
    folder = project_dir(trip_id) / EXPORT_KINDS[kind][0]
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def history_dir(trip_id: str) -> Path:
    folder = project_dir(trip_id) / HISTORY_SUB
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def ensure_project(trip_id: str) -> Path:
    base = project_dir(trip_id)
    (base / FILES_SUB).mkdir(parents=True, exist_ok=True)
    (base / DOCS_SUB).mkdir(parents=True, exist_ok=True)
    (base / HISTORY_SUB).mkdir(parents=True, exist_ok=True)
    for folder, _ext, _mime in EXPORT_KINDS.values():
        (base / folder).mkdir(parents=True, exist_ok=True)
    return base


def init() -> None:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    _migrate_flat_layout()


def _migrate_flat_layout() -> None:
    """Move data written by the pre-projects layout into per-project folders."""
    old_trips = DATA_DIR / "trips"
    old_uploads = DATA_DIR / "uploads"
    if not old_trips.exists():
        return

    for src in sorted(old_trips.glob("*.json")):
        trip_id = src.stem
        try:
            target = trip_path(trip_id)
        except ValueError:
            continue
        if not target.exists():
            ensure_project(trip_id)
            shutil.move(str(src), str(target))
        old_docs = old_uploads / trip_id
        if old_docs.is_dir():
            dest = uploads_for(trip_id)
            for f in old_docs.iterdir():
                if f.is_file() and not (dest / f.name).exists():
                    shutil.move(str(f), str(dest / f.name))
            if not any(old_docs.iterdir()):
                old_docs.rmdir()

    if old_trips.exists() and not any(old_trips.iterdir()):
        old_trips.rmdir()
    if old_uploads.exists() and not any(old_uploads.iterdir()):
        old_uploads.rmdir()


# ---------------------------------------------------------------- trips

def list_trips() -> list[dict]:
    out = []
    if not PROJECTS_DIR.exists():
        return out
    for folder in sorted(PROJECTS_DIR.iterdir()):
        record = folder / FILES_SUB / TRIP_FILE
        if not record.exists():
            continue
        try:
            t = json.loads(record.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "id": t.get("id", folder.name),
            "name": t.get("name") or t.get("title") or folder.name,
            "title": t.get("title", folder.name),
            "start_date": t.get("start_date", ""),
            "end_date": t.get("end_date", ""),
            "cards": len(t.get("cards", [])),
            "exports": sum(len(list_exports(folder.name, k)) for k in EXPORT_KINDS),
        })
    out.sort(key=lambda t: (t["start_date"] or "9999", t["name"]))
    return out


def load_trip(trip_id: str) -> dict | None:
    p = trip_path(trip_id)
    if not p.exists():
        return None
    trip = json.loads(p.read_text("utf-8"))
    trip.setdefault("cards", [])
    trip.setdefault("tags", [])
    # records written before project names existed: fall back to the page title
    trip.setdefault("name", trip.get("title") or trip.get("id", ""))
    return trip


def save_trip(trip: dict) -> None:
    trip["id"] = safe_id(trip["id"])
    ensure_project(trip["id"])
    p = trip_path(trip["id"])
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(trip, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(p)


def delete_trip(trip_id: str) -> None:
    """Removes the project folder, exports included."""
    shutil.rmtree(project_dir(trip_id), ignore_errors=True)


def unique_trip_id(base: str) -> str:
    tid = slugify(base)
    candidate, n = tid, 2
    while project_dir(candidate).exists():
        candidate = f"{tid}-{n}"
        n += 1
    return candidate


def duplicate_trip(trip_id: str) -> str:
    trip = load_trip(trip_id)
    if trip is None:
        raise ValueError("unknown trip")
    base = trip.get("name") or trip.get("title") or trip_id
    new = unique_trip_id(base + " copy")
    ensure_project(new)
    src, dst = uploads_for(trip_id), uploads_for(new)
    for f in src.iterdir():
        if f.is_file():
            shutil.copy2(f, dst / f.name)
    trip["id"] = new
    trip["name"] = base + " (copy)"
    save_trip(trip)
    return new


def rename_project(trip_id: str, new_name: str) -> str:
    """Make the folder follow the project name. Returns the (possibly new) id."""
    current = safe_id(trip_id)
    wanted = slugify(new_name)
    if not wanted or wanted == current:
        return current
    new_id = unique_trip_id(new_name)
    shutil.move(str(project_dir(current)), str(project_dir(new_id)))
    trip = load_trip(new_id)
    if trip is not None:
        trip["id"] = new_id
        save_trip(trip)
    return new_id


# ---------------------------------------------------------------- live link
def live_token(trip_id: str) -> str:
    """The trip's live-link secret, created on first use."""
    path = project_dir(trip_id) / LIVE_TOKEN_FILE
    if path.is_file():
        token = path.read_text("utf-8").strip()
        if token:
            return token
    return reset_live_token(trip_id)


def reset_live_token(trip_id: str) -> str:
    """Issue a new secret; the old live link stops working at once."""
    token = secrets.token_urlsafe(24)
    path = project_dir(trip_id) / LIVE_TOKEN_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(token, "utf-8")
    tmp.replace(path)
    return token


def trip_for_live_token(token: str) -> str | None:
    """Project id whose live link uses ``token``, or None."""
    token = (token or "").strip()
    if len(token) < 20 or not PROJECTS_DIR.exists():
        return None
    for folder in PROJECTS_DIR.iterdir():
        path = folder / LIVE_TOKEN_FILE
        if not path.is_file():
            continue
        try:
            stored = path.read_text("utf-8").strip()
        except OSError:
            continue
        if stored and hmac.compare_digest(stored.encode(), token.encode()):
            return folder.name
    return None


# ---------------------------------------------------------------- history

def snapshot(trip_id: str, label: str) -> None:
    """Keep a copy of the trip as it stands *now*, before something changes it.

    Called at the top of every mutating route, so "Revert" on an entry puts
    back the state that entry describes moving away from. Only the newest
    HISTORY_KEEP are kept; once a snapshot falls off the end, any upload it was
    the last thing referencing is collected too.
    """
    current = trip_path(trip_id)
    if not current.exists():
        return
    try:
        trip = json.loads(current.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return                                  # nothing safe to snapshot

    # Two JSON documents, one per line: a small header, then the trip. Listing
    # the history and sweeping uploads both only need the header, and reading
    # one line beats parsing ten whole trips on every page load and every save.
    head = {"at": datetime.now().isoformat(timespec="seconds"),
            "label": label,
            "cards": len(trip.get("cards", [])),
            "files": sorted(_referenced_uploads(trip))}
    folder = history_dir(trip_id)
    name = datetime.now().strftime("%Y%m%d%H%M%S%f") + ".json"
    (folder / name).write_text(
        json.dumps(head, ensure_ascii=False) + "\n" + json.dumps(trip, ensure_ascii=False),
        "utf-8")

    for old in sorted(folder.glob("*.json"), reverse=True)[HISTORY_KEEP:]:
        old.unlink(missing_ok=True)
    _collect_orphan_uploads(trip_id)


def _read_head(path: Path) -> dict | None:
    """What a snapshot is, without parsing the trip inside it."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            head = json.loads(fh.readline())
    except (OSError, ValueError):
        return None
    return head if isinstance(head, dict) else None


def _read_snapshot(path: Path) -> dict | None:
    """The whole snapshot, trip included."""
    try:
        text = path.read_text("utf-8")
    except OSError:
        return None
    head_text, _, body = text.partition("\n")
    try:
        head = json.loads(head_text)
        trip = json.loads(body) if body.strip() else head.get("trip")
    except ValueError:
        return None
    if not isinstance(head, dict) or not isinstance(trip, dict):
        return None
    return {**head, "trip": trip}


def list_history(trip_id: str) -> list[dict]:
    """The undo points, newest first."""
    folder = project_dir(trip_id) / HISTORY_SUB
    if not folder.exists():
        return []
    out = []
    for path in sorted(folder.glob("*.json"), reverse=True):
        head = _read_head(path)
        if head is None:
            continue
        when = head.get("at", "")
        cards = head.get("cards")
        if cards is None:                      # a snapshot from before the header
            cards = len((head.get("trip") or {}).get("cards", []))
        out.append({
            "id": path.stem,
            "label": head.get("label", "Change"),
            "at": when,
            "when": when.replace("T", " ")[:16],
            "cards": cards,
        })
    return out


def restore_snapshot(trip_id: str, snap_id: str) -> dict:
    """The trip as it was at that point. Raises ValueError if it isn't there."""
    name = Path(snap_id).name
    if not re.fullmatch(r"\d{4,24}", name):
        raise ValueError("bad snapshot id")
    data = _read_snapshot(history_dir(trip_id) / f"{name}.json")
    if data is None:
        raise ValueError("no such snapshot")
    trip = data["trip"]
    trip["id"] = safe_id(trip_id)          # the folder wins over what was stored
    return trip


def all_docs(trip: dict):
    """Every document record in a trip: the cards' and the trip's own (📎 documents items).
    Anything that decides which upload files are still wanted must go through here, or the
    sweeper deletes the files of whatever it doesn't know about."""
    for card in trip.get("cards", []):
        yield from card.get("docs", []) or []
    for item in trip.get("items") or []:
        if isinstance(item, dict) and item.get("kind") == "doc":
            yield from item.get("docs", []) or []


def _referenced_uploads(trip: dict) -> set[str]:
    names: set[str] = set()
    for doc in all_docs(trip):
        if doc.get("file"):
            names.add(Path(str(doc["file"])).name)
        for page in doc.get("pages", []) or []:
            names.add(Path(str(page)).name)
    return names


def _collect_orphan_uploads(trip_id: str) -> None:
    """Delete uploads that neither the live trip nor any snapshot still wants.

    This is why deleting a card doesn't unlink its documents on the spot: while
    a snapshot still points at them, reverting has to bring them back intact.

    Two things keep it from eating live data. It bails out entirely if anything
    can't be read — a partial picture would delete files that are still in use.
    And it never touches a file written in the last UPLOAD_GRACE seconds: a
    just-uploaded document isn't referenced by anything until the card holding
    it is saved, so a sweep in that window would take it for an orphan. Callers
    snapshot before writing files for that reason; this is the net under it.
    """
    live = load_trip(trip_id)
    if live is None:
        return
    keep = _referenced_uploads(live)

    folder = project_dir(trip_id) / HISTORY_SUB
    if folder.exists():
        for path in folder.glob("*.json"):
            head = _read_head(path)
            names = head.get("files") if head else None
            if names is None:               # written before the header existed
                data = _read_snapshot(path)
                names = _referenced_uploads(data["trip"]) if data else None
            if names is None:
                return                      # unreadable snapshot: collect nothing
            keep |= set(names)

    cutoff = time.time() - UPLOAD_GRACE
    for f in uploads_for(trip_id).iterdir():
        if not f.is_file() or f.name in keep:
            continue
        try:
            if f.stat().st_mtime > cutoff:
                continue                    # too new to judge — see the docstring
        except OSError:
            continue
        f.unlink(missing_ok=True)


# ---------------------------------------------------------------- exports

def export_filename(trip: dict, kind: str, when: datetime | None = None) -> str:
    """Named after the project, so files match the folder they live in."""
    ext = EXPORT_KINDS[kind][1]
    label = trip.get("name") or trip.get("title") or trip.get("id", "")
    return f"{safe_filename(label)}-{stamp(when)}{ext}"


def save_export(trip: dict, kind: str, blob: bytes) -> Path:
    """Keep a timestamped copy alongside the project and return its path."""
    folder = exports_dir(trip["id"], kind)
    target = folder / export_filename(trip, kind)
    if target.exists():                       # same minute: don't clobber
        base, ext = target.stem, target.suffix
        n = 2
        while (folder / f"{base}-{n}{ext}").exists():
            n += 1
        target = folder / f"{base}-{n}{ext}"
    target.write_bytes(blob)
    return target


def list_exports(trip_id: str, kind: str) -> list[dict]:
    folder = project_dir(trip_id) / EXPORT_KINDS[kind][0]
    if not folder.exists():
        return []
    items = []
    for f in folder.iterdir():
        if not f.is_file():
            continue
        st = f.stat()
        items.append({
            "name": f.name,
            "kind": kind,
            "size_kb": round(st.st_size / 1024),
            "size_mb": round(st.st_size / 1024 / 1024, 1),
            "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "sort": st.st_mtime,
        })
    items.sort(key=lambda i: i["sort"], reverse=True)
    return items


def safe_export_path(trip_id: str, kind: str, name: str) -> Path:
    """Resolve one saved export, refusing anything outside its folder."""
    folder = exports_dir(trip_id, kind)
    clean = Path(name).name
    target = (folder / clean).resolve()
    if target.parent != folder.resolve() or not target.is_file():
        raise ValueError("no such export")
    return target


def delete_export(trip_id: str, kind: str, name: str) -> None:
    safe_export_path(trip_id, kind, name).unlink()


# ---------------------------------------------------------------- documents

def store_document(trip_id: str, filename: str, mime: str, blob: bytes, label: str = "") -> dict:
    """Save an upload and return its document record.

    Images are kept as-is (down-scaled if huge); PDFs are also rasterised to
    JPEG pages so the generated page can show them on any phone.
    """
    folder = uploads_for(trip_id)
    doc_id = new_id("d")
    mime = (mime or "").lower()
    ext = IMAGE_MIMES.get(mime)

    if mime == PDF_MIME or filename.lower().endswith(".pdf"):
        stored = f"{doc_id}.pdf"
        (folder / stored).write_bytes(blob)
        pages = _render_pdf(blob, folder, doc_id)
        return {"id": doc_id, "label": label or filename, "name": filename,
                "mime": PDF_MIME, "file": stored, "pages": pages}

    if ext is None:
        raise ValueError("Only JPG, PNG, WEBP or PDF files are supported.")

    stored = f"{doc_id}{ext}"
    blob = _shrink_image(blob, mime)
    (folder / stored).write_bytes(blob)
    return {"id": doc_id, "label": label or filename, "name": filename,
            "mime": mime, "file": stored, "pages": [stored]}


def copy_document(src_trip_id: str, dst_trip_id: str, doc: dict) -> dict | None:
    """Duplicate a document (original + rendered pages) under a fresh id.

    Files are copied rather than referenced, so the two cards stay genuinely
    independent — editing or deleting one can never reach into the other.
    Returns None when nothing was left to copy.
    """
    src, dst = uploads_for(src_trip_id), uploads_for(dst_trip_id)
    old_id = str(doc.get("id") or "")
    fresh = new_id("d")
    done: dict[str, str] = {}

    def copy_one(name: str) -> str | None:
        leaf = Path(str(name)).name
        if leaf in done:                      # an image is its own single page
            return done[leaf]
        source = src / leaf
        if not source.is_file():
            return None
        target = leaf.replace(old_id, fresh, 1) if old_id and leaf.startswith(old_id) \
            else f"{fresh}-{leaf}"
        shutil.copy2(source, dst / target)
        done[leaf] = target
        return target

    out = dict(doc)
    out["id"] = fresh
    out["file"] = (copy_one(doc["file"]) or "") if doc.get("file") else ""
    out["pages"] = [p for p in (copy_one(p) for p in doc.get("pages", [])) if p]
    if not out["file"] and not out["pages"]:
        return None
    return out




def _shrink_image(blob: bytes, mime: str) -> bytes:
    """Keep uploads sensible so the exported HTML stays small."""
    try:
        from PIL import Image
    except ImportError:
        return blob
    try:
        img = Image.open(io.BytesIO(blob))
        if max(img.size) <= MAX_PAGE_PX:
            return blob
        img.thumbnail((MAX_PAGE_PX, MAX_PAGE_PX))
        buf = io.BytesIO()
        if mime == "image/png":
            img.save(buf, "PNG", optimize=True)
        else:
            img.convert("RGB").save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True)
        return buf.getvalue()
    except Exception:
        return blob


def renderer_status() -> str:
    """Why PDF pages might not render, or "" when the renderer is fine."""
    try:
        import pypdfium2                                   # noqa: F401
    except ImportError as exc:
        return f"pypdfium2 is not installed ({exc})"
    try:
        from PIL import Image                              # noqa: F401
    except ImportError as exc:
        return f"Pillow is not installed ({exc})"
    return ""


def _render_pdf(blob: bytes, folder: Path, doc_id: str) -> list[str]:
    """Rasterise every PDF page to JPEG. Returns stored page filenames.

    Failing is survivable — the PDF itself is still stored and downloadable —
    but it must never be *silent*, or a card just quietly shows no preview and
    there is nothing to go on. Every failure says why on the container log, and
    one bad page no longer throws away the pages that did render.
    """
    why = renderer_status()
    if why:
        print(f"[pdf] {doc_id}: not rendered — {why}", flush=True)
        return []
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(blob)
        count = len(pdf)
    except Exception as exc:
        print(f"[pdf] {doc_id}: cannot open ({type(exc).__name__}: {exc})", flush=True)
        return []

    pages: list[str] = []
    for i in range(count):
        try:
            img = pdf[i].render(scale=PDF_DPI / 72).to_pil().convert("RGB")
            if max(img.size) > MAX_PAGE_PX:
                img.thumbnail((MAX_PAGE_PX, MAX_PAGE_PX))
            name = f"{doc_id}_p{i + 1}.jpg"
            img.save(folder / name, "JPEG", quality=JPEG_QUALITY, optimize=True)
            pages.append(name)
        except Exception as exc:
            print(f"[pdf] {doc_id}: page {i + 1}/{count} failed "
                  f"({type(exc).__name__}: {exc})", flush=True)
    try:
        pdf.close()
    except Exception:
        pass
    if not pages:
        print(f"[pdf] {doc_id}: {count} page(s) found, none could be rendered", flush=True)
    return pages


def rerender_document(trip_id: str, doc: dict) -> list[str]:
    """Have another go at rasterising a PDF that is already stored.

    Lets a document fixed by a newer renderer — or one that failed for a
    passing reason — be repaired without re-uploading it.
    """
    stored = doc.get("file")
    if (doc.get("mime") or "").lower() != PDF_MIME or not stored:
        return list(doc.get("pages") or [])
    folder = uploads_for(trip_id)
    source = folder / Path(str(stored)).name
    if not source.is_file():
        print(f"[pdf] {doc.get('id')}: original file is missing, cannot re-render", flush=True)
        return list(doc.get("pages") or [])
    return _render_pdf(source.read_bytes(), folder, str(doc.get("id") or new_id("d")))


# ---------------------------------------------------------------- archives

def export_zip(trip_id: str) -> bytes:
    """One project: trip.json + its documents."""
    import zipfile

    trip = load_trip(trip_id)
    if trip is None:
        raise ValueError("unknown trip")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("trip.json", json.dumps(trip, ensure_ascii=False, indent=2))
        for f in sorted(uploads_for(trip_id).iterdir()):
            if f.is_file():
                z.write(f, f"documents/{f.name}")
    return buf.getvalue()


def backup_all_zip() -> bytes:
    """Every project folder, exports included, in the on-disk shape."""
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(PROJECTS_DIR.rglob("*")):
            if path.is_file():
                # zip entries are always POSIX-style, whatever the host
                z.write(path, path.relative_to(DATA_DIR).as_posix())
    return buf.getvalue()


def import_zip(blob: bytes) -> tuple[list[str], str]:
    """Accept a single-project export or a full backup.

    Returns (trip ids touched, mode). Archives from the older flat layout
    (trips/*.json, uploads/<id>/) are still understood.
    """
    import zipfile

    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]

        if "trip.json" in names:                          # one project
            trip = json.loads(z.read("trip.json").decode("utf-8"))
            trip["id"] = unique_trip_id(trip.get("id") or trip.get("title") or "trip")
            ensure_project(trip["id"])
            docs = uploads_for(trip["id"])
            for name in names:
                if name.startswith(("documents/", "uploads/")):
                    (docs / PurePosixPath(name).name).write_bytes(z.read(name))
            trip.setdefault("cards", [])
            trip.setdefault("tags", [])
            trip.setdefault("day_notes", {})
            save_trip(trip)
            return [trip["id"]], "trip"

        records = [n for n in names
                   if n.endswith(f"{FILES_SUB}/{TRIP_FILE}") or
                   (n.startswith("trips/") and n.endswith(".json"))]
        if not records:
            raise ValueError("No trip.json found in that archive.")

        ids: list[str] = []
        for name in records:                              # restore in place
            trip = json.loads(z.read(name).decode("utf-8"))
            parts = PurePosixPath(name).parts
            fallback = parts[1] if len(parts) > 2 else PurePosixPath(name).stem
            tid = safe_id(trip.get("id") or fallback)
            trip["id"] = tid
            save_trip(trip)
            ids.append(tid)

        for name in names:
            parts = PurePosixPath(name).parts
            leaf = PurePosixPath(name).name
            if len(parts) >= 3 and parts[0] == "projects":
                tid, sub = parts[1], parts[2]
                if sub == FILES_SUB:
                    continue
                known = {DOCS_SUB, HISTORY_SUB} | {f for f, _e, _m in EXPORT_KINDS.values()}
                if sub not in known:
                    continue                              # ignore unknown subfolders
                try:
                    dest = project_dir(tid) / sub
                except ValueError:
                    continue
                dest.mkdir(parents=True, exist_ok=True)
                (dest / leaf).write_bytes(z.read(name))
            elif len(parts) >= 3 and parts[0] == "uploads":   # legacy backup
                try:
                    dest = uploads_for(parts[1])
                except ValueError:
                    continue
                (dest / leaf).write_bytes(z.read(name))
        return ids, "library"
