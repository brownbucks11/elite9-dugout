"""
topgun_teams.py - save each team's Top Gun "Team Statistics" page (current season) as JSON.

For every team_page_id in the saved 9U standings (data/<ID>.json / .html), open
TeamPage/EntryPoint.aspx?p2=<id> -> Team Statistics, and record the Tournament Summary table:
date, tournament, won, lost, runs, final standing ("2nd (Gold)"), points. Top Gun's
"Current Season" range starts Aug 1, which is what the page shows by default.

Writes data/teams/<id>.json (committed) and data/teams/<id>_stats.html (ignored).

Usage
    python topgun_teams.py                      # every 9U team seen this season, skip ones fetched < 20h ago
    python topgun_teams.py --ids 31407 36883    # just these team page ids
    python topgun_teams.py --max-age 0          # re-fetch everything
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

import topgun_parse
from topgun_records import norm, start_date

BASE = "https://playtopgunsports.com/"
ENTRY_URL = BASE + "TeamPage/EntryPoint.aspx?p2={id}"
PAUSE_S = 3


class _Tables(HTMLParser):
    """Collect every <table> as rows of cell text, keyed by id."""

    def __init__(self):
        super().__init__()
        self.tables = {}
        self._stack = []   # [(id, rows)]
        self._cell = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._stack.append((a.get("id", f"_{len(self.tables)}"), []))
        elif tag == "tr" and self._stack:
            self._stack[-1][1].append([])
        elif tag in ("td", "th") and self._stack:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._stack and self._stack[-1][1]:
            self._stack[-1][1][-1].append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "table" and self._stack:
            tid, rows = self._stack.pop()
            self.tables[tid] = rows

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_stats(html):
    """Team name, class, season range, overall record, and the tournament summary rows."""
    p = _Tables()
    p.feed(html)
    m = re.search(r"Team Page for (.*?) \(([^)]*)\)", html)
    out = {
        "team": m.group(1).strip() if m else "",
        "label": m.group(2).strip() if m else "",
        "season": "", "games": None, "won": None, "lost": None, "tournaments": [],
    }
    m = re.search(r"tournamentSummaryDateRangeLabel[^>]*>([^<]*)<", html)
    if m:
        out["season"] = m.group(1).strip()
    for key, field in (("gamesPlayedLabel", "games"), ("gamesWonLabel", "won"), ("gamesLostLabel", "lost")):
        m = re.search(r'id="[^"]*_' + key + r'"[^>]*>([\d.]+)<', html)
        if m:
            out[field] = int(float(m.group(1)))
    grid = next((rows for tid, rows in p.tables.items() if tid.endswith("tournamentSummaryGridView")), [])
    for row in grid[1:]:
        if len(row) < 8 or not re.match(r"\d{2}/\d{2}/\d{4}", row[0]):
            continue
        mm, dd, yy = row[0].split("/")
        standing = row[6].replace("\xa0", "").strip()
        out["tournaments"].append({
            "date": f"{yy}-{mm}-{dd}", "name": row[1].strip(),
            "won": int(float(row[2] or 0)), "lost": int(float(row[3] or 0)),
            "rs": int(row[4] or 0), "ra": int(row[5] or 0),
            "standing": standing, "points": int(re.sub(r"[^\d]", "", row[7]) or 0),
        })
    return out


def open_statistics(page, team_page_id):
    page.goto(ENTRY_URL.format(id=team_page_id), wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    link = page.locator("a:has-text('Team Statistics')").first
    if link.count():
        link.click()
    else:
        page.goto(BASE + "TeamPage/Statistics.aspx", wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    return page.content()


def season_team_ids(data_dir, division, since, year):
    """{team_page_id: team name} for every team in the division's standings since `since`."""
    ids = {}
    for hp in sorted(data_dir.glob("*.html")):
        if not hp.stem.isdigit():
            continue
        p = topgun_parse.parse_html(hp.read_text(encoding="utf-8"))
        if since and (start_date(p.get("dates", ""), year) or "") < since:
            continue
        for dname, div in p["divisions"].items():
            if norm(dname) != norm(division):
                continue
            for s in div["standings"]:
                if s.get("team_page_id"):
                    ids[int(s["team_page_id"])] = s["team"]
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="*", type=int, default=[])
    ap.add_argument("--division", default="9U")
    ap.add_argument("--since", default="2026-08-01", help="teams from events starting on/after this date")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--max-age", type=float, default=20, help="hours; skip pages fetched more recently")
    ap.add_argument("--data", default="data")
    ap.add_argument("--visible", action="store_true")
    args = ap.parse_args()

    data_dir = Path(args.data)
    teams_dir = data_dir / "teams"
    teams_dir.mkdir(parents=True, exist_ok=True)

    ids = {i: "" for i in args.ids} or season_team_ids(data_dir, args.division, args.since, args.year)
    cutoff = datetime.now() - timedelta(hours=args.max_age)
    todo = []
    for tid, name in sorted(ids.items()):
        jp = teams_dir / f"{tid}.json"
        if jp.exists() and args.max_age > 0:
            try:
                fetched = datetime.fromisoformat(json.loads(jp.read_text(encoding="utf-8"))["fetched_at"])
                if fetched > cutoff:
                    continue
            except (KeyError, ValueError):
                pass
        todo.append((tid, name))
    print(f"{len(ids)} {args.division} teams, {len(todo)} to fetch")
    if not todo:
        return 0

    from playwright.sync_api import sync_playwright
    ok = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.visible)
        page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
        for i, (tid, name) in enumerate(todo, 1):
            print(f"[{i}/{len(todo)}] {tid} {name} ...", end=" ", flush=True)
            try:
                html = open_statistics(page, tid)
                stats = parse_stats(html)
                if not stats["team"]:
                    print("no team page")
                else:
                    stats["team_page_id"] = tid
                    stats["fetched_at"] = datetime.now().isoformat(timespec="minutes")
                    (teams_dir / f"{tid}_stats.html").write_text(html, encoding="utf-8")
                    (teams_dir / f"{tid}.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
                    pts = sum(t["points"] for t in stats["tournaments"])
                    print(f"{stats['team']!r} {len(stats['tournaments'])} events, {pts} pts")
                    ok += 1
            except Exception as exc:
                print(f"FAILED: {exc}")
            if i < len(todo):
                time.sleep(PAUSE_S)
        browser.close()
    print(f"saved {ok}/{len(todo)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
