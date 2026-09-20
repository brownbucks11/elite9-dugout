"""
topgun_fetch.py - pull Top Gun schedule pages for a team's tournaments and parse them.

No clicking. Give it tournament IDs (the trnid in the schedule URL) or let it find
them from the tournament list by date range and city, then it:
    1. opens each  https://playtopgunsports.com/GameTimesResults.aspx?trnid=<ID>
    2. saves the HTML to  data/<ID>.html
    3. parses it with topgun_parse.py to  data/<ID>.json
    4. writes  <team>_schedule.md  - your team's games, standings and brackets, all events

Setup: same as topgun_snapshot.py (pip install playwright; python -m playwright install chromium)

Usage
    python topgun_fetch.py --ids 12518 12521 12522 12524
    python topgun_fetch.py --from 2026-09-25 --to 2026-11-30 --city Charlotte --city "Tega Cay" --city Monroe
    python topgun_fetch.py --ids 12518 --team "Elite 9" --show      # also print the summary

Defaults: --team "Elite 9", headless (add --visible to watch it), 4 s pause between pages.
Re-running overwrites data/<ID>.* so the files always reflect the latest site state.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

import topgun_parse

SCHEDULE_URL = "https://playtopgunsports.com/GameTimesResults.aspx?trnid={id}"
LIST_URL = "https://topgunstats.com/tournaments?sport=1"
PAUSE_S = 4


def settle(page):
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass


def discover_ids(page, date_from, date_to, cities):
    """Load the tournament list and collect the JSON it fetches; filter by date/city."""
    found = {}

    def on_response(resp):
        if "tournament-data" in resp.url and "json" in resp.headers.get("content-type", ""):
            try:
                for t in resp.json().get("tournaments", []):
                    found[t["TournamentID"]] = t
            except Exception:
                pass

    page.on("response", on_response)
    page.goto(LIST_URL, wait_until="domcontentloaded", timeout=45000)
    settle(page)
    # The list is paged; keep clicking "next" while new tournaments keep arriving.
    for _ in range(30):
        before = len(found)
        nxt = page.locator("button:has-text('Next'), a:has-text('Next'), [aria-label*='next' i]").first
        try:
            if nxt.count() == 0 or not nxt.is_enabled():
                break
            nxt.click()
            page.wait_for_timeout(1500)
            settle(page)
        except Exception:
            break
        if len(found) == before:
            break
    page.remove_listener("response", on_response)

    picked = []
    for t in sorted(found.values(), key=lambda x: x["StartDate"]):
        start = t["StartDate"][:10]
        if date_from and start < date_from:
            continue
        if date_to and start > date_to:
            continue
        if cities and not any(c.lower() in t["CityState"].lower() for c in cities):
            continue
        picked.append(t)
    return picked


def fetch_schedule(page, tid, data_dir):
    url = SCHEDULE_URL.format(id=tid)
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    settle(page)
    html = page.content()
    (data_dir / f"{tid}.html").write_text(html, encoding="utf-8")
    parsed = topgun_parse.parse_html(html, url=url)
    parsed["tournament_id"] = tid
    parsed["fetched_at"] = datetime.now().isoformat(timespec="minutes")
    (data_dir / f"{tid}.json").write_text(json.dumps(parsed, indent=2), encoding="utf-8")
    return parsed


def fmt_game(g):
    sa = "" if g["score_a"] is None else f" {g['score_a']}"
    sb = "" if g["score_b"] is None else f" {g['score_b']}"
    seed_a = f"(#{g['seed_a']}) " if g.get("seed_a") else ""
    seed_b = f"(#{g['seed_b']}) " if g.get("seed_b") else ""
    return f"| {g['game']} | {g['day']} {g['time']} | {g['field']} | {seed_a}{g['team_a']}{sa} | {seed_b}{g['team_b']}{sb} |"


def write_summary(parsed_list, team, out_path):
    lines = [f"# {team} - Top Gun schedule", f"_Updated {datetime.now():%a %b %d, %Y %I:%M %p}_", ""]
    for p in parsed_list:
        tv = topgun_parse.team_view(p, team)
        title = p.get("name") or p["title"]
        lines += [f"## {title}", f"{p.get('dates', '')}  ", f"Tournament {p['tournament_id']} - [schedule page]({p['url']})", ""]
        if not tv["divisions"]:
            divs = ", ".join(p["divisions"]) or "none posted yet"
            lines += [f"_{team} not listed yet. Divisions posted: {divs}_", ""]
            continue
        for dname, dv in tv["divisions"].items():
            lines.append(f"### {dname}  " + (" / ".join(dv["notes"]) if dv["notes"] else ""))
            for s in dv["standings"]:
                lines.append(f"**Standing:** #{s['seed']} of {len(dv['all_standings'])}  -  {s['won']}-{s['lost']}, RS {s['runs_scored']}, RA {s['runs_allowed']}")
            lines += ["", f"**{team} games**", "", "| G | When | Field | Team A | Team B |", "|---|---|---|---|---|"]
            lines += [fmt_game(g).replace("| " + str(g["game"]), f"| {g['section'][:4]} {g['game']}", 1) for g in dv["games"]] or ["| - | no games posted | | | |"]
            lines += ["", f"**{dname} standings**", "", "| # | Team | From | W-L | RS | RA |", "|---|---|---|---|---|---|"]
            for s in dv["all_standings"]:
                mark = " **<--**" if topgun_parse.re.sub(r"[^a-z0-9]", "", s["team"].lower()) == topgun_parse.re.sub(r"[^a-z0-9]", "", team.lower()) else ""
                lines.append(f"| {s['seed']} | {s['team']}{mark} | {s['location']} | {s['won']}-{s['lost']} | {s['runs_scored']} | {s['runs_allowed']} |")
            for bname, games in dv["brackets"].items():
                lines += ["", f"**{bname}**", "", "| G | When | Field | Team A | Team B |", "|---|---|---|---|---|"]
                lines += [fmt_game(g) for g in games]
            lines.append("")
    # Complexes from the last tournament (same set for a venue group)
    if parsed_list and parsed_list[-1]["complexes"]:
        lines += ["## Field addresses", ""]
        for abbr, c in parsed_list[-1]["complexes"].items():
            lines.append(f"- **{abbr}** - {c['name']}, {c['address']}")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Fetch and parse Top Gun schedule pages.")
    ap.add_argument("--ids", nargs="*", type=int, default=[], help="tournament IDs (trnid)")
    ap.add_argument("--from", dest="date_from", help="discover: earliest start date YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="discover: latest start date YYYY-MM-DD")
    ap.add_argument("--city", action="append", default=[], help="discover: city filter, repeatable")
    ap.add_argument("--team", default="Elite 9")
    ap.add_argument("--data", default="data", help="output folder (default: data)")
    ap.add_argument("--visible", action="store_true", help="show the browser window")
    ap.add_argument("--show", action="store_true", help="print the summary when done")
    ap.add_argument("--list-only", action="store_true", help="discover: write data/tournaments.json and stop")
    args = ap.parse_args()

    data_dir = Path(args.data)
    data_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.visible)
        page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()

        ids = list(args.ids)
        if args.date_from or args.date_to or args.city:
            print("Discovering tournaments from the list page...")
            found = discover_ids(page, args.date_from, args.date_to, args.city)
            for t in found:
                print(f"  {t['TournamentID']}  {t['StartDate'][:10]}  {t['CityState']:<28} {t['TournamentName'].strip()[:55]}")
                if t["TournamentID"] not in ids:
                    ids.append(t["TournamentID"])
            (data_dir / "tournaments.json").write_text(json.dumps(found, indent=2), encoding="utf-8")
            if args.list_only:
                browser.close()
                return 0
        if not ids:
            print("No tournament IDs given or found. Use --ids or --from/--to/--city.")
            browser.close()
            return 1

        parsed = []
        for i, tid in enumerate(ids, 1):
            print(f"[{i}/{len(ids)}] tournament {tid} ...", end=" ", flush=True)
            try:
                p = fetch_schedule(page, tid, data_dir)
                divs = list(p["divisions"])
                print(f"{p.get('name', p['title'])[:50]} | divisions: {', '.join(divs) if divs else 'none yet'}")
                parsed.append(p)
            except Exception as exc:
                print(f"FAILED: {exc}")
            if i < len(ids):
                time.sleep(PAUSE_S)
        browser.close()

    slug = re.sub(r"[^A-Za-z0-9]+", "", args.team).lower()
    out = data_dir.parent / f"{slug}_schedule.md"
    write_summary(parsed, args.team, out)
    print(f"\nSummary: {out.resolve()}")
    if args.show:
        print(out.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
