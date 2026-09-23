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
land in the Standings table without anyone typing IDs. A schedule page covers every age group,
so the first fetch of an event records its divisions; events with no 9U bracket (or another
age in their name, like "...SERIES 10U") are marked skip and never fetched again.

Then build_site.py regenerates docs/data.js. This is what the GitHub Actions workflow runs
on its schedule; run it by hand any time with:  python refresh.py
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
DISCOVER_CITIES = ["Charlotte", "Tega Cay", "Monroe", "Mocksville", "Statesville"]


TRACKED = HERE / "data" / "tracked.json"


def load_tracked():
    return json.loads(TRACKED.read_text(encoding="utf-8")) if TRACKED.exists() else {}


def save_tracked(tracked):
    TRACKED.write_text(json.dumps(dict(sorted(tracked.items())), indent=1), encoding="utf-8")


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def other_age_in_name(name, division):
    """'FRIDAY NIGHT G.O.A.T. SERIES 10U' -> True for 9U; names with no age tag -> False."""
    tags = {norm(t) for t in re.findall(r"\b\d{1,2}U\b", name or "", re.I)}
    return bool(tags) and norm(division) not in tags


def discover(py, team, division, days_ahead, cities):
    """Sweep the list page (no schedule fetches) and merge what it finds into data/tracked.json."""
    d_from = (date.today() - timedelta(days=1)).isoformat()
    d_to = (date.today() + timedelta(days=days_ahead)).isoformat()
    print(f"discovering {d_from}..{d_to} in {', '.join(cities)}")
    cmd = [py, str(HERE / "topgun_fetch.py"), "--team", team, "--from", d_from, "--to", d_to, "--list-only"]
    for c in cities:
        cmd += ["--city", c]
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode:
        print("discovery had errors; continuing")
    found_path = HERE / "data" / "tournaments.json"
    if not found_path.exists():
        return
    tracked = load_tracked()
    new = 0
    for t in json.loads(found_path.read_text(encoding="utf-8")):
        tid = str(t["TournamentID"])
        name = t["TournamentName"].strip()
        if tid not in tracked:
            new += 1
            tracked[tid] = {"divisions": None, "skip": other_age_in_name(name, division)}
        tracked[tid].update({"name": name, "start": t["StartDate"][:10], "city": t["CityState"]})
    save_tracked(tracked)
    skipped = sum(1 for t in tracked.values() if t.get("skip"))
    print(f"tracked: {len(tracked)} events ({new} new, {skipped} skipped as not {division})")


def record_divisions(ids, division):
    """After a fetch, note each page's divisions; pages with brackets but no `division` get skipped."""
    tracked = load_tracked()
    changed = False
    for tid in ids:
        jp = HERE / "data" / f"{tid}.json"
        if not jp.exists():
            continue
        t = tracked.get(str(tid))
        if t is None:
            continue  # your own events from upcoming.json are always fetched; only tracked ones get pruned
        divs = list(json.loads(jp.read_text(encoding="utf-8")).get("divisions", {}))
        t["divisions"] = divs
        t["skip"] = bool(divs) and not any(norm(d) == norm(division) for d in divs)
        changed = True
        if t["skip"]:
            print(f"  {tid}: no {division} bracket ({', '.join(divs)}); won't fetch again")
    if changed:
        save_tracked(tracked)


def ids_to_refresh(extra, team, division, lookback_days, ahead_days):
    ids = set(extra)
    up = HERE / "upcoming.json"
    if up.exists():
        ids.update(int(u["id"]) for u in json.loads(up.read_text(encoding="utf-8")))

    lo = (date.today() - timedelta(days=lookback_days)).isoformat()
    hi = (date.today() + timedelta(days=ahead_days)).isoformat()
    for tid, t in load_tracked().items():
        if not t.get("skip") and lo <= t.get("start", "") <= hi:
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


ENTRIES_URL = "https://topgunstats.com/api/public/tournaments/{id}/whos-playing?api_key=secret"


LIST_URL = "https://topgunstats.com/api/queries/tournaments/tournament-data?sportId=1&page={page}&limit=50&api_key=secret"
TGS_FIELDS = ("TournamentID", "TournamentName", "StartDate", "EndDate", "CityState", "GameGuarantee", "TotalConfirmed",
              "ShowGamesScheduled", "ShowWeatherMessage", "WeatherMessage", "WeatherMessageDateTime",
              "ShowGeneralMessage", "GeneralMessage", "GeneralMessageDateTime", "TComplexArr", "TFeeObj",
              "FullName", "CellPhone", "Email", "ShowGameTimesResults")


