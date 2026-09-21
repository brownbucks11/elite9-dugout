"""
gameday.py - print "yes" if one of our tournaments is happening today, else "no".

Used by the GitHub Actions workflow to decide whether a 10-minute tick should fetch. Reads the
committed docs/data.json (no browser, no network), so it takes a second. A tournament counts as
live from its start date through the last scheduled day, including bracket Sunday. Uses the
local date (the workflow sets TZ=America/New_York).

    python gameday.py            # yes / no
    python gameday.py --date 2026-09-27
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path


def live_events(site, today):
    out = []
    for t in site.get("tournaments", []):
        games = t.get("games") or []
        start = t.get("date") or ""
        if not start or not games:
            continue
        end = max([g.get("date") or start for g in games] or [start])
        if start <= today <= end and t.get("mine"):
            out.append(t)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--site", default="docs/data.json")
    args = ap.parse_args()
    path = Path(args.site)
    if not path.exists():
        print("no")
        return 0
    live = live_events(json.loads(path.read_text(encoding="utf-8")), args.date)
    print("yes" if live else "no")
    for t in live:
        print(f"  live: {t['id']} {t['name'][:50]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
