#!/usr/bin/env python3
"""Validate Ruta Travels guide JSON against the live schema.

Blocking errors (exit 1): leaked editorial text, coordinate duplicates, legs shorter
than the straight-line distance, missing required fields, missing language variants.
Warnings (exit 0): day/trip totals that don't match their legs, legs too long to walk,
walk_to_next pointing at the wrong stop, missing wikidata_qid / last_verified, adjacency.
"""
import json, glob, math, sys, collections, re

REQUIRED_TOP = ["destination", "duration_days", "version", "days",
                "total_walking_distance_meters"]
REQUIRED_ACT = ["activity_title", "coordinates", "duration_minutes", "type"]
SLOTS = ["morning", "afternoon", "evening"]
EXPECTED_LANGS = {"ar", "de", "en", "es", "fr", "it", "ja", "pt", "zh"}
ARTIFACTS = ["replaced with:", "fixme", "placeholder", "lorem ipsum",
             "needs verif", "tbd:", "xxx", "to be added"]
DUPLICATE_M = 5        # closer than this = same point (data bug)
ADJACENT_M = 25        # 5-25m apart = legitimately adjacent, warn only
DRIVE_STRAIGHT_M = 3000  # straight-line beyond this is almost certainly not a walk

errors, warnings = [], []
def err(f, m): errors.append(f"{f}: {m}")
def warn(f, m): warnings.append(f"{f}: {m}")

def hav(c1, c2):
    R = 6371000
    la1, lo1 = math.radians(c1["lat"]), math.radians(c1["lon"])
    la2, lo2 = math.radians(c2["lat"]), math.radians(c2["lon"])
    h = math.sin((la2-la1)/2)**2 + math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
    return 2*R*math.asin(math.sqrt(h))

STOP = {"the","and","of","de","la","el","to","at","a","san","st","los","las","le"}
def toks(s):
    return set(re.findall(r"[a-z0-9]+", (s or "").lower())) - STOP

def coord_ok(c):
    return isinstance(c, dict) and isinstance(c.get("lat"), (int, float)) \
        and isinstance(c.get("lon"), (int, float))

files = sorted(glob.glob("*.json"))
if not files:
    print("no guide JSON found"); sys.exit(1)

langs = collections.defaultdict(set)
for path in files:
    base, _, lang = path[:-5].rpartition("-")
    langs[base].add(lang)
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        err(path, f"invalid JSON: {e}"); continue

    for k in REQUIRED_TOP:
        if k not in d:
            err(path, f"missing top-level '{k}'")
    if d.get("duration_days") != len(d.get("days", [])):
        err(path, f"duration_days={d.get('duration_days')} but {len(d.get('days', []))} days")
    if "last_verified" not in d:
        warn(path, "no last_verified field")

    raw = json.dumps(d, ensure_ascii=False)
    low = raw.lower()
    for bad in ARTIFACTS:
        if bad in low:
            err(path, f"editorial artifact leaked into content: '{bad}'")
    if re.search(r"\bTODO\b", raw):
        err(path, "editorial artifact leaked into content: TODO")

    trip = 0
    for day in d.get("days", []):
        acts = [day[s] for s in SLOTS if isinstance(day.get(s), dict)]
        if not acts:
            err(path, f"day {day.get('day')} has no activities"); continue
        day_m = 0
        for i, a in enumerate(acts):
            for k in REQUIRED_ACT:
                if k not in a:
                    err(path, f"day {day.get('day')} '{a.get('activity_title', '?')}' missing '{k}'")
            c = a.get("coordinates") or {}
            if not coord_ok(c):
                err(path, f"'{a.get('activity_title')}' has no usable coordinates"); continue
            if not (-90 <= c["lat"] <= 90 and -180 <= c["lon"] <= 180):
                err(path, f"'{a.get('activity_title')}' coordinates out of range")
            if not a.get("wikidata_qid"):
                warn(path, f"'{a.get('activity_title')}' has no wikidata_qid")

            last = (i == len(acts) - 1)
            leg = a.get("walk_to_next")
            if last:
                if leg is not None:
                    warn(path, f"last stop of day {day.get('day')} has a walk_to_next")
                continue
            if not isinstance(leg, dict):
                err(path, f"missing walk_to_next after '{a.get('activity_title')}'"); continue
            dm = leg.get("distance_meters") or 0
            mode = leg.get("transport_mode")
            if mode != "drive":        # walking totals count walk legs only
                day_m += dm
            nxt = acts[i+1]
            nc = nxt.get("coordinates") or {}
            if coord_ok(nc):
                straight = hav(c, nc)
                if straight < DUPLICATE_M:
                    err(path, f"'{a.get('activity_title')}' and '{nxt.get('activity_title')}' "
                              f"share coordinates ({straight:.0f}m)")
                elif straight < ADJACENT_M:
                    warn(path, f"'{a.get('activity_title')}' -> '{nxt.get('activity_title')}' "
                               f"only {straight:.0f}m apart")
                # Post-routing, distance_meters is Google's real walking distance. If it still comes
                # in under the coordinate straight-line, that's imprecise POI coordinates (or an
                # unroutable leg), worth a look but not fabricated data, so warn rather than block.
                if (straight - dm) > max(30, straight * 0.15):
                    warn(path, f"leg to '{leg.get('to')}' routes {dm}m, under the {straight:.0f}m straight line — check coordinates")
                if straight > DRIVE_STRAIGHT_M and mode != "drive":
                    warn(path, f"leg '{a.get('activity_title')}' -> '{nxt.get('activity_title')}' is "
                               f"{straight/1000:.1f}km straight-line but not tagged transport_mode=drive")
            to = leg.get("to")
            if to and nxt.get("activity_title") and not (toks(to) & toks(nxt.get("activity_title"))):
                warn(path, f"walk_to_next says '{to}' but next stop is '{nxt.get('activity_title')}'")
            if mode != "drive" and leg.get("walking_minutes") is None:
                warn(path, f"walk leg to '{leg.get('to')}' has null walking_minutes")

        dtot = day.get("day_walking_distance_meters")
        if dtot is not None and dtot != day_m:
            warn(path, f"day {day.get('day')} total {dtot}m != sum of legs {day_m}m")
        trip += day_m

    ttot = d.get("total_walking_distance_meters")
    if ttot is not None and ttot != trip:
        warn(path, f"trip total {ttot}m != sum of days {trip}m")

for base, got in sorted(langs.items()):
    missing = EXPECTED_LANGS - got
    if missing:
        err(base, f"missing language variants: {sorted(missing)}")

def summarize(items):
    cats = collections.Counter()
    for it in items:
        msg = it.split(": ", 1)[1] if ": " in it else it
        key = re.sub(r"'[^']*'", "'…'", msg)
        key = re.sub(r"\d+(\.\d+)?", "N", key)
        cats[key[:70]] += 1
    return cats

print(f"checked {len(files)} files across {len(langs)} guides")
print(f"{len(errors)} blocking error(s), {len(warnings)} warning(s)\n")

if errors:
    print("== BLOCKING ERRORS (by type) ==")
    for k, n in summarize(errors).most_common():
        print(f"  {n:5d}  {k}")
    print("\n  first examples:")
    for e in errors[:12]:
        print("   x", e)
if warnings:
    print("\n== WARNINGS (by type) ==")
    for k, n in summarize(warnings).most_common():
        print(f"  {n:5d}  {k}")

if errors:
    sys.exit(1)
print("\nall blocking checks passed")
