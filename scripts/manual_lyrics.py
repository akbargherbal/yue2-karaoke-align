#!/usr/bin/env python3
"""Turn reviewer-supplied lyric URLs into a corpus CSV.

When the review app recorded "the lyrics for this track are at <url>", neither
scrape had the song, so the track was still counted as *no lyrics*. This script
closes that gap while keeping the text out of git:

    index/lyrics/manual_sources.csv   queue: track_id, title, url   (committed)
    work/lyrics/manual/corpus.csv     extracted text + provenance   (gitignored)

The corpus CSV is the format `lyrics_coverage.py --corpus-csv` already accepts
(`artist,title,lyrics,source,source_url`), so the sheets join the index like any
other source, as `corpus:<source>`.

Fetching differs per host, which is why it is not one code path:

* **genius.com** serves the lyrics inside `data-lyrics-container` divs with
  `<br/>` line breaks, but only to a browser-shaped request. A bare curl gets a
  JS challenge page, so `--fetch` sends browser headers.
* **wneen.com** is an SPA: the raw HTML contains no lyrics. Use `--cdp` with a
  Chrome started as `chrome --remote-debugging-port=9222`, or drop a rendered
  dump (one line per lyric line) in `work/lyrics/manual/text/<slug>.txt`.

Line extraction keeps repeats: a chorus written twice is sung twice, and the
aligner needs the repeat. Only section tags (`[اللازمة]`) and page chrome are
dropped.

Usage
-----
    python scripts/manual_lyrics.py --fetch
    python scripts/manual_lyrics.py --cdp http://127.0.0.1:9222
    python scripts/manual_lyrics.py --dry-run      # parse nothing, just report
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

ARABIC = re.compile(r"[\u0621-\u064a\u0671-\u06d3]")
SECTION_TAG = re.compile(r"^\[[^\]]*\]$")
CHROME_LINES = {
    "You might also like", "Embed", "Read More", "Cancel",
    "كلمات اغاني", "وضع القراءة", "موضع القراءة",
}
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                   " (KHTML, like Gecko) Chrome/128.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}
COLUMNS = ["artist", "title", "lyrics", "source", "source_url", "year",
           "n_lines", "n_words", "method", "fetched_at"]


def slug_for(track_id: str) -> str:
    """`Fadl Shaker/10.Saharni El Shoq.2005/11.El-Hal.mp3` -> `10.Saharni_..-El-Hal`."""
    parts = Path(track_id).parts
    raw = "-".join(p.rsplit(".", 1)[0] if "." in p else p for p in parts)
    return re.sub(r"[^\w\-]+", "_", raw, flags=re.UNICODE).strip("_")[-60:]


def clean_lines(raw_lines: list[str]) -> list[str]:
    out = []
    for line in raw_lines:
        line = " ".join(line.replace("\u200f", " ").replace("\u200e", " ").split())
        # Section tags are bare text nodes in Genius' HTML, so "[المقطع الأول]"
        # arrives glued to the first lyric line rather than on its own line.
        line = re.sub(r"^(?:\[[^\]]*\]\s*)+", "", line).strip()
        if not line or SECTION_TAG.match(line) or line in CHROME_LINES:
            continue
        if not ARABIC.search(line):
            continue
        out.append(line)
    return out


class _LyricsContainer(HTMLParser):
    """Collect text of every `<div data-lyrics-container="true">`, <br/> -> line."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        # Genius wraps "N Contributors / <title> Lyrics / [Verse 1]" in a
        # LyricsHeader div *inside* the container, glued to the first line.
        self.skip_depth: int | None = None
        self.lines: list[str] = []
        self.buf: list[str] = []

    def _flush(self) -> None:
        line = "".join(self.buf)
        self.buf = []
        if line.strip():
            self.lines.append(line)

    def handle_starttag(self, tag, attrs):
        if self.depth == 0:
            if tag == "div" and dict(attrs).get("data-lyrics-container") == "true":
                self.depth = 1
            return
        if tag == "div":
            self.depth += 1
            # Only the outermost LyricsHeader counts: its children
            # (GroupContainer, TitleContainer) also carry the prefix.
            if self.skip_depth is None and "LyricsHeader" in (dict(attrs).get("class") or ""):
                self.skip_depth = self.depth
        elif tag == "br" and self.skip_depth is None:
            self._flush()

    def handle_endtag(self, tag):
        if self.depth and tag == "div":
            if self.skip_depth == self.depth:
                self.skip_depth = None
                self.buf = []          # drop whatever the header left behind
            self.depth -= 1
            if self.depth == 0:
                self._flush()

    def handle_data(self, data):
        if self.depth and self.skip_depth is None:
            self.buf.append(data)


def parse_genius_html(html: str) -> list[str]:
    parser = _LyricsContainer()
    parser.feed(html)
    if not parser.lines:
        raise ValueError("no data-lyrics-container found (challenge page?)")
    return clean_lines(parser.lines)


def fetch_genius(url: str, timeout: float = 30.0) -> tuple[list[str], str]:
    import requests

    res = requests.get(url, headers=BROWSER_HEADERS, timeout=timeout)
    if res.status_code != 200:
        raise ValueError(f"HTTP {res.status_code}")
    return parse_genius_html(res.text), "genius+requests"


# --- CDP (rendered fetch, for SPAs like wneen) ------------------------------

# Hosts whose lyrics block has a stable selector. Guessing by text density does
# not work on wneen: its "same artist / same composer" link lists are as dense
# as the lyrics, so the heuristic can pick a recommendation block.
HOST_SELECTORS = {
    "wneen.com": "#lyricsText",
}

