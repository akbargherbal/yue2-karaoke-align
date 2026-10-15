#!/usr/bin/env python3
"""A0 — compare two word-timing runs (e.g. two acoustic models on one track).

The best cheap evidence for alignment *quality* is **agreement**: two models
trained independently should put a word in the same place if the audio really
says it there. Disagreement localises where alignment is unreliable — no ground
truth needed.

Also reports each model's energy-tracking against the vocal stem, if given.

Usage
-----
    python scripts/a0_compare.py \
        --a results/a0a.words.json --b results/a0a.elgeish.words.json \
        --audio work/separated/htdemucs/<name>/vocals.wav
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from a0_qa import qa  # noqa: E402


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def energy_tracking(words: list[dict], audio: str) -> dict:
    """Word spans vs the vocal stem's energy — an independent check."""
    import numpy as np
    import soundfile as sf

    x, sr = sf.read(audio)
    if x.ndim > 1:
        x = x.mean(axis=1)
    x = x.astype("float32")

    def rms(a: float, b: float) -> float:
        i, j = int(a * sr), int(b * sr)
        return float(np.sqrt((x[i:j] ** 2).mean())) if j > i else 0.0

    hop = max(1, int(0.02 * sr))
    env = np.sqrt(np.convolve(x ** 2, np.ones(hop) / hop, "same"))[::hop]
    n = min(len(env), len(x) // hop)
    env = env[:n]
    lab = np.zeros(n, bool)
    for w in words:
        lab[int(w["start"] * sr // hop):int(w["end"] * sr // hop)] = True

    on = float(env[lab].mean()) if lab.any() else 0.0
    off = float(env[~lab].mean()) if (~lab).any() else 0.0
    gaps = [rms(words[i - 1]["end"], words[i]["start"])
            for i in range(1, len(words))
            if words[i]["start"] - words[i - 1]["end"] > 2.0]
    return {
        "on_word_env": round(on, 4),
        "off_word_env": round(off, 4),
        "on_off_ratio": round(on / off, 2) if off else None,
        "first_word_start": round(words[0]["start"], 2) if words else None,
        "inter_line_gap_env_median": round(statistics.median(gaps), 4) if gaps else None,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--audio", help="vocal stem, for energy tracking")
    ap.add_argument("--a-name", default="A")
    ap.add_argument("--b-name", default="B")
    args = ap.parse_args(argv)

    da, db = load(args.a), load(args.b)
    wa, wb = da["words"], db["words"]

    print(f"{args.a_name}: {da.get('model')}  translit={da.get('transliteration','?')}  n={len(wa)}")
    print(f"{args.b_name}: {db.get('model')}  translit={db.get('transliteration','?')}  n={len(wb)}")

    print("\n-- sanity (a0_qa) --")
    for name, d in ((args.a_name, da), (args.b_name, db)):
        m, warns = qa(d["words"], d.get("duration_s"), 2.0)
        print(f"  {name}: monotone={m.get('monotone_starts')} overlaps={m.get('overlaps')} "
              f"coverage={m.get('coverage')} med_dur={m.get('word_dur_median_s')} "
              f"warnings={len(warns)}")

    if len(wa) != len(wb):
        print(f"\n!! different word counts ({len(wa)} vs {len(wb)}) — "
              "not index-comparable; skipping agreement")
        return 1

    ds = [abs(a["start"] - b["start"]) for a, b in zip(wa, wb)]
    de = [abs(a["end"] - b["end"]) for a, b in zip(wa, wb)]
    worst = sorted(range(len(ds)), key=lambda i: -ds[i])[:5]

    print("\n-- agreement (same word index) --")
    print(f"  |dstart|: median {statistics.median(ds):.2f}s  mean {statistics.mean(ds):.2f}s  "
          f"p90 {sorted(ds)[int(0.9*len(ds))]:.2f}s  max {max(ds):.2f}s")
    print(f"  |dend|  : median {statistics.median(de):.2f}s  max {max(de):.2f}s")
    for thr in (0.3, 0.5, 1.0):
        print(f"  within {thr}s: {sum(1 for x in ds if x <= thr)/len(ds):.0%}")
    print("  worst disagreements:")
    for i in worst:
        print(f"    [{i:>3}] {wa[i]['word']:20} A={wa[i]['start']:7.2f} B={wb[i]['start']:7.2f} "
              f"(d={ds[i]:.2f}s)")

    if args.audio:
        print("\n-- energy tracking vs vocal stem --")
        for name, w in ((args.a_name, wa), (args.b_name, wb)):
            print(f"  {name}: {json.dumps(energy_tracking(w, args.audio), ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
