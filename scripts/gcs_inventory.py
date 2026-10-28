#!/usr/bin/env python3
"""Build the audio inventory for the Elissa / Fadl Shaker SRT pipeline.

L0 of `docs/CROSS-PROJECT.md`. Lists the real GCS objects, joins durations from
the caption JSONs (including the tracks the captioner skipped as too long), and
writes a CSV the coverage tool can consume:

    track_id,artist,album,file,gcs_uri,duration_s,instrumental

The GCS listing is authoritative; captions are only a metadata join. Nothing is
downloaded and no model is loaded.

Usage
-----
    python scripts/gcs_inventory.py \\
        --caption-root /path/to/music_captioning_oct_2026/outputs \\
        --out work/lyrics/inventory.csv

    # then
    python scripts/lyrics_coverage.py --inventory work/lyrics/inventory.csv \\
        --lyrics-root /path/to/arabic-music-lyrics/lyrics --out work/lyrics
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

ARTIST_DIRS = {"ELISSA": "Elissa", "FADL_SHAKER": "Fadl Shaker"}
SKIP_RE = re.compile(r"(\d+(?:\.\d+)?)s")


def gcs_list(uri: str) -> list[str]:
    proc = subprocess.run(["gcloud", "storage", "ls", "-r", uri],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"gcloud storage ls failed for {uri}:\n{proc.stderr.strip()}")
    return [line.strip() for line in proc.stdout.splitlines()
            if line.strip().endswith(".mp3")]


def load_durations(caption_root: Path) -> dict[tuple[str, str, str], float | None]:
    """(artist, album, file) -> duration, including skipped_long tracks."""
    out: dict[tuple[str, str, str], float | None] = {}
    for artist_dir, artist in ARTIST_DIRS.items():
        for path in sorted((caption_root / artist_dir).glob("*.json")):
            if path.parent.name == "abc":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            album = path.stem
            for track in data.get("tracks", []):
                out[(artist, album, track.get("file", ""))] = track.get("duration_s")
            for track in data.get("skipped_tracks", []):
                match = SKIP_RE.search(track.get("reason", ""))
                out[(artist, album, track.get("file", ""))] = (
                    float(match.group(1)) if match else None)
    return out


def parse_uri(uri: str) -> tuple[str, str, str] | None:
    parts = uri.split("/")
    if len(parts) < 4:
        return None
    artist_dir = next((d for d in ARTIST_DIRS if f"/{d}/" in uri), None)
    if artist_dir is None:
        return None
    return ARTIST_DIRS[artist_dir], parts[-2], parts[-1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root",
                    default="gs://akbar-december-2024-backup/DISCOGRAPHY",
                    help="GCS root containing ELISSA/ and FADL_SHAKER/")
    ap.add_argument("--caption-root", required=True,
                    help="music_captioning_oct_2026/outputs/")
    ap.add_argument("--out", default="work/lyrics/inventory.csv")
    ap.add_argument("--artists", nargs="+", default=sorted(ARTIST_DIRS),
                    metavar="DIR", help="GCS artist directories (default: both)")
    args = ap.parse_args(argv)

    durations = load_durations(Path(args.caption_root))
    rows: list[dict] = []
    for artist_dir in args.artists:
        if artist_dir not in ARTIST_DIRS:
            ap.error(f"unknown artist directory {artist_dir!r}; "
                     f"choose from {sorted(ARTIST_DIRS)}")
        for uri in gcs_list(f"{args.root.rstrip('/')}/{artist_dir}/"):
            parsed = parse_uri(uri)
            if parsed is None:
                continue
            artist, album, file = parsed
            rows.append({
                "track_id": f"{artist}/{album}/{file}",
                "artist": artist,
                "album": album,
                "file": file,
                "gcs_uri": uri,
                "duration_s": durations.get((artist, album, file), ""),
                "instrumental": "(music)" in file.lower(),
            })

    if not rows:
        sys.exit("no .mp3 objects found (check --root / --artists)")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    by_artist: dict[str, int] = {}
    for row in rows:
        by_artist[row["artist"]] = by_artist.get(row["artist"], 0) + 1
    missing = [r["track_id"] for r in rows if not r["duration_s"]]
    print(f"wrote {out}: {len(rows)} tracks {by_artist}")
    if missing:
        print(f"no duration for {len(missing)} track(s): {missing}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
