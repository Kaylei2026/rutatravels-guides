#!/usr/bin/env python3
"""Re-route every guide leg with real Google Directions and tag it walk vs drive.

Run locally where GOOGLE_MAPS_API_KEY is set and the network is open:
    GOOGLE_MAPS_API_KEY=AIza... python3 fix-guides.py            # dry run, reports only
    GOOGLE_MAPS_API_KEY=AIza... python3 fix-guides.py --write     # rewrites files

For each unique leg (deduped by rounded coordinates, so ~740 API calls, not 6,642):
  - try WALKING; if Google returns a walking route under WALK_MAX_M, mode = walk
  - otherwise DRIVING, mode = drive
Then every language variant of that leg gets: transport_mode, real distance_meters,
duration_minutes, and walking_minutes (null for drives). Day/trip walking totals are
recomputed from WALK legs only. Non-walk legs no longer masquerade as walks.
"""
import json, glob, math, os, sys, time, urllib.parse, urllib.request

API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")
WRITE = "--write" in sys.argv
SLOTS = ["morning", "afternoon", "evening"]
WALK_MAX_M = 8000          # a routed walk longer than this -> treat as a drive
ROUND = 5                  # coord decimals for dedupe (~1 m)
# Genuine long walking trails that legitimately exceed WALK_MAX_M (keyed from/to, rounded).
# The Fira -> Oia caldera hike (~9 km) is a signature walk, not a drive.
FORCE_WALK = {(36.42, 25.4317, 36.4618, 25.3753)}

if not API_KEY:
    sys.exit("set GOOGLE_MAPS_API_KEY in the environment")

_last = {"status": None}
def directions(o, d, mode):
    # Distance Matrix API (same one the backend uses for drive times), so the existing
    # server key already has it enabled. Returns (distance_m, duration_min) or None.
    url = "https://maps.googleapis.com/maps/api/distancematrix/json?" + urllib.parse.urlencode({
        "origins": f"{o['lat']},{o['lon']}", "destinations": f"{d['lat']},{d['lon']}",
        "mode": mode, "key": API_KEY})
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.load(r)
    except Exception as e:
        _last["status"] = f"network error: {e}"
        return None
    _last["status"] = data.get("status")
    try:
        el = data["rows"][0]["elements"][0]
    except (KeyError, IndexError, TypeError):
        return None
    if el.get("status") != "OK":
        _last["status"] = f"{data.get('status')}/{el.get('status')}"
        return None
    return el["distance"]["value"], int(round(el["duration"]["value"] / 60.0))

def key(c1, c2):
    return (round(c1["lat"], ROUND), round(c1["lon"], ROUND),
            round(c2["lat"], ROUND), round(c2["lon"], ROUND))

# 1) collect unique legs by coordinate pair
files = sorted(glob.glob("*.json"))
unique = {}
for path in files:
    d = json.load(open(path, encoding="utf-8"))
    for day in d.get("days", []):
        acts = [day[s] for s in SLOTS if isinstance(day.get(s), dict)]
        for i in range(len(acts) - 1):
            c1, c2 = acts[i].get("coordinates"), acts[i+1].get("coordinates")
            if c1 and c2 and all(k in c1 for k in ("lat","lon")) and all(k in c2 for k in ("lat","lon")):
                unique.setdefault(key(c1, c2), (c1, c2))

print(f"{len(files)} files, {len(unique)} unique legs to route")

# 2) route each unique leg once
routed, walk_n, drive_n, fail_n = {}, 0, 0, 0
for i, (k, (c1, c2)) in enumerate(unique.items(), 1):
    res = directions(c1, c2, "walking")
    if res and (res[0] <= WALK_MAX_M or k in FORCE_WALK):
        routed[k] = ("walk", res[0], res[1]); walk_n += 1
    else:
        dr = directions(c1, c2, "driving")
        if dr:
            routed[k] = ("drive", dr[0], dr[1]); drive_n += 1
        elif res:
            routed[k] = ("walk", res[0], res[1]); walk_n += 1
        else:
            fail_n += 1
    if i == 1:
        print(f"  first leg -> Google status: {_last['status']}", flush=True)
    if i % 50 == 0:
        print(f"  {i}/{len(unique)} routed  (walk {walk_n}, drive {drive_n}, fail {fail_n})", flush=True)
    time.sleep(0.02)

print(f"routed: {walk_n} walk, {drive_n} drive, {fail_n} unroutable")
if not WRITE:
    print("\nDRY RUN — rerun with --write to apply."); sys.exit(0)

if fail_n > len(unique) * 0.05:
    sys.exit(f"\nABORTING WRITE: {fail_n}/{len(unique)} legs unroutable. Fix the API key first "
             "(use your real key, enable the Directions API on it, and make sure it is NOT "
             "HTTP-referrer-restricted). No files were changed.")

# 3) rewrite every file
changed = 0
for path in files:
    d = json.load(open(path, encoding="utf-8"))
    trip = 0
    for day in d.get("days", []):
        acts = [day[s] for s in SLOTS if isinstance(day.get(s), dict)]
        day_walk = 0
        for i in range(len(acts)):
            a = acts[i]
            if i == len(acts) - 1:
                continue
            leg = a.get("walk_to_next")
            if not isinstance(leg, dict):
                continue
            c1, c2 = a.get("coordinates"), acts[i+1].get("coordinates")
            r = routed.get(key(c1, c2)) if c1 and c2 else None
            if not r:
                continue
            mode, meters, minutes = r
            leg["transport_mode"] = mode
            leg["distance_meters"] = meters
            leg["duration_minutes"] = minutes
            leg["walking_minutes"] = minutes if mode == "walk" else None
            if mode == "walk":
                day_walk += meters
        # recompute walking-only totals from the fixed legs
        if "day_walking_distance_meters" in day:
            day["day_walking_distance_meters"] = day_walk
        trip += day_walk
    if "total_walking_distance_meters" in d:
        d["total_walking_distance_meters"] = trip
    json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    changed += 1

print(f"rewrote {changed} files. Run validate-guides.py to confirm.")
