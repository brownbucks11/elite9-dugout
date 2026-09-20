"""
topgun_roster.py - save your team's Top Gun "Players" page as JSON.

Opens TeamPage/EntryPoint.aspx?p2=<team page id> -> Players and records every player with
their status (Active / Current Inactive) and the season stat columns Top Gun shows
(home runs, perfect games, no-hitters, shutouts). Jersey numbers are not on Top Gun; they
live in roster.json and build_site.py merges the two.

Writes data/teams/<id>_players.json (committed) and data/teams/<id>_players.html (ignored).

Usage
    python topgun_roster.py                # Elite 9 (page id from docs/data.json or --id)
    python topgun_roster.py --id 31407
    python topgun_roster.py --parse-only   # re-parse the saved HTML without fetching
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from topgun_teams import _Tables

BASE = "https://playtopgunsports.com/"
ENTRY_URL = BASE + "TeamPage/EntryPoint.aspx?p2={id}"


def parse_players(html):
    p = _Tables()
    p.feed(html)
    m = re.search(r"Team Page for (.*?) \(([^)]*)\)", html)
    out = {"team": m.group(1).strip() if m else "", "label": m.group(2).strip() if m else "", "players": []}
    m = re.search(r"(\d{2}/\d{2}/\d{4})\s*(?:</[^>]+>\s*)*-\s*(?:<[^>]+>\s*)*(\d{2}/\d{2}/\d{4})", html)
    out["season"] = f"{m.group(1)} - {m.group(2)}" if m else ""
    grid = next((rows for tid, rows in p.tables.items() if tid.endswith("playerListGridView")), None)
    if not grid:
        return out
    head = [c.lower() for c in grid[0]]

    def col(row, label):
        i = next((i for i, h in enumerate(head) if label in h), None)
        return row[i].replace("\xa0", "").strip() if i is not None and i < len(row) else ""

    for row in grid[1:]:
        name = col(row, "name")
        if not name:
            continue
        status = col(row, "status")
        out["players"].append({
            "name": name, "hr": int(col(row, "home") or 0), "perfect_games": int(col(row, "perfect") or 0),
            "no_hitters": int(col(row, "no hit") or 0), "shutouts": int(col(row, "shutout") or 0),
            "status": status, "active": status.lower() == "active",
        })
    return out


def fetch_players(team_page_id, visible=False):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not visible)
        page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
        page.goto(ENTRY_URL.format(id=team_page_id), wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        link = page.get_by_role("link", name="Players", exact=True)
        if link.count():
            link.first.click()
        else:
            page.goto(BASE + "TeamPage/Players.aspx", wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        html = page.content()
        browser.close()
    return html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", type=int, help="team page id (default: your team's, from docs/data.json)")
    ap.add_argument("--data", default="data")
    ap.add_argument("--parse-only", action="store_true")
    ap.add_argument("--visible", action="store_true")
    args = ap.parse_args()

    tid = args.id
    if not tid:
        site = Path("docs") / "data.json"
        if site.exists():
            tid = json.loads(site.read_text(encoding="utf-8")).get("page_id")
    if not tid:
        print("need --id (team page id)")
        return 1

    teams_dir = Path(args.data) / "teams"
    teams_dir.mkdir(parents=True, exist_ok=True)
    html_path = teams_dir / f"{tid}_players.html"
    if args.parse_only:
        html = html_path.read_text(encoding="utf-8")
    else:
        html = fetch_players(tid, args.visible)
        html_path.write_text(html, encoding="utf-8")
    roster = parse_players(html)
    if not roster["players"]:
        print("no players table found; page saved for inspection")
        return 1
    roster["team_page_id"] = tid
    roster["fetched_at"] = datetime.now().isoformat(timespec="minutes")
    (teams_dir / f"{tid}_players.json").write_text(json.dumps(roster, indent=1), encoding="utf-8")
    active = [p["name"] for p in roster["players"] if p["active"]]
    print(f"{roster['team']}: {len(roster['players'])} players, {len(active)} active: {', '.join(active)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
