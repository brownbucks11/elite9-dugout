"""
tournament.py - update a tournament's status in upcoming.json and publish it.

    python tournament.py 12521 --confirmed        # coach confirmed we're playing  -> "Confirmed" tag
    python tournament.py 12521 --official         # coach posted the final schedule -> "Official (per coach)"
    python tournament.py 12521 --not-confirmed    # undo either flag
    python tournament.py 12521 --not-official
    python tournament.py --add 12530 "October 31 - November 1 in Greater Charlotte Area, NC" "Halloween Havoc"
    python tournament.py --remove 12530
    python tournament.py --list
    python tournament.py 12521 --confirmed --no-push   # edit only, don't commit/push

Each change is committed and pushed (unless --no-push); the site rebuilds itself in a couple of minutes.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
UPCOMING = HERE / "upcoming.json"


def load():
    return json.loads(UPCOMING.read_text(encoding="utf-8")) if UPCOMING.exists() else []


def save(items):
    UPCOMING.write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")


def publish(message):
    def git(*args):
        return subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True)
    git("add", "upcoming.json")
    if git("diff", "--cached", "--quiet").returncode == 0:
        print("nothing changed")
        return
    git("commit", "-q", "-m", message)
    git("pull", "--rebase", "-q")
    r = git("push", "-q")
    print("published" if r.returncode == 0 else f"push failed: {r.stderr.strip()} - run: git pull --rebase && git push")


def main():
    ap = argparse.ArgumentParser(description="Update a tournament's status in upcoming.json")
    ap.add_argument("id", nargs="?", type=int, help="Top Gun tournament ID")
    ap.add_argument("--confirmed", action="store_true", help="coach confirmed we're playing")
    ap.add_argument("--not-confirmed", action="store_true")
    ap.add_argument("--official", action="store_true", help="coach posted the final schedule")
    ap.add_argument("--not-official", action="store_true")
    ap.add_argument("--add", nargs=3, metavar=("ID", "DATES", "NAME"), help="add a tournament")
    ap.add_argument("--remove", type=int, metavar="ID")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()

    items = load()
    if args.list or not (args.id or args.add or args.remove):
        for u in items:
            flags = [k for k in ("confirmed", "official") if u.get(k)]
            print(f"  {u['id']}  {u.get('dates', ''):<48} {u.get('name', '')}  {'[' + ', '.join(flags) + ']' if flags else ''}")
        return 0

    msg = None
    if args.add:
        tid, dates, name = int(args.add[0]), args.add[1], args.add[2]
        if any(u["id"] == tid for u in items):
            print(f"{tid} is already listed")
            return 1
        items.append({"id": tid, "dates": dates, "name": name})
        items.sort(key=lambda u: u.get("dates", ""))
        msg = f"Add tournament {tid}: {name}"
    elif args.remove:
        items = [u for u in items if u["id"] != args.remove]
        msg = f"Remove tournament {args.remove}"
    else:
        u = next((x for x in items if x["id"] == args.id), None)
        if not u:
            print(f"{args.id} is not in upcoming.json (use --add ID DATES NAME)")
            return 1
        changes = []
        for flag, on, off in (("confirmed", args.confirmed, args.not_confirmed), ("official", args.official, args.not_official)):
            if on:
                u[flag] = True
                changes.append(flag)
            elif off:
                u.pop(flag, None)
                changes.append("not " + flag)
        if not changes:
            print("nothing to change - use --confirmed / --official (or --not-...)")
            return 1
        msg = f"Tournament {args.id}: {', '.join(changes)}"
    save(items)
    print(msg)
    if not args.no_push:
        publish(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
