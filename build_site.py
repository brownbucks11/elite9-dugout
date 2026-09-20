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


def clock(t):
    """'9:00 AM' -> minutes since midnight, for ordering games within a day."""
    try:
        return datetime.strptime((t or "").strip().upper(), "%I:%M %p").hour * 60 + datetime.strptime((t or "").strip().upper(), "%I:%M %p").minute
    except ValueError:
        return 0


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


def load_team_stats(data_dir):
    """{team_page_id: stats} from data/teams/<id>.json written by topgun_teams.py."""
    stats = {}
    for jp in (data_dir / "teams").glob("*.json"):
        if jp.stem.isdigit():
            stats[int(jp.stem)] = json.loads(jp.read_text(encoding="utf-8"))
    return stats


def season_summary(st):
    """Points and finishes from a team's statistics page (empty if we don't have the page)."""
    tours = (st or {}).get("tournaments", [])
    finishes = [{"date": t["date"], "name": t["name"], "won": t["won"], "lost": t["lost"],
                 "standing": t["standing"], "points": t["points"]} for t in tours]
    return {
        "points": sum(t["points"] for t in tours),
        "titles": sum(1 for t in tours if t["standing"].lower().startswith("1st")),
        "finishes": sorted(finishes, key=lambda f: f["date"]),
        "season": (st or {}).get("season", ""),
        "stats_fetched": (st or {}).get("fetched_at"),
    }


def rank_event(ev):
    """Pool finish for every team in the event.

    Top Gun's standings table is in entry order, not rank order, so the pool finish is computed
    here with Top Gun's own Random Pool Play tie-breaker (rules/2022 Tie Breaker Rules.pdf):
        1. win-loss record            4. runs scored, all pool games
        2. head-to-head (2 teams only) 5. run differential of the last game played
        3. runs allowed, all pool games 6. coin flip (flagged; we can't know it)
    Checked against the bracket seeds Top Gun printed and it matches. Bracket games
    (Gold/Silver/...) are reported separately, not ranked.
    Returns {norm(team): {rank, tiebreak, pool_w, pool_l, pool_t, pool_rs, pool_ra, seed, bracket, bracket_games}}.
    """
    info = defaultdict(lambda: {"pool_w": 0, "pool_l": 0, "pool_t": 0, "pool_rs": 0, "pool_ra": 0,
                                "seed": None, "bracket": None, "bracket_games": [], "tiebreak": "record",
                                "h2h": {}, "last": None})
    for s in ev["standings"]:
        info[norm(s["team"])]
    for g in ev["games"]:
        a, b = g["team_a"], g["team_b"]
        for t, seed in ((a, g["seed_a"]), (b, g["seed_b"])):
            if seed and not is_placeholder(t):
                info[norm(t)]["seed"] = seed
                info[norm(t)]["bracket"] = g["section"]
        if g["score_a"] is None or g["score_b"] is None or is_placeholder(a) or is_placeholder(b):
            continue
        pool = g["section"].lower().startswith("pool")
        for t1, t2, s1, s2 in ((a, b, g["score_a"], g["score_b"]), (b, a, g["score_b"], g["score_a"])):
            r = info[norm(t1)]
            res = "W" if s1 > s2 else "L" if s1 < s2 else "T"
            if pool:
                r["pool_" + res.lower()] += 1
                r["pool_rs"] += s1
                r["pool_ra"] += s2
            else:
                r["bracket"] = r["bracket"] or g["section"]
                r["bracket_games"].append({"section": g["section"], "opponent": t2, "rs": s1, "ra": s2, "result": res})

    # Head-to-head results and each team's last pool game, for the tiebreak steps.
    h2h = {}        # (a, b) -> "W" if a beat b in pool play (only if they met exactly once)
    last_diff = {}  # team -> run differential in its last pool game
    pool_games = [g for g in ev["games"] if g["section"].lower().startswith("pool")
                  and g["score_a"] is not None and g["score_b"] is not None
                  and not is_placeholder(g["team_a"]) and not is_placeholder(g["team_b"])]
    pool_games.sort(key=lambda g: (g["date"] or "", clock(g["time"]), g["game"]))
    for g in pool_games:
        a, b = norm(g["team_a"]), norm(g["team_b"])
        sa, sb = g["score_a"], g["score_b"]
        if sa != sb:
            w, l = (a, b) if sa > sb else (b, a)
            h2h[(w, l)] = "W" if (w, l) not in h2h else None   # met twice -> no clear winner
            h2h[(l, w)] = None
        last_diff[a] = sa - sb
        last_diff[b] = sb - sa
        # kept per team so the page can show the tie-break working
        info[a]["h2h"][g["team_b"]] = "W" if sa > sb else "L" if sa < sb else "T"
        info[b]["h2h"][g["team_a"]] = "W" if sb > sa else "L" if sb < sa else "T"
        info[a]["last"] = {"opponent": g["team_b"], "rs": sa, "ra": sb}
        info[b]["last"] = {"opponent": g["team_a"], "rs": sb, "ra": sa}

    def pct(k):
        r = info[k]
        gp = r["pool_w"] + r["pool_l"] + r["pool_t"]
        return (r["pool_w"] + 0.5 * r["pool_t"]) / gp if gp else -1

    def split(group, value, reverse=False):
        """Order a tied group by `value`, returning sub-groups that are still tied."""
        buckets = defaultdict(list)
        for k in group:
            buckets[value(k)].append(k)
        return [buckets[v] for v in sorted(buckets, reverse=reverse)]

    def beat(a, b):
        return h2h.get((a, b)) == "W"

    def resolve(group, step):
        """Apply the tiebreak steps from `step` on; returns teams in rank order and marks why."""
        if len(group) == 1:
            return group
        if step == "h2h":  # step 2: only when exactly two teams are tied
            if len(group) == 2:
                a, b = group
                if beat(a, b) or beat(b, a):
                    order = [a, b] if beat(a, b) else [b, a]
                    for k in order:
                        info[k]["tiebreak"] = "head-to-head"
                    return order
            return resolve(group, "ra")
        if step == "ra":  # step 3: runs allowed, all pool games
            out = []
            for sub in split(group, lambda k: info[k]["pool_ra"]):
                if len(sub) > 1:
                    for k in sub:
                        info[k]["tiebreak"] = "runs allowed"
                    # still tied on RA: two teams that met go back to head-to-head, otherwise runs scored
                    if len(sub) == 2 and (beat(sub[0], sub[1]) or beat(sub[1], sub[0])):
                        sub = resolve(sub, "h2h")
                    else:
                        sub = resolve(sub, "rs")
                else:
                    info[sub[0]]["tiebreak"] = "runs allowed"
                out += sub
            return out
        if step == "rs":  # step 4: runs scored, all pool games
            out = []
            for sub in split(group, lambda k: info[k]["pool_rs"], reverse=True):
                for k in sub:
                    info[k]["tiebreak"] = "runs scored"
                out += resolve(sub, "diff") if len(sub) > 1 else sub
            return out
        if step == "diff":  # step 5: run differential of the last game played
            out = []
            for sub in split(group, lambda k: last_diff.get(k, 0), reverse=True):
                for k in sub:
                    info[k]["tiebreak"] = "last-game run diff"
                if len(sub) > 1:  # step 6: coin flip - we can't know it, so flag it
                    for k in sub:
                        info[k]["tiebreak"] = "coin flip (unresolved)"
                    sub = sorted(sub)
                out += sub
            return out

    order = []
    for group in split(list(info), pct, reverse=True):
        for k in group:
            info[k]["tiebreak"] = "record"
        order += resolve(group, "h2h")
    for i, k in enumerate(order, 1):
        info[k]["rank"] = i
    return info