def fetch_tgs(ids):
    """topgunstats.com tournament list: the director's weather/general messages, complexes and fees
    for our events. Plain JSON, a few pages, no browser. Past events drop off the list, which is fine."""
    import urllib.request
    want = set(ids)
    out_dir = HERE / "data" / "tgs"
    out_dir.mkdir(parents=True, exist_ok=True)
    found = 0
    for page in range(1, 12):
        try:
            with urllib.request.urlopen(urllib.request.Request(LIST_URL.format(page=page), headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as r:
                d = json.loads(r.read())
        except Exception as exc:
            print(f"  tgs page {page}: {exc}")
            break
        for t in d.get("tournaments", []):
            if t.get("TournamentID") in want:
                rec = {k: t.get(k) for k in TGS_FIELDS}
                rec["fetched_at"] = datetime.now().isoformat(timespec="minutes")
                (out_dir / f"{t['TournamentID']}.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
                found += 1
        if page >= int(d.get("totalPages") or 1):
            break
    print(f"tgs: {found}/{len(want)} events found on topgunstats.com")


def fetch_entries(ids, team="Elite 9"):
    """topgunstats.com 'Who's Playing': keep only per-division counts and OUR entry status
    (entered / confirmed / waitlist). Other teams' names are not stored - directors hide that list."""
    import urllib.request
    out_dir = HERE / "data" / "entries"
    out_dir.mkdir(parents=True, exist_ok=True)
    got, ours = 0, []
    for tid in ids:
        try:
            with urllib.request.urlopen(urllib.request.Request(ENTRIES_URL.format(id=tid), headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as r:
                data = json.loads(r.read())
        except Exception as exc:
            print(f"  entries {tid}: {exc}")
            continue
        if data.get("AgeGroups") is None:
            continue
        rec = {"tournament": data.get("TournamentName"), "start": data.get("StartDate"), "hidden": bool(data.get("IsHiddenWhosPlaying")),
               "divisions": {}, "team": {"entered": False}, "fetched_at": datetime.now().isoformat(timespec="minutes")}
        for grp in data["AgeGroups"]:
            label = (grp.get("AgeGroupText") or "").strip()
            teams = grp.get("Teams", [])
            rec["divisions"][label] = {"count": len(teams), "confirmed": sum(1 for t in teams if t.get("IsEntryConfirmed")),
                                       "waitlist": sum(1 for t in teams if t.get("IsOnWaitingList"))}
            for t in teams:
                if norm(t.get("TeamName")) == norm(team):
                    rec["team"] = {"entered": True, "division": label, "confirmed": bool(t.get("IsEntryConfirmed")),
                                   "waitlist": bool(t.get("IsOnWaitingList")), "entered_at": t.get("DateTimeEntered"), "level": t.get("DivisionAbbr")}
        (out_dir / f"{tid}.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
        got += 1
        if rec["team"]["entered"]:
            ours.append(str(tid))
    print(f"entries: {got}/{len(ids)} events; {team} registered in: {', '.join(ours) or 'none'}")


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
    ap.add_argument("--teams", action="store_true", help="also refresh every team's statistics page (points, finishes)")
    args = ap.parse_args()

    sys.stdout.reconfigure(line_buffering=True)
    py = sys.executable
    if args.discover and not args.no_fetch:
        discover(py, args.team, args.division, args.discover_days, args.city or DISCOVER_CITIES)
    if not args.no_fetch:
        ids = ids_to_refresh(args.ids, args.team, args.division, args.lookback, args.ahead)
        up_ids = {int(u["id"]) for u in json.loads((HERE / "upcoming.json").read_text(encoding="utf-8"))} if (HERE / "upcoming.json").exists() else set()
        fetch_tgs(sorted(up_ids | set(ids)))
        fetch_entries(sorted(up_ids | set(ids)), args.team)
        print("refreshing:", " ".join(map(str, ids)) or "(nothing)")
        if ids:
            r = subprocess.run([py, str(HERE / "topgun_fetch.py"), "--team", args.team, "--ids", *map(str, ids)], cwd=HERE)
            if r.returncode:
                print("fetch had errors; rebuilding from whatever was saved")
            record_divisions(ids, args.division)
    if args.teams and not args.no_fetch:
        r = subprocess.run([py, str(HERE / "topgun_teams.py"), "--division", args.division], cwd=HERE)
        if r.returncode:
            print("team stats had errors; continuing")
        r = subprocess.run([py, str(HERE / "topgun_roster.py")], cwd=HERE)
        if r.returncode:
            print("roster fetch had errors; continuing")
    r = subprocess.run([py, str(HERE / "build_site.py"), "--team", args.team, "--division", args.division], cwd=HERE)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
