"""
refresh.py - one-shot "snapshot": re-fetch the pages that can still change, rebuild the site.

Which tournament IDs get fetched:
    - every ID in upcoming.json (their schedules appear / fill in over the week)
    - every event your team has already played that still has unscored games
    - anything passed with --ids
    - every tracked event (data/tracked.json) starting within the last --lookback days or the
      next --ahead days, so scores keep updating through the weekend until they are final

--discover sweeps Top Gun's tournament list (which only shows current and upcoming events)
for the Charlotte-area cities and adds what it finds to data/tracked.json, so other 9U events
land in the Standings table without anyone typing IDs.

Then build_site.py regenerates docs/data.js. This is what the GitHub Actions workflow runs
on its schedule; run it by hand any time with:  python refresh.py
"""

import argparse
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
DISCOVER_CITIES = ["Charlotte", "Tega Cay", "Monroe", "Mocksville", "Statesville"]


TRACKED = HERE / "data" / "tracked.json"


def load_tracked():
    return json.loads(TRACKED.read_text(encoding="utf-8")) if TRACKED.exists() else {}


def discover(py, team, days_ahead, cities):
    """Sweep the list page and merge what it finds into data/tracked.json."""
    d_from = (date.today() - timedelta(days=1)).isoformat()
    d_to = (date.today() + timedelta(days=days_ahead)).isoformat()
    print(f"discovering {d_from}..{d_to} in {', '.join(cities)}")
    cmd = [py, str(HERE / "topgun_fetch.py"), "--team", team, "--from", d_from, "--to", d_to]
    for c in cities:
        cmd += ["--city", c]
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode:
        print("discovery had errors; continuing")
    found_path = HERE / "data" / "tournaments.json"
    if not found_path.exists():
        return set()
    tracked = load_tracked()
    new, fetched = 0, set()
    for t in json.loads(found_path.read_text(encoding="utf-8")):
        tid = str(t["TournamentID"])
        if tid not in tracked:
            new += 1
        tracked[tid] = {"name": t["TournamentName"].strip(), "start": t["StartDate"][:10], "city": t["CityState"]}
        fetched.add(int(tid))
    TRACKED.write_text(json.dumps(dict(sorted(tracked.items())), indent=1), encoding="utf-8")
    print(f"tracked: {len(tracked)} events ({new} new)")
    return fetched  # already on disk from this run; no need to fetch again


def ids_to_refresh(extra, team, division, lookback_days, ahead_days):
    ids = set(extra)
    up = HERE / "upcoming.json"
    if up.exists():
        ids.update(int(u["id"]) for u in json.loads(up.read_text(encoding="utf-8")))

    lo = (date.today() - timedelta(days=lookback_days)).isoformat()
    hi = (date.today() + timedelta(days=ahead_days)).isoformat()
    for tid, t in load_tracked().items():
        if lo <= t.get("start", "") <= hi:
            ids.add(int(tid))

    site = HERE / "docs" / "data.json"
    if site.exists():
        d = json.loads(site.read_text(encoding="utf-8"))
        recent = (date.today() - timedelta(days=lookback_days)).isoformat()
        for e in d.get("my_events", []):
            unfinished = any(g["result"] is None for g in d["my_games"] if g["event_id"] == e["id"])
            if unfinished or (e.get("date") or "") >= recent:
                ids.add(e["id"])
    return sorted(ids)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="*", type=int, default=[])
    ap.add_argument("--team", default="Elite 9")
    ap.add_argument("--division", default="9U")
    ap.add_argument("--lookback", type=int, default=3, help="refresh events that started within the last N days")
    ap.add_argument("--ahead", type=int, default=7, help="refresh tracked events starting within the next N days")
    ap.add_argument("--no-fetch", action="store_true", help="only rebuild the site from saved pages")
    ap.add_argument("--discover", action="store_true", help="sweep the tournament list for upcoming area events first")
    ap.add_argument("--discover-days", type=int, default=21, help="how far ahead discovery looks")
    ap.add_argument("--city", action="append", default=[], help="discovery city filter (default: Charlotte area)")
    args = ap.parse_args()

    py = sys.executable
    done = set()
    if args.discover and not args.no_fetch:
        done = discover(py, args.team, args.discover_days, args.city or DISCOVER_CITIES)
    if not args.no_fetch:
        ids = [i for i in ids_to_refresh(args.ids, args.team, args.division, args.lookback, args.ahead) if i not in done]
        print("refreshing:", " ".join(map(str, ids)) or "(nothing)")
        if ids:
            r = subprocess.run([py, str(HERE / "topgun_fetch.py"), "--team", args.team, "--ids", *map(str, ids)], cwd=HERE)
            if r.returncode:
                print("fetch had errors; rebuilding from whatever was saved")
    r = subprocess.run([py, str(HERE / "build_site.py"), "--team", args.team, "--division", args.division], cwd=HERE)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
