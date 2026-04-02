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
| `snapshot.py` | Script to capture final sales data for past events into the JSON |
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

```bash
# Preview what will be updated
python snapshot.py --dry-run

# Capture snapshots for all past instances
python snapshot.py

# Re-capture all past instances (overwrites existing snapshots)
python snapshot.py --all
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
