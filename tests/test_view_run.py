#!/usr/bin/env python3
"""Tests for the score + karaoke viewer and its batch wrapper.

No MuseScore and no network: the archive/discovery tests use tiny fake trees,
and the end-to-end test writes a one-second WAV and skips if ffmpeg/ffprobe are
not installed.
"""
from __future__ import annotations

import json
import math
import shutil
import struct
import sys
import tarfile
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import score_viewer  # noqa: E402
import view_run  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_take(root: Path, name: str) -> Path:
    track = root / name
    track.mkdir(parents=True)
    (track / "score.abc").write_text("X:1\nM:4/4\nQ:1/4=90\nK:D\n")
    return track


def make_words_take(root: Path, name: str, words=None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    payload = words or {
        "song": name,
        "lines": [{"i": 0, "text": "hello world"}],
        "words": [
            {"i": 0, "word": "hello", "start": 0.1, "end": 0.4, "line": 0},
            {"i": 1, "word": "world", "start": 0.4, "end": 0.8, "line": 0},
        ],
    }
    (root / f"{name}.words.json").write_text(json.dumps(payload))
    return root


def write_wav(path: Path, seconds: float = 1.0, rate: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / rate)))
            for i in range(int(rate * seconds)))
        wav.writeframes(frames)
    return path


def names(takes) -> list[str]:
    return [take.name for take in takes]


class DiscoverTakes(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cache = self.tmp / "cache"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_plain_score_dirs(self) -> None:
        src = self.tmp / "transcripts"
        make_take(src, "b")
        make_take(src, "a")
        self.assertEqual(names(view_run.discover_takes([src], self.cache)),
                         ["a", "b"])

    def test_words_files(self) -> None:
        src = self.tmp / "a1"
        make_words_take(src, "b")
        make_words_take(src, "a")
        takes = view_run.discover_takes([src], self.cache)
        self.assertEqual(names(takes), ["a", "b"])
        self.assertTrue(takes[0].words.is_file())
        self.assertIsNone(takes[0].lab)

    def test_score_and_words_in_one_take(self) -> None:
        src = self.tmp / "transcripts"
        track = make_take(src, "a")
        make_words_take(track, "a")
        takes = view_run.discover_takes([src], self.cache)
        self.assertEqual(names(takes), ["a"])
        self.assertEqual(takes[0].lab, track)
        self.assertEqual(takes[0].words, track / "a.words.json")

    def test_tar_gz_per_take_is_unpacked(self) -> None:
        src = self.tmp / "transcripts"
        track = make_take(src, "a")
        with tarfile.open(src / "a.tar.gz", "w:gz") as tar:
            tar.add(track, arcname="a")
        for f in track.iterdir():
            f.unlink()
        track.rmdir()

        takes = view_run.discover_takes([src], self.cache)
        self.assertEqual(names(takes), ["a"])
        self.assertTrue((takes[0].root / "score.abc").is_file())
        self.assertTrue(takes[0].root.is_relative_to(self.cache))

    def test_zip_with_transcripts_wrapper(self) -> None:
        build = self.tmp / "build"
        tree = build / "transcripts"
        make_take(tree, "p01")
        make_take(tree, "p02")
        archive = self.tmp / "run_abc_transcript.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            for path in tree.rglob("*"):
                zf.write(path, path.relative_to(build))

        takes = view_run.discover_takes([archive], self.cache)
        self.assertEqual(names(takes), ["p01", "p02"])
        self.assertTrue(all(t.root.is_relative_to(self.cache) for t in takes))

    def test_zip_with_toplevel_words_file(self) -> None:
        archive = self.tmp / "words.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("foo.words.json", json.dumps(
                {"words": [{"word": "a", "start": 0, "end": 1, "line": 0}]}))
        takes = view_run.discover_takes([archive], self.cache)
        self.assertEqual(names(takes), ["foo"])
        self.assertTrue(takes[0].words.is_file())

    def test_expanded_take_wins_over_archive(self) -> None:
        src = self.tmp / "transcripts"
        track = make_take(src, "a")
        with tarfile.open(src / "a.tar.gz", "w:gz") as tar:
            tar.add(track, arcname="a")

        takes = view_run.discover_takes([src], self.cache)
        self.assertEqual(names(takes), ["a"])
        self.assertEqual(takes[0].root, track)
        self.assertFalse(self.cache.exists())


