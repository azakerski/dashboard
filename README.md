# JAS Ticket Sales Dashboard

Real-time ticket sales dashboard for Jazz Aspen Snowmass, backed by the Spektrix API v3.
Flask handles all HMAC-SHA1 authentication server-side. Deployed on Render and accessible
to the full team via a password-protected URL.

---

## Files

| File | Purpose |
|------|---------|
| `app.py` | Flask server — Spektrix auth, caching, API endpoints |
| `dashboard.html` | Web dashboard UI |
| `events-config-dashboard.json` | Events, seating plans, seasons, and sales snapshots |
| `sync_events.py` | Script to add new Spektrix events and upcoming instances to the JSON |
| `build_snapshot_list.py` | Script to fill `eventsToSnapshot.json` with past instances that need a snapshot |
| `snapshot.py` | Script to capture final sales data for the instances in `eventsToSnapshot.json` |
| `eventsToSnapshot.json` | Target list for `snapshot.py` |
| `requirements.txt` | Python dependencies |
| `config.py` | Local credentials — **never commit this file** |
| `.env` | Local environment variables — **never commit this file** |

---

## Local Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure credentials

Edit `config.py` with your Spektrix credentials and dashboard password:

```python
CLIENT_NAME        = "jazzaspensnowmass"
API_KEY            = "your_api_key"
API_SECRET         = "your_base64_secret"
DASHBOARD_PASSWORD = "your_password"
```

### 3. Start the server

```bash
python app.py
```

Open **http://localhost:5000** in any browser. Enter username `jas` and your password.

---

## Authentication

### Dashboard (HTTP Basic Auth)
The dashboard is password-protected. Credentials:
- **Username:** `jas`
- **Password:** set via `DASHBOARD_PASSWORD` in `config.py` (local) or Render environment variables (production)

### Spektrix API (HMAC-SHA1)
All Spektrix API calls are signed server-side using HMAC-SHA1. The signature format is:
```
string_to_sign = "GET\n{full_url}\n{date}"
Authorization: SpektrixAPI3 {API_KEY}:{base64_signature}
```

---

## Events Config (`events-config-dashboard.json`)

This JSON file is the single source of truth for events, seating plans, and seasons.
The live API is only called for seat availability — everything else comes from here.

### Structure

```
{
  "seasons": { "Winter 2026": { "label": "...", "year": 2026 } },
  "planConfigs": {
    "<fullPlanId>": {
      "seatingAreas": { "area-name": "<fullAreaId>" }
    }
  },
  "events": [
    {
      "name": "Artist Name",
      "attribute_Season": "Winter 2026",
      "instances": [
        {
          "id": "...",
          "planId": "...",
          "start": "2026-04-09T19:00:00",
          "salesSnapshot": { ... }   ← added by snapshot.py after show ends
        }
      ]
    }
  ]
}
```

### Seating plan logic

| Instance | Action |
|---|---|
| Has `salesSnapshot` | Uses JSON data — no API call |
| `planId` in `planConfigs` | Fetches per-area status from Spektrix |
| `planId` not in `planConfigs` | Fetches instance-level status (single-area fallback) |
| No `planId` | Skipped |

### Adding a new seating plan

Add an entry to `planConfigs` keyed by the full planId:

```json
"planConfigs": {
  "NEW_FULL_PLAN_ID": {
    "layoutName": "new-layout",
    "seatingAreas": {
      "area-name": "FULL_AREA_ID"
    }
  }
}
```

Any instance whose `planId` matches will automatically use it.

### Adding a new season

Add an entry to `seasons` and set `attribute_Season` on the relevant events:

```json
"seasons": {
  "Summer 2026": { "label": "Summer 2026", "year": 2026 }
}
```

Each key in `seasons` becomes a filter button, in the order listed. The first one is
selected by default. A season that isn't in `seasons` gets no button, and its instances
only show under **All Seasons**.

### Season per instance

Season is set per instance, so a returning artist's new dates can stay under the same
event but belong to a different season. `app.py` uses the instance's `attribute_Season`
(an instance-level attribute in Spektrix) and falls back to the event's `attribute_Season`
when the instance value is empty.

