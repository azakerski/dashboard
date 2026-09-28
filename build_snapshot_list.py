"""
Build eventsToSnapshot.json
===========================
Scans events-config-dashboard.json for instances that are ready to be
snapshotted and writes them into eventsToSnapshot.json, which is the
target list snapshot.py reads from.

An instance is included when:
  - it has no salesSnapshot yet
  - its start date is before today (venue-local date)
  - it has a planId and is not cancelled

Selection is per instance: an event with some instances already
snapshotted (or still upcoming) only carries over the instances that
qualify. The planConfigs for any multi-area plans used by those instances
are copied over too — plans not in planConfigs fall back to snapshot.py's
single-area (instance-level) status call.

Usage:
  python build_snapshot_list.py            # overwrite eventsToSnapshot.json
  python build_snapshot_list.py --dry-run  # show what would be included
"""

import argparse
import json
from datetime import date

MAIN_CONFIG_FILE = "events-config-dashboard.json"
OUTPUT_FILE      = "eventsToSnapshot.json"


def main():
    parser = argparse.ArgumentParser(description="Build eventsToSnapshot.json from past, un-snapshotted instances.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be included without writing the file")
    args = parser.parse_args()

    with open(MAIN_CONFIG_FILE, encoding="utf-8") as f:
        main_config = json.load(f)

    today        = date.today().isoformat()
    plan_configs = main_config.get("planConfigs", {})
    used_plans   = set()
    events_out   = []
    total        = 0

    for event in main_config["events"]:
        instances = []
        for inst in event.get("instances", []):
            start = inst.get("start", "")
            if inst.get("salesSnapshot") or not start or start[:10] >= today:
                continue
            if inst.get("cancelled"):
                continue
            if not inst.get("id") or not inst.get("planId"):
                print(f"⚠  {event['name']} — {start}: missing id/planId, skipping")
                continue
            instances.append(inst)

        if not instances:
            continue

        print(f"\n{event['name']}")
        for inst in instances:
            plan_id = inst["planId"]
            mode    = "multi-area" if plan_id in plan_configs else "single-area"
            print(f"  {inst['start']}  plan {plan_id} ({mode})")
            if plan_id in plan_configs:
                used_plans.add(plan_id)

        events_out.append({**event, "instances": instances})
        total += len(instances)

    output = {
        "planConfigs": {pid: plan_configs[pid] for pid in plan_configs if pid in used_plans},
        "events":      events_out,
    }

    print(f"\n{'─' * 50}")
    print(f"{total} instance(s) across {len(events_out)} event(s); {len(used_plans)} plan config(s) included.")

    if args.dry_run:
        print(f"Dry run — {OUTPUT_FILE} not written.")
        return

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"Wrote {OUTPUT_FILE}. Next: python snapshot.py --dry-run, then python snapshot.py")


if __name__ == "__main__":
    main()
