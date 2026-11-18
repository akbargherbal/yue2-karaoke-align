#!/usr/bin/env python3
"""Detect untexted repeats in an A1 word alignment.

A forced aligner walks the lyrics exactly once.  If the singer repeats a line
more often than the sheet does, the aligner cannot fail: it simply stretches
neighbouring words over the extra audio.  The result can have 100% of its words
aligned, high coverage and no loud gap, so the existing gap audit stays quiet.

This screen looks at the *vocal stem* for the signature that survives that
failure: a lyric line whose words are stretched far beyond the track's own
singing pace, where the stretched words are actually full-energy vocal (not a
quiet instrumental stretch).  It reports every such line and an estimated
number of extra renditions.  It is deliberately conservative: a single long,
melismatic word is not enough, and a line spread apart by instrumental gaps is
not enough.

The primary output key is intentionally the one from the issue:

    suspected_extra_rendition: int   # 0 = clean; >= 1 means re-align/expand

The detector is audio-assisted but has no model dependency.  It reads the same
16 kHz mono vocal stem every aligner resamples to, with soundfile + numpy only.
When no stem is available it reports zero flags and says why; a word list alone
cannot distinguish a repeated rendition from melisma.

Usage
-----
    python scripts/a1_repeats.py --words results/a1/song/song.words.json
    python scripts/a1_repeats.py --words song.words.json --audio work/stems/.../vocals.flac
    python scripts/a1_repeats.py --words song.words.json --json repeats.json --md repeats.md

Exit status is 0 when clean and 2 when at least one line is suspected.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

# --- thresholds ------------------------------------------------------------
# All durations are seconds.  These operate on *active* word time (the part of
# a word whose frames are within ACTIVE_DB of the track's singing level), never
# on raw span alone: instrumental gaps are the main false-positive source.
FRAME_S = 0.02
ACTIVE_DB = 10.0          # within 10 dB of the singing level = vocal
SINGING_PERCENTILE = 90.0  # estimate the singing level from the louder frames
MIN_ACTIVE_FRAC = 0.55    # a word must be mostly sung, not half silence
STRETCH_FACTOR = 3.0      # "long" = >3x the track's median word duration
MIN_STRETCHED_S = 2.0     # and at least this long, to ignore short pickups
MIN_ACTIVE_LINE_S = 6.0   # a suspicious line must contain this much singing
RATIO_FLAG = 4.5          # stretched sung time / pace-implied duration
# Secondary, line-span fallback.  A repeated rendition of a line that already
# contains a long, quiet tail (melisma, breath, instrumental) can leave only a
# couple of individually stretched words.  The line as a whole is then still
# far longer than its own pace, with real singing spread across the copies.
LINE_ACTIVE_RATIO = 5.0   # whole-line active time / pace-implied duration
LINE_CONTENT_RATIO = 8.0  # whole-line span content / pace-implied duration
ONE_WORD_LINE_RATIO = 7.0  # a one-word line must be this expanded to call it a repeat


def _frame_db(path: str | Path, frame_s: float = FRAME_S) -> tuple[np.ndarray, float]:
    """Return (per-frame RMS in dB, duration in seconds) for any sample rate."""
    data, sr = sf.read(path, always_2d=True, dtype="float32")
    if not len(data):
        return np.zeros(0, dtype="float32"), 0.0
    mono = data.mean(axis=1)
    n = max(1, int(round(sr * frame_s)))
    frames = mono[: len(mono) // n * n].reshape(-1, n)
    if not len(frames):
        return np.zeros(0, dtype="float32"), len(mono) / sr
    rms = np.sqrt((frames ** 2).mean(axis=1))
    return (20.0 * np.log10(np.maximum(rms, 1e-6))).astype("float32"), len(mono) / sr


def _slice(db: np.ndarray, start: float, end: float) -> np.ndarray:
    a = max(0, int(math.floor(start / FRAME_S)))
    b = min(len(db), int(math.ceil(end / FRAME_S)))
    return db[a:b]


def _summarize_words(words: list[dict], db: np.ndarray, med_db: float,
                     median_word_s: float) -> list[dict]:
    out = []
    for i, w in enumerate(words):
        start, end = float(w["start"]), float(w["end"])
        dur = max(0.0, end - start)
        seg = _slice(db, start, end)
        active_frac = float((seg > med_db - ACTIVE_DB).mean()) if len(seg) else 0.0
        out.append({
            "i": i,
            "line": w.get("line"),
            "word": w.get("word", ""),
            "start": start,
            "end": end,
            "dur_s": dur,
            "active_frac": round(active_frac, 3),
            "stretched": (dur > max(MIN_STRETCHED_S, STRETCH_FACTOR * median_word_s)
                          and active_frac >= MIN_ACTIVE_FRAC),
        })
    return out


def _group_by_line(words: list[dict]) -> list[tuple[int, list[dict]]]:
    """Group by `line`; without line indices, treat every word as its own line."""
    if any(w.get("line") is not None for w in words):
        groups: dict[int, list[dict]] = {}
        for w in words:
            li = w.get("line")
            if li is None:
                li = max(groups) + 1 if groups else 0
            groups.setdefault(int(li), []).append(w)
        return sorted(groups.items())
    return [(i, [w]) for i, w in enumerate(words)]


def detect_repeats(words: list[dict], db: np.ndarray, lines: list[dict] | None = None,
                   *, min_active_line_s: float = MIN_ACTIVE_LINE_S,
                   ratio_flag: float = RATIO_FLAG) -> dict:
    """Return a repeat-gate report for one word list and its vocal envelope.

    `words` is the A1 `words` list.  `db` is the output of `_frame_db` (or any
    per-FRAME_S dB envelope).  `lines` is the optional A1 `lines` list, used to
    name the affected lyric line in the report.
    """
    empty = {
        "suspected_extra_rendition": 0,
        "suspected_lines": [],
        "median_word_s": None,
        "thresholds": {
            "active_db": ACTIVE_DB,
            "singing_percentile": SINGING_PERCENTILE,
            "min_active_frac": MIN_ACTIVE_FRAC,
            "stretch_factor": STRETCH_FACTOR,
            "min_active_line_s": min_active_line_s,
            "ratio": ratio_flag,
            "line_active_ratio": LINE_ACTIVE_RATIO,
            "line_content_ratio": LINE_CONTENT_RATIO,
            "one_word_line_ratio": ONE_WORD_LINE_RATIO,
        },
    }
    if not words or len(db) == 0:
        return {**empty, "skipped": "empty word list or vocal envelope"}

    durs = [max(0.0, float(w["end"]) - float(w["start"])) for w in words]
    positive = [d for d in durs if d > 0]
    if not positive:
        return {**empty, "skipped": "zero-length words"}
    median_word_s = float(statistics.median(positive))
    # A sparse vocal stem can have silence for most of its frames, which would
    # make a median "singing" level nonsense.  A high percentile is a stable
    # reference for both dense mixes and mostly-instrumental tracks.
    med_db = float(np.percentile(db, SINGING_PERCENTILE))
    stats = _summarize_words(words, db, med_db, median_word_s)

    suspected = []
    for line_i, group in _group_by_line(stats):
        content_s = sum(w["dur_s"] for w in group)
        line_active_s = sum(w["dur_s"] * w["active_frac"] for w in group)
        span_s = max(w["end"] for w in group) - min(w["start"] for w in group)
        stretched = [w for w in group if w["stretched"]]
        active_s = sum(w["dur_s"] for w in stretched)
        expected_s = max(1e-6, len(group) * median_word_s)
        ratio = content_s / expected_s
        line_active_ratio = line_active_s / expected_s

        active_ratio = active_s / expected_s
        # One long word is ordinary Arabic melisma, not proof of a repeat.  The
        # strong path therefore needs either two independently stretched words
        # in the line, or -- for a lyric line that is a single word -- a much
        # larger expansion than any melisma leaves behind.
        strong = (active_s >= min_active_line_s
                  and active_ratio >= ratio_flag
                  and (len(stretched) >= 2
                       or (len(group) == 1
                           and active_ratio >= 2 * ratio_flag)))
        # Backup for a repeated line whose own copies are partly quiet: the
        # whole line is still very long and still contains a lot of singing.
        # Do not require an individually `stretched` word here.  A copy that
        # contains a long cadential tail can keep its active fraction just under
        # MIN_ACTIVE_FRAC even while it repeats five times.
        spread = (line_active_s >= min_active_line_s
                  and line_active_ratio >= LINE_ACTIVE_RATIO
                  and ratio >= LINE_CONTENT_RATIO
                  # One long word can be ordinary melisma; for a one-word line
                  # demand a far more extreme expansion before calling it a
                  # repeated rendition.
                  and (len(group) >= 2 or line_active_ratio >= ONE_WORD_LINE_RATIO))
        if not (strong or spread):
            continue

        # Every extra rendition adds sung time.  The pace-implied duration is
        # one rendition, so round the expansion down to whole renditions.  The
        # line-span fallback uses the whole-line active ratio, which is less
        # inflated by a long quiet tail than raw content time.
        expansion = max(ratio, active_ratio) if strong else line_active_ratio
        # Round half up.  A line at the 4.5 threshold is five renditions, i.e.
        # four extra ones; Python's round(4.5) is 4 and would undercount it.
        renditions = max(1, int(math.floor(expansion + 0.5)))
        extra = max(0, renditions - 1)
        if extra < 1:
            continue

        text = ""
        if lines and 0 <= line_i < len(lines):
            item = lines[line_i]
            text = item.get("text", "") if isinstance(item, dict) else str(item)
        suspected.append({
            "line": line_i,
            "text": text,
            "span_s": round(span_s, 3),
            "content_s": round(content_s, 3),
            "active_s": round(active_s, 3),
            "line_active_s": round(line_active_s, 3),
            "expected_s": round(expected_s, 3),
            "ratio": round(ratio, 2),
            "active_ratio": round(active_ratio, 2),
            "line_active_ratio": round(line_active_ratio, 2),
            "n_words": len(group),
            "n_stretched_words": len(stretched),
            "longest_word_s": round(max(w["dur_s"] for w in group), 3),
            "extra_renditions": int(extra),
            "words": [
                {"i": w["i"], "word": w["word"], "start": round(w["start"], 3),
                 "end": round(w["end"], 3), "dur_s": round(w["dur_s"], 3),
                 "active_frac": w["active_frac"]}
                for w in stretched
            ],
        })

    suspected.sort(key=lambda r: (r["extra_renditions"], r["active_s"]), reverse=True)
    return {
        "suspected_extra_rendition": max([r["extra_renditions"] for r in suspected], default=0),
        "suspected_lines": suspected,
        "median_word_s": round(median_word_s, 3),
        "thresholds": {
            "active_db": ACTIVE_DB,
            "singing_percentile": SINGING_PERCENTILE,
            "min_active_frac": MIN_ACTIVE_FRAC,
            "stretch_factor": STRETCH_FACTOR,
            "min_active_line_s": min_active_line_s,
            "ratio": ratio_flag,
            "line_active_ratio": LINE_ACTIVE_RATIO,
            "line_content_ratio": LINE_CONTENT_RATIO,
            "one_word_line_ratio": ONE_WORD_LINE_RATIO,
        },
    }


def _is_file(value) -> bool:
    try:
        return bool(value) and Path(value).is_file()
    except (OSError, TypeError):
        return False


def find_vocal_stem(words_path: str | Path, data: dict, explicit: str | Path | None = None) -> Path | None:
    """Resolve a vocal stem from an explicit path, the JSON, or nearby fixtures.

    A1 word files carry an absolute `stem`; corpus fixtures often keep the word
    list under `karaoke/<batch>/<track>.words.json` and the stem under
    `stems16k/<batch>/<track>/vocals.flac`.  Supporting both keeps the audit
    useful without a pack file.
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    for raw in (data.get("stem"), data.get("audio")):
        if raw:
            candidates.append(Path(raw))

    p = Path(words_path)
    track = p.name.split(".words.json", 1)[0]
    candidates += [
        p.parent / track / "vocals.flac",
        p.parent / track / "vocals.wav",
        p.parent / "vocals.flac",
        p.parent / "vocals.wav",
    ]

    parts = p.parts
    for i, part in enumerate(parts):
        if part != "karaoke":
            continue
        root = Path(*parts[:i]) if i else Path(".")
        after = parts[i + 1:-1]
        if after:
            candidates.append(root / "stems16k" / Path(*after) / track / "vocals.flac")
        candidates.append(root / "stems16k" / track / "vocals.flac")

    for parent in (p.parent, *p.parent.parents):
        candidates += [
            parent / "stems16k" / track / "vocals.flac",
            parent / "stems16k" / p.parent.name / track / "vocals.flac",
        ]

    for raw in (data.get("stem"), data.get("audio")):
        if raw:
            candidates += [p.parent / Path(raw).name,
                            p.parent / (Path(raw).stem + ".flac")]

    for candidate in candidates:
        if _is_file(candidate):
            return candidate
    return None


