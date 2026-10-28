"""Audit an A1 run against the vocal stem (dB-relative).

For every track in <out>/summary.json:
  - coverage (last word end / duration) and the tail stretch
  - every gap > 2 s, with mean level relative to the track's vocal median
  - a per-word sheet per track

A gap is "skipped singing" when at least a quarter of it is within 6 dB of the
track's median vocal level. Quieter gaps are instrumental breaks and are fine.
"""
import argparse
import csv
import json
from pathlib import Path

import numpy as np
import soundfile as sf

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="work/srt/ready10")
    ap.add_argument("--pack", default="work/ready-pack/pack.json")
    ap.add_argument("--dir", default="work/audit")
    args = ap.parse_args()

    out, audit_dir = Path(args.out), Path(args.dir)
    audit_dir.mkdir(parents=True, exist_ok=True)
    cues_dir = audit_dir / "cues"
    cues_dir.mkdir(exist_ok=True)

    pack = {p["name"]: p for p in json.load(open(args.pack))}
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

        db, audio_s = frame_db(pack[name]["audio"])
        med = round(float(np.median(db)), 1)

        gaps = []
        for i in range(1, len(words)):
            g = starts[i] - ends[i - 1]
            if g > GAP_S:
                win = window(db, ends[i - 1], starts[i], med)
                gaps.append({"from_s": round(ends[i - 1], 3), "to_s": round(starts[i], 3),
                             "gap_s": round(g, 3), **win,
                             "verdict": "skipped_singing" if win["loud_frac"] >= 0.25 else "instrumental"})
        tail = window(db, last_end, dur, med)
        skipped = [g for g in gaps if g["verdict"] == "skipped_singing"]
        worst = max(gaps, key=lambda g: g["gap_s"]) if gaps else None

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
        })
        details[name] = {"median_db": med, "audio_s": round(audio_s, 2),
                         "src_words": max(x.get("src_i", 0) for x in words) + 1,
                         "gaps": gaps, "tail": tail}

        with open(cues_dir / (words_file.name.replace(".words.json", "") + ".words.csv"), "w", newline="") as fh:
            wr = csv.writer(fh)
            wr.writerow(["i", "src_i", "line", "start_s", "end_s", "dur_s", "gap_before_s", "word"])
            for i, x in enumerate(words):
                gp = starts[i] - ends[i - 1] if i else 0.0
                wr.writerow([i, x.get("src_i"), x.get("line"), round(x["start"], 3), round(x["end"], 3),
                             round(x["end"] - x["start"], 3), round(gp, 3), x.get("word", "")])

    json.dump({"tracks": rows, "detail": details},
              open(audit_dir / "audit.json", "w"), ensure_ascii=False, indent=1)

    hdr = ["song", "words", "coverage", "tail_s", "tail_delta_db", "gaps_gt2s",
           "gaps_skipped", "worst_gap_s", "worst_gap_at", "worst_gap_delta_db", "worst_gap_verdict"]
    lines = ["# A1 gap audit vs vocal stem", "",
             "`delta_db` is the window's mean level minus the track's median vocal level.",
             "Negative = quieter than singing. A gap counts as `skipped_singing` when >= 25% of it",
             "sits within 6 dB of the median; otherwise it is an instrumental break.", "",
             "| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
    for r in rows:
        lines.append("| `" + r["song"] + "` | " + " | ".join(str(r[k]) for k in hdr[1:]) + " |")
    (audit_dir / "AUDIT.md").write_text("\n".join(lines) + "\n")

    for r in rows:
        print(f"{r['song'][:44]:46s} tail {r['tail_s']:5.1f}s {r['tail_delta_db']:+5.1f}dB  "
              f"gaps {r['gaps_gt2s']} skipped {r['gaps_skipped']}  worst {r['worst_gap_s']:5.1f}s "
              f"{r['worst_gap_delta_db']:+5.1f}dB {r['worst_gap_verdict']}")


if __name__ == "__main__":
    main()
