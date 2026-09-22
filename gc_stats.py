"""
gc_stats.py - pull our team's GameChanger data (web.gc.com) into data/gc/, per game.

Runs on YOUR computer only, logged in as you. The login is kept in a browser profile under
local/gc-profile/ (gitignored) - no password is stored anywhere in the project, and the
GitHub bot never touches GameChanger.

First time
    python gc_stats.py --login --team-url "https://web.gc.com/teams/<id>/<slug>"
        A browser window opens on web.gc.com. Log in (2FA and all), then come back to this
        window and press Enter. LEAVE THE BROWSER WINDOW OPEN - the script drives it from here
        and closes it itself. The team URL is remembered in local/gc.json.

After that (after each weekend)
    python gc_stats.py                 # schedule + every game's box score -> data/gc/games.json,
                                       # season tables -> data/gc/stats.json
    python gc_stats.py --games-only    # skip the season tables
    python gc_stats.py --visible       # watch it work
    git add data/gc && git commit -m "GC stats" && git push

Every page it reads is also saved under local/gc/ (gitignored) so the parser can be adjusted
when GameChanger changes their site. Games already saved are re-read only with --refresh.
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
OUT_DIR = HERE / "data" / "gc"

CATEGORIES = ["Batting", "Pitching", "Fielding"]
VIEWS = ["Standard", "Advanced"]

# GameChanger's stats are AG Grid: a pinned "player" column in one container and the numbers
# in another, rows tied together by aria-rowindex, cells carrying col-id. Columns virtualize,
# so the grid is read at several horizontal scroll positions and merged.
READ_GRIDS = """
() => {
  const clean = (t) => (t || "").replace(/\\s+/g, " ").trim();
  return [...document.querySelectorAll('[role="grid"]')].map((grid) => {
    const headers = {};
    grid.querySelectorAll('[role="columnheader"][col-id]').forEach((h) => {
      const label = h.querySelector('[data-ref="eText"]');
      headers[h.getAttribute("col-id")] = clean(label ? label.textContent : h.textContent);
    });
    const rows = {};
    grid.querySelectorAll('[role="row"][aria-rowindex]').forEach((r) => {
      const idx = +r.getAttribute("aria-rowindex");
      r.querySelectorAll('[role="gridcell"][col-id]').forEach((c) => {
        (rows[idx] = rows[idx] || {})[c.getAttribute("col-id")] = clean(c.textContent);
      });
    });
    const vp = grid.querySelector(".ag-body-viewport, .ag-center-cols-viewport");
    return { headers, rows, colcount: +grid.getAttribute("aria-colcount") || 0, rowcount: +grid.getAttribute("aria-rowcount") || 0,
             scrollWidth: vp ? vp.scrollWidth : 0, clientWidth: vp ? vp.clientWidth : 0 };
  });
}
"""
SCROLL_GRIDS = """
(x) => { document.querySelectorAll('[role="grid"] .ag-center-cols-viewport, [role="grid"] .ag-body-viewport, .ag-body-horizontal-scroll-viewport')
           .forEach((v) => { v.scrollLeft = x; }); }
