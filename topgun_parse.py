"""
topgun_parse.py - turn a saved Top Gun "Game Times & Scores" page into structured data.

Works on the HTML from playtopgunsports.com/GameTimesResults.aspx?trnid=NNNNN
(the file topgun_snapshot.py / topgun_fetch.py saves). Standard library only.

Usage
    python topgun_parse.py page.html                      # print every division as JSON
    python topgun_parse.py page.html --division 9U        # one division
    python topgun_parse.py page.html --team "Elite 9"     # that team's standings row + games
    python topgun_parse.py page.html --out data.json      # write JSON instead of printing

Output shape
    {
      "title": "...", "dates": "...", "url": "...",
      "complexes": {"Ferguson": {"name": "Ferguson Park", "address": "..."}, ...},
      "divisions": {
        "9U": {
          "notes": ["Modified Stealing"],
          "standings": [{"seed": 1, "team": "...", "location": "...", "won": 1, "lost": 1,
                         "runs_allowed": 15, "runs_scored": 24, "team_page_id": 36883}, ...],
          "sections": {
            "Pool Play":    [{"game": 1, "day": "Sat", "time": "9:00 AM", "field": "...",
                              "team_a": "...", "score_a": 3, "team_b": "...", "score_b": 15,
                              "seed_a": null, "seed_b": null}, ...],
            "Gold Bracket": [...], "Silver Bracket": [...]
          }
        }, ...
      }
    }
"""

import argparse
import json
import re
from urllib.parse import unquote_plus
import sys
from html.parser import HTMLParser
from pathlib import Path


