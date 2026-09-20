"""
build_site.py - turn the saved Top Gun pages in data/ into docs/data.js for the web page.

Re-parses every data/<ID>.html with topgun_parse (so nothing is stale), keeps the chosen
division, and writes one JSON blob the static page in docs/ renders:
    - your team's season record, game log, and every event it played (with pool standing)
    - upcoming events from upcoming.json (game times appear once the page has been fetched)
    - every team's record across all events, with a per-team game log
    - field addresses

Usage
    python build_site.py                                  # Elite 9, 9U, from 2026-09-01
    python build_site.py --team "Elite 9" --division 9U --since 2026-09-01
    python build_site.py --all                            # include the summer events too

Then open docs/index.html, or commit + push and GitHub Pages serves docs/.
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import topgun_parse
from topgun_records import norm, start_date

SCHEDULE_URL = "https://playtopgunsports.com/GameTimesResults.aspx?trnid={id}"
TEAM_URL = "https://playtopgunsports.com/TeamPage/EntryPoint.aspx?p2={id}"
DAYS = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
PLACEHOLDER = ("winner", "loser", "seed", "tbd")


def game_date(start, day):
    """Tournament start 'YYYY-MM-DD' + 'Sun' -> that weekday on/after the start."""
    if not start or day not in DAYS:
        return start
    d = date.fromisoformat(start)
    return (d + timedelta(days=(DAYS[day] - d.weekday()) % 7)).isoformat()


def load_events(data_dir, division, since, year):
    """Parse every saved page; return {id: event} for pages that have the division."""
    events = {}
    for hp in sorted(data_dir.glob("*.html")):
        if not hp.stem.isdigit():
            continue
        tid = int(hp.stem)
        p = topgun_parse.parse_html(hp.read_text(encoding="utf-8"), url=SCHEDULE_URL.format(id=tid))
        start = start_date(p.get("dates", ""), year)
        if since and start and start < since:
            continue
        div = next((v for k, v in p["divisions"].items() if norm(k) == norm(division)), None)
        if not div:
            continue
        games = []
        for section, gl in div["sections"].items():
            for g in gl:
                games.append({
                    "section": section, "game": g["game"], "day": g["day"], "time": g["time"],
                    "date": game_date(start, g["day"]), "field": g["field"],
                    "team_a": g["team_a"], "seed_a": g["seed_a"], "score_a": g["score_a"],
                    "team_b": g["team_b"], "seed_b": g["seed_b"], "score_b": g["score_b"],
                })
        events[tid] = {
            "id": tid, "name": p.get("name", ""), "dates": p.get("dates", ""), "date": start,
            "url": SCHEDULE_URL.format(id=tid), "notes": div.get("notes", []),
            "complexes": p.get("complexes", {}),
            "standings": div["standings"], "games": games,
        }
    return events


def is_placeholder(name):
    return (name or "").lower().startswith(PLACEHOLDER)


def build(events, team, upcoming, year):
    me = norm(team)
    rec = defaultdict(lambda: {"team": "", "location": "", "page_id": None,
                               "w": 0, "l": 0, "t": 0, "rs": 0, "ra": 0, "events": set(), "log": []})
    my_games, my_events, fields = [], [], {}

    for tid, ev in sorted(events.items(), key=lambda kv: (kv[1]["date"], kv[0])):
        fields.update(ev["complexes"])
        for s in ev["standings"]:
            r = rec[norm(s["team"])]
            r["team"] = r["team"] or s["team"]
            r["location"] = r["location"] or s.get("location", "")
            r["page_id"] = r["page_id"] or s.get("team_page_id")

        played_here = False
        for g in ev["games"]:
            a, b = g["team_a"], g["team_b"]
            if me in (norm(a), norm(b)):
                played_here = True
                opp = b if norm(a) == me else a
                rs, ra = (g["score_a"], g["score_b"]) if norm(a) == me else (g["score_b"], g["score_a"])
                res = None if rs is None or ra is None else ("W" if rs > ra else "L" if rs < ra else "T")
                my_games.append({
                    "event_id": tid, "event": ev["name"], "date": g["date"], "day": g["day"],
                    "time": g["time"], "field": g["field"], "section": g["section"], "game": g["game"],
                    "opponent": opp, "rs": rs, "ra": ra, "result": res,
                })
            if g["score_a"] is None or g["score_b"] is None or is_placeholder(a) or is_placeholder(b):
                continue
            for t1, t2, s1, s2 in ((a, b, g["score_a"], g["score_b"]), (b, a, g["score_b"], g["score_a"])):
                r = rec[norm(t1)]
                r["team"] = r["team"] or t1
                r["rs"] += s1
                r["ra"] += s2
                res = "W" if s1 > s2 else "L" if s1 < s2 else "T"
                r[res.lower()] += 1
                r["events"].add(tid)
                r["log"].append({"date": g["date"], "event_id": tid, "event": ev["name"],
                                 "section": g["section"], "opponent": t2, "rs": s1, "ra": s2, "result": res})

        if played_here:
            mine = next((s for s in ev["standings"] if norm(s["team"]) == me), None)
            my_events.append({
                "id": tid, "name": ev["name"], "dates": ev["dates"], "date": ev["date"], "url": ev["url"],
                "notes": ev["notes"], "teams": len(ev["standings"]),
                "standing": mine and {"seed": mine["seed"], "won": mine["won"], "lost": mine["lost"],
                                      "rs": mine["runs_scored"], "ra": mine["runs_allowed"]},
                "standings": ev["standings"], "games": ev["games"],
            })

    played_ids = {e["id"] for e in my_events}
    upcoming_out = []
    for u in upcoming:
        tid = int(u["id"])
        ev = events.get(tid)
        if tid in played_ids and all(g["result"] for g in my_games if g["event_id"] == tid):
            continue  # fully played, it lives under results now
        upcoming_out.append({
            "id": tid,
            "name": (ev and ev["name"]) or u.get("name") or f"Tournament {tid}",
            "dates": (ev and ev["dates"]) or u.get("dates", ""),
            "date": (ev and ev["date"]) or start_date(u.get("dates", ""), year),
            "url": SCHEDULE_URL.format(id=tid), "posted": bool(ev),
            "games": ev["games"] if ev else [], "standings": ev["standings"] if ev else [],
        })
    upcoming_out.sort(key=lambda e: e["date"] or "9999")

    teams = []
    for r in rec.values():
        gp = r["w"] + r["l"] + r["t"]
        if gp == 0:
            continue
        teams.append({
            "team": r["team"], "location": r["location"], "page_id": r["page_id"],
            "url": TEAM_URL.format(id=r["page_id"]) if r["page_id"] else None,
            "w": r["w"], "l": r["l"], "t": r["t"], "rs": r["rs"], "ra": r["ra"],
            "diff": r["rs"] - r["ra"], "pct": round((r["w"] + 0.5 * r["t"]) / gp, 3),
            "events": len(r["events"]), "log": sorted(r["log"], key=lambda x: (x["date"], x["event_id"])),
        })
    teams.sort(key=lambda t: (-t["pct"], -t["diff"], t["team"].lower()))

    mine = rec[me]
    return {
        "team": mine["team"] or team, "location": mine["location"], "page_id": mine["page_id"],
        "team_url": TEAM_URL.format(id=mine["page_id"]) if mine["page_id"] else None,
        "built": datetime.now().strftime("%a %b %d, %Y %I:%M %p"),
        "record": {"w": mine["w"], "l": mine["l"], "t": mine["t"], "rs": mine["rs"], "ra": mine["ra"]},
        "my_games": sorted(my_games, key=lambda g: (g["date"], g["event_id"], g["section"], g["game"])),
        "my_events": my_events, "upcoming": upcoming_out, "teams": teams,
        "fields": dict(sorted(fields.items())),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", default="Elite 9")
    ap.add_argument("--division", default="9U")
    ap.add_argument("--since", default="2026-09-01", help="ignore events starting before this date")
    ap.add_argument("--all", action="store_true", help="no date cutoff")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--data", default="data")
    ap.add_argument("--upcoming", default="upcoming.json")
    ap.add_argument("--out", default="docs")
    args = ap.parse_args()

    events = load_events(Path(args.data), args.division, None if args.all else args.since, args.year)
    up_path = Path(args.upcoming)
    upcoming = json.loads(up_path.read_text(encoding="utf-8")) if up_path.exists() else []
    site = build(events, args.team, upcoming, args.year)
    site["division"] = args.division

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.json").write_text(json.dumps(site, indent=1), encoding="utf-8")
    # data.js lets index.html work from a plain double-click (file://) as well as on GitHub Pages
    (out / "data.js").write_text("window.SITE_DATA = " + json.dumps(site) + ";\n", encoding="utf-8")

    r = site["record"]
    print(f"{site['team']}: {r['w']}-{r['l']}-{r['t']} over {len(site['my_games'])} games, "
          f"{len(site['my_events'])} events played, {len(site['upcoming'])} upcoming, "
          f"{len(site['teams'])} teams in {len(events)} {args.division} events")
    print(f"wrote {out / 'data.js'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
