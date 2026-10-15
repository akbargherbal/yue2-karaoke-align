#!/usr/bin/env python3
"""Decide a winner between two aligned models from a bench_results.json.

Built for the two self-hosted mirrors (quran fine-tune vs its base), which are
the *same lineage* — so the usual cross-model consensus is useless with only two
models. This applies the BENCH-PLAN §7 rule using the checks that still work
with two models:

  1. hard gates   — load ok, >1 word/song (no collapse), monotone, coverage
  2. silent intro — word 0 must not land in the vocal stem's leading silence
  3. energy       — the on/off ratio (words should sit on the voice)
  4. agreement    — per-song |d first_word_start| between the two
  5. tie-break    — cheaper wins (sec/audio-min, then peak memory)

It prints a table and a verdict. If the two agree within one 20 ms CTC frame the
verdict is an explicit TIE (they are not separable at model resolution), and the
winner is reported as a cost tie-break, not an accuracy win.

Usage:
    .venv/bin/python scripts/duel_verdict.py --results results/duel/bench_results.json
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

FPS_FRAME_S = 0.02          # one 20 ms CTC frame
TIE_TOL_S = 2 * FPS_FRAME_S  # two frames (~40 ms) — below the ~100 ms a listener
                            # can perceive, so within this the two are the same


def lead_silence_s(stem: Path, win=1.0, hop=0.5, thresh=0.01, cap=90.0) -> float | None:
    """First 1 s window whose RMS >= thresh; None if the stem can't be read."""
    try:
        import numpy as np
        import soundfile as sf

        wav, sr = sf.read(str(stem), always_2d=True)
        mono = wav.mean(axis=1).astype("float32")
        n = int(win * sr)
        step = int(hop * sr)
        cap_n = min(len(mono), int(cap * sr))
        for start in range(0, cap_n - n + 1, step):
            seg = mono[start:start + n]
            if float(np.sqrt((seg.astype("float64") ** 2).mean())) >= thresh:
                return round(start / sr, 2)
        return round(cap_n / sr, 2)
    except Exception:  # noqa: BLE001
        return None