class _Walker(HTMLParser):
    """Emits ('heading', size, text) and ('table', rows) events in document order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events = []
        self._td_style = None
        self._td_text = None
        self._table_depth = 0
        self._tables = []      # stack of tables being built
        self._row = None
        self._cell = None
        self._cell_links = None
        self._pending_br = False
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "table":
            self._table_depth += 1
            self._tables.append([])
        elif tag == "tr" and self._tables:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._cell_links = []
            self._td_style = a.get("style", "")
        elif tag == "a" and self._cell is not None:
            self._cell_links.append(a.get("href", ""))
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in ("td", "th") and self._cell is not None:
            text = "".join(self._cell)
            text = re.sub(r"[ \t\r\xa0]+", " ", text)
            text = "\n".join(s.strip() for s in text.split("\n")).strip()
            self._row.append({"text": text, "links": self._cell_links, "style": self._td_style or ""})
            self._cell = None
        elif tag == "tr" and self._row is not None:
            # Heading = a row with a single bold cell that carries a font size
            if len(self._row) == 1:
                c = self._row[0]
                m = re.search(r"font-size:(\d+)pt", c["style"])
                if m and "font-weight:bold" in c["style"] and c["text"]:
                    self.events.append(("heading", int(m.group(1)), c["text"]))
            if self._tables and self._row:
                self._tables[-1].append(self._row)
            self._row = None
        elif tag == "table" and self._tables:
            rows = self._tables.pop()
            self._table_depth -= 1
            if rows and len(rows[0]) >= 3:   # single-row tables too (a page with one complex)
                self.events.append(("table", rows))

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._cell is not None:
            self._cell.append(data)


def _int(s):
    s = (s or "").strip()
    try:
        return int(float(s))
    except ValueError:
        return None


def _split_seed(text):
    """'Seed #2\\nHooligans' -> (2, 'Hooligans'); 'Winner Game #1' -> (None, 'Winner Game #1')"""
    m = re.match(r"Seed #(\d+)\s*\n?\s*(.*)", text, re.S)
    if m:
        return int(m.group(1)), m.group(2).strip().replace("\n", " ")
    return None, text.replace("\n", " ").strip()


def _team_page_id(links):
    for href in links:
        m = re.search(r"p2=(\d+)", href)
        if m:
            return int(m.group(1))
    return None


def parse_html(html, url=""):
    w = _Walker()
    w.feed(html)

    out = {"title": w.title.strip(), "name": "", "dates": "", "url": url, "complexes": {}, "divisions": {}}
    m = re.search(r'id="[^"]*sectionTitleLabel">(.*?)</span>', html, re.S)
    if m:
        out["name"] = re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r'id="[^"]*sectionSubTitleLabel">(.*?)</span>', html, re.S)
    if m:
        out["dates"] = re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r"Last Updated\s*([\d/]+\s+[\d:]+\s*[AP]M)", html)
    if m:
        out["site_last_updated"] = m.group(1)
    division = None
    section = None

    for ev in w.events:
        if ev[0] == "heading":
            _, size, text = ev
            if size >= 18:
                # "9U : 9U" or "13U 90 : 13U 90"
                division = text.split(":")[0].strip()
                out["divisions"].setdefault(division, {"notes": [], "standings": [], "sections": {}})
                section = None
            elif size >= 12 and division:
                if re.search(r"bracket|pool|play|round|championship|semi|final", text, re.I):
                    section = text
                else:
                    out["divisions"][division]["notes"].append(text)
            continue

        _, rows = ev
        header = [c["text"].replace("\n", " ") for c in rows[0]]

        # Complex address table has no header; 3 cols: abbrev, name, address
        def _address(r):
            """Address text, or the one inside the row's directions link (maps.google.com/?daddr=...)."""
            address = r[2]["text"].strip()
            if re.search(r"\d", address):
                return address
            for href in (h for c in r for h in c.get("links", [])):
                m = re.search(r"daddr=([^&]+)", href)
                if m:
                    return unquote_plus(m.group(1)).strip()
            return ""

        if len(header) == 3 and not division and all(len(r) == 3 for r in rows):
            addrs = [_address(r) for r in rows]
            if all(addrs):
                for r, address in zip(rows, addrs):
                    out["complexes"][r[0]["text"]] = {"name": r[1]["text"], "address": address}
                continue

        if not division:
            continue
        div = out["divisions"][division]

        if header[:2] == ["Team #", "Team Name"]:
            for r in rows[1:]:
                c = [x["text"] for x in r]
                if len(c) < 7:
                    continue
                div["standings"].append({
                    "seed": _int(c[0]), "team": c[1], "location": c[2],
                    "won": _int(c[3]), "lost": _int(c[4]),
                    "runs_allowed": _int(c[5]), "runs_scored": _int(c[6]),
                    "team_page_id": _team_page_id(r[-1]["links"]),
                })
        elif header[:1] == ["Game #"]:
            games = []
            for r in rows[1:]:
                c = [x["text"] for x in r]
                if len(c) < 8:
                    continue
                sa, ta = _split_seed(c[4])
                sb, tb = _split_seed(c[6])
                games.append({
                    "game": _int(c[0]), "day": c[1], "time": c[2], "field": c[3],
                    "team_a": ta, "seed_a": sa, "score_a": _int(c[5]),
                    "team_b": tb, "seed_b": sb, "score_b": _int(c[7]),
                })
            div["sections"][section or "Games"] = games

    return out


def team_view(data, team):
    """Standings row + every game (any section, any division) mentioning the team."""
    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    t = norm(team)
    hit = lambda name: norm(name) == t          # exact, ignoring case/spacing/punctuation
    result = {"team": team, "tournament": data.get("name", data["title"]), "dates": data["dates"],
              "url": data.get("url", ""), "divisions": {}}
    for dname, div in data["divisions"].items():
        rows = [s for s in div["standings"] if hit(s["team"])]
        games = []
        for sec, gl in div["sections"].items():
            for g in gl:
                if hit(g["team_a"]) or hit(g["team_b"]):
                    games.append(dict(section=sec, **g))
        if rows or games:
            result["divisions"][dname] = {
                "standings": rows, "games": games, "notes": div["notes"],
                "all_standings": div["standings"], "brackets": {k: v for k, v in div["sections"].items() if "bracket" in k.lower()},
            }
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("html")
    ap.add_argument("--division")
    ap.add_argument("--team")
    ap.add_argument("--out")
    args = ap.parse_args()

    html = Path(args.html).read_text(encoding="utf-8", errors="replace")
    data = parse_html(html)
    if args.team:
        data = team_view(data, args.team)
    elif args.division:
        data = {"title": data["title"], "dates": data["dates"], "complexes": data["complexes"],
                "divisions": {k: v for k, v in data["divisions"].items() if k.upper() == args.division.upper()}}
    text = json.dumps(data, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    sys.exit(main())
