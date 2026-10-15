#!/usr/bin/env python3
"""A0 — sanity-check word timings before you judge them by ear.

Reads the JSON from `a0_align.py` and reports the failure signatures that mean
the aligner lost the path, so a bad result is obvious before listening:

  * non-monotone starts / overlaps
  * zero-length words
  * large gaps (the aligner skipped a stretch)
  * a runaway tail (last word ends far from the audio end)
  * collapsed spans (one word swallowing a whole phrase)

This is a screen, not a verdict: a clean report can still sound wrong. Listen.

Usage
-----
    python scripts/a0_qa.py results/a0a.words.json
    python scripts/a0_qa.py results/a0a.words.json --gap 2.0 --verbose
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path


def qa(words: list[dict], duration: float | None, gap_s: float) -> tuple[dict, list[str]]:
    warns: list[str] = []
    if not words:
        return {"n": 0}, ["no words aligned"]

    starts = [w["start"] for w in words]
    ends = [w["end"] for w in words]
    durs = [e - s for s, e in zip(starts, ends)]

    overlaps = sum(1 for i in range(1, len(words)) if starts[i] < ends[i - 1])
    backwards = sum(1 for i in range(1, len(words)) if starts[i] < starts[i - 1])
    zero = sum(1 for d in durs if d <= 0.0)
    gaps = [starts[i] - ends[i - 1] for i in range(1, len(words))]
    big_gaps = [g for g in gaps if g > gap_s]

    m = {
        "n": len(words),
        "duration_s": duration,
        "first_start": round(starts[0], 3),
        "last_end": round(ends[-1], 3),
        "monotone_starts": backwards == 0,
        "overlaps": overlaps,
        "backwards_starts": backwards,
        "zero_length_words": zero,
        "max_gap_s": round(max(gaps), 3) if gaps else 0.0,
        "gaps_over_threshold": len(big_gaps),
        "word_dur_median_s": round(statistics.median(durs), 3),
        "word_dur_max_s": round(max(durs), 3),
        "words_per_min": round(len(words) / max(ends[-1], 1e-6) * 60.0, 1),
    }
    if duration:
        m["coverage"] = round(ends[-1] / duration, 3)
        if ends[-1] > duration + 1.0:
            warns.append(f"last word ends {ends[-1] - duration:.1f}s past the audio")
        if ends[-1] / duration < 0.6:
            warns.append(f"only {ends[-1] / duration:.0%} of the audio is covered "
                         "(runaway start or early collapse?)")

    if backwards:
        warns.append(f"{backwards} words start before the previous word "
                     "(non-monotone path -> aligner lost it)")
    if overlaps:
        warns.append(f"{overlaps} overlapping word spans")
    if zero:
        warns.append(f"{zero} zero-length words")
    if m["gaps_over_threshold"]:
        warns.append(f"{len(big_gaps)} gaps > {gap_s}s (max {m['max_gap_s']}s) "
                     "-- skipped stretches")
    if m["word_dur_max_s"] > 8.0:
        warns.append(f"longest single word spans {m['word_dur_max_s']}s "
                     "(melisma, or a collapsed phrase)")
    return m, warns


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("json", help="word-timings JSON from a0_align.py")
    ap.add_argument("--gap", type=float, default=2.0, help="gap threshold, seconds")
    ap.add_argument("--verbose", action="store_true", help="list flagged words")
    args = ap.parse_args(argv)

    data = json.loads(Path(args.json).read_text(encoding="utf-8"))
    words = data.get("words", [])
    m, warns = qa(words, data.get("duration_s"), args.gap)

    print(json.dumps(m, indent=2, ensure_ascii=False))
    if warns:
        print("\nWARNINGS:")
        for w in warns:
            print(f"  - {w}")
    else:
        print("\nno red flags (still listen before trusting it)")

    if args.verbose:
        for i, w in enumerate(words):
            d = w["end"] - w["start"]
            prev_end = words[i - 1]["end"] if i else w["start"]
            if d <= 0 or d > 8.0 or w["start"] - prev_end > args.gap or (
                i and w["start"] < words[i - 1]["start"]
            ):
                print(f"  [{i:>4}] {w['start']:8.3f}-{w['end']:8.3f} "
                      f"({d:5.2f}s) {w['word']}")
    return 0 if not warns else 2


if __name__ == "__main__":
    raise SystemExit(main())
