#!/usr/bin/env python3
"""A0 — extract the sung lyric lines from a YuE2/Suno caption file.

A `v3_arabmaqamrock_dataset/*.txt` caption is one block:

    arabmaqamrock <style sentence...> Mood: ...
    [Lyrics]
    [Intro | single clean guitar | ...]
    <diacritized lyric line>
    <diacritized lyric line>

    [Verse 1 | ...]
    ...

Everything before the lyric block is style conditioning, not sung text. This
script keeps the lyric lines **in sung order**, drops section tags and blank
lines, and leaves diacritics/word forms untouched.

Usage
-----
    python scripts/a0_lyrics.py caption.txt                 # -> lyric lines
    python scripts/a0_lyrics.py caption.txt --format words  # -> one word per line
    python scripts/a0_lyrics.py caption.txt --format json --out l.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys

LYRICS_MARKER = "[Lyrics]"
SECTION_TAG = re.compile(r"^\s*\[.*\]\s*$")
# Arabic words are whitespace-separated; harakat are combining marks (category
# Mn) that Python's `\w` does NOT match, so splitting on regex word-runs would
# shatter every diacritized word. Split on whitespace and strip edge punctuation.
EDGE_PUNCT = "….....,،؛:!?-–—\"'()[]{}«»<>"


def _leading_marker(text: str) -> int:
    for i, ln in enumerate(text.splitlines()):
        if ln.strip() == LYRICS_MARKER:
            return i + 1
    return 0


def lyric_lines(caption: str) -> list[str]:
    """Return the sung lyric lines, in order, tags and blanks removed."""
    lines = caption.splitlines()
    # Everything from the [Lyrics] marker onward is the lyric block; if the
    # marker is absent, fall back to the whole file (some captions omit it).
    start = _leading_marker(caption)
    out: list[str] = []
    for ln in lines[start:]:
        s = ln.strip()
        if not s or SECTION_TAG.match(s):
            continue
        out.append(s)
    return out


def words(lines: list[str]) -> list[str]:
    out: list[str] = []
    for ln in lines:
        for tok in ln.split():
            tok = tok.strip(EDGE_PUNCT)
            if tok:
                out.append(tok)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("caption", help="path to a caption .txt")
    ap.add_argument("--format", choices=("lines", "words", "json"), default="lines")
    ap.add_argument("--out", help="write here instead of stdout")
    args = ap.parse_args(argv)

    try:
        caption = open(args.caption, encoding="utf-8").read()
    except OSError as e:
        sys.exit(f"cannot read {args.caption}: {e}")

    lines = lyric_lines(caption)
    if not lines:
        sys.exit("no lyric lines found (is this a caption file?)")

    if args.format == "lines":
        text = "\n".join(lines) + "\n"
    elif args.format == "words":
        text = "\n".join(words(lines)) + "\n"
    else:
        text = json.dumps(
            {"lines": lines, "words": words(lines)}, ensure_ascii=False, indent=2
        ) + "\n"

    if args.out:
        open(args.out, "w", encoding="utf-8").write(text)
        n_words = len(words(lines))
        print(f"wrote {args.out}: {len(lines)} lines, {n_words} words", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
