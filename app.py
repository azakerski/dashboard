"""
Spektrix Sales Dashboard — Flask Proxy Server
=============================================
On first request (or after cache expires), fetches area status for all
instances in parallel and caches the result for 5 minutes. Subsequent
requests are served instantly from cache.

Uses salesSnapshot in the JSON for past events — no API call needed once
a snapshot is saved.

Run:  python app.py
Open: http://localhost:5000
"""

import base64
import hashlib
import hmac
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import requests
from requests.adapters import HTTPAdapter
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from flask_httpauth import HTTPBasicAuth

# ─── Configuration ────────────────────────────────────────────────────────────
try:
    from config import API_KEY, API_SECRET, CLIENT_NAME, DASHBOARD_PASSWORD
except ImportError:
    CLIENT_NAME        = os.environ.get("SPEKTRIX_CLIENT", "")
    API_KEY            = os.environ.get("SPEKTRIX_API_KEY", "")
    API_SECRET         = os.environ.get("SPEKTRIX_API_SECRET", "")
    DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")

CACHE_TTL     = 300   # seconds (5 minutes)
AXS_CACHE_TTL = 3600  # seconds (1 hour)

AXS_S3_BUCKET = os.environ.get("AXS_S3_BUCKET", "jas-axs-s3-bucket")

app  = Flask(__name__, static_folder=".")
CORS(app)
auth = HTTPBasicAuth()

@auth.verify_password
def verify_password(username, password):
    return username == "jas" and password == DASHBOARD_PASSWORD

# ─── Load events config ───────────────────────────────────────────────────────
with open("events-config-dashboard.json") as f:
    _config = json.load(f)

SEASONS      = _config.get("seasons", {})
PLAN_CONFIGS = _config["planConfigs"]
EVENTS       = _config["events"]

# instance id → planId lookup
_instance_plan_map: dict[str, str] = {}
for _event in EVENTS:
    for _inst in _event.get("instances", []):
        if "planId" in _inst:
            _instance_plan_map[_inst["id"]] = _inst["planId"]

# ─── Cache ────────────────────────────────────────────────────────────────────
_cache_lock = threading.Lock()
_cache: dict = {"data": None, "expires_at": None}

_axs_lock = threading.Lock()
_axs_cache: dict = {"data": None, "expires_at": None}


def _cache_valid() -> bool:
    return _cache["data"] is not None and datetime.now() < _cache["expires_at"]


# ─── Spektrix API ─────────────────────────────────────────────────────────────
_session = requests.Session()
_session.mount("https://", HTTPAdapter(pool_connections=1, pool_maxsize=25))

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
    resp = _session.get(url, headers={
        "Authorization": f"SpektrixAPI3 {API_KEY}:{sig}",
        "Host":          "system.spektrix.com",
        "Date":          date,
        "Content-Type":  "application/json",
    }, timeout=8)
    resp.raise_for_status()
    return resp.json()


def fetch_area_status(instance_id: str, area_id: str) -> dict:
    return make_spektrix_request(
        f"/api/v3/instances/{instance_id}/status/areas/{area_id}"
        "?includeLockInformation=true&includeChildPlans=true"
    )


def fetch_instance_status(instance_id: str) -> dict:
    """Simplified call for instances with a single area and no planConfig entry."""
    return make_spektrix_request(
        f"/api/v3/instances/{instance_id}/status"
        "?includeLockInformation=true&includeChildPlans=true"
    )


def compute_summary(areas: list[dict]) -> dict:
    summary: dict = {}
    for area in areas:
        for key, val in area.items():
            if isinstance(val, (int, float)) and key != "areaId":
                summary[key] = summary.get(key, 0) + val
    return summary


# ─── Parallel fetch ───────────────────────────────────────────────────────────
def _build_full_dataset() -> list[dict]:
    """
    Fetch area status for all instances in parallel.
    Instances with a salesSnapshot in the JSON skip the API call entirely.
    """
    api_tasks: list[tuple]      = []
    snapshots: dict[str, list]  = {}

    for event in EVENTS:
        for inst in event.get("instances", []):
            inst_id = inst.get("id")
            if not inst_id:
                continue
            if "salesSnapshot" in inst:
                snapshots[inst_id] = inst["salesSnapshot"].get("areas", [])
            elif inst.get("planId") in PLAN_CONFIGS:
                # Multi-area: one task per area
                for area_name, area_id in PLAN_CONFIGS[inst["planId"]]["seatingAreas"].items():
                    api_tasks.append((inst_id, area_name, area_id))
            elif inst.get("planId"):
                # Single-area fallback: use instance-level status endpoint
                api_tasks.append((inst_id, None, None))

    def _fetch(inst_id: str, area_name: str | None, area_id: str | None) -> tuple:
        try:
            if area_id:
                status = fetch_area_status(inst_id, area_id)
                status["areaId"]   = area_id
                status["areaName"] = area_name
            else:
                status = fetch_instance_status(inst_id)
                status["areaName"] = status.get("name", "General Admission")
        except Exception as e:
            status = {"areaName": area_name or "General Admission", "error": str(e)}
        return inst_id, status

    live: dict[str, list] = {}
    if api_tasks:
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(_fetch, *t) for t in api_tasks]
            for future in as_completed(futures):
                inst_id, area_data = future.result()
                live.setdefault(inst_id, []).append(area_data)

    instances = []
    for event in EVENTS:
        season = event.get("attribute_Season", "")
        for inst in event.get("instances", []):
            inst_id = inst.get("id")
            if not inst_id:
                continue
            areas      = snapshots.get(inst_id) or live.get(inst_id, [])
            successful = [a for a in areas if "error" not in a]
            instances.append({
                "id":           inst_id,
                "eventName":    event["name"],
                "start":        inst.get("start", ""),
                "isOnSale":     inst.get("isOnSale", False),
                "cancelled":    inst.get("cancelled", False),
                "planId":       inst.get("planId", ""),
                "season":       season,
                "eventType":    event.get("attribute_EventType", ""),
                "fromSnapshot": inst_id in snapshots,
                "areas":        areas,
                "summary":      compute_summary(successful),
            })

    instances.sort(key=lambda x: x["start"])
    return instances


