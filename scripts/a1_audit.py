"""Audit an A1 run against the vocal stem (dB-relative).

For every track in <out>/summary.json:
  - coverage (last word end / duration) and the tail stretch
  - every gap > 2 s, with mean level relative to the track's vocal median
  - a per-word sheet per track

A gap is "skipped singing" when at least a quarter of it is within 6 dB of the
track's median vocal level. Quieter gaps are instrumental breaks and are fine.

The same pass also runs the repeat gate (`scripts/a1_repeats.py`).  A forced
alignment can absorb untexted repeated renditions into its neighbouring words
without leaving a gap; the repeat gate looks for that signature on the vocal
stem and writes `suspected_extra_rendition` / `suspected_lines` into the JSON
and the table.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from a1_repeats import (  # noqa: E402
    _frame_db as repeat_frame_db,
    detect_repeats,
    find_vocal_stem,
)

GAP_S = 2.0
NEAR_DB = 6.0


def frame_db(path, frame_s=1.0):
    data, sr = sf.read(path, always_2d=True, dtype="float32")
    mono = data.mean(axis=1)
    n = max(1, int(round(sr * frame_s)))
    frames = mono[: len(mono) // n * n].reshape(-1, n)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    return 20.0 * np.log10(np.maximum(rms, 1e-6)), len(mono) / sr


def window(db, t0, t1, med):
    a, b = max(0, int(np.floor(t0))), min(len(db), int(np.ceil(t1)))
    seg = db[a:b]
    if not len(seg):
        return {"len_s": 0.0, "mean_db": 0.0, "delta_db": 0.0, "loud_s": 0.0, "loud_frac": 0.0}
    loud = int((seg > med - NEAR_DB).sum())
    return {"len_s": round(b - a, 1), "mean_db": round(float(seg.mean()), 1),
            "delta_db": round(float(seg.mean()) - med, 1),
            "loud_s": loud, "loud_frac": round(loud / len(seg), 2)}


def resolve_audio(name: str, record: dict, pack: dict, summary_song: dict,
                  words_file: Path) -> Path | None:
    """Find the vocal stem: explicit pack first, then the record or nearby files."""
    candidates = []
    if name in pack:
        candidates.append(pack[name].get("audio"))
    for source in (record, summary_song):
        candidates += [source.get("stem"), source.get("audio")]
    for p in candidates:
        try:
            if p and Path(p).is_file():
                return Path(p)
        except (OSError, TypeError):
            pass
    return find_vocal_stem(words_file, record)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="work/srt/ready10")
    ap.add_argument("--pack", default="work/ready-pack/pack.json",
                    help="optional pack.json; without it, use `stem`/`audio` in the word files")
    ap.add_argument("--dir", default="work/audit")
    ap.add_argument("--no-repeats", action="store_true",
                    help="skip the untexted-repeat gate")
    ap.add_argument("--repeat-ratio", type=float, default=4.5,
                    help="flag a line when active sung time exceeds this multiple "
                         "of its pace-implied duration (default: 4.5)")
    args = ap.parse_args()

    out, audit_dir = Path(args.out), Path(args.dir)
    audit_dir.mkdir(parents=True, exist_ok=True)
    cues_dir = audit_dir / "cues"
    cues_dir.mkdir(exist_ok=True)

    pack_path = Path(args.pack) if args.pack else None
    pack = {}
    if pack_path and pack_path.is_file():
        pack = {p["name"]: p for p in json.load(open(pack_path))}
    summary = json.load(open(out / "summary.json"))
    rows, details = [], {}

    for song in summary["songs"]:
        name = song["song"]
        words_file = Path(song["files"]["words_json"])
        w = json.load(open(words_file))
        words = w["words"] if isinstance(w, dict) else w
        starts = [x["start"] for x in words]
        ends = [x["end"] for x in words]
        dur, last_end = song["duration_s"], ends[-1]

        audio = resolve_audio(name, w if isinstance(w, dict) else {},
                              pack, song, words_file)
        if audio is None:
            print(f"[audit] {name}: no vocal stem; gap and repeat gates skipped",
                  file=sys.stderr)
            db, med, audio_s = None, 0.0, None
        else:
            db, audio_s = frame_db(audio)
            med = round(float(np.median(db)), 1)

        gaps = []
        if db is not None:
            for i in range(1, len(words)):
                g = starts[i] - ends[i - 1]
                if g > GAP_S:
                    win = window(db, ends[i - 1], starts[i], med)
                    gaps.append({"from_s": round(ends[i - 1], 3), "to_s": round(starts[i], 3),
                                 "gap_s": round(g, 3), **win,
                                 "verdict": "skipped_singing" if win["loud_frac"] >= 0.25 else "instrumental"})
            tail = window(db, last_end, dur, med)
        else:
            tail = {"len_s": 0.0, "mean_db": 0.0, "delta_db": 0.0,
                    "loud_s": 0.0, "loud_frac": 0.0}
        skipped = [g for g in gaps if g["verdict"] == "skipped_singing"]
        worst = max(gaps, key=lambda g: g["gap_s"]) if gaps else None

        if args.no_repeats or audio is None:
            repeats = {"suspected_extra_rendition": 0, "suspected_lines": [],
                       "skipped": "disabled" if args.no_repeats else "no vocal stem"}
        else:
            repeat_db, _dur = repeat_frame_db(audio)
            repeats = detect_repeats(words, repeat_db,
                                     w.get("lines") if isinstance(w, dict) else None,
                                     ratio_flag=args.repeat_ratio)

        repeat_lines = repeats.get("suspected_lines", [])
        repeat_names = ", ".join(str(r["line"]) for r in repeat_lines)
        repeat_texts = " | ".join((r.get("text") or "") for r in repeat_lines)
        rows.append({
            "song": name,
            "words": f"{song['n_words_aligned']}/{song['n_words_in']}",
            "coverage": song["coverage"],
            "tail_s": round(dur - last_end, 1),
            "tail_delta_db": tail["delta_db"],
            "gaps_gt2s": len(gaps),
            "gaps_skipped": len(skipped),
            "worst_gap_s": worst["gap_s"] if worst else 0.0,
            "worst_gap_at": f"{worst['from_s']}-{worst['to_s']}" if worst else "",
            "worst_gap_delta_db": worst["delta_db"] if worst else 0.0,
            "worst_gap_verdict": worst["verdict"] if worst else "",
            "suspected_extra_rendition": repeats.get("suspected_extra_rendition", 0),
            "suspected_lines": repeat_names,
            "suspected_line_texts": repeat_texts,
        })
        detail_key = name
        if detail_key in details:
            # A/B fixture batches can share the same display song name.
            suffix = words_file.name.replace(".words.json", "")
            detail_key = f"{name} [{suffix}]"
            n = 2
            while detail_key in details:
                detail_key = f"{name} [{suffix}#{n}]"
                n += 1
        details[detail_key] = {"song": name, "median_db": med,
                               "audio_s": round(audio_s, 2) if audio_s is not None else None,
                               "audio": str(audio) if audio else None,
                               "src_words": max(x.get("src_i", 0) for x in words) + 1,
                               "gaps": gaps, "tail": tail, "repeats": repeats}

        with open(cues_dir / (words_file.name.replace(".words.json", "") + ".words.csv"), "w", newline="") as fh:
            wr = csv.writer(fh)
            wr.writerow(["i", "src_i", "line", "start_s", "end_s", "dur_s", "gap_before_s", "word"])
            for i, x in enumerate(words):
                gp = starts[i] - ends[i - 1] if i else 0.0
                wr.writerow([i, x.get("src_i"), x.get("line"), round(x["start"], 3), round(x["end"], 3),
                             round(x["end"] - x["start"], 3), round(gp, 3), x.get("word", "")])

    repeat_total = sum(r["suspected_extra_rendition"] for r in rows)
    repeat_tracks = [r["song"] for r in rows if r["suspected_extra_rendition"]]
    json.dump({"tracks": rows, "detail": details,
               "suspected_extra_rendition": repeat_total,
               "suspected_tracks": repeat_tracks},
              open(audit_dir / "audit.json", "w"), ensure_ascii=False, indent=1)

    hdr = ["song", "words", "coverage", "tail_s", "tail_delta_db", "gaps_gt2s",
           "gaps_skipped", "worst_gap_s", "worst_gap_at", "worst_gap_delta_db",
           "worst_gap_verdict", "suspected_extra_rendition", "suspected_lines",
           "suspected_line_texts"]
    lines = ["# A1 gap + repeat audit vs vocal stem", "",
             "`delta_db` is the window's mean level minus the track's median vocal level.",
             "Negative = quieter than singing. A gap counts as `skipped_singing` when >= 25% of it",
             "sits within 6 dB of the median; otherwise it is an instrumental break.", "",
             "`suspected_extra_rendition` is the repeat gate: a line whose *sung* words are",
             "stretched far beyond the track's own pace. It catches untexted repetitions that",
             "leave no gap. `suspected_lines` lists the affected lyric line indices and",
             "`suspected_line_texts` shows their text.", "",
             "| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
    for r in rows:
        lines.append("| `" + r["song"] + "` | " + " | ".join(str(r[k]) for k in hdr[1:]) + " |")
    (audit_dir / "AUDIT.md").write_text("\n".join(lines) + "\n")

    for r in rows:
        rep = r["suspected_extra_rendition"]
        flag = f"  repeat {rep} line {r['suspected_lines']}" if rep else ""
        print(f"{r['song'][:44]:46s} tail {r['tail_s']:5.1f}s {r['tail_delta_db']:+5.1f}dB  "
              f"gaps {r['gaps_gt2s']} skipped {r['gaps_skipped']}  worst {r['worst_gap_s']:5.1f}s "
              f"{r['worst_gap_delta_db']:+5.1f}dB {r['worst_gap_verdict']}{flag}")


if __name__ == "__main__":
    main()
