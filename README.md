# Baseball workspace

Elite 9 (9U, Fall 2026) - Top Gun tournament tracking.

## How the sites fit together
- topgunstats.com/tournaments?sport=1 ......... tournament list (loads JSON from /api/queries/tournaments/tournament-data)
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
`docs/index.html` is a static page for parents: Next Up, Results, Standings (all 9U teams,
tap one for its game log), Fields (with map links). It reads `docs/data.js`, which is built
from the saved pages in `data/`:

    python build_site.py            # rebuild docs/data.js from data/ (events since Aug 1, Top Gun's season)
    python refresh.py               # re-fetch upcoming + in-progress events, then rebuild
    python refresh.py --teams       # also refresh every team's statistics page (points, finishes)

Double-click `docs/index.html` to preview locally. `upcoming.json` is the list of events
shown under Next Up - add a tournament ID + dates there when a new one is booked.

## Hosting on GitHub Pages
1. Push this folder to a GitHub repo (public is simplest; Actions minutes are free there).
2. Repo Settings -> Pages -> Source: "Deploy from a branch", branch `main`, folder `/docs`.
3. The page is live at `https://<user>.github.io/<repo>/` within a minute or two.

## Automatic refresh (.github/workflows/refresh.yml)
GitHub Actions runs `refresh.py` and commits the results, so the page updates itself:
- three times a day (7 AM, 1 PM, 7 PM Eastern); the 7 AM run also refreshes every team's
  statistics page for the Standings points/finishes columns
- hourly Thursday through Sunday
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
