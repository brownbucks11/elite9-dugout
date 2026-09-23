# Baseball workspace

Elite 9 (9U, Fall 2026) - Top Gun tournament tracking.

## How the sites fit together
- topgunstats.com/tournaments?sport=1 ......... tournament list (loads JSON from /api/queries/tournaments/tournament-data)
- topgunstats.com/whos-playing/<ID> ........... entries per division, live (JSON: /api/public/tournaments/<ID>/whos-playing?api_key=secret);
                                                 saved by refresh.py to data/entries/<ID>.json. No schedules/scores on this site.
- playtopgunsports.com/GameTimesResults.aspx?trnid=<ID> ... the actual schedule, scores, standings, brackets
- The <ID> is TournamentID from the list. Known fall 2026 Charlotte-area IDs:
    12518  Sep 26-27   Charlotte      Spiderman vs Hulk ring weekend
    12521  Oct 17-18   Charlotte      Triple points Super Regional
    12522  Oct 24-25   Charlotte      Southeastern Winter World Series
    12524  Nov 7-8     Charlotte      Road Runner vs Coyote ring weekend
    12517  Sep 19-20   Charlotte      (done) Super NIT weekend
    13600  Sep 11      Monroe         (done) Friday Night GOAT Series 9U
  (per coach's Aug 16 post; upcoming.json is the live list)

## Files
- topgun_fetch.py    - MAIN: fetch schedule pages by ID, parse, write elite9_schedule.md
- topgun_parse.py    - parser (stdlib only); also usable alone on a saved .html
- topgun_snapshot.py - manual browse-and-save tool (first version; still handy for team pages)
- topgun_teams.py    - each team's Top Gun statistics page -> data/teams/<id>.json (points, finishes)
- topgun_roster.py   - our Players page -> data/teams/<id>_players.json (active players, season stats)
- roster.json        - jersey numbers + nicknames (hand-maintained); name_style: full | initial
- gc_stats.py        - LOCAL ONLY: reads our GameChanger season stats (web.gc.com, logged in as you)
                       into data/gc/stats.json; login kept in local/gc-profile/, never on the bot
- data/              - <ID>.html + <ID>.json per tournament, overwritten each run
- elite9_schedule.md - the summary Claude reads
- topgun_out/        - output from the manual snapshot tool

## First-time setup (Windows, in this folder)
    pip install -r requirements.txt
    python -m playwright install chromium

## Weekly run
    python topgun_fetch.py --ids 12518
    python topgun_fetch.py --ids 12518 12521 12522 12524 --show
Add --visible to watch the browser. Team defaults to "Elite 9" (--team to change).

## Web page (docs/)
`docs/index.html` is a static page for parents: Tournaments (the whole season, each event in
its current state), Results, Roster, Teams (all 9U teams, tap one for its game log), Fields (with
map links). It reads `docs/data.js`, which is built
from the saved pages in `data/`:

    python build_site.py            # rebuild docs/data.js from data/ (events since Aug 1, Top Gun's season)
    python refresh.py               # re-fetch upcoming + in-progress events, then rebuild
    python refresh.py --teams       # also refresh every team's statistics page (points, finishes)

Double-click `docs/index.html` to preview locally. `upcoming.json` is the list of events
shown under Tournaments - add a tournament ID + dates there when a new one is booked. Add
`"official": true` to an event once the coach posts the final schedule and its card flips from
"Tentative" to "Official (per coach)".

Each event moves through four states: announced (no schedule yet) -> scheduled (Tournaments;
tentative, with every change Top Gun makes recorded in `data/schedules/<id>.json` and shown on
the card) -> live (game day: Results, tagged LIVE, next game highlighted) -> final (stays on Tournaments
with its finish, linking to Results).
A `.ics` calendar file of our games is written to `docs/ics/<id>.ics` for scheduled and live
events. `python build_site.py --today 2026-09-26` previews the page as of another date.

## Hosting on GitHub Pages
1. Push this folder to a GitHub repo (public is simplest; Actions minutes are free there).
2. Repo Settings -> Pages -> Source: "Deploy from a branch", branch `main`, folder `/docs`.
3. The page is live at `https://<user>.github.io/<repo>/` within a minute or two.

## Automatic refresh (.github/workflows/refresh.yml)
GitHub Actions runs `refresh.py` and commits the results, so the page updates itself:
- three times a day (7 AM, 1 PM, 7 PM Eastern); the 7 AM run also refreshes every team's
  statistics page for the Standings points/finishes columns
- hourly Thursday through Sunday
- every 10 minutes on game day (a 10-minute cron runs Fri-Sun; `gameday.py` checks the committed
  data first and the run exits in seconds unless one of our tournaments is live that day). Quiet
  ticks that change nothing make no commit. GitHub can start scheduled runs a few minutes late,
  so expect a new score within 10-15 minutes of Top Gun posting it.
- Monday and Thursday 8:30 AM it also sweeps Top Gun's tournament list for upcoming Charlotte-area
  events (`refresh.py --discover`) and adds them to `data/tracked.json`; tracked events are
  re-fetched from a week before they start until 3 days after, so other 9U results land in
  Standings without anyone typing IDs. The list page only shows current/upcoming events, which is
  why it looks ahead rather than back. A schedule page covers every age group, so after the first
  fetch an event with no 9U bracket is marked `skip` in tracked.json and never fetched again; only
  the 9U division is ever built into the site.
- or on demand: Actions tab -> "Refresh scores and rebuild site" -> Run workflow

The bot owns the snapshot files (`data/`, `docs/data.js`, `docs/data.json`, `elite9_schedule.md`).
Running `refresh.py` locally is fine for previewing, but don't commit those files by hand - two
fetches of the same page never match byte-for-byte, so you'll hit merge conflicts. Commit only
code/config changes (`git add *.py docs/index.html upcoming.json`), `git pull --rebase`, push, and
let the next scheduled run (or the Run workflow button) refresh the data.

Cron times are in UTC in the file; they're set for EDT, so after Nov 1 they run an hour
earlier than listed. If Top Gun ever blocks the GitHub runner, run `python refresh.py`
locally instead (or schedule it with Windows Task Scheduler) and push.
