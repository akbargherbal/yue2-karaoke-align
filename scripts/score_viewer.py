#!/usr/bin/env python3
"""Build one synchronized score + karaoke page for a single take.

This is the viewer core ported from `akbargherbal/score-lab` (MIT), adapted to
this repo's data model:

  * **karaoke lane** — the A1 `<song>.words.json` word timings drive a
    word-by-word lyric highlight, plus word blocks on the waveform;
  * **score page** — optional PNG pages rendered from a MusicXML score, synced
    to the current bar (or linearly when there are no bar timings);
  * **musical lanes** — optional waveform, bar grid, chords, sections and a
    vocal/instrumental piano roll from a SheetSage2-style `.lab` directory.

Every input except `--audio` is optional. With only `--words` you get a karaoke
timeline; with only `--lab` you get the score-lab view; with both you get the
hybrid the two projects were missing.

Usage
-----
    # karaoke + score pages (MuseScore needed only for --musicxml)
    python scripts/score_viewer.py --audio take.mp3 \
        --words take.words.json --musicxml take.musicxml \
        --out out/take/viewer.html --title "take"

    # karaoke only, no score, no MuseScore
    python scripts/score_viewer.py --audio take.mp3 \
        --words take.words.json --no-pages --out out/take/viewer.html

    # score-lab transcript tree (score.abc + *.lab)
    python scripts/score_viewer.py --audio take.mp3 --lab transcripts/take \
        --pages out/take/pages --out out/take/viewer.html

For a whole run, use `scripts/view_run.py`.
"""
from __future__ import annotations

import argparse
import array
import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "score_player-template.html"
AUDIO_SUFFIXES = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus")
PRETTY_CHORDS = {"min": "m", "maj": "", "dim": "dim", "aug": "aug", "hdim": "m7b5"}


def pretty_chord(raw: str) -> str:
    """`D:maj` -> `D`, `D:min` -> `Dm`, `G:min7` -> `Gm7` (score-lab helper)."""
    if ":" not in raw:
        return raw
    root, quality = raw.split(":", 1)
    for suffix, repl in PRETTY_CHORDS.items():
        if quality == suffix:
            return root + repl
        if quality.startswith(suffix) and suffix != "maj":
            return root + repl + quality[len(suffix):]
    return root + quality


# --- small readers ---------------------------------------------------------

