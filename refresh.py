"""
refresh.py - one-shot "snapshot": re-fetch the pages that can still change, rebuild the site.

Which tournament IDs get fetched:
    - every ID in upcoming.json (their schedules appear / fill in over the week)
    - every event your team has already played that still has unscored games
    - anything passed with --ids

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


def ids_to_refresh(extra, team, division, lookback_days):
    ids = set(extra)
    up = HERE / "upcoming.json"
    if up.exists():
        ids.update(int(u["id"]) for u in json.loads(up.read_text(encoding="utf-8")))

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
    ap.add_argument("--lookback", type=int, default=3, help="also refresh events that started within N days")
    ap.add_argument("--no-fetch", action="store_true", help="only rebuild the site from saved pages")
    args = ap.parse_args()

    py = sys.executable
    if not args.no_fetch:
        ids = ids_to_refresh(args.ids, args.team, args.division, args.lookback)
        print("refreshing:", " ".join(map(str, ids)) or "(nothing)")
        if ids:
            r = subprocess.run([py, str(HERE / "topgun_fetch.py"), "--team", args.team, "--ids", *map(str, ids)], cwd=HERE)
            if r.returncode:
                print("fetch had errors; rebuilding from whatever was saved")
    r = subprocess.run([py, str(HERE / "build_site.py"), "--team", args.team, "--division", args.division], cwd=HERE)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
