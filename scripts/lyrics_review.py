#!/usr/bin/env python3
"""Build a human review sheet for unresolved title-map rows.

Reads ``work/lyrics/title_map.csv`` (or any committed copy) plus the
``arabic-music-lyrics`` per-artist scrapes, and writes a review sheet that puts
the two machine candidates, their first lyric lines and their source URLs next
to the audio identity.

The output contains lyric excerpts, so it belongs under ``work/`` and is never
committed. The only thing a human fills in is the identity decision:
``decision`` (match | no_match | ambiguous), ``lyric_title``, ``lyric_source``,
``notes``.

Usage::

    python scripts/lyrics_review.py \
        --title-map work/lyrics/title_map.csv \
        --lyrics-root /path/to/arabic-music-lyrics/lyrics \
        --out work/lyrics/review
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import unicodedata
from pathlib import Path

EXCERPT_LINES = 2
EXCERPT_CHARS = 110


def norm_title(text: str) -> str:
    """Fold Arabic orthography so candidate titles match scrape titles."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("ـ", "")
    for src, dst in (("أإآٱ", "ا"), ("ى", "ي"), ("ة", "ه"), ("ؤ", "و"), ("ئ", "ي")):
        for ch in src:
            text = text.replace(ch, dst)
    text = "".join(c if c.isalnum() or c.isspace() else " " for c in text)
    return " ".join(text.split()).lower()


def load_scrapes(root: Path) -> dict[tuple[str, str], dict]:
    """(artist_dir, normalized title) -> {source, title, url, excerpt, year}."""
    index: dict[tuple[str, str], dict] = {}
    for source in ("fnanen", "kalimataghani"):
        for path in sorted((root / source / "artists").glob("*/*_lyrics.json")):
            artist_dir = path.parent.name
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"skip {path}: {exc}", file=sys.stderr)
                continue
            songs = payload.get("songs") or []
            for song in songs:
                title = (song.get("title") or "").strip()
                if not title:
                    continue
                lines = [ln.strip() for ln in (song.get("lyrics") or "").splitlines()
                         if ln.strip()]
                excerpt = " / ".join(lines[:EXCERPT_LINES])[:EXCERPT_CHARS]
                key = (artist_dir, norm_title(title))
                # fnanen wins on ties: richer metadata, and it is the tier the
                # readiness pass already measured.
                if key in index and index[key]["source"] == "fnanen":
                    continue
                index[key] = {
                    "source": source,
                    "title": title,
                    "url": song.get("url") or "",
                    "excerpt": excerpt,
                    "year": song.get("year") or "",
                }
    return index


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--title-map", default="work/lyrics/title_map.csv")
    ap.add_argument("--lyrics-root", required=True)
    ap.add_argument("--out", default="work/lyrics/review")
    ap.add_argument("--status", default="ambiguous",
                    help="title_map decision value to export (default: ambiguous)")
    ap.add_argument("--artist", default=None, help="keep one artist only")
    args = ap.parse_args(argv)

    with open(args.title_map, encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("decision") == args.status]
    if args.artist:
        rows = [r for r in rows if r["artist"] == args.artist]
    if not rows:
        sys.exit(f"no rows with decision={args.status!r}")

    scrapes = load_scrapes(Path(args.lyrics_root))
    artist_dirs = {"Elissa": "elissa", "Fadl Shaker": "fadhl-shaker"}

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / f"{args.status}.csv"
    md_path = out / f"{args.status}.md"

    cols = ["idx", "artist", "album", "file", "duration_s",
            "cand1_title", "cand1_source", "cand1_score", "cand1_excerpt", "cand1_url",
            "cand2_title", "cand2_source", "cand2_score", "cand2_excerpt", "cand2_url",
            "decision", "lyric_title", "lyric_source", "notes"]

    enriched = []
    for i, row in enumerate(rows, 1):
        artist_dir = artist_dirs.get(row["artist"], "")

        def lookup(title: str) -> dict:
            if not title:
                return {}
            return scrapes.get((artist_dir, norm_title(title)), {})

        c1, c2 = lookup(row.get("candidate_1", "")), lookup(row.get("candidate_2", ""))
        enriched.append({
            "idx": i,
            "artist": row["artist"],
            "album": row.get("album", ""),
            "file": row.get("file", ""),
            "duration_s": row.get("duration_s", ""),
            "cand1_title": row.get("candidate_1", ""),
            "cand1_source": c1.get("source") or row.get("candidate_1_source", ""),
            "cand1_score": row.get("candidate_1_score", ""),
            "cand1_excerpt": c1.get("excerpt", "(not found in scrape)"),
            "cand1_url": c1.get("url", ""),
            "cand2_title": row.get("candidate_2", ""),
            "cand2_source": c2.get("source", ""),
            "cand2_score": row.get("candidate_2_score", ""),
            "cand2_excerpt": c2.get("excerpt", ""),
            "cand2_url": c2.get("url", ""),
            "decision": "", "lyric_title": "", "lyric_source": "", "notes": "",
        })

    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(enriched)

    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(f"# Title-map review — decision = `{args.status}`\n\n")
        fh.write("One question per row: **is candidate 1 the same song as this "
                 "audio file?**\n\n")
        fh.write("Write `match` / `no_match` / `ambiguous` into the `decision` "
                 "column of `review/%s.csv`, plus `lyric_title` and "
                 "`lyric_source` when you accept a candidate.\n\n" % args.status)
        current = None
        for row in enriched:
            if row["artist"] != current:
                current = row["artist"]
                fh.write(f"\n## {current}\n\n")
            fh.write(f"### {row['idx']}. {row['file']} — {row['duration_s']} s\n\n")
            fh.write(f"- album: `{row['album']}`\n")
            fh.write(f"- **1** · {row['cand1_score']} · {row['cand1_title']} "
                     f"({row['cand1_source']}) — {row['cand1_excerpt']}\n")
            if row["cand2_title"]:
                fh.write(f"- **2** · {row['cand2_score']} · {row['cand2_title']} "
                         f"({row['cand2_source'] or '?'}) — {row['cand2_excerpt']}\n")
            if row["cand1_url"]:
                fh.write(f"- source: {row['cand1_url']}\n")
            fh.write("\n")

    print(f"wrote {csv_path} ({len(enriched)} rows) and {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