def _get_cached_data() -> list[dict]:
    with _cache_lock:
        if _cache_valid():
            return _cache["data"]
        data = _build_full_dataset()
        _cache["data"]       = data
        _cache["expires_at"] = datetime.now() + timedelta(seconds=CACHE_TTL)
        return data


# ─── Routes ───────────────────────────────────────────────────────────────────
@app.route("/api/instances")
@auth.login_required
def api_instances():
    instances = _get_cached_data()
    return jsonify({
        "fetchedAt":   datetime.utcnow().isoformat() + "Z",
        "cachedUntil": _cache["expires_at"].isoformat() + "Z" if _cache["expires_at"] else None,
        "seasons":     SEASONS,
        "instances":   instances,
    })


@app.route("/api/instance/<instance_id>/areas")
@auth.login_required
def api_instance_areas(instance_id: str):
    """On-demand fetch for a single instance — bypasses cache."""
    plan_id = _instance_plan_map.get(instance_id)
    if not plan_id:
        return jsonify({"error": "Instance not found in config"}), 404
    plan_config = PLAN_CONFIGS.get(plan_id)
    if not plan_config:
        return jsonify({"error": f"No plan config for planId {plan_id}"}), 404

    areas = []
    for area_name, area_id in plan_config["seatingAreas"].items():
        try:
            status = fetch_area_status(instance_id, area_id)
            status["areaId"]   = area_id
            status["areaName"] = area_name
        except Exception as e:
            status = {"areaName": area_name, "areaId": area_id, "error": str(e)}
        areas.append(status)

    successful = [a for a in areas if "error" not in a]
    return jsonify({
        "instanceId": instance_id,
        "fetchedAt":  datetime.now().isoformat(),
        "areas":      areas,
        "summary":    compute_summary(successful),
    })


@app.route("/api/cache/clear", methods=["POST"])
@auth.login_required
def clear_cache():
    """Force a full re-fetch on the next /api/instances request."""
    with _cache_lock:
        _cache["data"]       = None
        _cache["expires_at"] = None
    return jsonify({"cleared": True})


@app.route("/api/config")
@auth.login_required
def api_config():
    return jsonify({"clientName": CLIENT_NAME, "seasons": SEASONS})


@app.route("/api/axs")
@auth.login_required
def api_axs():
    with _axs_lock:
        if _axs_cache["data"] is not None and datetime.now() < _axs_cache["expires_at"]:
            data = _axs_cache["data"]
        else:
            if not AXS_S3_BUCKET:
                return jsonify({"fetchedAt": datetime.utcnow().isoformat() + "Z", "events": [], "note": "AXS_S3_BUCKET not configured"})
            try:
                import boto3
                s3       = boto3.client("s3", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-2"))
                objects  = s3.list_objects_v2(Bucket=AXS_S3_BUCKET)
                contents = objects.get("Contents", [])
                if not contents:
                    return jsonify({"fetchedAt": datetime.utcnow().isoformat() + "Z", "events": [], "note": "No files found in bucket"})
                latest = max(contents, key=lambda o: o["LastModified"])
                obj    = s3.get_object(Bucket=AXS_S3_BUCKET, Key=latest["Key"])
                data   = json.loads(obj["Body"].read().decode("utf-8"))
            except Exception as e:
                return jsonify({"error": str(e), "events": []}), 500
            _axs_cache["data"]              = data
            _axs_cache["expires_at"]        = datetime.now() + timedelta(seconds=AXS_CACHE_TTL)
            _axs_cache["file_modified_at"]  = latest["LastModified"].isoformat()

    return jsonify({"fetchedAt": datetime.utcnow().isoformat() + "Z", "fileModifiedAt": _axs_cache.get("file_modified_at"), "events": data})


@app.route("/")
@auth.login_required
def index():
    return send_from_directory(".", "dashboard.html")


if __name__ == "__main__":
    if not CLIENT_NAME or not API_KEY:
        print("WARNING: CLIENT_NAME or API_KEY not set. Edit config.py before running.")
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 5000))
    print(f"\nSpektrix Dashboard running at http://localhost:{port}\n")
    app.run(host=host, port=port, debug=True)