def ffprobe_duration(audio: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", str(audio)],
        capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def read_pairs(path: Path) -> list[list[str]]:
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            rows.append(line.split())
    return rows


def read_ranged(path: Path) -> list[list]:
    """`start end label` (label may contain spaces)."""
    return [[float(p[0]), float(p[1]), " ".join(p[2:])]
            for p in read_pairs(path) if len(p) >= 2]


def read_melody(path: Path) -> list[list[float]]:
    return [[float(p[0]), float(p[1]), int(float(p[2]))]
            for p in read_pairs(path) if len(p) >= 3]


def parse_score_header(abc: Path) -> dict:
    head = abc.read_text(encoding="utf-8", errors="replace").splitlines()[:16]

    def get(prefix: str, default: str = "") -> str:
        for line in head:
            if line.startswith(prefix):
                return line[2:].strip()
        return default

    meter_s = get("M:", "4/4")
    try:
        meter = [int(x) for x in meter_s.split("/")]
        if len(meter) != 2:
            meter = [4, 4]
    except ValueError:
        meter = [4, 4]

    bpm = 0
    match = re.search(r"=\s*(\d+)", get("Q:", ""))
    if match:
        bpm = int(match.group(1))
    return {"key": get("K:"), "meter": meter, "bpm": bpm}


def load_lab(lab_dir: Path | None, duration: float) -> dict:
    """Read the optional SheetSage2 side artifacts, tolerating any missing one."""
    out: dict = {"bars": [], "beats": [], "chords": [], "sections": [],
                 "notes": []}
    if not lab_dir:
        return out
    lab_dir = Path(lab_dir)

    def attempt(name: str, key: str, fn) -> None:
        path = lab_dir / name
        if not path.is_file():
            return
        try:
            out[key] = fn(path)
        except (OSError, ValueError, IndexError) as exc:
            print(f"warn: skipping {path}: {exc}", file=sys.stderr)

    attempt("downbeat.lab", "bars",
            lambda p: [float(r[0]) for r in read_pairs(p)
                       if float(r[0]) < duration])
    attempt("beat.lab", "beats",
            lambda p: [[float(r[0]), int(float(r[1]))]
                       for r in read_pairs(p)
                       if len(r) >= 2 and float(r[0]) < duration])
    attempt("chord.lab", "chords",
            lambda p: [[a, b, pretty_chord(c)] for a, b, c in read_ranged(p)
                       if a < duration])
    attempt("structure.lab", "sections",
            lambda p: [r for r in read_ranged(p) if r[0] < duration])
    attempt("melody_vocal.lab", "vocal",
            lambda p: [[a, b, pitch, 0] for a, b, pitch in read_melody(p)])
    attempt("melody_instrumental.lab", "instrumental",
            lambda p: [[a, b, pitch, 1] for a, b, pitch in read_melody(p)])

    out["notes"] = out.pop("vocal", []) + out.pop("instrumental", [])
    return out


def peaks_from_audio(audio: Path, buckets: int = 3000) -> list[list[float]]:
    """Mono 4 kHz PCM -> [lo, hi] per bucket, normalised to the loudest bucket."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(audio),
         "-ac", "1", "-ar", "4000", "-f", "s16le", "-"],
        capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        print(f"warn: could not decode {audio} for the waveform", file=sys.stderr)
        return []
    samples = array.array("h")
    samples.frombytes(proc.stdout)
    if sys.byteorder == "big":
        samples.byteswap()
    step = max(1, len(samples) // buckets)
    out = []
    for i in range(0, len(samples), step):
        chunk = samples[i:i + step]
        if not chunk:
            break
        out.append([min(chunk) / 32768.0, max(chunk) / 32768.0])
    peak = max((max(abs(lo), abs(hi)) for lo, hi in out), default=0) or 1.0
    return [[lo / peak, hi / peak] for lo, hi in out]


# --- A1 words --------------------------------------------------------------

def normalize_words(data: dict) -> tuple[list[list], list[dict]]:
    """A1 `words`/`lines` -> the compact `[word, start, end, line]` shape.

    Accepts both the A1 schema (`word`/`start`/`end`/`line`) and the older a0
    player schema (`w`/`s`/`e`/`l`). Lines without words are dropped: the
    karaoke lane only has something to show where a word exists.
    """
    words: list[list] = []
    for w in data.get("words") or []:
        text = w.get("word", w.get("w", ""))
        start = w.get("start", w.get("s"))
        end = w.get("end", w.get("e"))
        if start is None or end is None:
            continue
        line = int(w.get("line", w.get("l", 0)))
        words.append([text, round(float(start), 3), round(float(end), 3), line])

    declared = {}
    for pos, line in enumerate(data.get("lines") or []):
        lid = int(line.get("i", pos))
        declared[lid] = str(line.get("text", line.get("t", "")))

    grouped: dict[int, list[list]] = {}
    for word in words:
        grouped.setdefault(word[3], []).append(word)

    lines = []
    for lid in sorted(grouped):
        ws = grouped[lid]
        lines.append({
            "i": lid,
            "t": declared.get(lid) or " ".join(w[0] for w in ws),
            "s": round(min(w[1] for w in ws), 3),
            "e": round(max(w[2] for w in ws), 3),
        })
    lines.sort(key=lambda line: (line["s"], line["i"]))
    return words, lines


# --- score pages -----------------------------------------------------------

def page_number(path: Path) -> int:
    match = re.search(r"(?:p|page)-(\d+)", path.name)
    return int(match.group(1)) if match else 0


def sorted_pages(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    pages = list(directory.glob("p-*.png")) + list(directory.glob("page-*.png"))
    return sorted(set(pages), key=page_number)


def render_pages(musicxml: Path, pages_dir: Path) -> list[Path]:
    """Render a MusicXML score to `p-N.png` pages with MuseScore, if available.

    MuseScore writes `raw.png` for a one-page score and `raw-N.png` for a
    multi-page one. Page images are downscaled with Pillow when it is present;
    otherwise the raw PNGs are kept.
    """
    pages_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted_pages(pages_dir)
    if existing:
        return existing

    mscore = (shutil.which("mscore-cli") or shutil.which("musescore")
              or shutil.which("MuseScore4") or shutil.which("mscore"))
    if not mscore:
        print("warn: no MuseScore found; building without score pages "
              "(install MuseScore or pass --no-pages)", file=sys.stderr)
        return []

    raw = pages_dir / "raw.png"
    try:
        subprocess.run(
            [mscore, "-o", str(raw), str(musicxml)],
            check=True, capture_output=True, text=True,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"warn: MuseScore could not render {musicxml}: {exc}",
              file=sys.stderr)
        return []

    numbered = sorted(pages_dir.glob("raw-*.png"))
    raws = numbered or ([raw] if raw.is_file() else [])
    for src in raws:
        match = re.search(r"raw(?:-(\d+))?\.png$", src.name)
        number = match.group(1) if match and match.group(1) else "1"
        dest = pages_dir / f"p-{number}.png"
        try:
            from PIL import Image
            image = Image.open(src).convert("RGB")
            image.thumbnail((1200, 1700))
            image.save(dest, optimize=True)
        except Exception:  # Pillow missing or unreadable: keep the raw page
            shutil.copy2(src, dest)
        if src != dest:
            src.unlink()
    return sorted_pages(pages_dir)


def copy_pages(src: Path, dest: Path) -> list[Path]:
    if not src.is_dir() or src.resolve() == dest.resolve():
        return sorted_pages(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for page in sorted_pages(src):
        shutil.copy2(page, dest / page.name)
    return sorted_pages(dest)


# --- build -----------------------------------------------------------------

def build_data(audio: Path, words_data: dict | None, lab_dir: Path | None,
               pages: list[Path], audio_rel: str) -> dict:
    duration = ffprobe_duration(audio)
    lab = load_lab(lab_dir, duration)
    words, lines = normalize_words(words_data or {})

    header = {"key": "", "meter": [4, 4], "bpm": 0}
    if lab_dir and (Path(lab_dir) / "score.abc").is_file():
        header = parse_score_header(Path(lab_dir) / "score.abc")

    bars = lab["bars"]
    return {
        "duration": round(duration, 3),
        "bars": [round(b, 4) for b in bars],
        "beats": lab["beats"],
        "chords": lab["chords"],
        "sections": lab["sections"],
        "notes": lab["notes"],
        "peaks": peaks_from_audio(audio),
        "pages": [f"pages/{p.name}" for p in pages],
        "barsPerPage": max(1, len(bars) / len(pages)) if pages and bars else 0,
        "zoomSpan": 8 * (duration / max(1, len(bars))) if bars else min(duration, 30),
        "key": header["key"],
        "meter": header["meter"],
        "bpm": header["bpm"],
        "audio": audio_rel,
        "totalBars": len(bars) + 1,
        "words": words,
        "lines": lines,
    }


def render_html(data: dict, out: Path, title: str) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    text = (TEMPLATE.read_text(encoding="utf-8")
            .replace("__TITLE__", html.escape(title))
            .replace("__AUDIO__", html.escape(data["audio"]))
            .replace("__DATA__", payload))
    out.write_text(text, encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audio", type=Path, required=True,
                    help="the take's audio file")
    ap.add_argument("--words", type=Path, default=None,
                    help="A1 <song>.words.json (word-level karaoke)")
    ap.add_argument("--lab", "--abc", dest="lab", type=Path, default=None,
                    help="dir with score.abc + *.lab (score-lab transcript)")
    ap.add_argument("--pages", type=Path, default=None,
                    help="dir of p-N.png score pages")
    ap.add_argument("--musicxml", type=Path, default=None,
                    help="render score pages from this MusicXML (needs MuseScore)")
    ap.add_argument("--out", type=Path, required=True,
                    help="viewer HTML to write")
    ap.add_argument("--title", default=None)
    ap.add_argument("--no-pages", action="store_true",
                    help="never render or show score pages")
    args = ap.parse_args(argv)

    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("error: ffmpeg and ffprobe are required for the waveform and "
              "duration", file=sys.stderr)
        return 2

    out = args.out
    pages_dir = out.parent / "pages"
    pages: list[Path] = []
    if not args.no_pages:
        if args.pages:
            pages = copy_pages(args.pages, pages_dir)
        elif args.musicxml:
            pages = render_pages(args.musicxml, pages_dir)

    words_data = None
    if args.words:
        words_data = json.loads(args.words.read_text(encoding="utf-8"))

    audio_rel = os.path.relpath(args.audio.resolve(), out.resolve().parent)
    data = build_data(args.audio, words_data, args.lab, pages, audio_rel)
    title = args.title or out.parent.name
    render_html(data, out, title)

    print(f"wrote {out}  ({len(data['words'])} words, {len(data['lines'])} lines, "
          f"{len(pages)} pages, {len(data['bars'])} bars, "
          f"{data['duration']:.0f}s audio)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
