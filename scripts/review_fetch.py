#!/usr/bin/env python3
"""Prefetch the review queue's audio into the local cache.

The app already downloads each track on first view (and prefetches the next
two), so this is optional — it just removes the 2–5 s wait per card and makes
the review work offline once it has run.

Uses the same cache layout as ``review_app.AudioCache``, so the app picks the
files up with no extra step. Safe to re-run: cached tracks are skipped.

Usage::

    python scripts/review_fetch.py --items work/review/items.jsonl \
        --audio-dir work/review/audio --jobs 4
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_app import AudioCache  # noqa: E402  (same cache key as the app)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--items", default="work/review/items.jsonl")
    ap.add_argument("--audio-dir", default="work/review/audio")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="first N items only")
    args = ap.parse_args(argv)

    path = Path(args.items)
    if not path.exists():
        sys.exit(f"{path} missing; run scripts/review_build.py first")
    items = [json.loads(line) for line in
             path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        items = items[:args.limit]

    cache = AudioCache(Path(args.audio_dir))
    todo = [x for x in items if not cache.path(x).exists()]
    have = len(items) - len(todo)
    print(f"{len(items)} cards · {have} cached · {len(todo)} to fetch")

    failed: list[str] = []
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = {pool.submit(cache.ensure, item): item for item in todo}
        for future in as_completed(futures):
            item = futures[future]
            done += 1
            if future.result() is None:
                failed.append(item["file"])
                mark = "FAIL"
            else:
                mark = "ok"
            print(f"  [{done}/{len(todo)}] {mark} {item['artist']} · {item['file']}",
                  file=sys.stderr, flush=True)

    total = sum(cache.path(x).stat().st_size for x in items if cache.path(x).exists())
    print(f"cached: {len(items) - len(failed)}/{len(items)} · {total / 1e6:.0f} MB "
          f"in {args.audio_dir}")
    if failed:
        print(f"failed ({len(failed)}):", file=sys.stderr)
        for name in failed:
            print(f"  {name}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
