#!/usr/bin/env python3
"""Stage the `ready` tracks for an A1 alignment run.

A1 consumes either a run directory or a `--songs` JSON of
``{name, audio, caption, stems_dir}``. This script builds exactly that from the
committed index, the public lyric sheets, and the GCS stem cache:

    work/ready-pack/
      pack.json                  songs list for `a1_align_run.py --songs`
      pack.csv                   slug -> track_id -> source_uri (provenance)
      lyrics/<slug>.txt          one sung line per line, tags removed
      stems/<slug>/vocals.flac   fetched from the cache (with --fetch)

Why not `--auto-songs`: that mode pairs `<name>.txt` with `<name>.mp3|wav` only,
and the cache stores FLAC. The `--songs` path accepts `stems_dir` directly, so
alignment runs on the cached vocal stem with no Demucs.

Usage::

    python scripts/ready_pack.py --lyrics-root /tmp/lyrics/lyrics \
        --out work/ready-pack --fetch
    python scripts/a1_align_run.py --songs work/ready-pack/pack.json \
        --data-dir work/ready-pack --skip-separation --no-player \
        --out work/srt/ready10
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from a0_lyrics import lyric_lines  # noqa: E402  (section-tag + blank removal)

ARABIC_RE = re.compile(r"[\u0621-\u064a\u0671-\u06d3]")
SLUG_RE = re.compile(r"[^\w.\- ]+", re.UNICODE)
DEFAULT_MANIFEST = "gs://akbar-december-2024-backup/STEMS/htdemucs/v1/manifest.jsonl"


def norm_title(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("\u0640", "")
    for src, dst in (("أإآٱ", "ا"), ("ى", "ي"), ("ة", "ه"), ("ؤ", "و"), ("ئ", "ي")):
        for ch in src:
            text = text.replace(ch, dst)
    text = "".join(c if c.isalnum() or c.isspace() else " " for c in text)
    return " ".join(text.split()).lower()


def gcs_glob_escape(uri: str) -> str:
    """gcloud treats [ * ? as wildcards; some track names contain brackets."""
    return uri.replace("[", "[[]").replace("*", "[*]").replace("?", "[?]")


def load_sheets(root: Path, corpus_csv: Path | None = None) -> dict[tuple[str, str], dict]:
    """(source, normalized title) -> sheet."""
    sheets: dict[tuple[str, str], dict] = {}
    for source in ("fnanen", "kalimataghani"):
        for path in sorted((root / source / "artists").glob("*/*_lyrics.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for song in payload.get("songs") or []:
                title = (song.get("title") or "").strip()
                if not title:
                    continue
                sheets[(source, norm_title(title))] = {
                    "title": title,
                    "text": song.get("lyrics") or "",
                    "url": song.get("url") or "",
                }
    # Reviewer-supplied sheets (scripts/manual_lyrics.py) live in a corpus CSV,
    # not in the scrape tree; the index labels them corpus:<source>.
    if corpus_csv and corpus_csv.is_file():
        with open(corpus_csv, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                title = (row.get("title") or "").strip()
                text = (row.get("lyrics") or "").strip()
                if not title or not text:
                    continue
                sheets[(f"corpus:{row.get('source', 'manual')}", norm_title(title))] = {
                    "title": title, "text": text, "url": row.get("source_url") or "",
                }
    return sheets


def load_manifest(uri: str) -> dict[str, dict]:
    """source_uri -> manifest row (with the vocals stem path)."""
    if uri.startswith("gs://"):
        proc = subprocess.run(["gcloud", "storage", "cat", uri],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            sys.exit(f"gcloud storage cat failed:\n{proc.stderr.strip()}")
        raw = proc.stdout
    else:
        raw = Path(uri).read_text(encoding="utf-8")
    rows = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("status") == "ok" and row.get("source_uri"):
            rows[row["source_uri"]] = row
    return rows


def slug_for(album: str, file: str) -> str:
    stem = Path(file).stem
    slug = SLUG_RE.sub("_", f"{album} - {stem}").strip("._ ")
    return re.sub(r"\s+", " ", slug)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--title-map", default="index/lyrics/title_map.csv")
    ap.add_argument("--readiness", default="index/lyrics/readiness.csv")
    ap.add_argument("--lyrics-root", required=True)
    ap.add_argument("--out", default="work/ready-pack")
    ap.add_argument("--corpus-csv", default="work/lyrics/manual/corpus.csv",
                    help="reviewer-supplied sheets (missing file is fine)")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST,
                    help="stem manifest URI or local JSONL")
    ap.add_argument("--fetch", action="store_true",
                    help="download vocals.flac for each track (skips cached)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", action="append", metavar="SUBSTR",
                    help="stage only tracks whose track_id contains SUBSTR "
                         "(repeatable); use it instead of --limit, which is "
                         "order-dependent")
    ap.add_argument("--status", default="ready",
                    help="readiness value to stage (default: ready)")
    args = ap.parse_args(argv)

    with open(args.title_map, encoding="utf-8") as fh:
        tracks = {r["track_id"]: r for r in csv.DictReader(fh)}
    with open(args.readiness, encoding="utf-8") as fh:
        ready = [r for r in csv.DictReader(fh) if r["readiness"] == args.status]
    if args.only:
        ready = [r for r in ready
                 if any(sub.lower() in r["track_id"].lower() for sub in args.only)]
        print(f"--only matched {len(ready)} track(s)", file=sys.stderr)
    if args.limit:
        ready = ready[:args.limit]
    if not ready:
        sys.exit(f"no tracks with readiness={args.status!r}")

    sheets = load_sheets(Path(args.lyrics_root), Path(args.corpus_csv))
    manifest = load_manifest(args.manifest)

    out = Path(args.out)
    (out / "lyrics").mkdir(parents=True, exist_ok=True)
    songs, prov, problems = [], [], []

    for row in ready:
        track = tracks.get(row["track_id"])
        if track is None:
            problems.append(f"{row['track_id']}: not in title map")
            continue
        sheet = sheets.get((track["lyric_source"], norm_title(track["lyric_title"])))
        if sheet is None:
            problems.append(f"{track['file']}: no sheet for "
                            f"{track['lyric_title']!r} ({track['lyric_source']})")
            continue
        lines = [ln for ln in lyric_lines(sheet["text"]) if ARABIC_RE.search(ln)]
        if not lines:
            problems.append(f"{track['file']}: sheet has no Arabic lines")
            continue

        entry = manifest.get(track.get("gcs_uri", ""))
        # the committed map has no gcs_uri column; find by album + file
        if entry is None:
            entry = next((r for r in manifest.values()
                          if r["album"] == track["album"] and r["file"] == track["file"]),
                         None)
        if entry is None:
            problems.append(f"{track['file']}: no cached stems in the manifest")
            continue

        slug = slug_for(track["album"], track["file"])
        txt = out / "lyrics" / f"{slug}.txt"
        txt.write_text("\n".join(lines) + "\n", encoding="utf-8")

        stem_dir = out / "stems" / slug
        stem = stem_dir / "vocals.flac"
        if args.fetch and not stem.exists():
            stem_dir.mkdir(parents=True, exist_ok=True)
            uri = entry["stems"]["vocals"]["uri"]
            print(f"fetch {slug}", file=sys.stderr, flush=True)
            proc = subprocess.run(["gcloud", "storage", "cp", gcs_glob_escape(uri),
                                   str(stem)], capture_output=True, text=True)
            if proc.returncode != 0:
                problems.append(f"{track['file']}: fetch failed: "
                                f"{proc.stderr.strip()[:120]}")
                continue

        songs.append({
            "name": slug,
            "audio": str(stem.resolve()),
            "caption": str(txt.resolve()),
            "stems_dir": str(stem.resolve()),
        })
        prov.append({
            "slug": slug, "track_id": track["track_id"], "artist": track["artist"],
            "album": track["album"], "file": track["file"],
            "lyric_title": track["lyric_title"], "lyric_source": track["lyric_source"],
            "n_lines": len(lines), "wpm": row.get("wpm", ""),
            "source_uri": entry["source_uri"], "vocals_uri": entry["stems"]["vocals"]["uri"],
        })

    if not songs:
        sys.exit("nothing staged:\n  " + "\n  ".join(problems))

    (out / "pack.json").write_text(json.dumps(songs, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
    with open(out / "pack.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(prov[0].keys()))
        w.writeheader()
        w.writerows(prov)

    print(f"staged {len(songs)} track(s) in {out}")
    for row in prov:
        print(f"  {row['n_lines']:>3} lines · {row['wpm']:>5} wpm · {row['file']}")
    if problems:
        print(f"\nskipped {len(problems)}:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
    missing = [s["name"] for s in songs if not Path(s["stems_dir"]).exists()]
    if missing:
        print(f"\n{len(missing)} stem(s) not downloaded yet; re-run with --fetch",
              file=sys.stderr)
    print("\nnext:")
    print(f"  python scripts/a1_align_run.py --songs {out}/pack.json \\\n"
          f"      --data-dir {out} --skip-separation --no-player \\\n"
          f"      --out work/srt/{args.status}{len(songs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
