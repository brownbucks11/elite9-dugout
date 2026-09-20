"""
topgun_records.py - build every team's record across many Top Gun tournaments.

Fetches schedule/results pages for a set of tournament IDs (explicit or ranges),
parses every division, and aggregates wins/losses/runs per team from games that
have a final score. Also produces a game log for each opponent your team faced.

Usage
    python topgun_records.py --range 12508-12518 --range 13596-13602 --since 2026-09-01
    python topgun_records.py --ids 12517 12516 --division 9U --team "Elite 9"
    python topgun_records.py --range 12508-12518 --refresh      # re-download pages already in data/

Pages already saved in data/<ID>.html are reused unless --refresh. IDs that don't exist
(empty page, no divisions) are skipped and remembered in data/empty_ids.txt.

Outputs (in the folder above data/):
    records_<division>.md   - standings-style table of every team + opponent game logs
    games_<division>.csv    - one row per scored game, all tournaments
"""

import argparse
import csv
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import topgun_parse

SCHEDULE_URL = "https://playtopgunsports.com/GameTimesResults.aspx?trnid={id}"
PAUSE_S = 3
MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], 1)}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def start_date(dates_text, year=2026):
    """'September 19 - 20 in Greater Charlotte Area, NC' -> '2026-09-19'"""
    m = re.match(r"\s*([A-Z][a-z]+)\s+(\d+)", dates_text or "")
    if not m or m.group(1) not in MONTHS:
        return ""
    return f"{year}-{MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"


def parse_ranges(ranges):
    ids = []
    for r in ranges:
        a, b = r.split("-")
        ids += list(range(int(a), int(b) + 1))
    return ids


def load_or_fetch(ids, data_dir, refresh, visible):
    """Return {id: parsed}. Fetches with Playwright only for IDs not already on disk."""
    empty_file = data_dir / "empty_ids.txt"
    empty = set(int(x) for x in empty_file.read_text().split()) if empty_file.exists() else set()
    parsed = {}
    need = []
    for tid in ids:
        html_path = data_dir / f"{tid}.html"
        if not refresh and tid in empty:
            continue
        if not refresh and html_path.exists():
            p = topgun_parse.parse_html(html_path.read_text(encoding="utf-8"), url=SCHEDULE_URL.format(id=tid))
            p["tournament_id"] = tid
            parsed[tid] = p
        else:
            need.append(tid)

    if need:
        from playwright.sync_api import TimeoutError as PWTimeout
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not visible)
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            for i, tid in enumerate(need, 1):
                url = SCHEDULE_URL.format(id=tid)
                print(f"[{i}/{len(need)}] {tid} ...", end=" ", flush=True)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except PWTimeout:
                        pass
                    html = page.content()
                    p = topgun_parse.parse_html(html, url=url)
                    p["tournament_id"] = tid
                    if p["divisions"]:
                        (data_dir / f"{tid}.html").write_text(html, encoding="utf-8")
                        (data_dir / f"{tid}.json").write_text(json.dumps(p, indent=2), encoding="utf-8")
                        parsed[tid] = p
                        empty.discard(tid)
                        print(f"{p['name'][:40]!r} {p['dates'][:30]!r} divs={len(p['divisions'])}")
                    else:
                        empty.add(tid)
                        print("no divisions (skipped)")
                except Exception as exc:
                    print(f"FAILED: {exc}")
                if i < len(need):
                    time.sleep(PAUSE_S)
            browser.close()
        empty_file.write_text("\n".join(str(x) for x in sorted(empty)))
    return parsed


def collect_games(parsed, division, since):
    """Flatten every scored game in the division across tournaments."""
    games = []
    for tid, p in sorted(parsed.items()):
        date = start_date(p.get("dates", ""))
        if since and date and date < since:
            continue
        for dname, div in p["divisions"].items():
            if division and norm(dname) != norm(division):
                continue
            for section, gl in div["sections"].items():
                for g in gl:
                    if g["score_a"] is None or g["score_b"] is None:
                        continue
                    if g["team_a"].lower().startswith(("winner", "loser")) or g["team_b"].lower().startswith(("winner", "loser")):
                        continue
                    games.append({
                        "tournament_id": tid, "tournament": p.get("name", ""), "date": date,
                        "dates": p.get("dates", ""), "division": dname, "section": section,
                        "game": g["game"], "day": g["day"], "time": g["time"], "field": g["field"],
                        "team_a": g["team_a"], "score_a": g["score_a"],
                        "team_b": g["team_b"], "score_b": g["score_b"],
                    })
    return games


