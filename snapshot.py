"""
Spektrix Sales Snapshot Tool
============================
Fetches final area sales data from the Spektrix API for past instances
and writes it into events-config-dashboard.json as a salesSnapshot.

Once an instance has a salesSnapshot, app.py uses it directly and makes
no API call for that instance — keeping page loads fast.

Usage:
  python snapshot.py           # snapshot all past instances missing a snapshot
  python snapshot.py --all     # re-snapshot all past instances (overwrites)
  python snapshot.py --dry-run # show what would be updated without writing
"""

import argparse
import base64
import hashlib
import hmac
import json
import sys
from datetime import datetime

import requests

# ─── Credentials ─────────────────────────────────────────────────────────────
try:
    from config import API_KEY, API_SECRET, CLIENT_NAME
except ImportError:
    import os
    CLIENT_NAME = os.environ.get("SPEKTRIX_CLIENT", "")
    API_KEY     = os.environ.get("SPEKTRIX_API_KEY", "")
    API_SECRET  = os.environ.get("SPEKTRIX_API_SECRET", "")

CONFIG_FILE = "events-config-dashboard.json"


# ─── Spektrix API ─────────────────────────────────────────────────────────────
def make_spektrix_request(path: str) -> dict | list:
    url  = f"https://system.spektrix.com/{CLIENT_NAME}{path}"
    date = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    sig  = base64.b64encode(
        hmac.new(
            base64.b64decode(API_SECRET),
            f"GET\n{url}\n{date}".encode(),
            hashlib.sha1,
        ).digest()
    ).decode()
    resp = requests.get(url, headers={
        "Authorization": f"SpektrixAPI3 {API_KEY}:{sig}",
        "Host":          "system.spektrix.com",
        "Date":          date,
        "Content-Type":  "application/json",
    }, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_area_status(instance_id: str, area_id: str) -> dict:
    return make_spektrix_request(
        f"/api/v3/instances/{instance_id}/status/areas/{area_id}"
        "?includeLockInformation=true&includeChildPlans=true"
    )


# ─── Snapshot logic ───────────────────────────────────────────────────────────
def snapshot_instance(instance_id: str, plan_configs: dict, plan_id: str) -> dict | None:
    """Fetch area data for one instance and return a salesSnapshot dict."""
    plan_config = plan_configs.get(plan_id)
    areas = []

    if plan_config:
        # Multi-area: fetch each area separately
        for area_name, area_id in plan_config["seatingAreas"].items():
            try:
                status = fetch_area_status(instance_id, area_id)
                status["areaName"] = area_name
                status["areaId"]   = area_id
                areas.append(status)
                print(f"    {area_name}: sold={status.get('sold', '?')} / capacity={status.get('capacity', '?')}")
            except Exception as e:
                print(f"    ⚠  {area_name}: API error — {e}")
    else:
        # Single-area fallback: use instance-level status endpoint
        try:
            status = make_spektrix_request(
                f"/api/v3/instances/{instance_id}/status"
                "?includeLockInformation=true&includeChildPlans=true"
            )
            status["areaName"] = status.get("name", "General Admission")
            areas.append(status)
            print(f"    {status['areaName']}: sold={status.get('sold', '?')} / capacity={status.get('capacity', '?')}")
        except Exception as e:
            print(f"    ⚠  API error — {e}")

    if not areas:
        return None

    return {
        "capturedAt": datetime.now().isoformat(),
        "areas":      areas,
    }


def main():
    parser = argparse.ArgumentParser(description="Capture Spektrix sales snapshots for past events.")
    parser.add_argument("--all",     action="store_true", help="Re-snapshot all past instances, overwriting existing snapshots")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without writing to the JSON")
    args = parser.parse_args()

    # Load config
    with open(CONFIG_FILE) as f:
        config = json.load(f)

    plan_configs = config["planConfigs"]
    now          = datetime.now()
    updated      = 0
    skipped      = 0

    for event in config["events"]:
        for inst in event.get("instances", []):
            inst_id  = inst.get("id")
            plan_id  = inst.get("planId")
            start    = inst.get("start", "")

            # Only process past instances
            try:
                inst_date = datetime.fromisoformat(start)
            except (ValueError, TypeError):
                continue

            if inst_date >= now:
                continue  # upcoming — skip

            if not plan_id:
                continue  # no seating plan (free event etc.)

            # Skip if already has a snapshot (unless --all)
            if "salesSnapshot" in inst and not args.all:
                skipped += 1
                continue

            print(f"\n{event['name']} — {start}")

            if args.dry_run:
                print("  [dry-run] would snapshot this instance")
                updated += 1
                continue

            snapshot = snapshot_instance(inst_id, plan_configs, plan_id)
            if snapshot:
                inst["salesSnapshot"] = snapshot
                updated += 1
            else:
                print("  ⚠  No data returned — snapshot not saved")

    print(f"\n{'─' * 50}")
    if args.dry_run:
        print(f"Dry run complete. Would snapshot {updated} instance(s). ({skipped} already have snapshots)")
    else:
        if updated > 0:
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)
            print(f"Done. Snapshots saved for {updated} instance(s). ({skipped} already had snapshots and were skipped)")
        else:
            print(f"Nothing to update. ({skipped} instance(s) already have snapshots — use --all to overwrite)")


if __name__ == "__main__":
    main()
