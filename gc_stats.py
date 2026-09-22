"""
gc_stats.py - pull our team's GameChanger data (web.gc.com) into data/gc/, per game.

Runs on YOUR computer only, logged in as you. The login is kept in a browser profile under
local/gc-profile/ (gitignored) - no password is stored anywhere in the project, and the
GitHub bot never touches GameChanger.

Recommended: attach to a real Chrome window (GameChanger's sign-in refuses automated browsers)
    1. Double-click start_gc_chrome.cmd  - opens a separate Chrome window (profile in local/chrome-gc)
    2. Sign in to web.gc.com in that window (once; it stays signed in)
    3. python gc_stats.py --cdp --team-url "https://web.gc.com/teams/<id>/<slug>"   (URL remembered)
Then after each weekend: start_gc_chrome.cmd (if not open), python gc_stats.py --cdp

Fallback: the script's own browser
    python gc_stats.py --login         # opens a Playwright browser to sign in (may spin on GC's login page)
    python gc_stats.py                 # later runs, headless

Outputs: data/gc/games.json (every game's box score), data/gc/stats.json (season tables), and
local/gc/ (page HTML + every API response, gitignored). --games-only / --season-only / --refresh.

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
      // pinned rows (the Team totals) restart their index at 1, so keep them apart from body rows
      const pinned = r.closest(".ag-floating-bottom, .ag-floating-top") ? "p" : "";
      const idx = pinned + r.getAttribute("aria-rowindex");
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
    const href = a.href.replace(new RegExp("/(box-score|recap|plays|videos|info)/?$"), "");
    if (!/\\/(games?|schedule)\\/[A-Za-z0-9_-]{6,}/.test(href) || seen.has(href)) return;
    seen.set(href, clean(a.innerText || a.textContent).slice(0, 160));
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
        for idx in sorted(g["rows"], key=lambda k: (k.startswith("p"), int(k.lstrip("p")))):
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


class ApiCapture:
    """Record JSON responses from GameChanger's API while the browser loads pages.
    The app fetches everything it shows as JSON, which is far more reliable than reading the grid."""

    def __init__(self, page):
        self.items = []
        page.on("response", self._on_response)

    def _on_response(self, resp):
        try:
            url = resp.url
            if "gc.com" not in url or "/api" not in url and "api." not in url:
                return
            ctype = resp.headers.get("content-type", "")
            if "json" not in ctype:
                return
            body = resp.json()
        except Exception:
            return
        path = re.sub(r"^https?://[^/]+", "", url)
        self.items.append({"url": url, "path": path, "status": resp.status, "json": body})

    def take(self):
        items, self.items = self.items, []
        return items

    @staticmethod
    def save(items, folder, stem):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{stem}.json").write_text(json.dumps(items, indent=1), encoding="utf-8")


def dismiss_popups(page):
    """GameChanger throws up 'Don't miss out! Follow team' style dialogs; close them so clicks land."""
    for _ in range(3):
        dialog = page.locator('[role="dialog"]')
        try:
            if not dialog.count() or not dialog.first.is_visible():
                return
        except Exception:
            return
        closed = False
        for label in ("Maybe later", "Not now", "No thanks", "Close", "Dismiss", "Got it", "×"):
            btn = dialog.first.get_by_role("button", name=re.compile(f"^{re.escape(label)}$", re.I))
            try:
                if btn.count():
                    btn.first.click(timeout=2000)
                    closed = True
                    break
            except Exception:
                continue
        if not closed:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
        page.wait_for_timeout(500)


def short_name(first, last):
    return f"{first} {last[0]}." if first and last else (first or last or "")


def structure_game(gid, details, boxscore, team_id, url=""):
    """One game from GameChanger's JSON: schedule/score details plus our box score.
    Opponent players are not kept - only their team totals."""
    game = {"id": gid, "url": url, "source": "gamechanger"}
    if details:
        opp = (details.get("opponent_team") or {}).get("name")
        game.update({
            "opponent": opp, "home_away": details.get("home_away"), "status": details.get("game_status"),
            "start": details.get("start_ts"), "end": details.get("end_ts"), "timezone": details.get("timezone"),
            "score": details.get("score"),
        })
        ls = details.get("line_score") or {}
        game["line_score"] = {
            "team": ls.get("team"), "opponent": ls.get("opponent_team"),
        }
    if boxscore and isinstance(boxscore, dict):
        ours = boxscore.get(team_id)
        opp_side = next((v for k, v in boxscore.items() if k != team_id), None)
        if ours:
            names = {pl["id"]: {"name": short_name(pl.get("first_name"), pl.get("last_name")),
                                "number": int(pl["number"]) if str(pl.get("number") or "").isdigit() else None} for pl in ours.get("players", [])}
            game["players"] = names
            game["box"] = {}
            for grp in ours.get("groups", []):
                extras = {}
                for e in grp.get("extra", []):
                    for st in e.get("stats", []):
                        extras.setdefault(st["player_id"], {})[e["stat_name"]] = st["value"]
                rows = []
                for st in grp.get("stats", []):
                    pid = st["player_id"]
                    row = {"player_id": pid, **names.get(pid, {"name": "?", "number": None}),
                           "positions": (st.get("player_text") or "").strip("() "), "primary": st.get("is_primary"),
                           **(st.get("stats") or {}), "extra": extras.get(pid, {})}
                    rows.append(row)
                game["box"][grp["category"]] = {"team": grp.get("team_stats"), "players": rows,
                                                "extra_names": [e["stat_name"] for e in grp.get("extra", [])]}
        if opp_side:
            game["opponent_box"] = {grp["category"]: grp.get("team_stats") for grp in opp_side.get("groups", [])}
    return game


