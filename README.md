# Itinerary Builder

Builds a travel itinerary card by card and exports it as **one self-contained HTML file**: a calendar you tap through day by day, a printable schedule, a cost summary, and every booking document embedded, so the file works on a phone with no network. Runs as a small web app in Docker (made for Unraid).

**Built by AI.** The code and docs in this repository were written by Claude (Anthropic's AI model), directed and tested by [@tim80732](https://github.com/tim80732). It's used for real trips, but review it yourself before trusting it with documents you can't replace, and keep backups.

## Why

A trip lives in a dozen places: booking emails, PDF vouchers, train tickets, a notes app, a spreadsheet of costs. On the road you want one thing on your phone that opens instantly, works without data roaming, and shows today's plan with the ticket one tap away.

Itinerary Builder keeps the editing on your server and gives you a single HTML file to carry. PDFs are turned into images when you upload them, because phones won't show a PDF inside a page.

## What it does

- **Cards, one per booking:** train/bus, flight, rental, hotel, restaurant, place, activity, or your own type. Each type only asks for the fields it needs, and knows which days it occupies: a hotel covers its nights, a rental every day from pick-up to drop-off.
- **The exported page** opens on a calendar. Tap a day for its plan, grouped into breakfast, morning, lunch, afternoon, dinner and evening, with the hotel pinned on top. Tap a card for everything about it, including its documents.
- **Schedule:** the whole trip as one spreadsheet, readable on a phone and printable. Pick any days to narrow it. **Save as PDF** uses the browser's own print dialog, so text stays selectable and CJK prints correctly.
- **Cost summary** in NT$ by category (travel, hotel, food, activity), with the original currency shown alongside.
- **Documents:** JPG, PNG, WEBP and PDF on cards, plus trip-wide documents and links in a bar at the bottom of the calendar.
- **Live link:** a secret address per trip that serves the itinerary read-only and fetches documents only when opened. Small enough for roaming data, and it always shows the latest version. **New link** revokes the old one.
- **Light and dark**, following the device unless you pick one. The schedule stays light either way.
- **Editing:** drag cards between parts of the day, copy a booking to another date or another trip, hide a multi-day card from the days in between, and revert any of the last 10 changes.
- **JSON API** at `/api/v1` for scripts and AI agents, with its own token. Every change it makes can be reverted from the history like an edit in the UI.

## Install on Unraid

The image is built on the server from this repository.

1. In a terminal on the server:

   ```bash
   git clone https://github.com/tim80732/itinerary-builder /mnt/user/appdata/itinerary-builder/src
   docker build -t itinerary-builder:latest /mnt/user/appdata/itinerary-builder/src
   wget -O /boot/config/plugins/dockerMan/templates-user/my-itinerary-builder.xml https://raw.githubusercontent.com/tim80732/itinerary-builder/main/unraid/itinerary-builder.xml
   ```

2. Docker tab → **Add Container** → pick **itinerary-builder** from the template list.
3. Set an **App Password**. The app holds names, booking references, door PINs and scanned tickets.
4. Apply, then open the WebUI and sign in with any username and that password. Turn on **autostart** for the container.

Trips are stored in `/mnt/user/appdata/itinerary-builder/data`, next to the source and not inside it. Include that folder in your appdata backup.

### Updating

```bash
bash /mnt/user/appdata/itinerary-builder/src/unraid/deploy.sh
```

It pulls the latest code, rebuilds the image and recreates the container. Trips, documents and saved exports are in the data folder and are kept.

## Any Docker host

```bash
git clone https://github.com/tim80732/itinerary-builder && cd itinerary-builder
docker build -t itinerary-builder:latest .
docker run -d --name itinerary-builder -p 8099:8080 \
  -v /path/to/appdata:/data \
  -e APP_PASSWORD=change-me -e TZ=Asia/Taipei \
  itinerary-builder:latest
```

Or `docker compose up -d` with the included `docker-compose.yml`.

Keep it on your LAN or behind a VPN. If you put it behind a reverse proxy, keep the password set, and only let `/live/` through without extra authentication.

| Variable | Default | |
|---|---|---|
| `APP_PASSWORD` | | Login for the editor (HTTP Basic, any username). Empty means no login. |
| `ITINERARY_API_TOKEN` | | Bearer token for `/api/v1`. Empty turns the API off. |
| `MAX_UPLOAD_MB` | `25` | Largest single upload |
| `TZ` | | Timezone for the calendar's "today" highlight |
| `SECRET_KEY` | random | Signs the flash-message cookie. Set it only to keep messages across restarts. |

## Using it

1. **New project:** a project name (used in the app and for the folder), a page title (the heading in the exported file), and the trip's dates.
2. **Add cards**, one per booking. Times are worded for the type: depart/arrive, pick-up/drop-off, check-in/check-out. The preview beside the form is drawn by the exporter itself, so it matches the finished page.
3. **Preview** opens the generated page. **Download HTML** saves the single file to carry on your phone, and keeps a dated copy under *Saved exports*. **Live link** gives you the shareable address.

Fields worth knowing:

- **Details:** one line per item, shown as the grey row on the card.
- **Heads-up:** a short red line under the title, such as `(closes at 4PM!)`, shown everywhere the card appears.
- **Listed under:** which part of the day the card sits in. Left on *Automatic*, the start time decides, but meals are never guessed: 12:30 could as easily be a museum.
- **Shown on:** for multi-day cards, untick the days it shouldn't be listed on. A ten-day rental can show only on its pick-up and drop-off days.
- **Price in NT$** feeds the cost summary. Leave it blank to keep a card out.
- **Subheading:** prefix a country with its code (`jp:Japan`) and it renders as a flag, which also works on Windows.

Each project folder holds `trip.json`, the documents, the last 10 undo points and every export you downloaded. The start page has **Download full backup** and **Import .zip / .json**. A trip `.zip` carries its documents and always imports as a new trip.

`tools/import_legacy_html.py` converts an older hand-built itinerary page, the format this app grew out of. You probably don't need it.

## How it's built

| Path | What |
|---|---|
| `app/main.py` | Flask app: editor routes, live link, HTTP Basic login |
| `app/api.py` | JSON API for scripts and agents (`/api/v1`) |
| `app/generator.py` | The exported page: its CSS, JavaScript and HTML |
| `app/storage.py` | Projects on disk, PDF rendering, history, zip import/export |
| `app/triptypes.py` | Card types and parts of the day |
| `app/templates/`, `app/static/` | Editor UI (Jinja, plain JavaScript, no build step) |
| `unraid/` | Unraid template and update script |

[HANDOFF.md](HANDOFF.md) explains how it works and why, for whoever changes it next. PDFs are rendered with [pypdfium2](https://github.com/pypdfium2-team/pypdfium2). Served by [Waitress](https://github.com/Pylons/waitress).

## License

MIT, see [LICENSE](LICENSE).
