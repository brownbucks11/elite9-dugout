# Elite 9 Dugout

Elite 9 (9U, Fall 2026, Rock Hill SC) — a parent-facing site for the Top Gun season.

- **Live site:** https://brownbucks11.github.io/elite9-dugout/
- **Repo:** https://github.com/brownbucks11/elite9-dugout (GitHub Pages serves `docs/`)
- Tabs: Tournaments · Results · Roster · Teams · Fields. Unlisted stats page: `…/#stats`.

Scores, schedules and brackets come from Top Gun's public sites; a GitHub Actions bot refreshes
them on a schedule. Box scores and player stats come from GameChanger and are pulled by you from
your own PC (see below).

---

## Routines

### Nothing to do most of the time
The bot (`.github/workflows/refresh.yml`) refreshes scores and rebuilds the site by itself:
- 7 AM, 1 PM, 7 PM Eastern every day (7 AM also refreshes every team's Top Gun points/finishes)
- hourly Thursday–Sunday
- **every 10 minutes on game day** (only while one of our tournaments is live)
- Monday and Thursday 8:30 AM it also discovers new Charlotte-area events for the Teams table
- on demand: GitHub → Actions → "Refresh scores and rebuild site" → Run workflow

### After a weekend: GameChanger stats (one click)
Double-click **`update_gc.cmd`**. It opens the GameChanger Chrome window if it isn't running,
reads new box scores and the season tables, and commits + pushes only if something changed.
The first time, sign in to web.gc.com in that Chrome window when it appears (it stays signed in).

Same thing by hand:
```
start_gc_chrome.cmd
python gc_stats.py --cdp
git add data/gc
git commit -m "GC stats"
git push
```

### A new tournament is booked
Add it to `upcoming.json` (Top Gun ID + dates + a short name), then:
```
git add upcoming.json
git commit -m "Add Oct 31 tournament"
git push
```
When the coach posts the final schedule, add `"official": true` to that event and push again —
the card flips from "Tentative" to "Official (per coach)".

### Roster change
Numbers and nicknames live in `roster.json`; players and their status come from Top Gun's Players
page automatically (7 AM run). `name_style` is `"initial"` (Lane S.) or `"full"`.
```
git add roster.json
git commit -m "Roster update"
git push
```

### Preview a change locally
```
python build_site.py                    # rebuild docs/data.js from data/
python build_site.py --today 2026-09-26 # see the page as of another date (live/scheduled states)
```
Then open `docs/index.html`. **Don't commit the generated files** (`docs/data.*`, `data/`,
`docs/ics/`, `elite9_schedule.md`) — the bot owns them and two fetches never match byte for byte.
Restore them before committing code:
```
git checkout -- docs/data.js docs/data.json data/schedules docs/ics
git add *.py docs/index.html upcoming.json roster.json
git commit -m "..."
git pull --rebase
git push
```

---

## First-time setup (Windows)
```
pip install -r requirements.txt
python -m playwright install chromium
```
Google Chrome must be installed for the GameChanger reader (`start_gc_chrome.cmd`).

---

## Scripts

| Script | What it does |
|---|---|
| `refresh.py` | The bot's job: fetch the pages that can still change, then rebuild. `--teams` also refreshes team points/finishes; `--discover` sweeps Top Gun's list for area events; `--no-fetch` just rebuilds. |
| `build_site.py` | Turns everything in `data/` into `docs/data.js` for the page. `--today YYYY-MM-DD` previews another date. |
| `gc_stats.py` | GameChanger reader (local only). `--cdp` attaches to the Chrome from `start_gc_chrome.cmd`; `--refresh` re-reads saved games; `--games-only` / `--season-only`; `--rebuild` rebuilds `data/gc/games.json` from the raw captures without fetching. |
| `update_gc.cmd` | One-click: Chrome window → `gc_stats.py --cdp` → commit + push if changed. |
| `start_gc_chrome.cmd` | Opens the separate Chrome window (profile in `local\chrome-gc`, gitignored) that the reader attaches to. |
| `gameday.py` | Prints `yes`/`no`: is one of our tournaments live today? Gates the 10-minute cron. |
| `topgun_fetch.py` | Fetches Top Gun schedule pages by ID into `data/` (`--ids 12518`, or `--from/--to/--city` to discover). |
| `topgun_teams.py` | Each team's Top Gun statistics page → `data/teams/<id>.json` (points, finishes). |
| `topgun_roster.py` | Our Top Gun Players page → `data/teams/<id>_players.json`. |
| `topgun_parse.py` | Parser for saved Top Gun schedule pages (stdlib only). |
| `topgun_records.py`, `topgun_opponents.py`, `topgun_snapshot.py` | Earlier command-line tools; still work, not used by the site. |

## Data files

| File / folder | Owner | Notes |
|---|---|---|
| `upcoming.json` | you | the season's tournaments (ID, dates, name, optional `"official": true`) |
| `roster.json` | you | jersey numbers, nicknames, `name_style` |
| `fields.json` (optional) | you | address overrides for a complex Top Gun leaves blank |
| `data/*.html`, `data/*.json` | bot | saved Top Gun schedule pages, re-parsed on every build |
| `data/teams/` | bot | team stats pages (JSON committed; HTML ignored) |
| `data/schedules/` | bot | every version of our schedule per event (change tracking) |
| `data/tracked.json` | bot | events found by discovery; `skip: true` = no 9U bracket |
| `data/tgs/`, `data/entries/` | bot | topgunstats.com: director notices; entry counts + our registration |
| `data/gc/` | you (via `update_gc.cmd`) | GameChanger games and season tables |
| `docs/` | bot (site) | `index.html` is the page (yours); `data.js`, `ics/` are generated |
| `local/` | you, gitignored | Chrome profiles, raw GameChanger captures, anything private |
| `rules/` | reference | Top Gun rules PDFs (the tie-breaker rule drives pool rankings) |

## How the sites fit together
- `topgunstats.com/tournaments?sport=1` — tournament list, director weather/general notices, "Who's Playing" entry counts (JSON API)
- `playtopgunsports.com/GameTimesResults.aspx?trnid=<ID>` — schedules, scores, standings, brackets (HTML, scraped)
- `playtopgunsports.com/TeamPage/…` — each team's points and finishes; our roster
- `web.gc.com` — GameChanger box scores and season stats (signed-in Chrome, JSON captured)

Top Gun's standings table is in entry order, not rank order; the page ranks pool play with Top
Gun's own tie-breaker (record → head-to-head for two-way ties → runs allowed → runs scored →
last-game run differential → coin flip), verified against the bracket seeds Top Gun prints.

## Event states
announced (no schedule yet) → scheduled (tentative; every change Top Gun makes is recorded and
shown on the card) → live (game day: Results, tagged LIVE, next game highlighted) → final (stays on
Tournaments with its finish, linking to Results). A `.ics` calendar of our games is written for
scheduled and live events.

## Hosting
GitHub Pages, branch `main`, folder `/docs`. Cron times in the workflow are UTC and set for EDT;
after Nov 1 the runs land an hour earlier than listed. If Top Gun ever blocks the GitHub runner,
`python refresh.py` locally and push.