def rebuild_from_captures(team_id, base=""):
    """Build data/gc/games.json from the raw API captures in local/gc/api/games/ (no fetching)."""
    out = {}
    for f in sorted((DUMP / "api" / "games").glob("*.json")):
        items = json.loads(f.read_text(encoding="utf-8"))
        details = next((i["json"] for i in items if "/game-stream-processing/" in i["path"] and "/details" in i["path"] and i["status"] == 200), None)
        box = next((i["json"] for i in items if i["path"].endswith("/boxscore") and i["status"] == 200), None)
        if details or box:
            out[f.stem] = structure_game(f.stem, details, box, team_id, f"{base}/schedule/{f.stem}/box-score" if base else "")
    return out


def parse_box_score(text, grids, tables):
    """Structure a box-score page: line score, teams, and the lineup/pitching grids per team.
    Grids arrive in page order: away lineup, away pitching, home lineup, home pitching."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    box = {"date": None, "status": None, "teams": [], "notes": {}}
    for l in lines:
        if re.match(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) [A-Z][a-z]{2} \d{1,2}, ", l):
            box["date"] = l
        elif l in ("Final", "In Progress", "Scheduled", "Postponed", "Canceled", "Cancelled") and not box["status"]:
            box["status"] = l
    # full team names sit just above the Recap/Box score tabs
    names = []
    if "Recap" in lines:
        i = lines.index("Recap")
        names = [l for l in lines[max(0, i - 2):i]]
    innings = next((t["rows"] for t in tables if t["rows"] and all(re.fullmatch(r"\d+", c) for c in t["rows"][0])), None)
    rhe = next((t["rows"] for t in tables if t["rows"] and t["rows"][0][:3] == ["R", "H", "E"]), None)
    for side in (0, 1):
        team = {"name": names[side] if len(names) == 2 else None, "home": side == 1}
        if innings and len(innings) > side + 1:
            team["innings"] = [None if c.upper() == "X" else num(c) for c in innings[side + 1]]
        if rhe and len(rhe) > side + 1:
            team["R"], team["H"], team["E"] = (num(c) for c in rhe[side + 1][:3])
        team["batting"] = grids[side * 2] if len(grids) > side * 2 else None
        team["pitching"] = grids[side * 2 + 1] if len(grids) > side * 2 + 1 else None
        for key in ("batting", "pitching"):
            g = team[key]
            if g:
                for p in g["players"]:
                    m = re.match(r"^(.*?)\s*#(\d+)\s*(?:\((.*?)\))?$", p["player"])
                    if m:
                        p["player"], p["number"], p["positions"] = m.group(1).strip(), int(m.group(2)), (m.group(3) or "").strip() or None
        box["teams"].append(team)
    for l in lines:
        m = re.match(r"^([A-Z0-9]{1,4}):\s*(.+)$", l)   # "HR: H Clare", "TB: H Clare 4, ..."
        if m and m.group(1) not in ("R", "H", "E"):
            box["notes"].setdefault(m.group(1), m.group(2))
    return box


def click_tab(page, label):
    dismiss_popups(page)
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


def logged_in(page, cap):
    """The team page is public; only the API's /me/... calls tell us whether we're signed in."""
    me = [i for i in cap.items if "/me/" in i["path"]]
    if me:
        return any(i["status"] == 200 for i in me)
    try:
        return page.get_by_role("link", name=re.compile(r"^sign in$", re.I)).count() == 0
    except Exception:
        return False


def season_stats(page, base, result, cap=None):
    if cap:
        cap.take()
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
    if cap:
        items = cap.take()
        ApiCapture.save(items, DUMP / "api", "season")
        result["_api"] = [{"path": i["path"], "status": i["status"]} for i in items]
        print(f"  season: captured {len(items)} API responses")


def games(page, base, known, refresh, cap=None):
    """Schedule -> each game page -> every grid/table on it (box score, batting, pitching, line score)."""
    if cap:
        cap.take()
    page.goto(base + "/schedule", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    (DUMP / "schedule.html").write_text(page.content(), encoding="utf-8")
    if cap:
        ApiCapture.save(cap.take(), DUMP / "api", "schedule")
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
            box_url = re.sub(r"/(box-score|recap|plays|videos|info)/?$", "", link["href"].rstrip("/")) + "/box-score"
            if cap:
                cap.take()
            page.goto(box_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            dismiss_popups(page)
            grids = read_grids_merged(page)
            tables = page.evaluate(READ_TABLES)
            text = page.evaluate("document.body.innerText")
            html = page.content()
            (DUMP / "games" / f"{gid}.html").write_text(html, encoding="utf-8")
            if "/login" in page.url or ("Sign In" in text and not grids):
                print("looks logged out - run with --login")
                break
            api = cap.take() if cap else []
            if api:
                ApiCapture.save(api, DUMP / "api" / "games", gid)
            details = next((i["json"] for i in api if "/game-stream-processing/" in i["path"] and "/details" in i["path"] and i["status"] == 200), None)
            boxj = next((i["json"] for i in api if i["path"].endswith("/boxscore") and i["status"] == 200), None)
            team_id = re.search(r"/teams/([A-Za-z0-9_-]+)", base).group(1)
            if details or boxj:
                rec = structure_game(gid, details, boxj, team_id, box_url)
            else:   # fall back to what the page shows
                box = parse_box_score(text, grids, tables)
                rec = {"id": gid, "url": box_url, "source": "page", "status": box["status"], "page_box": box}
            rec["fetched_at"] = datetime.now().isoformat(timespec="minutes")
            out[gid] = rec
            sc = rec.get("score") or {}
            print(f"{rec.get('status')} vs {rec.get('opponent')} {sc.get('team')}-{sc.get('opponent_team')} | box: {'yes' if rec.get('box') else 'no'}")
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
    ap.add_argument("--rebuild", action="store_true", help="rebuild data/gc/games.json from local/gc/api captures, no fetching")
    ap.add_argument("--cdp", nargs="?", const=9222, type=int, metavar="PORT",
                    help="attach to a Chrome started by start_gc_chrome.cmd (default port 9222) instead of launching a browser")
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

    if args.rebuild:
        team_id = re.search(r"/teams/([A-Za-z0-9_-]+)", base).group(1)
        games_out = rebuild_from_captures(team_id, base)
        (OUT_DIR / "games.json").write_text(json.dumps(games_out, indent=1), encoding="utf-8")
        print(f"rebuilt {OUT_DIR / 'games.json'} from captures: {len(games_out)} games")
        return 0

    from playwright.sync_api import sync_playwright
    season = {"team_url": base, "fetched_at": datetime.now().isoformat(timespec="minutes"), "stats": {}}
    gpath = OUT_DIR / "games.json"
    all_games = json.loads(gpath.read_text(encoding="utf-8")) if gpath.exists() else {}
    with sync_playwright() as pw:
        browser = None
        if args.cdp:
            try:
                browser = pw.chromium.connect_over_cdp(f"http://localhost:{args.cdp}")
            except Exception as exc:
                print(f"Could not attach to Chrome on port {args.cdp}: {exc}\nStart it with start_gc_chrome.cmd first.")
                return 1
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()
            page.set_viewport_size({"width": 1600, "height": 1000})
            args.visible = True     # it's the user's own window; prompts are allowed
        else:
            ctx = pw.chromium.launch_persistent_context(str(PROFILE), headless=not (args.visible or args.login),
                                                        viewport={"width": 1600, "height": 1000})
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
        cap = ApiCapture(page)
        try:
            page.goto(base, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            if args.login or not on_team_page(page) or not logged_in(page, cap):
                if not (args.visible or args.login):
                    print("Not signed in to GameChanger. Run once with --login (a browser window will open).")
                    return 1
                while True:
                    print("\nSign in to GameChanger in the browser window (the page will show your email at the top right),")
                    print("then press Enter here. Do NOT close the browser window - the script closes it when done.")
                    input()
                    cap.take()
                    page.goto(base, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(4000)
                    if logged_in(page, cap):
                        print("Signed in.")
                        break
                    print("Still not signed in - the page's own /me calls are being refused. Try again.")
            if not args.games_only:
                season_stats(page, base, season["stats"], cap)
            if not args.season_only:
                all_games = games(page, base, all_games, args.refresh, cap)
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
                if browser:
                    page.close()          # leave the user's Chrome running
                else:
                    ctx.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