def build(events, team, upcoming, year, team_stats=None):
    team_stats = team_stats or {}
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
            ranks = rank_event(ev)
            rows = []
            for s in ev["standings"]:
                r = ranks[norm(s["team"])]
                rows.append({"team": s["team"], "location": s.get("location", ""), "rank": r["rank"],
                             "tiebreak": r["tiebreak"], "h2h": r["h2h"], "last": r["last"],
                             "pool_w": r["pool_w"], "pool_l": r["pool_l"], "pool_t": r["pool_t"],
                             "pool_rs": r["pool_rs"], "pool_ra": r["pool_ra"],
                             "seed": r["seed"], "bracket": r["bracket"],
                             "won": s["won"], "lost": s["lost"], "rs": s["runs_scored"], "ra": s["runs_allowed"]})
            rows.sort(key=lambda x: x["rank"])
            mine = next((x for x in rows if norm(x["team"]) == me), None)
            if mine:
                mine = dict(mine, bracket_games=ranks[me]["bracket_games"])
            my_events.append({
                "id": tid, "name": ev["name"], "dates": ev["dates"], "date": ev["date"], "url": ev["url"],
                "notes": ev["notes"], "teams": len(rows),
                "standing": mine, "standings": rows, "games": ev["games"],
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
            **season_summary(team_stats.get(r["page_id"])),
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
        **season_summary(team_stats.get(mine["page_id"])),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", default="Elite 9")
    ap.add_argument("--division", default="9U")
    ap.add_argument("--since", default="2026-08-01", help="ignore events starting before this date (Top Gun's season starts Aug 1)")
    ap.add_argument("--all", action="store_true", help="no date cutoff")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--data", default="data")
    ap.add_argument("--upcoming", default="upcoming.json")
    ap.add_argument("--out", default="docs")
    args = ap.parse_args()

    events = load_events(Path(args.data), args.division, None if args.all else args.since, args.year)
    up_path = Path(args.upcoming)
    upcoming = json.loads(up_path.read_text(encoding="utf-8")) if up_path.exists() else []
    site = build(events, args.team, upcoming, args.year, load_team_stats(Path(args.data)))
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
