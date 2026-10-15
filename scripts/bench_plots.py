#!/usr/bin/env python3
"""Decision plots for the B2 alignment benchmark.

Reads results/bench/bench_results.json (summary + per-song rows) and writes a
set of PNGs under results/bench/plots/ that make the model choice legible:

  00_dashboard.png      -- 2x3 overview (start here)
  01_pareto.png         -- quality vs cost, bubble = params
  02_silent_intro.png   -- the decisive test: does word 0 wait for the voice?
  03_energy.png         -- on/off energy tracking (log axis)
  04_trap_metric.png    -- coverage (the trap) vs silent-intro violations
  05_lineage.png        -- rabah-quran vs its own base (jonatasgrosman)
  06_song_difficulty.png-- where the models disagree, by song

The lead-silence values for the silent-intro test are the measured numbers
recorded in results/BENCH-FINDINGS.md (vocal-stem, first 1 s window RMS>=0.01);
the stems for all eight songs are not checked into the repo, so they are
restated here rather than recomputed.

Usage:
    .venv/bin/python scripts/bench_plots.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import seaborn as sns  # noqa: E402

# --------------------------------------------------------------------------
# Model metadata: label, verdict, and lineage. All three survivors fine-tune
# the same wav2vec2-large-xlsr-53 backbone; rabah-quran is a fine-tune OF
# jonatasgrosman, so treat those two as one lineage.
# --------------------------------------------------------------------------
MODELS = {
    "rabah2026/wav2vec2-large-xlsr-53-arabic-quran-v_final": dict(
        label="rabah-quran", verdict="PICK", silent_viol=0, lineage="jonatasgrosman"),
    "jonatasgrosman/wav2vec2-large-xlsr-53-arabic": dict(
        label="jonatasgrosman", verdict="FALLBACK", silent_viol=0, lineage="jonatasgrosman"),
    "elgeish/wav2vec2-large-xlsr-53-arabic": dict(
        label="elgeish", verdict="REJECT", silent_viol=0, lineage="xlsr-53"),
    "facebook/mms-1b-all": dict(
        label="mms-1b-all", verdict="REJECT", silent_viol=8, lineage="mms"),
    "MostafaMaroof/wav2vec2-arabic-phoneme-asr": dict(
        label="phoneme-asr", verdict="REJECT", silent_viol=0, lineage="xlsr-53"),
    "IbrahimAmin/egyptian-arabic-wav2vec2-xlsr-53": dict(
        label="egyptian", verdict="REJECT", silent_viol=5, lineage="xlsr-53"),
}

COLOR = {"PICK": "#2ca02c", "FALLBACK": "#1f77b4", "REJECT": "#d62728"}
MARKER = {"PICK": "o", "FALLBACK": "s", "REJECT": "X"}

# short song key -> (display label, maqam, measured lead silence seconds)
SONGS = {
    "A_diction_mix_01": ("A01 Ajam", "Ajam", 2),
    "A_diction_mix_02": ("A02 Kurd", "Kurd", 14),
    "A_diction_mix_03": ("A03 Hijaz", "Hijaz", 2),
    "A_diction_mix_04": ("A04 Nahawand", "Nahawand", 1),
    "A_diction_mix_05": ("A05 Ajam", "Ajam", 10),
    "A_diction_mix_06": ("A06 Kurd", "Kurd", 9),
    "B_consonants_03": ("B03 Hijaz", "Hijaz", 8),
    "B_consonants_04": ("B04 Nahawand", "Nahawand", 26),
}


def song_key(name: str) -> str:
    for k in SONGS:
        if name.startswith(k):
            return k
    return name[:16]


def load(path: Path):
    data = json.loads(path.read_text())
    summary = data["summary"]
    rows = data["rows"]
    # per-(model,song) row, last wins (raw is append-only)
    by = {}
    for r in rows:
        by[(r["model"], r["song"])] = r
    song_full = {song_key(s): s for s in data["songs"]}
    return summary, by, song_full


def short(name: str) -> str:
    return MODELS.get(name, {}).get("label", name.split("/")[-1])


# The two survivors sit on top of each other (same lineage, ~10 ms apart), so
# nudge their callouts apart instead of letting them overprint.
LABEL_OFFSET = {
    "rabah-quran": (14, 12),
    "jonatasgrosman": (14, -30),
    "mms-1b-all": (12, 4),
}


# --------------------------------------------------------------------------
def fig_pareto(summary, out: Path):
    fig, ax = plt.subplots(figsize=(9, 6.5))
    xs, ys = [], []
    for m, s in summary.items():
        if s.get("consensus_dev_s") is None:
            continue  # egyptian: no consensus (broken output)
        x, y = s["sec_per_audio_min"], s["consensus_dev_s"]
        xs.append(x); ys.append(y)
        meta = MODELS[m]
        ax.scatter(x, y, s=meta and (60 + 12 * np.log1p(s["params_m"])),
                   color=COLOR[meta["verdict"]], marker=MARKER[meta["verdict"]],
                   edgecolor="black", linewidth=0.8, zorder=3)
        ax.annotate(f"{meta['label']}\n{y:.3f}s · {x:.0f}s/min",
                    (x, y), textcoords="offset points",
                    xytext=LABEL_OFFSET.get(meta["label"], (10, 8)),
                    fontsize=9)
    # top-cluster band (BENCH-PLAN tie-break window)
    best = min(ys)
    ax.axhspan(0, best + 0.05, color="#2ca02c", alpha=0.08, zorder=0)
    ax.text(0.98, best + 0.05, " tie window (±0.05 s)",
            transform=ax.get_yaxis_transform(), va="bottom", ha="right",
            fontsize=8, color="#2ca02c")
    # naive Pareto front (min cost & min consensus among plotted)
    pts = sorted(zip(xs, ys))
    front, besty = [], float("inf")
    for x, y in pts:
        if y < besty:
            front.append((x, y)); besty = y
    if len(front) > 1:
        ax.plot(*zip(*front), ls="--", lw=1, color="grey", alpha=0.8,
                zorder=1, label="Pareto front")
        ax.legend(loc="upper left", fontsize=8)
    ax.annotate("egyptian: broken output (1 word/song)\nno consensus — not plotted",
                xy=(0.02, 0.02), xycoords="axes fraction", fontsize=8,
                color="#7f7f7f")
    ax.set_xlabel("cost  →  seconds per audio-minute (CPU, lower better)")
    ax.set_ylabel("consensus deviation (s, lower better)")
    ax.set_title("Quality vs cost — bubble size = params\n"
                 "green = pick, blue = fallback, red = rejected", fontsize=11)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(out / "01_pareto.png", dpi=130)
    plt.close(fig)


def fig_silent_intro(by, song_full, out: Path):
    fig, ax = plt.subplots(figsize=(11, 6))
    keys = list(SONGS)
    ypos = np.arange(len(keys))[::-1]  # top = first song
    # grey band = measured leading silence (content before this is instrumental)
    for y, k in zip(ypos, keys):
        ax.barh(y, SONGS[k][2], height=0.75, color="#d9d9d9", zorder=0)
    order = ["jonatasgrosman/wav2vec2-large-xlsr-53-arabic",
             "rabah2026/wav2vec2-large-xlsr-53-arabic-quran-v_final",
             "elgeish/wav2vec2-large-xlsr-53-arabic",
             "MostafaMaroof/wav2vec2-arabic-phoneme-asr",
             "facebook/mms-1b-all",
             "IbrahimAmin/egyptian-arabic-wav2vec2-xlsr-53"]
    pal = sns.color_palette("tab10", len(order))
    for i, m in enumerate(order):
        xs = [by[(m, song_full[k])]["first_word_start"] for k in keys]
        ax.scatter(xs, ypos, color=pal[i], s=55, marker=MARKER[MODELS[m]["verdict"]],
                   edgecolor="black", linewidth=0.6, zorder=3,
                   label=f"{MODELS[m]['label']}  ({MODELS[m]['silent_viol']}/8)")
    ax.set_yticks(ypos)
    ax.set_yticklabels([f"{SONGS[k][0]}  (sil {SONGS[k][2]}s)" for k in keys])
    ax.set_xlabel("time (s) where the model placed word 0")
    ax.set_title("The decisive test — did word 0 wait for the voice?\n"
                 "grey = measured instrumental intro; a marker inside grey is a violation",
                 fontsize=11)
    ax.legend(fontsize=8, ncol=3, loc="upper center",
              bbox_to_anchor=(0.5, -0.12), title="silent-intro violations (/8)")
    ax.grid(alpha=0.25, axis="x")
    fig.tight_layout()
    fig.savefig(out / "02_silent_intro.png", dpi=130)
    plt.close(fig)


def fig_energy(summary, out: Path):
    items = [(m, s) for m, s in summary.items() if s.get("on_off") is not None]
    items.sort(key=lambda kv: kv[1]["on_off"])
    labels = [short(m) for m, _ in items]
    vals = [s["on_off"] for _, s in items]
    cols = [COLOR[MODELS[m]["verdict"]] for m, _ in items]
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.barh(labels, vals, color=cols, edgecolor="black", linewidth=0.6)
    ax.axvline(1.0, color="black", ls="--", lw=1.2)
    ax.text(1.0, -0.6, " on/off = 1: spans no more energetic than gaps",
            fontsize=8, color="black")
    for i, v in enumerate(vals):
        ax.text(v * 1.08, i, f"{v:.2f}", va="center", fontsize=8)
    ax.set_xscale("log")
    ax.set_xlabel("on/off energy ratio (log) — words should sit ON the voice (>1)")
    ax.set_title("Energy tracking — does each word span actually cover vocal energy?",
                 fontsize=11)
    ax.grid(alpha=0.25, axis="x")
    fig.tight_layout()
    fig.savefig(out / "03_energy.png", dpi=130)
    plt.close(fig)


def fig_trap(summary, out: Path):
    models = [m for m in MODELS]
    models = [m for m in models if summary.get(m)]
    labels = [short(m) for m in models]
    cov = [summary[m]["coverage"] for m in models]
    viol = [MODELS[m]["silent_viol"] for m in models]
    y = np.arange(len(models))
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 5), sharey=True)
    a1.barh(y, cov, color="#8c8c8c", edgecolor="black", linewidth=0.6)
    a1.axvline(0.9, color="red", ls="--", lw=1)
    a1.set_yticks(y); a1.set_yticklabels(labels)
    a1.invert_yaxis()
    a1.set_xlim(0.8, 1.02)
    a1.set_xlabel("coverage (higher looks better)")
    a1.set_title("The metric that looks decisive…", fontsize=10)
    for i, v in enumerate(cov):
        a1.text(v + 0.003, i, f"{v:.3f}", va="center", fontsize=8)
    a2.barh(y, viol, color=["#2ca02c" if v == 0 else "#d62728" for v in viol],
            edgecolor="black", linewidth=0.6)
    a2.set_xlabel("silent-intro violations (/8, lower better)")
    a2.set_title("…vs the one that actually is", fontsize=10)
    for i, v in enumerate(viol):
        a2.text(v + 0.1, i, str(v), va="center", fontsize=8)
    fig.suptitle("MMS-1b has the BEST coverage and the WORST intro behaviour",
                 fontsize=12)
    fig.tight_layout()
    fig.savefig(out / "04_trap_metric.png", dpi=130)
    plt.close(fig)


def fig_lineage(by, song_full, out: Path):
    rab = "rabah2026/wav2vec2-large-xlsr-53-arabic-quran-v_final"
    jona = "jonatasgrosman/wav2vec2-large-xlsr-53-arabic"
    keys = list(SONGS)
    d = [abs(by[(rab, song_full[k])]["first_word_start"]
             - by[(jona, song_full[k])]["first_word_start"]) for k in keys]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.bar([SONGS[k][0] for k in keys], d, color="#9467bd",
           edgecolor="black", linewidth=0.6)
    ax.axhline(0.02, color="grey", ls="--", lw=1)
    ax.text(0.05, 0.0205, "one 20 ms CTC frame", ha="left",
            fontsize=8, color="grey")
    ax.set_ylabel("|word-0 start:  rabah-quran − its base|  (s)")
    ax.set_title("rabah-quran is a fine-tune OF jonatasgrosman —\n"
                 "they are the same lineage; 'consensus' between them is self-agreement",
                 fontsize=11)
    ax.grid(alpha=0.25, axis="y")
    ax.tick_params(axis="x", rotation=20)
    fig.tight_layout()
    fig.savefig(out / "05_lineage.png", dpi=130)
    plt.close(fig)


def fig_song_difficulty(summary, by, song_full, out: Path):
    # mean pairwise |dstart| across measured models (word 0 proxy)
    import itertools
    keys = list(SONGS)
    models = [m for m in MODELS if summary.get(m) and summary[m].get("consensus_dev_s") is not None]
    diffs = []
    for k in keys:
        vals = [by[(m, song_full[k])]["first_word_start"] for m in models]
        pair = [abs(a - b) for a, b in itertools.combinations(vals, 2)]
        diffs.append(np.mean(pair) if pair else 0.0)
    order = np.argsort(diffs)[::-1]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.bar([SONGS[keys[i]][0] for i in order], [diffs[i] for i in order],
           color="#ff7f0e", edgecolor="black", linewidth=0.6)
    for x, i in enumerate(order):
        ax.text(x, diffs[i] + 0.02, f"{diffs[i]:.2f}", ha="center", fontsize=8)
    ax.set_ylabel("mean pairwise |word-0| (s)")
    ax.set_title("Where models disagree (word-0 proxy)\n"
                 "Nahawand dominates — inflated by the two failing models",
                 fontsize=11)
    ax.grid(alpha=0.25, axis="y")
    ax.tick_params(axis="x", rotation=20)
    fig.tight_layout()
    fig.savefig(out / "06_song_difficulty.png", dpi=130)
    plt.close(fig)


def fig_dashboard(summary, by, song_full, out: Path):
    sns.set_theme(style="whitegrid")
    fig = plt.figure(figsize=(16, 10))

    # (1) pareto
    ax = fig.add_subplot(2, 3, 1)
    dash_off = {"rabah-quran": (8, 5), "jonatasgrosman": (8, -11), "mms-1b-all": (8, 4)}
    for m, s in summary.items():
        if s.get("consensus_dev_s") is None:
            continue
        meta = MODELS[m]
        ax.scatter(s["sec_per_audio_min"], s["consensus_dev_s"],
                   s=90, color=COLOR[meta["verdict"]], marker=MARKER[meta["verdict"]],
                   edgecolor="black", linewidth=0.7, zorder=3)
        ax.annotate(meta["label"], (s["sec_per_audio_min"], s["consensus_dev_s"]),
                    textcoords="offset points",
                    xytext=dash_off.get(meta["label"], (7, 5)), fontsize=8)
    ax.set_ylim(0, max(s["consensus_dev_s"] for m, s in summary.items()
                       if s.get("consensus_dev_s") is not None) * 1.15)
    ax.set_xlabel("sec / audio-min"); ax.set_ylabel("consensus dev (s)")
    ax.set_title("Quality vs cost", fontsize=10)

    # (2) silent intro
    ax = fig.add_subplot(2, 3, 2)
    keys = list(SONGS)
    yp = np.arange(len(keys))[::-1]
    for y, k in zip(yp, keys):
        ax.barh(y, SONGS[k][2], height=0.7, color="#d9d9d9", zorder=0)
    for m in MODELS:
        xs = [by[(m, song_full[k])]["first_word_start"] for k in keys]
        ax.scatter(xs, yp, s=30, marker=MARKER[MODELS[m]["verdict"]],
                   color=COLOR[MODELS[m]["verdict"]], edgecolor="black",
                   linewidth=0.4, zorder=3)
    ax.set_yticks(yp); ax.set_yticklabels([SONGS[k][0] for k in keys], fontsize=7)
    ax.set_xlabel("word-0 time (s)"); ax.set_title("Silent-intro test", fontsize=10)

    # (3) on/off
    ax = fig.add_subplot(2, 3, 3)
    items = sorted(((m, s) for m, s in summary.items()), key=lambda kv: kv[1]["on_off"])
    ax.barh([short(m) for m, _ in items], [s["on_off"] for _, s in items],
            color=[COLOR[MODELS[m]["verdict"]] for m, _ in items],
            edgecolor="black", linewidth=0.5)
    ax.axvline(1, color="black", ls="--", lw=1)
    ax.set_xscale("log"); ax.set_xlabel("on/off (log)")
    ax.set_title("Energy tracking", fontsize=10)
    ax.tick_params(axis="y", labelsize=7)

    # (4) consensus bar
    ax = fig.add_subplot(2, 3, 4)
    ranked = sorted(((m, s) for m, s in summary.items()
                     if s.get("consensus_dev_s") is not None),
                    key=lambda kv: kv[1]["consensus_dev_s"])
    ax.bar([short(m) for m, _ in ranked],
           [s["consensus_dev_s"] for _, s in ranked],
           color=[COLOR[MODELS[m]["verdict"]] for m, _ in ranked],
           edgecolor="black", linewidth=0.5)
    ax.set_ylabel("consensus dev (s)"); ax.set_title("Ranking (lower better)", fontsize=10)
    ax.tick_params(axis="x", rotation=25, labelsize=7)

    # (5) silent violations
    ax = fig.add_subplot(2, 3, 5)
    ms = list(MODELS)
    ax.barh([short(m) for m in ms], [MODELS[m]["silent_viol"] for m in ms],
            color=["#2ca02c" if MODELS[m]["silent_viol"] == 0 else "#d62728" for m in ms],
            edgecolor="black", linewidth=0.5)
    ax.set_xlabel("violations /8"); ax.set_title("Silent-intro violations", fontsize=10)
    ax.tick_params(axis="y", labelsize=7)

    # (6) song difficulty
    ax = fig.add_subplot(2, 3, 6)
    import itertools
    models = [m for m in MODELS if summary.get(m) and summary[m].get("consensus_dev_s") is not None]
    diffs = []
    for k in keys:
        vals = [by[(m, song_full[k])]["first_word_start"] for m in models]
        diffs.append(np.mean([abs(a - b) for a, b in itertools.combinations(vals, 2)]))
    o = np.argsort(diffs)[::-1]
    ax.bar([SONGS[keys[i]][0] for i in o], [diffs[i] for i in o],
           color="#ff7f0e", edgecolor="black", linewidth=0.5)
    ax.set_ylabel("mean pairwise |word-0| (s)")
    ax.set_title("Disagreement by song", fontsize=10)
    ax.tick_params(axis="x", rotation=25, labelsize=7)

    fig.suptitle("B2 alignment benchmark — decision dashboard  "
                 "(pick: rabah-quran; note it is a fine-tune of jonatasgrosman)",
                 fontsize=14, y=0.995)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(out / "00_dashboard.png", dpi=130)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", default="results/bench/bench_results.json")
    ap.add_argument("--out", default="results/bench/plots")
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    summary, by, song_full = load(Path(args.results))
    sns.set_theme(style="whitegrid")
    fig_pareto(summary, out)
    fig_silent_intro(by, song_full, out)
    fig_energy(summary, out)
    fig_trap(summary, out)
    fig_lineage(by, song_full, out)
    fig_song_difficulty(summary, by, song_full, out)
    fig_dashboard(summary, by, song_full, out)
    for p in sorted(out.glob("*.png")):
        print("wrote", p)


if __name__ == "__main__":
    main()
