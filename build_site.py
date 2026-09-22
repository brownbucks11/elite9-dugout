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
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import topgun_parse
from topgun_records import norm, start_date

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
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
                # Bracket slots read "Winner Game #1" before they're played and
                # "Winner Game #1 Hooligans" after: keep the team, remember where it came from.
                team_a, from_a = split_ref(g["team_a"])
                team_b, from_b = split_ref(g["team_b"])
                games.append({
                    "section": section, "game": g["game"], "day": g["day"], "time": g["time"],
                    "date": game_date(start, g["day"]), "field": g["field"],
                    "team_a": team_a, "seed_a": g["seed_a"], "score_a": g["score_a"], "from_a": from_a,
                    "team_b": team_b, "seed_b": g["seed_b"], "score_b": g["score_b"], "from_b": from_b,
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


def split_ref(name):
    """'Loser Game #2 Dirtbags Shook' -> ('Dirtbags Shook', {'type': 'loser', 'game': 2});
    'Winner Game #1' (not played yet) -> ('Winner Game #1', {'type': 'winner', 'game': 1});
    a plain team name -> (name, None)."""
    m = re.match(r"^\s*(Winner|Loser)\s+Game\s*#?\s*(\d+)\s*(.*)$", name or "", re.I)
    if not m:
        return name, None
    rest = m.group(3).strip()
    return (rest or name.strip()), {"type": m.group(1).lower(), "game": int(m.group(2))}


def load_team_stats(data_dir):
    """{team_page_id: stats} from data/teams/<id>.json written by topgun_teams.py."""
    stats = {}
    for jp in (data_dir / "teams").glob("*.json"):
        if jp.stem.isdigit():
            stats[int(jp.stem)] = json.loads(jp.read_text(encoding="utf-8"))
    return stats


def build_roster(data_dir, page_id, roster_path):
    """Active players from Top Gun's Players page + jersey numbers/nicknames from roster.json."""
    pj = data_dir / "teams" / f"{page_id}_players.json" if page_id else None
    tg = json.loads(pj.read_text(encoding="utf-8")) if pj and pj.exists() else {"players": []}
    cfg = json.loads(roster_path.read_text(encoding="utf-8")) if roster_path.exists() else {"players": []}
    style = cfg.get("name_style", "full")
    by_full = {norm(p["name"]): p for p in cfg.get("players", [])}
    by_last = defaultdict(list)
    for p in cfg.get("players", []):
        by_last[norm(p["name"].split()[-1])].append(p)

    def display(name):
        parts = name.split()
        return f"{parts[0]} {parts[-1][0]}." if style == "initial" and len(parts) > 1 else name

    players, matched = [], set()
    for p in tg["players"]:
        if not p.get("active"):
            continue
        cfg_p = by_full.get(norm(p["name"]))
        if not cfg_p:
            cands = [c for c in by_last.get(norm(p["name"].split()[-1]), []) if norm(c["name"]) not in matched]
            cfg_p = cands[0] if len(cands) == 1 else None
        if cfg_p:
            matched.add(norm(cfg_p["name"]))
        shown = (cfg_p or {}).get("goes_by") or p["name"]
        players.append({
            "number": (cfg_p or {}).get("number"), "name": display(shown), "full_name": p["name"],
            "goes_by": (cfg_p or {}).get("goes_by"),
            "hr": p["hr"], "perfect_games": p["perfect_games"], "no_hitters": p["no_hitters"], "shutouts": p["shutouts"],
        })
    # Optional photos of the pin-back buttons (docs/buttons/<number>.png), keyed by jersey number.
    # Player photos deliberately stay out of the public site (see local/ in .gitignore).
    buttons = {}
    for f in (HERE / "docs" / "buttons").glob("*"):
        if f.stem.isdigit() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            buttons[int(f.stem)] = f"buttons/{f.name}"
    for x in players:
        x["button_photo"] = buttons.get(x["number"])
    players.sort(key=lambda x: (x["number"] is None, x["number"] or 0, x["name"]))
    unmatched = [c["name"] for c in cfg.get("players", []) if norm(c["name"]) not in matched]
    dup = defaultdict(list)
    for x in players:
        if x["number"] is not None:
            dup[x["number"]].append(x["name"])
    return {
        "players": players, "season": tg.get("season", ""), "fetched_at": tg.get("fetched_at"),
        "inactive": sum(1 for p in tg["players"] if not p.get("active")),
        "not_on_topgun": unmatched,                      # in roster.json but not an active Top Gun player
        "duplicate_numbers": {str(n): v for n, v in dup.items() if len(v) > 1},
    }


def event_state(ev, mine, today):
    """announced -> scheduled -> live -> final, from the calendar and what has been played."""
    if not ev or not ev["games"]:
        return "announced"
    if not mine:
        return "posted"                      # schedule is up but our team is not on it
    start = ev["date"] or ""
    end = max([g["date"] for g in ev["games"] if g["date"]] or [start])
    if today < start:
        return "scheduled"
    if today <= end:
        return "live"
    return "final"


SCHED_FIELDS = ("day", "time", "field", "opponent")


def update_schedule_history(data_dir, tid, mine, state, now):
    """Keep every version of our schedule for an event in data/schedules/<id>.json.
    Top Gun moves times and fields around during the week; a new version is stored whenever
    day/time/field (or a real opponent) changes. Returns the history."""
    path = data_dir / "schedules" / f"{tid}.json"
    hist = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"event_id": tid, "versions": []}
    cur = [{"key": f"{g['section']}#{g['game']}", "date": g["date"], "day": g["day"], "time": g["time"],
            "field": g["field"], "opponent": g["opponent"]} for g in mine]
    if not cur or state == "final":
        return hist
    last = hist["versions"][-1]["games"] if hist["versions"] else None

    def sig(games):
        # bracket slots resolving from "Winner Game #1" to a team is not a schedule change
        return [(g["key"], g["day"], g["time"], g["field"], None if is_placeholder(g["opponent"]) else g["opponent"]) for g in games]
    if last is None or sig(last) != sig(cur):
        # a slot turning from placeholder into a team name only: update in place, no new version
        if last is not None and [(k, d, t, f) for k, d, t, f, _ in sig(last)] == [(k, d, t, f) for k, d, t, f, _ in sig(cur)] \
                and all(is_placeholder(a["opponent"]) or a["opponent"] == b["opponent"] for a, b in zip(last, cur)):
            hist["versions"][-1]["games"] = cur
        else:
            hist["versions"].append({"seen": now.isoformat(timespec="minutes"), "games": cur})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(hist, indent=1), encoding="utf-8")
    return hist