def detect_repeats_file(words_path: str | Path, audio_path: str | Path | None = None,
                        *, lines: list[dict] | None = None) -> dict:
    """Convenience wrapper: read a word file, find its stem, run the detector."""
    data = json.loads(Path(words_path).read_text(encoding="utf-8"))
    words = data.get("words") if isinstance(data, dict) else data
    if lines is None and isinstance(data, dict):
        lines = data.get("lines")
    audio = find_vocal_stem(words_path, data if isinstance(data, dict) else {}, audio_path)
    if audio is None:
        return {
            "suspected_extra_rendition": 0,
            "suspected_lines": [],
            "skipped": "no vocal stem (word timings alone cannot rule out repeats)",
        }
    try:
        db, duration_s = _frame_db(audio)
    except Exception as e:  # noqa: BLE001 - gate must never break a run
        return {
            "suspected_extra_rendition": 0,
            "suspected_lines": [],
            "skipped": f"could not read vocal stem: {e}",
        }
    report = detect_repeats(words or [], db, lines)
    report.update({"song": data.get("song") if isinstance(data, dict) else None,
                   "audio": str(audio), "duration_s": round(duration_s, 3)})
    return report


def _markdown(report: dict) -> str:
    lines = ["# A1 repeat gate", "",
             "A line is flagged when its words are individually stretched, the "
             "stretched words are actually sung (not a quiet instrumental gap), "
             "and the line is long relative to this track's own word pace.", ""]
    if report.get("skipped"):
        return "\n".join(lines + [f"Skipped: {report['skipped']}.", ""])
    n = report["suspected_extra_rendition"]
    lines.append(f"**suspected_extra_rendition: {n}**")
    if not report["suspected_lines"]:
        return "\n".join(lines + ["", "No repeat signature found.", ""])
    lines += ["", "| line | text | span_s | active_s | line_active_s | ratio | "
              "active_ratio | line_active_ratio | extra | words |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in report["suspected_lines"]:
        text = (r["text"] or "").replace("|", "\\|")
        lines.append(f"| {r['line']} | {text} | {r['span_s']} | {r['active_s']} | "
                     f"{r['line_active_s']} | {r['ratio']} | {r['active_ratio']} | "
                     f"{r['line_active_ratio']} | {r['extra_renditions']} | "
                     f"{r['n_stretched_words']} |")
    return "\n".join(lines + [""])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--words", required=True, help="A1 <song>.words.json")
    ap.add_argument("--audio", help="vocal stem (default: `stem`/`audio` in the JSON)")
    ap.add_argument("--json", dest="json_out", help="write the report JSON here")
    ap.add_argument("--md", dest="md_out", help="write a Markdown report here")
    ap.add_argument("--min-active-line-s", type=float, default=MIN_ACTIVE_LINE_S)
    ap.add_argument("--ratio", type=float, default=RATIO_FLAG,
                    help="flag a line above this duration/pace ratio")
    args = ap.parse_args(argv)

    report = detect_repeats_file(args.words, args.audio)
    if not report.get("skipped"):
        # Re-run with the CLI's thresholds, keeping the resolved audio metadata.
        data = json.loads(Path(args.words).read_text(encoding="utf-8"))
        words = data.get("words") if isinstance(data, dict) else data
        audio = report["audio"]
        db, dur = _frame_db(audio)
        report = detect_repeats(
            words or [], db,
            data.get("lines") if isinstance(data, dict) else None,
            min_active_line_s=args.min_active_line_s,
            ratio_flag=args.ratio)
        report.update({
            "song": data.get("song") if isinstance(data, dict) else None,
            "audio": audio,
            "duration_s": round(dur, 3),
        })

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    n = report.get("suspected_extra_rendition", 0)
    if n:
        lines = ", ".join(str(r["line"]) for r in report.get("suspected_lines", []))
        print(f"\nREPEAT GATE: {n} suspected extra rendition(s) on line(s) {lines}",
              file=sys.stderr)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
    if args.md_out:
        Path(args.md_out).write_text(_markdown(report), encoding="utf-8")
    return 2 if n else 0


if __name__ == "__main__":
    raise SystemExit(main())