"""

# Anything table-like on a game page (box scores may not be AG Grid): real tables, then ARIA grids.
READ_TABLES = """
() => {
  const clean = (t) => (t || "").replace(/\\s+/g, " ").trim();
  const out = [];
  document.querySelectorAll("table").forEach((tb) => {
    const rows = [...tb.querySelectorAll("tr")].map((tr) => [...tr.querySelectorAll("th,td")].map((c) => clean(c.textContent)));
    if (rows.length > 1 && rows[0].length > 1) out.push({ kind: "table", rows });
  });
  return out;
}
"""
GAME_LINKS = """
() => {
  const clean = (t) => (t || "").replace(/\\s+/g, " ").trim();
  const seen = new Map();
  document.querySelectorAll("a[href]").forEach((a) => {
    const href = a.href;
    if (!/\\/(games?|schedule)\\/[A-Za-z0-9_-]{6,}/.test(href) || seen.has(href)) return;
    let row = a; for (let i = 0; i < 4 && row.parentElement && clean(row.textContent).length < 40; i++) row = row.parentElement;
    seen.set(href, clean(row.textContent).slice(0, 300));
  });
  return [...seen.entries()].map(([href, text]) => ({ href, text }));
}
"""


def num(s):
    s = (s or "").strip()
    if s in ("", "-", "—"):
        return None
    try:
        return int(s) if re.fullmatch(r"-?\d+", s) else float(s)
    except ValueError:
        return s


def parse_player(cell):
    m = re.match(r"^(.*?)(?:,\s*#?(\d+))?$", (cell or "").strip())
    return (m.group(1).strip(), int(m.group(2)) if m and m.group(2) else None) if m else (cell.strip(), None)


def read_grids_merged(page, settle_ms=500):
    """Read every AG Grid on the page at several horizontal scroll positions and merge."""
    merged = []
    first = page.evaluate(READ_GRIDS)
    if not first:
        return []
    width = max((g["scrollWidth"] for g in first), default=0)
    step = max(300, min((g["clientWidth"] for g in first if g["clientWidth"]), default=600) - 100)
    positions = [0] + list(range(step, width + step, step))
    grids = [{"headers": {}, "rows": {}} for _ in first]
    for x in positions:
        page.evaluate(SCROLL_GRIDS, x)
        page.wait_for_timeout(settle_ms)
        for i, g in enumerate(page.evaluate(READ_GRIDS)):
            if i >= len(grids):
                break
            grids[i]["headers"].update(g["headers"])
            for idx, cells in g["rows"].items():
                grids[i]["rows"].setdefault(idx, {}).update(cells)
    page.evaluate(SCROLL_GRIDS, 0)
    for g in grids:
        cols = [c for c in g["headers"] if c != "player"]
        players, totals = [], None
        for idx in sorted(g["rows"], key=int):
            cells = g["rows"][idx]
            name, number = parse_player(cells.get("player", ""))
            if not name:
                continue
            rec = {"player": name, "number": number}
            for c in cols:
                rec[g["headers"][c] or c] = num(cells.get(c))
            if re.match(r"^(team|totals?)\b", name, re.I):
                totals = rec
            else:
                players.append(rec)
        merged.append({"columns": [g["headers"][c] or c for c in cols], "players": players, "totals": totals})
    return merged


def click_tab(page, label):
    for loc in (page.get_by_role("tab", name=label, exact=True), page.get_by_role("button", name=label, exact=True),
                page.get_by_role("link", name=label, exact=True), page.get_by_text(label, exact=True)):
        try:
            if loc.count():
                loc.first.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


def on_team_page(page):
    try:
        return page.get_by_role("tab", name="Stats", exact=True).count() > 0 and "login" not in page.url.lower()
    except Exception:
        return False


def season_stats(page, base, result):
    page.goto(base + "/season-stats", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    (DUMP / "season-stats.html").write_text(page.content(), encoding="utf-8")
    for cat in CATEGORIES:
        if not click_tab(page, cat):
            print(f"  season {cat}: tab not found")
            continue
        page.wait_for_timeout(1000)
        result[cat.lower()] = {}
        for view in VIEWS:
            click_tab(page, view)
            page.wait_for_timeout(1200)
            grids = read_grids_merged(page)
            (DUMP / f"season-{cat.lower()}-{view.lower()}.html").write_text(page.content(), encoding="utf-8")
            if not grids or not grids[0]["players"]:
                print(f"  season {cat}/{view}: no grid found")
                continue
            best = max(grids, key=lambda g: len(g["players"]))
            result[cat.lower()][view.lower()] = best
            print(f"  season {cat}/{view}: {len(best['players'])} players, {len(best['columns'])} columns")


def games(page, base, known, refresh):
    """Schedule -> each game page -> every grid/table on it (box score, batting, pitching, line score)."""
    page.goto(base + "/schedule", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    (DUMP / "schedule.html").write_text(page.content(), encoding="utf-8")
    links = page.evaluate(GAME_LINKS)
    print(f"  schedule: {len(links)} game links")
    (DUMP / "games").mkdir(parents=True, exist_ok=True)
    out = dict(known)
    for i, link in enumerate(links, 1):
        gid = re.search(r"/(?:games?|schedule)/([A-Za-z0-9_-]{6,})", link["href"]).group(1)
        if gid in out and not refresh:
            continue
        print(f"  [{i}/{len(links)}] game {gid} ...", end=" ", flush=True)
        try:
            page.goto(link["href"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            for label in ("Box Score", "Box score", "Stats"):
                if click_tab(page, label):
                    page.wait_for_timeout(1500)
                    break
            grids = read_grids_merged(page)
            tables = page.evaluate(READ_TABLES)
            html = page.content()
            (DUMP / "games" / f"{gid}.html").write_text(html, encoding="utf-8")
            heads = [re.sub(r"\s+", " ", h).strip() for h in page.locator("h1, h2, h3").all_text_contents()][:12]
            out[gid] = {"id": gid, "url": link["href"], "schedule_text": link["text"], "title": re.sub(r"\s+", " ", page.title() or "").strip(),
                        "headings": heads, "grids": grids, "tables": tables, "fetched_at": datetime.now().isoformat(timespec="minutes")}
            print(f"{len(grids)} grids, {len(tables)} tables | {link['text'][:60]}")
        except Exception as exc:
            print(f"FAILED: {exc}")
        time.sleep(1.5)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true", help="open a visible browser to log in, then continue")
    ap.add_argument("--team-url", help="the team's page on web.gc.com, e.g. https://web.gc.com/teams/<id>/<slug> (remembered)")
    ap.add_argument("--visible", action="store_true")
    ap.add_argument("--games-only", action="store_true")
    ap.add_argument("--season-only", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="re-read games already saved")
    args = ap.parse_args()

    LOCAL.mkdir(exist_ok=True)
    DUMP.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    if args.team_url:
        cfg["team_url"] = re.sub(r"/(season-stats|schedule|stats|home|team)/?$", "", args.team_url.strip().rstrip("/"))
        CONFIG.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    base = cfg.get("team_url")
    if not base:
        print("Need the team URL once:  python gc_stats.py --login --team-url https://web.gc.com/teams/<id>/<slug>")
        return 1

    from playwright.sync_api import sync_playwright
    season = {"team_url": base, "fetched_at": datetime.now().isoformat(timespec="minutes"), "stats": {}}
    gpath = OUT_DIR / "games.json"
    all_games = json.loads(gpath.read_text(encoding="utf-8")) if gpath.exists() else {}
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(str(PROFILE), headless=not (args.visible or args.login),
                                                    viewport={"width": 1600, "height": 1000})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(base, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            if args.login or not on_team_page(page):
                if not (args.visible or args.login):
                    print("Not logged in (or the team page didn't load). Run once with --login.")
                    return 1
                print("Log in to GameChanger in the browser window until the team page shows, then press Enter here.")
                print("Leave the browser window open - the script closes it when done.")
                input()
                page.goto(base, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
            if not args.games_only:
                season_stats(page, base, season["stats"])
            if not args.season_only:
                all_games = games(page, base, all_games, args.refresh)
        except Exception as exc:
            print(f"stopped early: {exc}")
        finally:
            if season["stats"]:
                (OUT_DIR / "stats.json").write_text(json.dumps(season, indent=1), encoding="utf-8")
                print(f"wrote {OUT_DIR / 'stats.json'}")
            if all_games:
                gpath.write_text(json.dumps(all_games, indent=1), encoding="utf-8")
                print(f"wrote {gpath} ({len(all_games)} games)")
            try:
                ctx.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
