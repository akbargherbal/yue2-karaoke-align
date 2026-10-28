#!/usr/bin/env python3
"""Build the review-queue items for the title-map app.

Joins the committed title map (which rows are unresolved) with the excerpt sheet
(`work/lyrics/review/<status>.csv`, produced by `scripts/lyrics_review.py`) and
reconstructs each track's GCS URI. The result is one JSON object per card in
`work/review/items.jsonl`, which carries lyric excerpts and is therefore never
committed.

Audio existence is checked with a single recursive `gcloud storage ls`, so a
broken URI is caught here rather than at review time.

Usage::

    python scripts/lyrics_review.py --title-map work/lyrics/title_map.csv \
        --lyrics-root /path/to/arabic-music-lyrics/lyrics --out work/lyrics/review
    python scripts/review_build.py \
        --title-map work/lyrics/title_map.csv \
        --review-csv work/lyrics/review/ambiguous.csv \
        --out work/review/items.jsonl
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = "gs://akbar-december-2024-backup/DISCOGRAPHY"
ARTIST_DIR = {"Elissa": "ELISSA", "Fadl Shaker": "FADL_SHAKER"}


def gcs_objects(root: str) -> set[str]:
    proc = subprocess.run(["gcloud", "storage", "ls", "-r", root],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"gcloud storage ls failed for {root}:\n{proc.stderr.strip()}")
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def index_objects(objects: set[str]) -> dict[tuple[str, str, str], str]:
    """(artist_dir, album, file) -> real URI.

    The two artists are not laid out identically: Fadl Shaker's albums sit under
    an extra ``…Full Discography…/`` directory. Matching on the last two path
    components is therefore more reliable than constructing the URI.
    """
    out: dict[tuple[str, str, str], str] = {}
    for uri in objects:
        parts = uri.split("/")
        if len(parts) < 3:
            continue
        artist_dir = next((d for d in ARTIST_DIR.values() if f"/{d}/" in uri), None)
        if not artist_dir:
            continue
        out[(artist_dir, parts[-2], parts[-1])] = uri
    return out


def gcs_uri(item: dict, root: str = ROOT,
            index: dict[tuple[str, str, str], str] | None = None) -> str:
    """Resolve the object URI; fall back to constructing it from the fields."""
    artist_dir = ARTIST_DIR.get(item["artist"])
    if not artist_dir:
        raise SystemExit(f"unknown artist: {item['artist']!r}")
    if index:
        found = index.get((artist_dir, item["album"], item["file"]))
        if found:
            return found
    return f"{root}/{artist_dir}/{item['album']}/{item['file']}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--title-map", default="index/lyrics/title_map.csv",
                    help="committed map by default, so a fresh clone works")
    ap.add_argument("--review-csv", default="work/lyrics/review/ambiguous.csv",
                    help="optional excerpt sheet; without it cards show titles only")
    ap.add_argument("--out", default="work/review/items.jsonl")
    ap.add_argument("--status", default="ambiguous",
                    help="title_map decision value to queue")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--skip-check", action="store_true",
                    help="do not list GCS (no network)")
    ap.add_argument("--max-duration", type=float, default=360.0,
                    help="drop cards longer than this (0 = keep all); matches the "
                         "214-track stem scope")
    args = ap.parse_args(argv)

    with open(args.title_map, encoding="utf-8") as fh:
        wanted = {r["track_id"]: r for r in csv.DictReader(fh)
                  if r.get("decision") == args.status}
    if not wanted:
        sys.exit(f"no rows with decision={args.status!r} in {args.title_map}")

    excerpts: dict[str, dict] = {}
    if args.review_csv and Path(args.review_csv).exists():
        with open(args.review_csv, encoding="utf-8") as fh:
            excerpts = {r["track_id"]: r for r in csv.DictReader(fh)}
    else:
        print(f"note: {args.review_csv} not found; building cards without excerpts",
              file=sys.stderr)

    objects = set() if args.skip_check else gcs_objects(args.root)
    index = index_objects(objects)

    items, missing, dropped = [], [], 0
    for track_id, base in wanted.items():
        duration = float(base.get("duration_s") or 0)
        if args.max_duration > 0 and duration > args.max_duration:
            dropped += 1
            continue
        row = excerpts.get(track_id) or {}
        uri = gcs_uri(base, args.root, index)
        exists = True if args.skip_check else uri in objects
        if not exists:
            missing.append(uri)
        cands = [{
            "n": 1,
            "title": row.get("cand1_title") or base.get("candidate_1") or "",
            "source": row.get("cand1_source") or base.get("candidate_1_source") or "",
            "score": float(row.get("cand1_score") or base.get("candidate_1_score") or 0),
            "excerpt": row.get("cand1_excerpt") or "",
            "url": row.get("cand1_url") or "",
        }]
        if row.get("cand2_title") or base.get("candidate_2"):
            cands.append({
                "n": 2,
                "title": row.get("cand2_title") or base.get("candidate_2") or "",
                "source": row.get("cand2_source") or "",
                "score": float(row.get("cand2_score") or base.get("candidate_2_score") or 0),
                "excerpt": row.get("cand2_excerpt") or "",
                "url": row.get("cand2_url") or "",
            })
        items.append({
            "track_id": track_id,
            "artist": base["artist"],
            "album": base["album"],
            "file": base["file"],
            "duration_s": float(base["duration_s"] or 0),
            "gcs_uri": uri,
            "audio_exists": exists,
            "candidates": cands,
        })

    # Album order keeps context: adjacent cards are usually the same record.
    items.sort(key=lambda x: (x["artist"], x["album"], x["file"]))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for i, item in enumerate(items, 1):
            item["idx"] = i
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    by_artist: dict[str, int] = {}
    for item in items:
        by_artist[item["artist"]] = by_artist.get(item["artist"], 0) + 1
    print(f"wrote {out}: {len(items)} cards {by_artist}")
    if dropped:
        print(f"dropped {dropped} track(s) longer than {args.max_duration:g}s")
    if not excerpts:
        print("cards have no excerpt text; the app shows titles, scores and "
              "source links only (run scripts/lyrics_review.py to enrich)")
    if missing:
        print(f"WARNING: {len(missing)} audio object(s) not found in GCS:",
              file=sys.stderr)
        for uri in missing[:10]:
            print(f"  {uri}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