---

## Adding New Events (`sync_events.py`)

`sync_events.py` pulls `/api/v3/events?$expand=instances` from Spektrix and adds anything
new to `events-config-dashboard.json`, so events don't need to be pasted in by hand.

```bash
# Preview what will be added
python sync_events.py --dry-run

# Add new events and instances to the JSON
python sync_events.py
```

- **New events** are added with their upcoming instances only (today or later, not cancelled).
- **Existing events** get any new upcoming instances added, matched by instance ID.
- **Existing events and instances are never changed**, so snapshots and hand edits are safe.
- **New events with no season are skipped.** An event is only added if its season (on the
  event, or on one of its upcoming instances) matches a key in the `seasons` block. This keeps
  test events, rentals and RSVP-only events out. Skipped events are listed at the end of the
  output. To include one, set its Season in Spektrix to a season that's in the `seasons` block
  and run the sync again.
- **Instances on existing events are added whatever their season.** If a season isn't in the
  `seasons` block (e.g. a season too far off to have a button yet), the script shows a warning
  and the instance only appears under **All Seasons**.
- **Other warnings** cover a missing `planId`, a plan that isn't in `planConfigs` (single-area
  totals only), and a new instance on an existing event with no instance-level Season (it
  inherits the event's season, which is usually wrong for a returning artist).

Review the changes with `git diff`, then commit and push to deploy.

---

## Server-Side Caching

On first page load (or after cache expires), `app.py` fetches area status for all instances
in parallel using `ThreadPoolExecutor` (up to 20 concurrent requests). Results are cached
for **5 minutes**. Subsequent loads are served instantly from cache.

To force a refresh before the cache expires:
```bash
curl -X POST http://localhost:5000/api/cache/clear
```

---

## Sales Snapshots

Once a show has passed, its ticket counts are final. Run `snapshot.py` to capture the
final numbers into the JSON so the dashboard no longer needs to call the API for past events.

Snapshots are per instance. `snapshot.py` snapshots the instances listed in
`eventsToSnapshot.json` and always overwrites any existing snapshot for them. To fill that
file with every past instance that doesn't have a snapshot yet, run `build_snapshot_list.py`
first. It also copies over the `planConfigs` those instances use.

```bash
# Rebuild eventsToSnapshot.json from past, un-snapshotted instances
python build_snapshot_list.py --dry-run
python build_snapshot_list.py

# Preview, then capture the snapshots into events-config-dashboard.json
python snapshot.py --dry-run
python snapshot.py
```

Run this at the end of each season, then commit and push the updated JSON.

---

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/instances` | All instances with area data — served from cache |
| `GET /api/instance/{id}/areas` | Live area fetch for one instance — bypasses cache |
| `POST /api/cache/clear` | Forces a full re-fetch on next `/api/instances` request |
| `GET /api/config` | Returns client name and seasons |

All endpoints require Basic Auth.

---

## Deployment (Render)

The dashboard is deployed at [jas-dashboard.onrender.com](https://jas-dashboard.onrender.com).

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `gunicorn app:app`

### Environment variables (set in Render dashboard)

| Key | Description |
|-----|-------------|
| `SPEKTRIX_CLIENT` | Spektrix client name |
| `SPEKTRIX_API_KEY` | API key (username) |
| `SPEKTRIX_API_SECRET` | Base64-encoded API secret |
| `DASHBOARD_PASSWORD` | Dashboard access password |

### Deploying updates

```bash
git add .
git commit -m "describe what changed"
git push
```

Render auto-deploys within ~1 minute of a push to `main`.

---

## Troubleshooting

**"Could not reach the Spektrix proxy"**
Make sure `python app.py` is running locally, or check the Render deployment logs.

**401 from Spektrix API**
Check `API_KEY` and `API_SECRET` in `config.py`. The secret must be base64-encoded.

**Season filter buttons not working**
Season names with spaces must be passed via `data-season` attribute — check `buildSeasonFilters()` in `dashboard.html`.

**Page loads but shows no instances**
The default filter is "Upcoming Only". Toggle it off or check that instances in the JSON have future `start` dates.