# Fallback for an unknown host: the densest Arabic block that is not a wrapper.
_PICK_TEXT_JS = """
(() => {
  const AR = /[\\u0600-\\u06FF]/g;
  const count = (t) => (t.match(AR) || []).length;
  let best = '', bestN = 0;
  for (const el of document.querySelectorAll('div,section,article,main')) {
    const t = el.innerText || '';
    const n = count(t);
    if (n <= bestN) continue;
    let childMax = 0;
    for (const c of el.children) childMax = Math.max(childMax, count(c.innerText || ''));
    if (childMax > 0.9 * n) continue;   // wrapper, not the block itself
    bestN = n; best = t;
  }
  return best;
})()
"""


def _text_js(selector: str | None) -> str:
    if selector:
        return f"(() => {{ const el = document.querySelector({json.dumps(selector)}); return el ? el.innerText : ''; }})()"
    return _PICK_TEXT_JS


def fetch_cdp(http_base: str, url: str, selector: str | None = None,
              wait: float = 3.0) -> tuple[list[str], str]:
    import urllib.request

    import websocket  # websocket-client

    base = http_base.rstrip("/")
    targets = json.load(urllib.request.urlopen(base + "/json/list", timeout=10))
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        raise ValueError("no page target on the CDP endpoint")
    ws = websocket.create_connection(pages[0]["webSocketDebuggerUrl"], timeout=45)
    try:
        seq = 0

        def call(method: str, **params):
            nonlocal seq
            seq += 1
            ws.send(json.dumps({"id": seq, "method": method, "params": params}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == seq:
                    if "error" in msg:
                        raise ValueError(f"{method}: {msg['error']}")
                    return msg.get("result", {})

        call("Page.enable")
        call("Page.navigate", url=url)
        expr = _text_js(selector)
        text = ""
        # Poll for the block instead of sleeping a fixed time: an SPA may need a
        # moment, and one page load is all this ever asks of the site.
        deadline = time.time() + max(wait, 1.0) * 4
        while time.time() < deadline:
            time.sleep(0.5)
            res = call("Runtime.evaluate", expression=expr, returnByValue=True)
            text = (res.get("result") or {}).get("value") or ""
            if ARABIC.search(text):
                break
    finally:
        ws.close()
    lines = clean_lines(text.splitlines())
    if not lines:
        raise ValueError("rendered page produced no Arabic lines"
                         + (f" for {selector}" if selector else ""))
    return lines, "cdp+chrome" + (f" {selector}" if selector else " auto")


def load_queue(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if (r.get("url") or "").strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--queue", default="index/lyrics/manual_sources.csv")
    ap.add_argument("--out", default="work/lyrics/manual/corpus.csv")
    ap.add_argument("--text-dir", default="work/lyrics/manual/text",
                    help="rendered dumps: <slug>.txt, one lyric line per line")
    ap.add_argument("--fetch", action="store_true", help="fetch what can be fetched headless")
    ap.add_argument("--cdp", metavar="HTTP",
                    help="Chrome DevTools endpoint, e.g. http://127.0.0.1:9222")
    ap.add_argument("--selector",
                    help="CSS selector for the lyrics block on hosts that need "
                         "rendering (default: HOST_SELECTORS)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    queue = load_queue(Path(args.queue))
    if not queue:
        sys.exit(f"empty queue: {args.queue}")

    rows, pending, failed = [], [], []
    for item in queue:
        track_id = item["track_id"]
        title = (item.get("title_ar") or "").strip()
        artist = (item.get("artist") or track_id.split("/")[0]).strip()
        url = item["url"].strip()
        slug = slug_for(track_id)

        lines, method = None, ""
        dump = Path(args.text_dir) / f"{slug}.txt"
        try:
            if args.dry_run:
                pass
            elif dump.is_file():
                lines, method = clean_lines(dump.read_text(encoding="utf-8").splitlines()), "text-dump"
            elif "genius.com" in url and args.fetch:
                lines, method = fetch_genius(url)
            elif args.cdp:
                host = urlparse(url).netloc.replace("www.", "")
                lines, method = fetch_cdp(args.cdp, url,
                                          selector=args.selector or HOST_SELECTORS.get(host))
            else:
                pending.append((track_id, url, "needs --fetch (genius) or --cdp (SPA)"))
                continue
        except Exception as exc:  # one bad page never kills the batch
            failed.append((track_id, url, f"{type(exc).__name__}: {exc}"))
            continue

        if lines is None:
            pending.append((track_id, url, "dry run"))
            continue
        if not lines:
            failed.append((track_id, url, "no Arabic lines after cleaning"))
            continue

        rows.append({
            "artist": artist, "title": title, "lyrics": "\n".join(lines),
            # Short, stable label (the index shows corpus:<source>). Keeping the
            # domain would tie the label to the host, not to the lyric body.
            "source": urlparse(url).netloc.replace("www.", "").split(".")[0],
            "source_url": url, "year": "",
            "n_lines": len(lines), "n_words": sum(len(x.split()) for x in lines),
            "method": method, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        print(f"ok   {len(lines):>3} lines · {rows[-1]['n_words']:>3} words · {track_id}")

    if rows and not args.dry_run:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
        print(f"\nwrote {out}: {len(rows)} sheet(s) — no text in git, work/ is ignored")

    if pending:
        print(f"\npending {len(pending)}:", file=sys.stderr)
        for track_id, url, why in pending:
            print(f"  {track_id}\n    {url}\n    {why}", file=sys.stderr)
    if failed:
        print(f"\nfailed {len(failed)}:", file=sys.stderr)
        for track_id, url, why in failed:
            print(f"  {track_id}\n    {why}", file=sys.stderr)
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
