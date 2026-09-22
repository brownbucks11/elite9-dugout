"""
gc_stats.py - pull our team's GameChanger data (web.gc.com) into data/gc/, per game.

Runs on YOUR computer only, logged in as you. The login is kept in a browser profile under
local/gc-profile/ (gitignored) - no password is stored anywhere in the project, and the
GitHub bot never touches GameChanger.

First time
    python gc_stats.py --login --team-url "https://web.gc.com/teams/<id>/<slug>"
        A browser window opens on web.gc.com. Log in (2FA and all), then come back to this
        window and press Enter. The team URL is remembered in local/gc.json.

After that (after each weekend)
    python gc_stats.py                 # schedule + every game's box score -> data/gc/games.json,
                                       # season tables -> data/gc/stats.json
    python gc_stats.py --games-only    # skip the season tables
    python gc_stats.py --visible       # watch it work
    git add data/gc && git commit -m "GC stats" && git push

Every page it reads is also saved under local/gc/ (gitignored) so the parser can be adjusted
when GameChanger changes their site. Games already saved are re-read only if --refresh.
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

# Every table-like thing on the page: real <table>s, then ARIA grids. Each comes back as
# {"heading": nearest heading text above it, "rows": [[cell, ...], ...]}.
EXTRACT_TABLES = """
() => {
  const clean = (t) => (t || "").replace(/\\s+/g, " ").trim();
  const headingFor = (el) => {
    let n = el;
    for (let i = 0; i < 6 && n; i++) {
      let p = n.previousElementSibling;
      while (p) { if (/^H[1-6]$/.test(p.tagName) || p.querySelector("h1,h2,h3,h4,h5,h6")) { const h = /^H[1-6]$/.test(p.tagName) ? p : p.querySelector("h1,h2,h3,h4,h5,h6"); return clean(h.textContent); } p = p.previousElementSibling; }
      n = n.parentElement;
    }
    return "";
  };
  const out = [];
  document.querySelectorAll("table").forEach((tb) => {
    const rows = [...tb.querySelectorAll("tr")].map((tr) => [...tr.querySelectorAll("th,td")].map((c) => clean(c.textContent)));
    if (rows.length > 1 && rows[0].length > 1) out.push({ heading: headingFor(tb), rows });
  });
  if (!out.length) {
    document.querySelectorAll('[role="table"], [role="grid"]').forEach((tb) => {
      const rows = [...tb.querySelectorAll('[role="row"]')].map((r) => [...r.querySelectorAll('[role="cell"],[role="columnheader"],[role="gridcell"],[role="rowheader"]')].map((c) => clean(c.textContent)));
      if (rows.length > 1 && rows[0].length > 1) out.push({ heading: headingFor(tb), rows });
    });
  }
  return out;
}
"""

# Links on the schedule page that look like games, with the text of the row they sit in.
EXTRACT_GAME_LINKS = """
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
    m = re.match(r"^(.*?)(?:,\s*#?(\d+))?$", cell.strip())
    return (m.group(1).strip(), int(m.group(2)) if m and m.group(2) else None) if m else (cell.strip(), None)


def table_to_records(rows):
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
    for loc in (page.get_by_role("button", name=label, exact=True), page.get_by_role("tab", name=label, exact=True),
                page.get_by_role("link", name=label, exact=True), page.get_by_text(label, exact=True)):
        try:
            if loc.count():
                loc.first.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


def wait_for_tables(page, timeout=15000):
    end = time.time() + timeout / 1000
    while time.time() < end:
        tables = page.evaluate(EXTRACT_TABLES)
        if tables:
            return tables
        page.wait_for_timeout(400)
    return []


def logged_out(page):
    return "login" in page.url.lower() or page.get_by_role("button", name=re.compile("log in|sign in", re.I)).count() > 0


def season_stats(page, base, result):
    page.goto(base + "/season-stats", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    (DUMP / "season-stats.html").write_text(page.content(), encoding="utf-8")
    for cat in CATEGORIES:
        if not click_tab(page, cat):
            print(f"  season {cat}: tab not found")
            continue
        page.wait_for_timeout(800)
        result[cat.lower()] = {}
        for view in VIEWS:
            click_tab(page, view)
            page.wait_for_timeout(800)
            tables = wait_for_tables(page)
            (DUMP / f"season-{cat.lower()}-{view.lower()}.html").write_text(page.content(), encoding="utf-8")
            if not tables:
                print(f"  season {cat}/{view}: no table found")
                continue
            parsed = table_to_records(max(tables, key=lambda t: len(t["rows"]))["rows"])
            result[cat.lower()][view.lower()] = parsed
            print(f"  season {cat}/{view}: {len(parsed['players'])} players, {len(parsed['columns'])} columns")


def games(page, base, known, refresh):
    """Schedule -> each game page -> every table on it (box score, batting, pitching, line score)."""
    page.goto(base + "/schedule", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    (DUMP / "schedule.html").write_text(page.content(), encoding="utf-8")
    links = page.evaluate(EXTRACT_GAME_LINKS)
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
            page.wait_for_timeout(2500)
            # a game page may have its own sub-tabs; try to surface the box score
            for label in ("Box Score", "Box score", "Stats"):
                if click_tab(page, label):
                    page.wait_for_timeout(1200)
                    break
            tables = wait_for_tables(page, timeout=8000)
            html = page.content()
            (DUMP / "games" / f"{gid}.html").write_text(html, encoding="utf-8")
            title = re.sub(r"\s+", " ", page.title() or "").strip()
            heads = [re.sub(r"\s+", " ", h).strip() for h in page.locator("h1, h2").all_text_contents()][:6]
            out[gid] = {"id": gid, "url": link["href"], "schedule_text": link["text"], "title": title, "headings": heads,
                        "tables": tables, "fetched_at": datetime.now().isoformat(timespec="minutes")}
            print(f"{len(tables)} tables | {link['text'][:60]}")
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
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(str(PROFILE), headless=not (args.visible or args.login),
                                                    viewport={"width": 1400, "height": 1000})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(base, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        if args.login or logged_out(page):
            if not (args.visible or args.login):
                ctx.close()
                print("Not logged in. Run once with --login (a browser window will open).")
                return 1
            print("Log in to GameChanger in the browser window until you see the team page, then press Enter here.")
            input()
            page.goto(base, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)

        if not args.games_only:
            season = {"team_url": base, "fetched_at": datetime.now().isoformat(timespec="minutes"), "stats": {}}
            season_stats(page, base, season["stats"])
            (OUT_DIR / "stats.json").write_text(json.dumps(season, indent=1), encoding="utf-8")
            print(f"wrote {OUT_DIR / 'stats.json'}")
        if not args.season_only:
            gpath = OUT_DIR / "games.json"
            known = json.loads(gpath.read_text(encoding="utf-8")) if gpath.exists() else {}
            all_games = games(page, base, known, args.refresh)
            gpath.write_text(json.dumps(all_games, indent=1), encoding="utf-8")
            print(f"wrote {gpath} ({len(all_games)} games)")
        ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
