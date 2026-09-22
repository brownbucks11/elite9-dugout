"""
gc_stats.py - pull our team's season stats from GameChanger (web.gc.com) into data/gc/stats.json.

Runs on YOUR computer only, logged in as you. The login is kept in a browser profile under
local/gc-profile/ (gitignored) - no password is stored anywhere in the project, and the
GitHub bot never touches GameChanger.

First time
    python gc_stats.py --login --team-url "https://web.gc.com/teams/<id>/<slug>/season-stats"
        A browser window opens on web.gc.com. Log in (2FA and all), then come back to this
        window and press Enter. The team URL is remembered in local/gc.json.

After that (after each weekend)
    python gc_stats.py              # reads Batting/Pitching/Fielding x Standard/Advanced, writes data/gc/stats.json
    python gc_stats.py --visible    # watch it work
    git add data/gc && git commit -m "GC stats" && git push

Every page it reads is also saved under local/gc/*.html so the parser can be fixed if GC changes
their site. If a table can't be found, run with --visible and look at what the page shows.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
LOCAL = HERE / "local"
PROFILE = LOCAL / "gc-profile"
CONFIG = LOCAL / "gc.json"
DUMP = LOCAL / "gc"
OUT = HERE / "data" / "gc" / "stats.json"

CATEGORIES = ["Batting", "Pitching", "Fielding"]
VIEWS = ["Standard", "Advanced"]

EXTRACT_TABLES = """
() => {
  // Real <table>s first; fall back to ARIA grids (role=table/row/cell), which some React apps use.
  const clean = (t) => (t || "").replace(/\\s+/g, " ").trim();
  const out = [];
  document.querySelectorAll("table").forEach((tb) => {
    const rows = [...tb.querySelectorAll("tr")].map((tr) => [...tr.querySelectorAll("th,td")].map((c) => clean(c.textContent)));
    if (rows.length > 1 && rows[0].length > 3) out.push(rows);
  });
  if (!out.length) {
    document.querySelectorAll('[role="table"], [role="grid"]').forEach((tb) => {
      const rows = [...tb.querySelectorAll('[role="row"]')].map((r) => [...r.querySelectorAll('[role="cell"],[role="columnheader"],[role="gridcell"],[role="rowheader"]')].map((c) => clean(c.textContent)));
      if (rows.length > 1 && rows[0].length > 3) out.push(rows);
    });
  }
  return out;
}
"""


def num(s):
    """'.444' -> 0.444, '12' -> 12, '1.028' -> 1.028, '-' / '' -> None"""
    s = (s or "").strip()
    if s in ("", "-", "—"):
        return None
    try:
        return int(s) if re.fullmatch(r"-?\d+", s) else float(s)
    except ValueError:
        return s


def parse_player(cell):
    """'Caiden P, #11' -> ('Caiden P', 11)"""
    m = re.match(r"^(.*?)(?:,\s*#?(\d+))?$", cell.strip())
    return (m.group(1).strip(), int(m.group(2)) if m and m.group(2) else None) if m else (cell.strip(), None)


def table_to_records(rows):
    """Header row + data rows -> [{'player': ..., 'number': ..., 'GP': 2, ...}], team totals separate."""
    header = rows[0]
    recs, totals = [], None
    for r in rows[1:]:
        if len(r) < len(header) - 1 or not r[0]:
            continue
        name, number = parse_player(r[0])
        rec = {"player": name, "number": number}
        for h, v in zip(header[1:], r[1:]):
            if h:
                rec[h] = num(v)
        if re.match(r"^(team|totals?)\b", name, re.I):
            totals = rec
        else:
            recs.append(rec)
    return {"columns": header[1:], "players": recs, "totals": totals}


def click_tab(page, label):
    """Click the Batting/Pitching/Fielding or Standard/Advanced control by its visible text."""
    for loc in (page.get_by_role("button", name=label, exact=True), page.get_by_role("tab", name=label, exact=True),
                page.get_by_role("link", name=label, exact=True), page.get_by_text(label, exact=True)):
        try:
            if loc.count():
                loc.first.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


def wait_for_table(page, timeout=15000):
    end = time.time() + timeout / 1000
    while time.time() < end:
        tables = page.evaluate(EXTRACT_TABLES)
        if tables:
            return tables
        page.wait_for_timeout(400)
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true", help="open a visible browser to log in, then continue")
    ap.add_argument("--team-url", help="the team's season-stats page on web.gc.com (remembered)")
    ap.add_argument("--visible", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    LOCAL.mkdir(exist_ok=True)
    DUMP.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    if args.team_url:
        cfg["team_url"] = args.team_url
        CONFIG.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    url = cfg.get("team_url")
    if not url:
        print("Need the team's stats URL once:  python gc_stats.py --login --team-url https://web.gc.com/teams/.../season-stats")
        return 1

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(str(PROFILE), headless=not (args.visible or args.login),
                                                    viewport={"width": 1400, "height": 1000})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)

        if args.login or "login" in page.url or page.get_by_text("Log in", exact=False).count() and not page.get_by_text("Stats", exact=True).count():
            if not (args.visible or args.login):
                ctx.close()
                print("Not logged in. Run once with --login (a browser window will open).")
                return 1
            print("Log in to GameChanger in the browser window, make sure the team's Stats page is showing, then press Enter here.")
            input()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)

        (DUMP / "season-stats.html").write_text(page.content(), encoding="utf-8")
        head = re.sub(r"\s+", " ", page.locator("h1").first.text_content() or "").strip() if page.locator("h1").count() else ""
        result = {"team": head, "url": url, "fetched_at": datetime.now().isoformat(timespec="minutes"), "stats": {}}

        for cat in CATEGORIES:
            if not click_tab(page, cat):
                print(f"  could not find the {cat} tab")
                continue
            page.wait_for_timeout(800)
            result["stats"][cat.lower()] = {}
            for view in VIEWS:
                click_tab(page, view)
                page.wait_for_timeout(800)
                tables = wait_for_table(page)
                (DUMP / f"{cat.lower()}-{view.lower()}.html").write_text(page.content(), encoding="utf-8")
                if not tables:
                    print(f"  {cat}/{view}: no table found (page saved to local/gc/)")
                    continue
                parsed = table_to_records(max(tables, key=lambda t: len(t)))
                result["stats"][cat.lower()][view.lower()] = parsed
                print(f"  {cat}/{view}: {len(parsed['players'])} players, {len(parsed['columns'])} columns")
        ctx.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
