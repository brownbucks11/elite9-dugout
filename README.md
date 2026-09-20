# Baseball workspace

Elite 9 (9U, Fall 2026) - Top Gun tournament tracking.

## How the sites fit together
- topgunstats.com/tournaments?sport=1 ......... tournament list (loads JSON from /api/queries/tournaments/tournament-data)
- playtopgunsports.com/GameTimesResults.aspx?trnid=<ID> ... the actual schedule, scores, standings, brackets
- The <ID> is TournamentID from the list. Known fall 2026 Charlotte-area IDs:
    12518  Sep 26-27   Charlotte      Spiderman vs Hulk ring weekend
    13602  Sep 25      Monroe         Friday Night GOAT Series 9U
    12521  Oct 17-18   Charlotte      Triple points Super Regional
    12522  Oct 24-25   Charlotte      Southeastern Winter World Series
    12524  Nov 7-8     Charlotte      Road Runner vs Coyote ring weekend
    12517  Sep 19-20   Charlotte      (done) Super NIT weekend

## Files
- topgun_fetch.py    - MAIN: fetch schedule pages by ID, parse, write elite9_schedule.md
- topgun_parse.py    - parser (stdlib only); also usable alone on a saved .html
- topgun_snapshot.py - manual browse-and-save tool (first version; still handy for team pages)
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

    python build_site.py            # rebuild docs/data.js from data/
    python refresh.py               # re-fetch upcoming + in-progress events, then rebuild

Double-click `docs/index.html` to preview locally. `upcoming.json` is the list of events
shown under Next Up - add a tournament ID + dates there when a new one is booked.

## Hosting on GitHub Pages
1. Push this folder to a GitHub repo (public is simplest; Actions minutes are free there).
2. Repo Settings -> Pages -> Source: "Deploy from a branch", branch `main`, folder `/docs`.
3. The page is live at `https://<user>.github.io/<repo>/` within a minute or two.

## Automatic refresh (.github/workflows/refresh.yml)
GitHub Actions runs `refresh.py` and commits the results, so the page updates itself:
- three times a day (7 AM, 1 PM, 7 PM Eastern)
- hourly Thursday through Sunday
- Monday and Thursday 8:30 AM it also sweeps Top Gun's tournament list for upcoming Charlotte-area
  events (`refresh.py --discover`) and adds them to `data/tracked.json`; tracked events are
  re-fetched from a week before they start until 3 days after, so other 9U results land in
  Standings without anyone typing IDs. The list page only shows current/upcoming events, which is
  why it looks ahead rather than back.
- or on demand: Actions tab -> "Refresh scores and rebuild site" -> Run workflow

Cron times are in UTC in the file; they're set for EDT, so after Nov 1 they run an hour
earlier than listed. If Top Gun ever blocks the GitHub runner, run `python refresh.py`
locally instead (or schedule it with Windows Task Scheduler) and push.
