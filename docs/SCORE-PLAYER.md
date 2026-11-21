# SCORE-PLAYER.md — the score and the karaoke line on one timeline

Status: **optional inspection surface, 2026-11-21.** This is a viewer, not a new
alignment path and not an accuracy claim.

The A1 player (`<song>.player.html`) highlights words but has no musical
context. The `score-lab` viewer (`akbargherbal/score-lab`, MIT) has the musical
context — waveform, bar grid, chords, sections, piano roll, engraved score pages
— but no lyrics. This page describes the hybrid pulled into this repo: **score
page + karaoke lane + word blocks, all driven by one clock.**

## What was pulled, and what was added

| Piece | Source | Here |
|---|---|---|
| batch take discovery, `.tar.gz`/`.zip` unpacking, `gs://` audio fetch, index gallery | `score-lab/tools/view_run.py` | `scripts/view_run.py` |
| waveform, bars, chords, sections, piano roll, score-page sync | `score-lab/tools/score_viewer.py` + `viewer-template.html` | `scripts/score_viewer.py` + `scripts/score_player-template.html` |
| word-by-word karaoke lane, word blocks, A1 `words.json` input | new | same two files |

The bridge stays thin and read-only: nothing is imported from `score-lab` at
runtime, and no SheetSage2 or MuseScore install is required unless you ask for
score pages. See [CROSS-PROJECT.md](CROSS-PROJECT.md) §10.8 for the caveat on
`.lab` timings.

## Inputs

Both input shapes can be mixed in one run.

### A1 output (karaoke + optional score)

```text
<run>/
├── <song>.words.json          # A1: word timings + lines + QA
├── <song>.wav | .mp3          # or pass --audio-dir
└── <song>.musicxml            # optional, only for score pages
```

### SheetSage2 transcript tree (score-lab contract)

```text
<run>/transcripts/<take>/
├── score.abc                  # key / meter / tempo / bar count
├── downbeat.lab               # bar start times
├── beat.lab                   # beat times + beat number
├── chord.lab                  # start end chord
├── structure.lab              # start end section
├── melody_vocal.lab           # start end midi_pitch
└── melody_instrumental.lab    # start end midi_pitch
```

`<run>/<take>.musicxml` and `<take>.mp3` are optional. Transcripts may also be
per-take archives (`<take>.tar.gz`) or one whole-tree `.zip`; they are unpacked
under `<out>/.transcripts/`, never inside the run.

## Commands

### One take

```bash
# karaoke + score pages (MuseScore is used only for --musicxml)
python scripts/score_viewer.py \
    --audio take.mp3 \
    --words take.words.json \
    --musicxml take.musicxml \
    --out out/take/viewer.html --title "take"

# karaoke only: no MuseScore, no MusicXML, no page images
python scripts/score_viewer.py \
    --audio take.mp3 --words take.words.json --no-pages \
    --out out/take/viewer.html

# score-lab transcript tree, with or without score pages
python scripts/score_viewer.py \
    --audio take.mp3 --lab transcripts/take \
    --pages out/take/pages --out out/take/viewer.html
```

### A whole run

```bash
# A1 results dir; audio and words already together
python scripts/view_run.py --run results/a1/yue2-real --out /tmp/view --no-pages

# YuE2 run dir, audio fetched from GCS
python scripts/view_run.py --run /path/to/out/<run> \
    --audio-dir gs://<bucket>/.../<run> \
    --out /path/to/out/<run>/score --no-pages

# score-lab transcript tree (the upstream command shape)
python scripts/view_run.py --run <run-dir> \
    --transcripts transcripts \
    --audio-dir gs://<bucket>/.../<run> \
    --out <run-dir>/score --no-pages
```

`--out` may also point outside the run (e.g. `~/score-view/<run>`); the run is
only written to when you choose an output path inside it.

`--transcripts` defaults to `<run>/transcripts/` when it exists, else the run
dir. `--musicxml-dir` adds a score library to search when the MusicXML is not
next to the take. `--limit N` is for a smoke test; `--force` rebuilds viewers;
audio already cached in `<out>/<take>/audio.*` is never re-fetched.

`view_run.py` **does not align**. It consumes existing `<song>.words.json`.
Run [`scripts/a1_align_run.py`](../scripts/a1_align_run.py) first.

## Output

```text
<out>/
├── index.html                 # duration, words, lines, coverage, bars, chords
├── <take>/
│   ├── viewer.html            # self-contained page, opens from file://
│   ├── audio.mp3              # copied/fetched so the page is portable
│   └── pages/p-1.png …        # optional score pages
└── .transcripts/              # archive unpack cache (only if archives were used)
```

## How the timeline is built

- **Karaoke lane** — reads `<song>.words.json` (A1 schema, or the older a0
  `w/s/e/l` schema), derives one line block per lyric line, highlights the
  current word, and shows the previous/next line. Word blocks are also drawn
  along the bottom of the waveform.
- **Score pages** — `--musicxml` renders pages with `mscore-cli`, `musescore`,
  `MuseScore4` or `mscore` (whichever is on `PATH`) into `<out>/pages/p-N.png`.
  Pillow downscales them when installed. `--no-pages` skips all of this.
  This repo consumes **MusicXML**, not ABC: if your transcription is
  `score.abc`, convert it first (score-lab's `abc_to_musicxml.py`, MIT, does
  this) or export MusicXML from the captioning pipeline. The `.lab` files are
  still read directly for bars/chords/sections/notes.
- **Page sync** — when `downbeat.lab` exists, the current bar selects the page
  by a linear bars-per-page split. Without bars, time selects the page
  linearly. This is **approximate**: MuseScore does not export bar coordinates.
- **Chords, sections, piano roll** — read from the `.lab` files when present.
  Chord labels are prettified (`D:maj` → `D`, `D:min` → `Dm`).
- **Transport** — play/pause, click-to-seek, `Space`, `←`/`→`, 8-bar zoom,
  `viewer.html#t=75` deep link.

## Limits

- `.lab` timings are SheetSage2 output and **unverified** here
  ([CROSS-PROJECT.md](CROSS-PROJECT.md) §10.8). Chords, sections and notes are
  only as good as that transcript.
- Score-page sync is approximate, for the same reason.
- The viewer is an inspection surface, not a dataset artifact. The source of
  truth remains `<song>.words.json` and the line-level `.srt`/`.vtt`.
- No lyric text or audio is committed; the viewer copies audio into `<out>` so a
  page travels with its take.

## Attribution

`scripts/score_viewer.py`, `scripts/view_run.py` and
`scripts/score_player-template.html` are adapted from
[`akbargherbal/score-lab`](https://github.com/akbargherbal/score-lab), MIT,
Copyright (c) 2026 Akbar Gherbal. See
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md). The karaoke lane and the
A1 `words.json` input are new here; the batch/archive/`gs://` mechanics and the
score-lab lanes are preserved.

## Tests

```bash
python3 -m unittest discover tests -q
```

The viewer tests use fake `.lab`/words trees and a one-second WAV; they skip the
end-to-end cases when `ffmpeg`/`ffprobe` are unavailable. MuseScore is never
required by the tests.
