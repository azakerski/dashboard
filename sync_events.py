"""
Sync New Events from Spektrix
=============================
Pulls /api/v3/events?$expand=instances from Spektrix and adds anything new
to events-config-dashboard.json:

  - New events (id not in the config) are added with their upcoming
    instances only — but only if the event or one of those instances has
    an attribute_Season that exists in the config's `seasons` block. This
    keeps test events, rentals, etc. out; anything skipped is listed.
  - Existing events get any new upcoming instances appended (by instance id).

"Upcoming" means the instance starts today or later (venue-local date), and
cancelled instances are ignored. Existing events and instances are never
modified, so salesSnapshots and hand edits are left alone.

Usage:
  python sync_events.py            # add new events/instances to the config
  python sync_events.py --dry-run  # show what would be added without writing
"""

import argparse
import json
from datetime import date

from snapshot import make_spektrix_request

MAIN_CONFIG_FILE = "events-config-dashboard.json"
EVENTS_PATH      = "/api/v3/events?$expand=instances"


def season_of(inst: dict, event: dict) -> str:
    """Same resolution app.py uses: instance-level season, else the event's."""
    return inst.get("attribute_Season") or event.get("attribute_Season", "")


def upcoming_instances(event: dict, today: str) -> list[dict]:
    return sorted(
        (i for i in event.get("instances", [])
         if i.get("id") and i.get("start", "")[:10] >= today and not i.get("cancelled")),
        key=lambda i: i["start"],
    )


def describe(inst: dict, event: dict, plan_configs: dict, seasons: dict, existing: bool = False) -> str:
    notes   = []
    plan_id = inst.get("planId")
    season  = season_of(inst, event)
    if not plan_id:
        notes.append("no planId — app will skip it")
    elif plan_id not in plan_configs:
        notes.append(f"plan {plan_id} not in planConfigs — single-area totals only")
    if season not in seasons:
        notes.append(f"season {season!r} not in seasons block")
    elif existing and not inst.get("attribute_Season"):
        notes.append("no instance-level Season — inheriting the event's")
    suffix = f"  ⚠  {'; '.join(notes)}" if notes else ""
    return f"  + {inst['start']}  [{season or 'no season'}]{suffix}"


def main():
    parser = argparse.ArgumentParser(description="Add new Spektrix events/instances to the dashboard config.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be added without writing to the JSON")
    args = parser.parse_args()

    with open(MAIN_CONFIG_FILE, encoding="utf-8") as f:
        main_config = json.load(f)

    today          = date.today().isoformat()
    seasons        = main_config.get("seasons", {})
    plan_configs   = main_config.get("planConfigs", {})
    config_events  = {e["id"]: e for e in main_config["events"]}
    known_inst_ids = {
        i["id"] for e in main_config["events"] for i in e.get("instances", []) if i.get("id")
    }

    print(f"Fetching {EVENTS_PATH} ...")
    api_events = make_spektrix_request(EVENTS_PATH, timeout=60)

    new_events, new_inst_count, skipped = [], 0, []

    for api_event in api_events:
        upcoming = [i for i in upcoming_instances(api_event, today) if i["id"] not in known_inst_ids]
        if not upcoming:
            continue

        existing = config_events.get(api_event["id"])
        if existing:
            print(f"\n{existing['name']}  (existing event — {len(upcoming)} new instance(s))")
            for inst in upcoming:
                print(describe(inst, existing, plan_configs, seasons, existing=True))
            existing.setdefault("instances", []).extend(upcoming)
            new_inst_count += len(upcoming)
            continue

        if not any(season_of(i, api_event) in seasons for i in upcoming):
            skipped.append(api_event)
            continue

        print(f"\n{api_event['name']}  (NEW event — {len(upcoming)} instance(s))")
        for inst in upcoming:
            print(describe(inst, api_event, plan_configs, seasons))
        new_events.append({**api_event, "instances": upcoming})
        new_inst_count += len(upcoming)

    main_config["events"].extend(new_events)

    if skipped:
        print(f"\nSkipped {len(skipped)} new event(s) with no season from the seasons block:")
        for e in skipped:
            print(f"  - {e['name']}  (season {e.get('attribute_Season', '')!r}, first {e.get('firstInstanceDateTime', '')[:10]})")

    print(f"\n{'─' * 50}")
    print(f"{len(new_events)} new event(s), {new_inst_count} new instance(s) total.")

    if args.dry_run:
        print(f"Dry run — {MAIN_CONFIG_FILE} not written.")
    elif new_inst_count:
        with open(MAIN_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(main_config, f, indent=2)
        print(f"Wrote {MAIN_CONFIG_FILE}. Review with `git diff`, then commit to deploy.")
    else:
        print("Nothing to add.")


if __name__ == "__main__":
    main()