def aggregate(games):
    rec = defaultdict(lambda: {"team": "", "w": 0, "l": 0, "t": 0, "rs": 0, "ra": 0, "events": set(), "log": []})
    for g in games:
        for me, opp, rs, ra in ((g["team_a"], g["team_b"], g["score_a"], g["score_b"]),
                                (g["team_b"], g["team_a"], g["score_b"], g["score_a"])):
            r = rec[norm(me)]
            r["team"] = r["team"] or me
            r["rs"] += rs
            r["ra"] += ra
            res = "W" if rs > ra else "L" if rs < ra else "T"
            r[res.lower()] += 1
            r["events"].add(g["tournament_id"])
            r["log"].append({"date": g["date"], "tournament": g["tournament"], "section": g["section"],
                             "opp": opp, "rs": rs, "ra": ra, "res": res})
    return rec


def write_outputs(games, rec, division, team, out_dir):
    tag = norm(division) or "all"
    csv_path = out_dir / f"games_{tag}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(games[0].keys()) if games else ["none"])
        w.writeheader()
        w.writerows(games)

    def pct(r):
        n = r["w"] + r["l"] + r["t"]
        return (r["w"] + 0.5 * r["t"]) / n if n else 0

    rows = sorted(rec.values(), key=lambda r: (-pct(r), -(r["rs"] - r["ra"]), r["team"].lower()))
    lines = [f"# {division or 'All divisions'} records across Top Gun events",
             f"_Built {datetime.now():%a %b %d, %Y %I:%M %p} from {len(games)} scored games in "
             f"{len({g['tournament_id'] for g in games})} tournaments_", "",
             "| Team | W-L-T | Win% | RS | RA | Diff | Events |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        mark = " **<--**" if team and norm(r["team"]) == norm(team) else ""
        lines.append(f"| {r['team']}{mark} | {r['w']}-{r['l']}-{r['t']} | {pct(r):.3f} | {r['rs']} | {r['ra']} | "
                     f"{r['rs'] - r['ra']:+d} | {len(r['events'])} |")

    if team and norm(team) in rec:
        mine = rec[norm(team)]
        opps = sorted({norm(x["opp"]) for x in mine["log"]})
        lines += ["", f"## Teams {team} has played", ""]
        for o in opps:
            r = rec.get(o)
            if not r:
                continue
            vs = [x for x in mine["log"] if norm(x["opp"]) == o]
            head = ", ".join(f"{x['res']} {x['rs']}-{x['ra']} ({x['date']})" for x in vs)
            lines += [f"### {r['team']}  -  {r['w']}-{r['l']}-{r['t']}, RS {r['rs']}, RA {r['ra']}",
                      f"vs {team}: {head}", "",
                      "| Date | Tournament | Round | Opponent | Result |", "|---|---|---|---|---|"]
            for x in sorted(r["log"], key=lambda x: x["date"]):
                lines.append(f"| {x['date']} | {x['tournament'][:40]} | {x['section']} | {x['opp']} | {x['res']} {x['rs']}-{x['ra']} |")
            lines.append("")
        lines += ["", f"## {team} game log", "", "| Date | Tournament | Round | Opponent | Result |", "|---|---|---|---|---|"]
        for x in sorted(mine["log"], key=lambda x: x["date"]):
            lines.append(f"| {x['date']} | {x['tournament'][:40]} | {x['section']} | {x['opp']} | {x['res']} {x['rs']}-{x['ra']} |")

    md_path = out_dir / f"records_{tag}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, csv_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="*", type=int, default=[])
    ap.add_argument("--range", action="append", default=[], help="e.g. 12508-12518 (repeatable)")
    ap.add_argument("--since", default="2026-09-01", help="ignore tournaments starting before this date")
    ap.add_argument("--division", default="9U")
    ap.add_argument("--team", default="Elite 9")
    ap.add_argument("--data", default="data")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--visible", action="store_true")
    args = ap.parse_args()

    ids = sorted(set(args.ids + parse_ranges(args.range)))
    if not ids:
        print("give --ids or --range")
        return 1
    data_dir = Path(args.data)
    data_dir.mkdir(parents=True, exist_ok=True)

    parsed = load_or_fetch(ids, data_dir, args.refresh, args.visible)
    print(f"\n{len(parsed)} tournaments with data:")
    for tid, p in sorted(parsed.items()):
        print(f"  {tid}  {start_date(p.get('dates','')) or '????-??-??'}  {p.get('dates','')[:45]:<45} {p.get('name','')[:40]}")

    games = collect_games(parsed, args.division, args.since)
    rec = aggregate(games)
    md, csvp = write_outputs(games, rec, args.division, args.team, data_dir.parent)
    print(f"\n{len(games)} scored {args.division} games, {len(rec)} teams")
    print(f"wrote {md.resolve()}\nwrote {csvp.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
