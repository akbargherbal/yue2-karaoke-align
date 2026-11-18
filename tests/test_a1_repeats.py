"""Tests for the untexted-repeat gate (scripts/a1_repeats.py).

The positive fixture mirrors the issue's synthetic repro: the audio sings one
line five times while the lyric sheet lists it once, so the aligned words of
that line span five copies.  The negative fixtures are the dangerous lookalikes:
a long line spread over quiet instrumental time, and one long melismatic word.
Both must stay silent.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from a1_repeats import detect_repeats_file  # noqa: E402


def write_track(directory: Path, durations: list[float],
                *, quiet: int | None = None) -> dict:
    """Write a small vocal stem + word file and return the paths.

    `durations` are the lengths of the words on the test line.  `quiet` is an
    index into them; that word is written as silence (an instrumental stretch).
    """
    sr = 16000
    words: list[dict] = []
    t = 0.0

    # Eight ordinary lines establish the track's own word pace.
    for i in range(40):
        words.append({"i": i, "word": f"w{i}", "start": round(t, 3),
                      "end": round(t + 0.5, 3), "line": i // 5})
        t += 0.5

    # One line.  In the positive case each word spans five normal words; in the
    # negative cases one word is long only because it is quiet or melismatic.
    line_i = 8
    word_spans = []
    for j, dur in enumerate(durations):
        word_spans.append((t, t + dur))
        words.append({"i": 40 + j, "word": f"rep{j}", "start": round(t, 3),
                      "end": round(t + dur, 3), "line": line_i})
        t += dur

    lines = [{"i": i, "text": f"line {i}"} for i in range(line_i)]
    lines.append({"i": line_i, "text": "the repeated last line"})

    audio = directory / "vocals.flac"
    n = int(round(t * sr))
    tt = np.arange(n) / sr
    rng = np.random.default_rng(0)
    y = 0.1 * np.sin(2 * np.pi * 220 * tt) + 0.002 * rng.normal(size=n)
    if quiet is not None:
        start, end = (int(round(x * sr)) for x in word_spans[quiet])
        y[start:end] = 0.0
    sf.write(audio, y.astype("float32"), sr)

    word_file = directory / "song.words.json"
    word_file.write_text(json.dumps({
        "song": "synthetic",
        "duration_s": round(t, 3),
        "stem": str(audio),
        "n_words_in": len(words),
        "n_words_aligned": len(words),
        "coverage": 1.0,
        "lines": lines,
        "words": words,
    }, ensure_ascii=False), encoding="utf-8")
    return {"words": word_file, "audio": audio, "line": line_i}


class RepeatGateTest(unittest.TestCase):
    def test_five_copies_are_flagged_and_line_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            track = write_track(Path(tmp), [2.5, 2.5, 2.5, 2.5])
            report = detect_repeats_file(track["words"], track["audio"])

        self.assertGreaterEqual(report["suspected_extra_rendition"], 4)
        self.assertTrue(report["suspected_lines"])
        hit = report["suspected_lines"][0]
        self.assertEqual(hit["line"], track["line"])
        self.assertEqual(hit["text"], "the repeated last line")
        self.assertGreaterEqual(hit["extra_renditions"], 4)

    def test_quiet_instrumental_stretch_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            track = write_track(Path(tmp), [0.5, 0.5, 30.0, 0.5], quiet=2)
            report = detect_repeats_file(track["words"], track["audio"])

        self.assertEqual(report["suspected_extra_rendition"], 0)
        self.assertEqual(report["suspected_lines"], [])

    def test_one_long_melismatic_word_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            # A single 10 s word is 6.7x the track's pace but only one word is
            # long: that is the reference alignment's melisma signature.
            track = write_track(Path(tmp), [0.5, 0.5, 10.0])
            report = detect_repeats_file(track["words"], track["audio"])

        self.assertEqual(report["suspected_extra_rendition"], 0)
        self.assertEqual(report["suspected_lines"], [])

    def test_no_audio_is_zero_with_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            track = write_track(Path(tmp), [2.5, 2.5, 2.5, 2.5])
            data = json.loads(track["words"].read_text(encoding="utf-8"))
            data.pop("stem")
            track["words"].write_text(json.dumps(data), encoding="utf-8")
            track["audio"].unlink()
            report = detect_repeats_file(track["words"])

        self.assertEqual(report["suspected_extra_rendition"], 0)
        self.assertIn("no vocal stem", report["skipped"])

    def test_audit_surfaces_repeat_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            track = write_track(tmp, [2.5, 2.5, 2.5, 2.5])
            data = json.loads(track["words"].read_text(encoding="utf-8"))
            out = tmp / "out"
            out.mkdir()
            (out / "summary.json").write_text(json.dumps({"songs": [{
                "song": data["song"],
                "duration_s": data["duration_s"],
                "n_words_in": data["n_words_in"],
                "n_words_aligned": data["n_words_aligned"],
                "coverage": data["coverage"],
                "files": {"words_json": str(track["words"])},
            }]}), encoding="utf-8")

            audit_dir = tmp / "audit"
            subprocess.run([
                sys.executable, str(ROOT / "scripts" / "a1_audit.py"),
                "--out", str(out),
                "--pack", str(tmp / "missing-pack.json"),
                "--dir", str(audit_dir),
            ], check=True, capture_output=True, text=True)

            audit = json.loads((audit_dir / "audit.json").read_text(encoding="utf-8"))

        row = audit["tracks"][0]
        self.assertGreaterEqual(row["suspected_extra_rendition"], 4)
        self.assertEqual(row["suspected_lines"], str(track["line"]))
        self.assertIn("the repeated last line",
                      audit["detail"][data["song"]]["repeats"]["suspected_lines"][0]["text"])


if __name__ == "__main__":
    unittest.main()
