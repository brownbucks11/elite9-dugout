"""
topgun_opponents.py - follow opponents from your games to their team pages and on to
every tournament they've played, then build their records.

Flow
    1. Read data/<ID>.json for your tournaments (from topgun_fetch/topgun_records) and
       collect every opponent + their team_page_id (the p2= number on the schedule page).
    2. For each opponent: open TeamPage/EntryPoint.aspx?p2=<id>, click "Team Statistics",
       save data/teams/<id>_stats.html, and parse the tournaments table for schedule links.
    3. Fetch each linked tournament schedule page not already in data/, parse it.
    4. Aggregate records with topgun_records and write opponents_<division>.md.

Usage
    python topgun_opponents.py --dump 36883            # step 2 only, for one team; prints what it saw
    python topgun_opponents.py                          # full run for Elite 9's opponents
    python topgun_opponents.py --team "Elite 9" --division 9U --since 2026-09-01
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import topgun_parse
import topgun_records

BASE = "https://playtopgunsports.com/"
ENTRY_URL = BASE + "TeamPage/EntryPoint.aspx?p2={id}"
PAUSE_S = 3


def norm(s):
    return topgun_records.norm(s)


def my_opponents(data_dir, team, division):
    """{team_page_id: name} for every team that appears in a game with `team` in saved tournaments."""
    opps = {}
    for jp in sorted(data_dir.glob("*.json")):
        if not jp.stem.isdigit():
            continue
        p = json.loads(jp.read_text(encoding="utf-8"))
        for dname, div in p["divisions"].items():
            if division and norm(dname) != norm(division):
                continue
            ids = {norm(s["team"]): s["team_page_id"] for s in div["standings"]}
            for gl in div["sections"].values():
                for g in gl:
                    pair = (g["team_a"], g["team_b"])
                    if norm(team) in (norm(pair[0]), norm(pair[1])):
                        other = pair[1] if norm(pair[0]) == norm(team) else pair[0]
                        if other.lower().startswith(("winner", "loser", "seed")):
                            continue
                        tid = ids.get(norm(other))
                        if tid:
                            opps[tid] = other
    return opps


def open_statistics(page, team_page_id):
    """Navigate EntryPoint -> Team Statistics tab. Returns page HTML."""
    page.goto(ENTRY_URL.format(id=team_page_id), wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    link = page.locator("a:has-text('Team Statistics')").first
    if link.count():
        link.click()
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
    else:
        # fall back to the direct URL, which works once the session knows the team
        page.goto(BASE + "TeamPage/Statistics.aspx", wait_until="domcontentloaded", timeout=45000)
    # widen the date range if a "Custom Range"/"All" option exists
    for label in ("All", "All Time", "Custom Range"):
        opt = page.locator(f"label:has-text('{label}'), input[type=radio] + label:has-text('{label}')").first
        if opt.count():
            try:
                opt.click(timeout=2000)
                page.wait_for_timeout(1500)
            except Exception:
                pass
            break
    return page.content()


def tournaments_from_stats(html):
    """Find (tournament_id, link_text) pairs: any link with trnid=NNNN."""
    found = {}
    for m in re.finditer(r'href="([^"]*trnid=(\d+)[^"]*)"[^>]*>(.*?)</a>', html, re.S | re.I):
        found[int(m.group(2))] = re.sub(r"<[^>]+>|\s+", " ", m.group(3)).strip()
    return found


def team_name_from_stats(html):
    m = re.search(r"Team Page for (.*?) \(", html)
    return m.group(1).strip() if m else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", type=int, help="team_page_id: open its Statistics page, save HTML, print findings, stop")
    ap.add_argument("--team", default="Elite 9")
    ap.add_argument("--division", default="9U")
    ap.add_argument("--since", default="2026-09-01")
    ap.add_argument("--data", default="data")
    ap.add_argument("--visible", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="re-download team stats pages")
    args = ap.parse_args()

    data_dir = Path(args.data)
    teams_dir = data_dir / "teams"
    teams_dir.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    if args.dump:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.visible)
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            html = open_statistics(page, args.dump)
            browser.close()
        out = teams_dir / f"{args.dump}_stats.html"
        out.write_text(html, encoding="utf-8")
        text = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        print(f"saved {out}\nteam: {team_name_from_stats(html)!r}")
        print("tournament links:", tournaments_from_stats(html))
        i = text.find("Team Statistics")
        print("\npage text around the statistics section:\n", text[i:i + 2500])
        return 0

    opps = my_opponents(data_dir, args.team, args.division)
    print(f"{len(opps)} opponents of {args.team} in saved {args.division} data:")
    for tid, name in opps.items():
        print(f"  {tid:>6}  {name}")
    if not opps:
        print("No opponents found. Run topgun_fetch.py / topgun_records.py first so data/<ID>.json exists.")
        return 1

    tournament_ids = set()
    team_events = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.visible)
        page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
        for i, (tid, name) in enumerate(opps.items(), 1):
            path = teams_dir / f"{tid}_stats.html"
            if path.exists() and not args.refresh:
                html = path.read_text(encoding="utf-8")
            else:
                print(f"[{i}/{len(opps)}] {name} team page ...", end=" ", flush=True)
                try:
                    html = open_statistics(page, tid)
                    path.write_text(html, encoding="utf-8")
                except Exception as exc:
                    print(f"FAILED: {exc}")
                    continue
                time.sleep(PAUSE_S)
            events = tournaments_from_stats(html)
            team_events[name] = events
            tournament_ids |= set(events)
            print(f"{name}: {len(events)} tournaments linked")
        browser.close()

    print(f"\n{len(tournament_ids)} distinct tournaments across opponents")
    parsed = topgun_records.load_or_fetch(sorted(tournament_ids), data_dir, False, args.visible)
    # include our own tournaments too so head-to-heads are in the record set
    for jp in data_dir.glob("*.json"):
        if jp.stem.isdigit() and int(jp.stem) not in parsed:
            p = json.loads(jp.read_text(encoding="utf-8"))
            p["tournament_id"] = int(jp.stem)
            parsed[int(jp.stem)] = p

    games = topgun_records.collect_games(parsed, args.division, args.since)
    rec = topgun_records.aggregate(games)
    md, csvp = topgun_records.write_outputs(games, rec, args.division, args.team, data_dir.parent)
    # rename to opponents_* so it doesn't clobber the range-based file
    target = md.with_name(md.name.replace("records_", "opponents_"))
    target.write_text(md.read_text(encoding="utf-8"), encoding="utf-8")
    (teams_dir / "team_events.json").write_text(json.dumps(team_events, indent=2), encoding="utf-8")
    print(f"\n{len(games)} scored games, {len(rec)} teams\nwrote {target.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