def schedule_changes(hist):
    """What moved between the last two versions: [{key, kind, was}]"""
    vs = hist.get("versions", [])
    if len(vs) < 2:
        return []
    prev = {g["key"]: g for g in vs[-2]["games"]}
    cur = {g["key"]: g for g in vs[-1]["games"]}
    out = []
    for k, g in cur.items():
        p = prev.get(k)
        if not p:
            out.append({"key": k, "kind": "added"})
            continue
        was = {f: p[f] for f in SCHED_FIELDS if p[f] != g[f] and not (f == "opponent" and (is_placeholder(p[f]) or is_placeholder(g[f])))}
        if was:
            out.append({"key": k, "kind": "changed", "was": was})
    for k, p in prev.items():
        if k not in cur:
            out.append({"key": k, "kind": "removed", "was": p})
    return out


def ics_escape(t):
    return str(t or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def write_ics(out_dir, tid, ev_name, team, mine, fields, tentative):
    """docs/ics/<id>.ics with our games, so a parent can add the weekend to their phone calendar."""
    games = [g for g in mine if g["date"] and g["time"]]
    if not games:
        return None
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Elite 9 Dugout//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{ics_escape(team)} - {ics_escape(ev_name[:40])}", "X-WR-TIMEZONE:America/New_York"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for g in games:
        try:
            start = datetime.strptime(f"{g['date']} {g['time'].strip().upper()}", "%Y-%m-%d %I:%M %p")
        except ValueError:
            continue
        end = start + timedelta(minutes=105)
        abbr = (g["field"] or "").split(":")[0].strip()
        sub = (g["field"] or "").split(":")[1].strip() if ":" in (g["field"] or "") else ""
        cx = fields.get(abbr, {})
        loc = ", ".join(x for x in (f"{cx.get('name', abbr)} {sub}".strip(), cx.get("address", "")) if x)
        opp = g["opponent"] if not is_placeholder(g["opponent"]) else "TBD"
        desc = f"{ev_name}. {g['section']} game {g['game']}." + (" Tentative: Top Gun schedules change during the week - the coach's TeamReach post is official." if tentative else "")
        lines += ["BEGIN:VEVENT", f"UID:e9-{tid}-{re.sub(r'[^a-z0-9]', '', g['section'].lower())}-{g['game']}@elite9dugout",
                  f"DTSTAMP:{stamp}", f"DTSTART;TZID=America/New_York:{start.strftime('%Y%m%dT%H%M%S')}",
                  f"DTEND;TZID=America/New_York:{end.strftime('%Y%m%dT%H%M%S')}",
                  f"SUMMARY:{ics_escape(team)} vs {ics_escape(opp)}", f"LOCATION:{ics_escape(loc)}",
                  f"DESCRIPTION:{ics_escape(desc)}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    ics_dir = out_dir / "ics"
    ics_dir.mkdir(parents=True, exist_ok=True)
    (ics_dir / f"{tid}.ics").write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return f"ics/{tid}.ics"


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def bracket_placings(ev):
    """{norm(team): '1st (Gold)'} for every decided place in an event's brackets, from the games
    themselves - the same shape rules the page draws with (a tree has a final and a consolation
    game; standalone games place 1st/2nd, then 3rd/4th, ...). Used to fill in finishes before
    Top Gun posts them on the team pages."""
    out = {}
    sections = [sec for sec in dict.fromkeys(g["section"] for g in ev["games"]) if not sec.lower().startswith("pool")]
    for sec in sections:
        games = [g for g in ev["games"] if g["section"] == sec]
        name = re.sub(r"\s*bracket$", "", sec, flags=re.I)
        by_num = {g["game"]: g for g in games}
        referenced = {r["game"] for g in games for r in (g["from_a"], g["from_b"]) if r}

        def played(g):
            return g["score_a"] is not None and g["score_b"] is not None

        def winner(g):
            if not played(g) or g["score_a"] == g["score_b"]:
                return None
            return g["team_a"] if g["score_a"] > g["score_b"] else g["team_b"]

        def loser(g):
            if not played(g) or g["score_a"] == g["score_b"]:
                return None
            return g["team_b"] if g["score_a"] > g["score_b"] else g["team_a"]

        def consol(g):
            return bool(g["from_a"] and g["from_a"]["type"] == "loser" and g["from_b"] and g["from_b"]["type"] == "loser")

        def round_of(g, depth=0):
            refs = [r for r in (g["from_a"], g["from_b"]) if r and r["game"] in by_num]
            return 1 + max(round_of(by_num[r["game"]], depth + 1) for r in refs) if refs and depth < 12 else 1

        places = []
        if referenced:
            winners = [g for g in games if not consol(g)]
            last = max(round_of(g) for g in winners) if winners else 0
            final = next((g for g in winners if round_of(g) == last and g["game"] not in referenced), None)
            if final:
                places += [(1, winner(final)), (2, loser(final))]
            con = next((g for g in games if consol(g)), None)
            if con:
                places += [(3, winner(con)), (4, loser(con))]
        else:
            for i, g in enumerate(sorted(games, key=lambda g: g["game"])):
                places += [(2 * i + 1, winner(g)), (2 * i + 2, loser(g))]
        for place, team in places:
            if team and not is_placeholder(team):
                out[norm(team)] = f"{ordinal(place)} ({name})"
    return out


def load_gc_games(data_dir):
    """data/gc/games.json from gc_stats.py -> list of games with a local date and a normalized opponent."""
    path = data_dir / "gc" / "games.json"
    if not path.exists():
        return []
    out = []
    for g in json.loads(path.read_text(encoding="utf-8")).values():
        if not g.get("start"):
            continue
        try:
            from zoneinfo import ZoneInfo
            dt = datetime.fromisoformat(g["start"].replace("Z", "+00:00")).astimezone(ZoneInfo(g.get("timezone") or "America/New_York"))
        except Exception:
            dt = datetime.fromisoformat(g["start"].replace("Z", "+00:00"))
        g["local_date"] = dt.date().isoformat()
        g["local_time"] = dt.strftime("%I:%M %p").lstrip("0")
        g["opp_norm"] = norm(re.sub(r"\b(fall|spring|summer)?\s*20\d\d\b|\b\d{1,2}u\b", "", g.get("opponent") or "", flags=re.I))
        out.append(g)
    return out


def match_gc(my_game, gc_games):
    """The GameChanger game for one of our Top Gun games: same day, same score, similar opponent."""
    cands = [g for g in gc_games if g["local_date"] == my_game["date"]]
    if not cands:
        return None
    opp = norm(my_game["opponent"])
    def name_ok(g):
        return g["opp_norm"] and (g["opp_norm"] in opp or opp in g["opp_norm"] or g["opp_norm"][:8] == opp[:8])
    def score_ok(g):
        sc = g.get("score") or {}
        return my_game["rs"] is not None and sc.get("team") == my_game["rs"] and sc.get("opponent_team") == my_game["ra"]
    for test in (lambda g: name_ok(g) and score_ok(g), score_ok, name_ok):
        hit = [g for g in cands if test(g)]
        if len(hit) == 1:
            return hit[0]
    return None


def gc_summary(g):
    """What the page needs for one game's box score (our side named, theirs as totals)."""
    if not g:
        return None
    box = g.get("box") or {}
    return {
        "url": g.get("url") or "", "home_away": g.get("home_away"), "status": g.get("status"), "time": g.get("local_time"),
        "line_score": g.get("line_score"), "opponent_box": g.get("opponent_box"),
        "lineup": box.get("lineup"), "pitching": box.get("pitching"),
    }


def season_summary(st, derived=()):
    """Points and finishes from a team's statistics page, with finishes Top Gun hasn't posted yet
    filled in from our bracket results (`derived`: [{date, name, standing, won, lost}])."""
    tours = (st or {}).get("tournaments", [])
    finishes = [{"date": t["date"], "name": t["name"], "won": t["won"], "lost": t["lost"],
                 "standing": t["standing"], "points": t["points"]} for t in tours]
    for d in derived:
        row = next((f for f in finishes if f["date"] == d["date"]), None)
        if row is None:
            finishes.append({"date": d["date"], "name": d["name"], "won": d["won"], "lost": d["lost"],
                             "standing": d["standing"], "points": 0, "derived": True})
        elif not row["standing"]:
            row["standing"] = d["standing"]
            row["derived"] = True
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


def build(events, team, upcoming, year, team_stats=None, today=None, out_dir=None):
    team_stats = team_stats or {}
    today = today or date.today().isoformat()
    now = datetime.now()
    me = norm(team)
    gc_games = load_gc_games(DATA_DIR)
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
            mine_games = [g for g in my_games if g["event_id"] == tid]
            state = event_state(ev, mine_games, today)
            hist = update_schedule_history(DATA_DIR, tid, mine_games, state, now)
            if state not in ("live", "final"):
                continue                       # scheduled-only events live under Next Up
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
            unplayed = sorted([g for g in mine_games if g["result"] is None], key=lambda g: (g["date"] or "", clock(g["time"]), g["game"]))
            my_events.append({
                "id": tid, "name": ev["name"], "dates": ev["dates"], "date": ev["date"], "url": ev["url"],
                "notes": ev["notes"], "teams": len(rows), "state": state,
                "next_game": unplayed[0] if unplayed else None,
                "ics": write_ics(out_dir, tid, ev["name"], team, mine_games, fields, state != "final") if out_dir and state == "live" else None,
                "standing": mine, "standings": rows, "games": ev["games"],
            })

    for g in my_games:
        g["gc"] = gc_summary(match_gc(g, gc_games))
    gc_matched = sum(1 for g in my_games if g["gc"])

    # Tournaments: everything from upcoming.json plus every event we're in, finished ones included
    listed = {int(u["id"]): u for u in upcoming}
    by_id = {e["id"]: e for e in my_events}
    ids = set(listed) | set(by_id) | \
          {tid for tid, ev in events.items() if any(me in (norm(g["team_a"]), norm(g["team_b"])) for g in ev["games"])}
    upcoming_out = []
    for tid in ids:
        u = listed.get(tid, {})
        ev = events.get(tid)
        mine_games = [g for g in my_games if g["event_id"] == tid]
        state = event_state(ev, mine_games, today)
        if state == "posted" and tid not in listed:
            continue
        done = by_id.get(tid)
        summary = None
        if done and done.get("standing"):
            st = done["standing"]
            summary = {"rank": st["rank"], "teams": done["teams"], "pool": f"{st['pool_w']}-{st['pool_l']}" + (f"-{st['pool_t']}" if st.get("pool_t") else ""),
                       "bracket": st.get("bracket"), "seed": st.get("seed"), "bracket_games": st.get("bracket_games", []),
                       "w": sum(1 for g in mine_games if g["result"] == "W"), "l": sum(1 for g in mine_games if g["result"] == "L")}
        hist = update_schedule_history(DATA_DIR, tid, mine_games, state, now) if mine_games and state != "final" else {"versions": []}
        upcoming_out.append({
            "id": tid, "state": state, "official": bool(u.get("official")), "summary": summary,
            "name": (ev and ev["name"]) or u.get("name") or f"Tournament {tid}",
            "dates": (ev and ev["dates"]) or u.get("dates", ""),
            "date": (ev and ev["date"]) or start_date(u.get("dates", ""), year),
            "url": SCHEDULE_URL.format(id=tid), "posted": bool(ev and ev["games"]),
            "games": ev["games"] if ev else [], "standings": ev["standings"] if ev else [],
            "mine": mine_games,
            "next_game": next((g for g in sorted(mine_games, key=lambda g: (g["date"] or "", clock(g["time"]), g["game"])) if g["result"] is None), None),
            "schedule_versions": len(hist["versions"]),
            "schedule_first": hist["versions"][0]["seen"] if hist["versions"] else None,
            "schedule_updated": hist["versions"][-1]["seen"] if hist["versions"] else None,
            "changes": schedule_changes(hist),
            "ics": write_ics(out_dir, tid, (ev and ev["name"]) or u.get("name", ""), team, mine_games, fields, not u.get("official")) if out_dir and mine_games and state != "final" else None,
        })
    upcoming_out.sort(key=lambda e: e["date"] or "9999")

    placings = {tid: bracket_placings(ev) for tid, ev in events.items()}

    def derived_for(k, r):
        out = []
        for tid in r["events"]:
            st = placings.get(tid, {}).get(k)
            if st:
                ev = events[tid]
                mine_here = [x for x in r["log"] if x["event_id"] == tid]
                out.append({"date": ev["date"], "name": ev["name"], "standing": st,
                            "won": sum(1 for x in mine_here if x["result"] == "W"), "lost": sum(1 for x in mine_here if x["result"] == "L")})
        return out

    teams = []
    for k, r in rec.items():
        gp = r["w"] + r["l"] + r["t"]
        if gp == 0:
            continue
        teams.append({
            "team": r["team"], "location": r["location"], "page_id": r["page_id"],
            "url": TEAM_URL.format(id=r["page_id"]) if r["page_id"] else None,
            "w": r["w"], "l": r["l"], "t": r["t"], "rs": r["rs"], "ra": r["ra"],
            "diff": r["rs"] - r["ra"], "pct": round((r["w"] + 0.5 * r["t"]) / gp, 3),
            "events": len(r["events"]), "log": sorted(r["log"], key=lambda x: (x["date"], x["event_id"])),
            **season_summary(team_stats.get(r["page_id"]), derived_for(k, r)),
        })
    teams.sort(key=lambda t: (-t["pct"], -t["diff"], t["team"].lower()))

    # Complexes we play at that no schedule page described (some pages have no address table),
    # plus optional overrides from fields.json: {"NWP": {"name": "...", "address": "..."}}
    for g in my_games:
        abbr = (g["field"] or "").split(":")[0].strip()
        if abbr and abbr not in fields:
            fields[abbr] = {"name": abbr, "address": ""}
    fx = HERE / "fields.json"
    if fx.exists():
        for abbr, info in json.loads(fx.read_text(encoding="utf-8")).items():
            if not abbr.startswith("_"):
                fields[abbr] = {**fields.get(abbr, {"name": abbr, "address": ""}), **info}

    mine = rec[me]
    return {
        "team": mine["team"] or team, "location": mine["location"], "page_id": mine["page_id"],
        "team_url": TEAM_URL.format(id=mine["page_id"]) if mine["page_id"] else None,
        "built": datetime.now().strftime("%a %b %d, %Y %I:%M %p"),
        "record": {"w": mine["w"], "l": mine["l"], "t": mine["t"], "rs": mine["rs"], "ra": mine["ra"]},
        "my_games": sorted(my_games, key=lambda g: (g["date"], g["event_id"], g["section"], g["game"])),
        "my_events": my_events, "tournaments": upcoming_out, "teams": teams,
        "fields": dict(sorted(fields.items())),
        "roster": build_roster(DATA_DIR, mine["page_id"], HERE / "roster.json"),
        "gc": {"games": len(gc_games), "matched": gc_matched},
        **season_summary(team_stats.get(mine["page_id"]), derived_for(me, mine)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", default="Elite 9")
    ap.add_argument("--division", default="9U")
    ap.add_argument("--since", default="2026-08-01", help="ignore events starting before this date (Top Gun's season starts Aug 1)")
    ap.add_argument("--all", action="store_true", help="no date cutoff")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--season", default="Fall 2026", help="season label shown in the page title and header")
    ap.add_argument("--data", default="data")
    ap.add_argument("--upcoming", default="upcoming.json")
    ap.add_argument("--out", default="docs")
    ap.add_argument("--today", default=None, help="YYYY-MM-DD, to preview how the page looks on another day")
    args = ap.parse_args()

    global DATA_DIR
    DATA_DIR = Path(args.data)
    events = load_events(Path(args.data), args.division, None if args.all else args.since, args.year)
    up_path = Path(args.upcoming)
    upcoming = json.loads(up_path.read_text(encoding="utf-8")) if up_path.exists() else []
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    site = build(events, args.team, upcoming, args.year, load_team_stats(Path(args.data)), args.today, out)
    site["division"] = args.division
    site["today"] = args.today or date.today().isoformat()
    site["season_label"] = args.season

    (out / "data.json").write_text(json.dumps(site, indent=1), encoding="utf-8")
    # data.js lets index.html work from a plain double-click (file://) as well as on GitHub Pages
    (out / "data.js").write_text("window.SITE_DATA = " + json.dumps(site) + ";\n", encoding="utf-8")

    r = site["record"]
    print(f"{site['team']}: {r['w']}-{r['l']}-{r['t']} over {len(site['my_games'])} games, "
          f"{len(site['my_events'])} events played, {len(site['tournaments'])} tournaments listed, "
          f"{len(site['teams'])} teams in {len(events)} {args.division} events")
    print(f"wrote {out / 'data.js'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