class Words(unittest.TestCase):
    def test_a1_schema(self) -> None:
        data = {
            "lines": [{"i": 0, "text": "one two"}, {"i": 1, "text": "three"}],
            "words": [
                {"word": "one", "start": 1.0, "end": 1.2, "line": 0},
                {"word": "two", "start": 1.2, "end": 1.5, "line": 0},
                {"word": "three", "start": 2.0, "end": 2.4, "line": 1},
            ],
        }
        words, lines = score_viewer.normalize_words(data)
        self.assertEqual([w[0] for w in words], ["one", "two", "three"])
        self.assertEqual(lines[0]["t"], "one two")
        self.assertEqual(lines[0]["s"], 1.0)
        self.assertEqual(lines[1]["e"], 2.4)

    def test_a0_schema(self) -> None:
        data = {"words": [{"w": "a", "s": 0.5, "e": 0.9, "l": 0},
                          {"w": "b", "s": 1.0, "e": 1.3, "l": 0}]}
        words, lines = score_viewer.normalize_words(data)
        self.assertEqual(words[0], ["a", 0.5, 0.9, 0])
        self.assertEqual(lines[0]["t"], "a b")

    def test_wordless_lines_are_dropped(self) -> None:
        data = {
            "lines": [{"i": 0, "text": "kept"}, {"i": 1, "text": "empty"}],
            "words": [{"word": "kept", "start": 0.0, "end": 0.5, "line": 0}],
        }
        _, lines = score_viewer.normalize_words(data)
        self.assertEqual([line["i"] for line in lines], [0])


class Chords(unittest.TestCase):
    def test_pretty_chord(self) -> None:
        self.assertEqual(score_viewer.pretty_chord("D:maj"), "D")
        self.assertEqual(score_viewer.pretty_chord("D:min"), "Dm")
        self.assertEqual(score_viewer.pretty_chord("G:min7"), "Gm7")
        self.assertEqual(score_viewer.pretty_chord("B:hdim"), "Bm7b5")
        self.assertEqual(score_viewer.pretty_chord("C"), "C")


class Lab(unittest.TestCase):
    def test_load_lab(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "downbeat.lab").write_text("0.0\n1.5\n3.0\n")
            (d / "beat.lab").write_text("0.0 1\n0.5 2\n1.0 3\n1.5 4\n")
            (d / "chord.lab").write_text("0.0 1.5 D:maj\n1.5 3.0 G:maj\n")
            (d / "structure.lab").write_text("0.0 3.0 intro\n")
            (d / "melody_vocal.lab").write_text("0.5 1.0 62\n")
            (d / "melody_instrumental.lab").write_text("0.0 0.4 50\n")
            lab = score_viewer.load_lab(d, duration=10.0)
            self.assertEqual(lab["bars"], [0.0, 1.5, 3.0])
            self.assertEqual(lab["beats"][1], [0.5, 2])
            self.assertEqual(lab["chords"][0][2], "D")
            self.assertEqual(lab["sections"][0][2], "intro")
            self.assertEqual(lab["notes"], [[0.5, 1.0, 62, 0],
                                            [0.0, 0.4, 50, 1]])


class Render(unittest.TestCase):
    def test_render_html_contains_words_and_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "take" / "viewer.html"
            data = {
                "duration": 3.0, "bars": [], "beats": [], "chords": [],
                "sections": [], "notes": [], "peaks": [], "pages": ["pages/p-1.png"],
                "barsPerPage": 0, "zoomSpan": 3.0, "key": "D", "meter": [4, 4],
                "bpm": 90, "audio": "audio.mp3", "totalBars": 1,
                "words": [["hello", 0.1, 0.4, 0]],
                "lines": [{"i": 0, "t": "hello", "s": 0.1, "e": 0.4}],
            }
            score_viewer.render_html(data, out, "take")
            text = out.read_text()
            self.assertIn("hello", text)
            self.assertIn("pages/p-1.png", text)
            self.assertIn("const DATA", text)
            self.assertIn("audio.mp3", text)

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg/ffprobe not installed")
    def test_viewer_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = write_wav(root / "take.wav")
            words = make_words_take(root, "take")
            out = root / "out" / "take" / "viewer.html"
            rc = score_viewer.main([
                "--audio", str(audio),
                "--words", str(words / "take.words.json"),
                "--no-pages", "--out", str(out), "--title", "take",
            ])
            self.assertEqual(rc, 0)
            self.assertTrue(out.is_file())
            text = out.read_text()
            self.assertIn("hello", text)
            self.assertTrue("audio.wav" in text or "take.wav" in text)

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg/ffprobe not installed")
    def test_batch_wrapper_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            write_wav(run / "take.wav")
            make_words_take(run, "take")
            out = Path(tmp) / "score"
            rc = view_run.main(["--run", str(run), "--out", str(out),
                                "--no-pages"])
            self.assertEqual(rc, 0)
            self.assertTrue((out / "index.html").is_file())
            self.assertTrue((out / "take" / "viewer.html").is_file())
            self.assertTrue((out / "take" / "audio.wav").is_file())


if __name__ == "__main__":
    unittest.main()
