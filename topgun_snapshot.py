"""
topgun_snapshot.py - save copies of Top Gun schedule/bracket pages from your own PC.

What it does
    Opens topgunstats.com in a real Chromium window. You browse to the page you
    want (tournament, schedule, bracket, standings), press Enter in the terminal,
    and it saves that page as:
        - full-page screenshot (.png)
        - page text (.txt)
        - every HTML table as rows (.tables.json)
        - raw HTML (.html)
        - any JSON the page loaded behind the scenes (api/ folder)
    Everything lands in a timestamped folder and is zipped at the end, so you can
    drop one file into the chat.

Setup (one time)
    pip install playwright
    playwright install chromium

Usage
    python topgun_snapshot.py                      # interactive: browse, Enter = snapshot, q = quit
    python topgun_snapshot.py --batch URL [URL...] # no clicking: snapshot each URL and exit
    python topgun_snapshot.py --batch --headless URL

This is deliberately low-volume: one browser, one page at a time, pauses between
loads. It is meant for saving the handful of pages you would look at anyway.
"""

import argparse
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

DEFAULT_URL = "https://topgunstats.com/tournaments?sport=1"
PAUSE_BETWEEN_PAGES_S = 4


def slug(text, maxlen=70):
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    return text[:maxlen] or "page"


class ApiCapture:
    """Saves JSON responses the page fetches (often the cleanest source of game data)."""

    def __init__(self, outdir: Path):
        self.dir = outdir / "api"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest = []
        self.count = 0

    def on_response(self, response):
        try:
            if response.request.resource_type not in ("xhr", "fetch"):
                return
            if "json" not in response.headers.get("content-type", ""):
                return
            body = response.body()
        except Exception:
            return  # body unavailable (navigation, redirect, etc.) - skip quietly
        self.count += 1
        name = f"{self.count:03d}_{slug(urlparse(response.url).path)}.json"
        (self.dir / name).write_bytes(body)
        self.manifest.append(
            {"file": name, "url": response.url, "status": response.status, "bytes": len(body)}
        )

    def write_manifest(self):
        (self.dir / "_manifest.json").write_text(json.dumps(self.manifest, indent=2), encoding="utf-8")


TABLES_JS = """
() => Array.from(document.querySelectorAll('table')).map((t, i) => ({
    index: i,
    caption: (t.caption && t.caption.innerText.trim()) || null,
    rows: Array.from(t.rows).map(r =>
        Array.from(r.cells).map(c => c.innerText.replace(/\\s+/g, ' ').trim()))
}))
"""

SCROLL_JS = """
async () => {
    const step = 800;
    for (let y = 0; y < document.body.scrollHeight; y += step) {
        window.scrollTo(0, y);
        await new Promise(r => setTimeout(r, 250));
    }
    window.scrollTo(0, 0);
}
"""


def settle(page):
    """Wait for the page to finish loading and trigger any lazy-loaded content."""
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass
    try:
        page.evaluate(SCROLL_JS)
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass


def snapshot(page, outdir: Path, seq: int, label: str = ""):
    settle(page)
    title = page.title() or urlparse(page.url).path
    base = outdir / f"{seq:02d}_{slug(label or title)}"

    page.screenshot(path=f"{base}.png", full_page=True)
    Path(f"{base}.html").write_text(page.content(), encoding="utf-8")

    try:
        text = page.inner_text("body")
    except Exception:
        text = ""
    Path(f"{base}.txt").write_text(f"URL: {page.url}\nTITLE: {title}\n\n{text}", encoding="utf-8")

    try:
        tables = page.evaluate(TABLES_JS)
    except Exception:
        tables = []
    Path(f"{base}.tables.json").write_text(json.dumps(tables, indent=2), encoding="utf-8")

    print(f"  saved {base.name}  ({len(tables)} table(s), {len(text):,} chars of text)")
    print(f"  url: {page.url}")


def run_interactive(page, outdir, start_url):
    page.goto(start_url, wait_until="domcontentloaded")
    print("\nBrowser is open. Navigate to a page you want saved, then come back here.")
    print("  Enter            = snapshot the current page")
    print("  some text + Enter = snapshot, using that text as the file label (e.g. 'bracket')")
    print("  q + Enter        = finish\n")
    seq = 0
    while True:
        try:
            cmd = input("snapshot> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if cmd.lower() in ("q", "quit", "exit"):
            break
        # If a link opened a new tab, snapshot whichever tab is newest.
        pages = page.context.pages
        current = pages[-1] if pages else page
        seq += 1
        try:
            snapshot(current, outdir, seq, cmd)
        except Exception as exc:
            print(f"  snapshot failed: {exc}")


def run_batch(page, outdir, urls):
    for seq, url in enumerate(urls, start=1):
        print(f"\n[{seq}/{len(urls)}] {url}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            snapshot(page, outdir, seq)
        except Exception as exc:
            print(f"  failed: {exc}")
        if seq < len(urls):
            time.sleep(PAUSE_BETWEEN_PAGES_S)


def main():
    ap = argparse.ArgumentParser(description="Save Top Gun schedule pages for offline parsing.")
    ap.add_argument("urls", nargs="*", help="URLs (start page in interactive mode; all pages in --batch mode)")
    ap.add_argument("--batch", action="store_true", help="snapshot the given URLs without prompting")
    ap.add_argument("--headless", action="store_true", help="batch mode only: no visible window")
    ap.add_argument("--out", default="topgun_out", help="parent output folder (default: topgun_out)")
    args = ap.parse_args()

    urls = args.urls or [DEFAULT_URL]
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    outdir = Path(args.out) / stamp
    outdir.mkdir(parents=True, exist_ok=True)

    headless = args.batch and args.headless
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        capture = ApiCapture(outdir)
        context.on("response", capture.on_response)
        page = context.new_page()
        try:
            if args.batch:
                run_batch(page, outdir, urls)
            else:
                run_interactive(page, outdir, urls[0])
        finally:
            capture.write_manifest()
            context.close()
            browser.close()

    zip_path = shutil.make_archive(str(outdir), "zip", root_dir=outdir)
    print(f"\nDone. {capture.count} background JSON response(s) captured.")
    print(f"Folder: {outdir.resolve()}")
    print(f"Zip to upload: {Path(zip_path).resolve()}")


if __name__ == "__main__":
    sys.exit(main())
