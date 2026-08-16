#!/usr/bin/env python3
"""Ruta Travels guide normalizer -> schema v1.4. Run from inside the repo."""
import json, glob, math, collections

# How far it is reasonable to tell someone to walk, by city character.
WALKABLE = {  # 2500 m
 "amsterdam","barcelona","berlin","copenhagen","florence","kyoto","london","munich",
 "new-york","osaka","paris","prague","rome","san-sebastian","tokyo","vienna","chicago",
 "charleston","savannah","new-orleans","dubrovnik","hallstatt","cinque-terre","hiroshima",
 "marrakech","carmel-by-the-sea","sydney",
}
MODERATE = {  # 1800 m - heat, hills, or a spread-out core
 "athens","bangkok","istanbul","lisbon","mexico-city","rio-de-janeiro","singapore",
 "santorini","amalfi-coast","lake-como","interlaken","martha-s-vineyard","bar-harbor",
 "queenstown","santa-barbara",
}
# everything else -> 1200 m (car-first cities, resort and outdoor destinations)

# Cities where rail genuinely covers a 2.5-8 km hop. los-angeles is deliberately
# absent: Metro exists but does not connect the stops these guides pair.
RAIL = {
 "amsterdam","athens","bangkok","barcelona","berlin","chicago","copenhagen","istanbul",
 "kyoto","lisbon","london","mexico-city","munich","new-york","osaka","paris","prague",
 "rio-de-janeiro","rome","singapore","sydney","tokyo","vienna","new-orleans","hiroshima",
 "charlotte","cinque-terre","hallstatt","interlaken","lake-como",
}
SLOTS = ["morning","afternoon","evening"]

def walk_cap(city):
    return 2500 if city in WALKABLE else 1800 if city in MODERATE else 1200

def haversine(c1, c2):
    R = 6371000
    la1, lo1 = math.radians(c1["lat"]), math.radians(c1["lon"])
    la2, lo2 = math.radians(c2["lat"]), math.radians(c2["lon"])
    h = math.sin((la2-la1)/2)**2 + math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
    return 2*R*math.asin(math.sqrt(h))

def route_factor(m):
    if m < 2000:  return 1.30
    if m < 10000: return 1.35
    if m < 50000: return 1.40
    return 1.50

def build_leg(city, a, b):
    straight = haversine(a["coordinates"], b["coordinates"])
    m = int(round(straight * route_factor(straight) / 10.0)) * 10
    if m <= walk_cap(city):
        mode, mins = "walk", max(1, round(m/80))
    elif m <= 8000 and city in RAIL:
        mode, mins = "transit", max(8, round(m/500) + 8)
    else:
        spd = 45000/60 if m < 20000 else 65000/60 if m < 80000 else 80000/60
        mode, mins = "drive", max(5, round(m/spd) + 5)
    return {"to": b["activity_title"], "transport_mode": mode,
            "distance_meters": m, "travel_minutes": mins,
            "walking_minutes": mins if mode == "walk" else None}

# Charlotte 1-day: the Levine Museum has no premises. It sold its 7th Street
# building in 2022, closed its interim space in May 2025, and its new South End
# campus has not been built. Promote the Gantt Center to the morning anchor.
CHARLOTTE_DESC_EN = ("One day in Charlotte's Uptown core, covering the Levine Center for the Arts "
    "museum campus, a Victorian-era neighborhood pocket behind the glass towers, and the park "
    "that threads them together. The cluster is walkable end-to-end and mixes indoor depth with "
    "outdoor orientation.")

def rebuild_charlotte(doc, lang):
    day = doc["days"][0]
    if "Levine Museum" not in json.dumps(day):
        return False
    bonus = {x["activity_title"]: x for x in day.get("if_time_permits", [])}
    gantt = next((v for k, v in bonus.items() if "Gantt" in k), None)
    mint  = next((v for k, v in bonus.items() if "Mint"  in k), None)
    if not gantt:
        return False
    slot = {k: v for k, v in gantt.items() if k not in ("neighborhood","walk_from_main_route")}
    slot["unesco_designation"] = None
    slot["walk_to_next"] = None
    slot["duration_minutes"] = 75
    slot["optimal_timing"] = "morning"
    day["morning"] = slot
    if mint:
        mint["walk_from_main_route"] = {"from": day["afternoon"]["activity_title"],
                                        "distance_meters": 80, "walking_minutes": 1}
        day["if_time_permits"] = [mint]
    else:
        day["if_time_permits"] = []
    if lang == "en":
        doc["guide_description"] = CHARLOTTE_DESC_EN
    else:
        doc["needs_retranslation"] = ["guide_description"]
    return True

def main():
    st = collections.Counter()
    for path in sorted(glob.glob("*.json")):
        stem = path[:-5]
        lang = stem.rsplit("-", 1)[1]
        city = stem.rsplit("-", 1)[0].rsplit("-", 1)[0]
        doc = json.load(open(path))

        for day in doc.get("days", []):
            for s in SLOTS:
                a = day.get(s)
                if isinstance(a, dict) and "replaced with:" in (a.get("activity_title") or ""):
                    a["activity_title"] = a["activity_title"].split("replaced with:", 1)[1].strip()
                    st["leaked_title_fixed"] += 1

        if city == "charlotte" and doc.get("duration_days") == 1:
            if rebuild_charlotte(doc, lang):
                st["charlotte_rebuilt"] += 1

        trip_m = trip_walk_min = trip_travel_min = 0
        for day in doc.get("days", []):
            acts = [day[s] for s in SLOTS if isinstance(day.get(s), dict)]
            d_m = d_walk = d_travel = 0
            for i, a in enumerate(acts):
                if i == len(acts) - 1:
                    a["walk_to_next"] = None
                    continue
                leg = build_leg(city, a, acts[i+1])
                a["walk_to_next"] = leg
                st["mode_" + leg["transport_mode"]] += 1
                if leg["transport_mode"] == "walk":
                    d_m += leg["distance_meters"]; d_walk += leg["travel_minutes"]
                d_travel += leg["travel_minutes"]
            day["day_walking_distance_meters"] = d_m
            day["day_walking_minutes"] = d_walk
            day["day_travel_minutes"] = d_travel
            trip_m += d_m; trip_walk_min += d_walk; trip_travel_min += d_travel

        doc["total_walking_distance_meters"] = trip_m
        doc["total_walking_minutes"] = trip_walk_min
        doc["total_travel_minutes"] = trip_travel_min
        doc.setdefault("last_verified", None)
        doc["version"] = "1.4"
        json.dump(doc, open(path, "w"), ensure_ascii=False, indent=2)
        st["files"] += 1

    for k in sorted(st):
        print(f"{st[k]:>6}  {k}")

if __name__ == "__main__":
    main()
