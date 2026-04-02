# Spektrix Ticket Sales Dashboard

Real-time ticket sales dashboard backed by the Spektrix API v3.  
Flask handles all HMAC-SHA1 authentication server-side — no CORS issues.

---

## Files

| File | Purpose |
|------|---------|
| `app.py` | Flask proxy server — handles Spektrix auth, exposes local API endpoints |
| `config.py` | Your credentials + seating area IDs — **edit this first** |
| `dashboard.html` | Web dashboard — open in browser after starting the server |
| `requirements.txt` | Python dependencies |

---

## Setup

### 1. Install dependencies

```bash
pip install flask flask-cors requests
```

Or use the requirements file:
```bash
pip install -r requirements.txt
```

### 2. Edit `config.py`

Fill in your Spektrix credentials and seating area IDs:

```python
CLIENT_NAME   = "your_client_name"   # from your Spektrix URL
API_KEY       = "your_api_key"
API_SECRET    = "your_api_secret"

SEATING_AREAS = {
    "JAS Café - Dance":    "6602ATBVLJQKCMGMLQRDTTBNMTTCNKCBH",
    "JAS Café - Reserved": "YOUR_RESERVED_AREA_ID",
}
```

**Finding your area IDs:** Run your existing Python auth script against:
```
GET /api/v3/instances/{instanceId}/status?includeChildPlans=true
```
The response will include child area data with their IDs. Or check the ID
embedded in your existing test calls.

### 3. Replace the auth function (if needed)

`app.py` includes a default HMAC-SHA1 implementation, but if your working
`make_spektrix_request()` function uses a different signature format, paste
it into `app.py` directly (look for the `# ↓ Paste your existing...` comment).

### 4. Start the server

```bash
python app.py
```

You should see:
```
🎵  Spektrix Dashboard running at http://localhost:5000
```

### 5. Open the dashboard

Navigate to **http://localhost:5000** in any browser.

---

## Sharing with your team

### Option A — Web Dashboard (same network)

Find your machine's local IP address:
- Windows: `ipconfig` → look for IPv4 Address (e.g. `192.168.1.42`)
- Mac: `ifconfig` or System Settings → Network

Share the URL: `http://192.168.1.42:5000`

Anyone on the same network can open it. The server needs to keep running
on your machine (or leave it running on a shared/always-on PC).

### Option B — Excel via Power Query

1. Open Excel → **Data** tab → **Get Data** → **From Web**
2. Enter URL: `http://localhost:5000/api/instances`
3. Click **OK** → in the Power Query editor, expand the `instances` column
4. Expand `status` to get sold, reserved, locked, available, capacity columns
5. Load to a worksheet or pivot table
6. Hit **Refresh All** in Excel any time you want live data

The Flask server must be running when you refresh in Excel.

### Option C — Schedule with Task Scheduler (Windows)

To keep data "auto-refreshed" in Excel without manually triggering:
1. Create a Python script that calls the Spektrix API and writes to a CSV/Excel file
2. Schedule it in Windows Task Scheduler to run every 15–30 minutes
3. Excel auto-refreshes when the source file updates

---

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/instances` | All upcoming instances with top-level status. Good for Excel. |
| `GET /api/instance/{id}/areas` | Per-area breakdown for one instance (uses config.py area IDs) |
| `GET /api/instance/{id}/area/{areaId}` | Status for a single specific area |
| `GET /api/config` | Returns configured client name and seating areas |

---

## Troubleshooting

**"Could not reach the Spektrix proxy"**  
Make sure `python app.py` is running in a terminal window.

**401 Unauthorized from Spektrix**  
Double-check `API_KEY` and `API_SECRET` in `config.py`.  
If the default HMAC format doesn't match, paste your working auth function into `app.py`.

**Area breakdown shows "No seating areas configured"**  
Add your area IDs to `SEATING_AREAS` in `config.py`.

**Excel Power Query error: "couldn't connect"**  
The Flask server needs to be running. Also confirm the URL is exactly `http://localhost:5000/api/instances`.

---

## Notes on rate limiting

Spektrix recommends server-side caching for availability calls. This dashboard
makes one status call per instance on load. If you have many instances (50+),
consider adding a small `time.sleep(0.1)` between requests in `app.py`'s
`api_instances()` route to avoid hitting rate limits.
