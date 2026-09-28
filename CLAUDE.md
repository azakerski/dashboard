# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Sensitive Files

Never read `config.py` or `.env` — these contain API keys and credentials.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally (http://localhost:5000)
python app.py

# Add new Spektrix events + upcoming instances to events-config-dashboard.json
python sync_events.py --dry-run
python sync_events.py

# Rebuild eventsToSnapshot.json from past instances that have no salesSnapshot yet
python build_snapshot_list.py --dry-run
python build_snapshot_list.py

# Capture sales snapshots for targeted instances (eventsToSnapshot.json)
python snapshot.py            # snapshot all instances in eventsToSnapshot.json
python snapshot.py --dry-run  # preview without writing

# Force cache refresh
curl -X POST http://localhost:5000/api/cache/clear
```

## Environment Variables

| Variable | Where | Purpose |
|----------|-------|---------|
| `SPEKTRIX_CLIENT` | Render + `config.py` | Spektrix client name |
| `SPEKTRIX_API_KEY` | Render + `config.py` | Spektrix API key |
| `SPEKTRIX_API_SECRET` | Render + `config.py` | Base64-encoded secret |
| `DASHBOARD_PASSWORD` | Render + `config.py` | Dashboard HTTP Basic Auth password |
| `AWS_ACCESS_KEY_ID` | Render + `aws configure` | AWS credentials for S3 (jas-dashboard IAM user) |
| `AWS_SECRET_ACCESS_KEY` | Render + `aws configure` | AWS credentials for S3 |
| `AWS_DEFAULT_REGION` | Render + `aws configure` | `us-east-2` |
| `AXS_S3_BUCKET` | Render + `.env` | S3 bucket name for AXS data (`jas-axs-s3-bucket`) |

Locally, AWS credentials come from `aws configure` — no need to add them to `.env`.

## Architecture

**Data flow:** `events-config-dashboard.json` is the source of truth for all event metadata (names, instance IDs, seating plans, seasons). The live Spektrix API is only called for seat availability on upcoming events — past events use a `salesSnapshot` embedded in the JSON.

**`app.py`** — Flask server that:
- Loads `events-config-dashboard.json` at startup into `EVENTS`, `PLAN_CONFIGS`, `SEASONS`, and `AFTER_DARK_CAPACITIES`
- Signs all Spektrix API requests server-side with HMAC-SHA1 (`make_spektrix_request`)
- Uses a shared `requests.Session` with `pool_maxsize=25` for connection pooling across parallel Spektrix calls
- On `/api/instances`, fetches area status for all instances in parallel (`ThreadPoolExecutor`, 20 workers) and caches the result for 5 minutes. Each instance response includes `eventType` (from `attribute_EventType`)
- Returns `seasonOrder` (a list of season keys in insertion order) alongside `seasons` in the `/api/instances` response — Flask's `jsonify` sorts dict keys alphabetically, so `seasonOrder` is the authoritative order for rendering filter buttons
- Resolves each instance's `season` from the instance-level `attribute_Season` (a Spektrix instance attribute), falling back to the event-level `attribute_Season` when the instance value is missing or empty. This lets a returning artist's new instances live under the same event but belong to a different season
- Skips the API entirely for instances that have a `salesSnapshot` in the JSON
- Protects all routes with HTTP Basic Auth (username `jas`, password from config)
- Credentials load from `config.py` locally, or from environment variables (`SPEKTRIX_CLIENT`, `SPEKTRIX_API_KEY`, `SPEKTRIX_API_SECRET`, `DASHBOARD_PASSWORD`) in production

**`dashboard.html`** — Single-file frontend. Fetches from `/api/instances` and renders the ticket sales UI. No build step.
- Season filter buttons are built from `seasonOrder` (not `Object.keys(seasons)`) to preserve the intended order. The first season in `seasonOrder` is selected by default. To change the button order, reorder the `seasons` keys in `events-config-dashboard.json`.
- "Upcoming Only" filter defaults to off (`showUpcoming = false`) so past seasons show all their events.
- All three tables (Spektrix, AXS, VIP Passes) support click-to-sort on column headers. Clicking a column sorts it; clicking again reverses direction. Numeric columns default to descending on first click; date columns default to ascending.

**`snapshot.py`** — Standalone script, not called by `app.py` or deployed in any way — pushing changes to it has zero effect on the running app until someone runs it by hand. Reads its target instance list from `eventsToSnapshot.json` (not the full `events-config-dashboard.json`), calls Spektrix for each one, and writes the `salesSnapshot` results into the matching instances in `events-config-dashboard.json` by instance ID lookup. No date filtering — it will snapshot any instance listed regardless of past/upcoming — and always overwrites any existing snapshot for that instance. Run this whenever VIP or other pre-snapshotted instances need refreshed data, then commit the updated `events-config-dashboard.json` (that file is what actually feeds the live dashboard).

**Seating plan resolution in `app.py`:**
1. Instance has `salesSnapshot` → use JSON data, no API call
2. Instance `planId` is in `planConfigs` → fetch per-area status (one request per area)
3. Instance has a `planId` not in `planConfigs` → fetch instance-level status (single-area fallback)
4. Instance has no `planId` → skipped

**Deployment:** Render auto-deploys from `main` via `gunicorn app:app --timeout 120`. The 120-second timeout is required because cold cache builds (all parallel Spektrix API calls) can take 20-30 seconds on Render's network — the default 30-second gunicorn timeout is too short. A `staging` branch and separate Render service exist for testing changes before merging to `main`.

**AXS tab:** A second tab in `dashboard.html` shows ticket data from AXS (a separate ticketing platform). AXS is configured to write JSON files to S3 bucket `jas-axs-s3-bucket` (us-east-2) daily via Amazon S3 export (Looker's S3 destination, use case "Third-party service"). Two IAM users exist: `jas-dashboard` (read-only, used by the app) and `axs-uploader` (write-only, credentials given to Looker/AXS — access key/secret pair is separate from `jas-dashboard`'s and must be rotated independently via the IAM console if lost, since AWS never re-displays a secret key after creation).

Three files land in the bucket daily, each identified by filename prefix (`app.py`'s `AXS_FILE_PREFIXES`) — `/api/axs` picks the most recently modified object matching each prefix independently, rather than just the single latest file in the bucket:
- `Dashboard_Sales_By_Event_` — the main daily report (all events)
- `After_Dark_SRO_` — After Dark SRO section breakout
- `After_Dark_Reserved_` — After Dark Reserved section breakout

All three are fetched and cached together under the existing 1-hour TTL. If a prefix has no matching file on a given day, that section comes back as an empty list rather than failing the whole endpoint. The main report currently still includes an aggregate "After Dark" line in addition to the two breakout files — no double-count filtering has been implemented for that yet. Once the Looker export for the main report is updated to drop that aggregate line, no code changes are needed on the dashboard side.

AXS JSON fields used by the dashboard:
| Field | Description |
|-------|-------------|
| `Event Date` | ISO date string `YYYY-MM-DD` |
| `Event Name` | Event name |
| `Venue Name` | Venue name |
| `Primary: Tickets Sold` | Comma-formatted integer string e.g. `"2,890"` |
| `Tickets Comp` | Integer string (no commas) |
| `Tickets Issued: Primary` | Comma-formatted integer string — total tickets (sold + comp) |

Note: all numeric fields arrive as strings and may include commas. The frontend strips commas (and `$` if present) before parsing.

**AXS filter:** Events are split into two buckets by month/day — anything before July 15 is "June", anything on or after July 15 is "Labor Day". A fourth filter button "June/Labor Day VIP" shows only VIP instances. Filter buttons are rendered in `buildAxsFilters()` in `dashboard.html`.

**VIP Passes section:** Below the AXS table, the AXS tab also shows a "VIP Passes — Spektrix" section. This pulls from `/api/instances` and filters to instances where `eventType` is `"June Experience"` or `"Labor Day Experience"` (set via `attribute_EventType` in the JSON). These events are excluded from the main Spektrix tab. Labor Day VIP rows are expandable to show per-area breakdowns (same as the Spektrix tab). VIP instances should be snapshotted via `snapshot.py` to avoid adding API call overhead to every cold cache build.

**After Dark — SRO & Reserved section:** Below the VIP Passes section, the AXS tab shows a flat, always-visible (not tied to the June/Labor Day filter) table of the `afterDark.sro` and `afterDark.reserved` rows from `/api/axs`, one row per event date per section (6 rows for the 3 After Dark dates × 2 sections). Capacity differs by day of week — Friday/Sunday share one capacity, Saturday has a different one — so capacity isn't a flat number per section; it's looked up per row via `afterDarkCapacityFor(section, eventDate)` in `dashboard.html`, keying off `getDay() === 6` (Saturday) vs. Friday/Sunday. The underlying capacity values live in `events-config-dashboard.json`'s top-level `afterDarkCapacities` block:
```json
"afterDarkCapacities": {
  "sro":      { "friSun": 190, "sat": 160 },
  "reserved": { "friSun": 58,  "sat": 90 }
}
```
This is exposed to the frontend via `/api/config` (alongside `seasons`). The `% Sold` column reuses the same `prog-wrap`/`prog-bar`/`prog-fill`/`prog-pct` markup and green/yellow/red (≥90% red, ≥70% yellow) thresholds as the Spektrix tab's progress bar, for visual consistency.

**`sync_events.py`** — Standalone script (run by hand, like `snapshot.py`). Calls `/api/v3/events?$expand=instances` and adds to `events-config-dashboard.json`: (1) new events, with only their upcoming (today or later, not cancelled) instances, and only if a season resolves to a key in the `seasons` block — which keeps test/rental events out, and the skipped ones are listed; (2) new upcoming instances on existing events, matched by instance ID. Never modifies existing events/instances, so snapshots are preserved. Warns about missing/unknown `planId`s and about instances added to existing events with no instance-level `attribute_Season` (they'd inherit the event's season).

**`build_snapshot_list.py`** — Standalone script. Overwrites `eventsToSnapshot.json` with every instance in the main config that has no `salesSnapshot`, a start date before today, a `planId`, and isn't cancelled, plus the `planConfigs` those instances use. Run it, then `snapshot.py`.

**`eventsToSnapshot.json`** — Config file for `snapshot.py`. Contains the `planConfigs` and `events`/`instances` you want to snapshot. Add events here when you want targeted snapshots without running against the full config.

**Theme toggle:** `dashboard.html` supports light and dark themes via a `body.light` CSS class override. The preference is persisted to `localStorage` under the key `theme`.