def stem_for(stems_dir: Path, song: str, audio_stem: str | None) -> Path:
    name = audio_stem or song
    return stems_dir / "htdemucs" / name / "vocals.wav"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", default="results/duel/bench_results.json")
    ap.add_argument("--stems-dir", default="work/stems",
                    help="root passed to bench --stems-dir (for the silent test)")
    args = ap.parse_args(argv)

    data = json.loads(Path(args.results).read_text(encoding="utf-8"))
    summary, rows = data["summary"], data["rows"]
    models = list(summary)
    if len(models) != 2:
        print(f"note: duel expects exactly 2 models, got {len(models)} — "
              "verdict below is still pairwise-ordered.", file=sys.stderr)

    by: dict[tuple[str, str], dict] = {}
    for r in rows:
        if r.get("ok"):
            by[(r["model"], r["song"])] = r
    songs = sorted({r["song"] for r in rows if r.get("ok")})

    stems_dir = Path(args.stems_dir)
    leads = {}
    for s in songs:
        stem = stem_for(stems_dir, s, None)
        leads[s] = lead_silence_s(stem) if stem.is_file() else None

    # ---- per-model table ---------------------------------------------------
    def agg(m: str) -> dict:
        rs = [by[(m, s)] for s in songs if (m, s) in by]
        nw = [r["n_words_aligned"] for r in rs]
        viol = 0
        for s in songs:
            r = by.get((m, s))
            if r and leads.get(s) is not None and r["first_word_start"] < leads[s]:
                viol += 1
        return {
            "model": m,
            "n_songs": len(rs),
            "collapsed": any(w <= 1 for w in nw),
            "coverage": st.mean(r["coverage"] for r in rs) if rs else 0.0,
            "mono": all(r["monotone"] for r in rs),
            "on_off": st.mean(r["on_off_ratio"] for r in rs) if rs else 0.0,
            "viol": viol,
            "viol_measurable": sum(1 for s in songs if leads.get(s) is not None),
            "sec_per_audio_min": summary[m].get("sec_per_audio_min"),
            "params_m": summary[m].get("params_m"),
            "mem": summary[m].get("model_rss_mb") or summary[m].get("peak_vram_mb"),
        }

    stats = {m: agg(m) for m in models}

    print(f"\nsongs ({len(songs)}):")
    for s in songs:
        lead = leads[s]
        print(f"  {s[:44]:46} lead-silence={'%.2fs' % lead if lead is not None else 'n/a'}")
    print()
    hdr = f"{'model':>44} {'songs':>5} {'cover':>6} {'on/off':>7} {'intro_viol':>10} {'s/min':>6} {'mem_MB':>7}"
    print(hdr)
    print("-" * len(hdr))
    for m in models:
        d = stats[m]
        viol = f"{d['viol']}/{d['viol_measurable']}" if d["viol_measurable"] else "n/a"
        print(f"{m:>44} {d['n_songs']:>5} {d['coverage']:>6.3f} {d['on_off']:>7.2f} "
              f"{viol:>10} {d['sec_per_audio_min'] or 0:>6.1f} {d['mem'] or 0:>7.0f}")

    # ---- agreement ---------------------------------------------------------
    diffs = []
    for s in songs:
        a, b = by.get((models[0], s)), by.get((models[1], s))
        if a and b:
            diffs.append(abs(a["first_word_start"] - b["first_word_start"]))
    mean_diff = st.mean(diffs) if diffs else float("nan")
    print(f"\nword-0 agreement between the two: mean |d| = {mean_diff:.3f}s "
          f"(max {max(diffs) if diffs else float('nan'):.3f}s, n={len(diffs)})")

    # ---- verdict -----------------------------------------------------------
    print("\nVERDICT")
    a, b = models
    da, db = stats[a], stats[b]
    disq = {a: [], b: []}
    for m, d in ((a, da), (b, db)):
        if d["collapsed"]:
            disq[m].append("collapsed (<=1 word/song)")
        if d["coverage"] < 0.9:
            disq[m].append(f"coverage {d['coverage']:.2f} < 0.9")
        if not d["mono"]:
            disq[m].append("non-monotone")
        if d["viol"] > 0:
            disq[m].append(f"{d['viol']} silent-intro violation(s)")

    if disq[a] and not disq[b]:
        print(f"  WINNER: {b} — {a} disqualified: {'; '.join(disq[a])}")
        return 0
    if disq[b] and not disq[a]:
        print(f"  WINNER: {a} — {b} disqualified: {'; '.join(disq[b])}")
        return 0
    if disq[a] and disq[b]:
        print(f"  BOTH disqualified: {a} ({'; '.join(disq[a])}); "
              f"{b} ({'; '.join(disq[b])})")
        return 1

    # energy divergence
    if max(da["on_off"], db["on_off"]) > 0:
        rel = abs(da["on_off"] - db["on_off"]) / max(da["on_off"], db["on_off"])
    else:
        rel = 0.0
    if rel > 0.10:
        w = a if da["on_off"] > db["on_off"] else b
        print(f"  WINNER (energy): {w} — on/off {max(da['on_off'], db['on_off']):.2f} "
              f"vs {min(da['on_off'], db['on_off']):.2f} ({rel:.0%} apart)")
        return 0

    if mean_diff <= TIE_TOL_S:
        cheap = a if (da["sec_per_audio_min"] or 9e9) < (db["sec_per_audio_min"] or 9e9) else b
        print(f"  TIE at model resolution: word-0 agreement {mean_diff:.3f}s "
              f"<= {int(TIE_TOL_S*1000)} ms; no accuracy winner.")
        print(f"  cost tie-break -> {cheap} "
              f"({min(da['sec_per_audio_min'] or 9e9, db['sec_per_audio_min'] or 9e9):.1f} "
              f"vs {max(da['sec_per_audio_min'] or 0, db['sec_per_audio_min'] or 0):.1f} s/audio-min).")
        print("  To break it for real: add gold anchors (hand-checked word starts).")
        return 0

    print(f"  INCONCLUSIVE: the two disagree by {mean_diff:.3f}s on word 0 with no "
          f"gold truth — add gold anchors to decide.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
