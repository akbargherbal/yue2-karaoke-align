# A1-RUNBOOK.md — batch alignment to karaoke assets

A1 is the hardened version of the A0 trial: **one command** turns a directory of
rendered songs into word timings, subtitles, and a playable karaoke page.

It uses the production aligner chosen in B2
(`akbargherbal/wav2vec2-large-xlsr-53-arabic`, pinned in `bench/models.lock`)
and a **forced** aligner — the lyrics are known, so it cannot invent words.

## What it writes

For a run label `<run>` (default output `results/a1/<run>/`):

```
<song>.words.json      word timings + QA + lyric lines (source of truth)
<song>.vtt             line-level WebVTT
<song>.srt             line-level SRT
<song>.player.html     self-contained word-highlighting player (mix audio)
summary.json           machine-readable run summary
A1-REPORT.md           QA table + links
index.html             links to every song player
```

Subtitles are **line-level** on purpose: one cue per lyric line is what a
subtitle player can display. Word-level timing lives in the JSON and drives the
HTML player. The player links its audio by relative path (default: the full
mix), so it travels with the audio it was generated from; move the WAV and
regenerate the page (timings are unchanged).

## Input: the minimum shape

One song is enough: an audio file plus a UTF-8 lyrics file. There is **no
manifest, no timestamps, and no pre-separation required**.

### A. Run directory (recommended — matches the sibling)

```
my_song/
  my_song_1234.wav
  prompts/
    my_song_lyrics.txt
```

```bash
.venv/bin/python scripts/a1_align_run.py --run-dir /path/to/my_song \
    --device cpu --sep-device cpu
```

Rules:

- Audio sits directly in the run directory. `.wav`, `.flac`, `.mp3` are accepted.
- The prompt is exactly `prompts/<name>_lyrics.txt`. `<name>` must equal the WAV
  stem or be a prefix ending at `_`: `my_song` matches `my_song.wav` **and**
  `my_song_1234.wav`.
- The `prompts` directory name is literal.
- Outputs are keyed by the **WAV stem**, so two seeds of the same lyric
  (`my_song_111.wav`, `my_song_222.wav`) do not collide.
- The run directory is **read only**; output defaults to
  `results/a1/<directory-name>/`.
- Vocal stems are cached under `work/stems/htdemucs/<wav stem>/vocals.wav` and
  reused on later runs.

### B. Flat directory (exactly two files, same basename)

```
my_song/
  my_song.mp3
  my_song.txt
```

```bash
.venv/bin/python scripts/a1_align_run.py --data-dir /path/to/my_song --auto-songs
```

`--auto-songs` pairs `<name>.txt` with `<name>.mp3` (or `.wav`). For `.flac`, or
if the basenames differ, use mode C.

### C. Explicit song list (anything non-standard)

```json
[
  {"name": "my_song", "audio": "my_song.flac", "caption": "lyrics/my_song.txt"}
]
```

```bash
.venv/bin/python scripts/a1_align_run.py --songs songs.json --data-dir /path/to/dir
```

Paths are relative to `--data-dir` (or to the JSON file if `--data-dir` is
omitted). `name` controls the output filename and report label, so it must be
unique per song.

### Lyrics file rules

- UTF-8 text, **one sung line per line**. Line breaks become VTT/SRT cues and
  player line breaks.
- Arabic script, diacritized is preferred. The aligner's vocabulary contains the
  full harakat set; it strips them only if a model lacks them.
- Section tags (`[Verse 1 | ...]`), an optional `[Lyrics]` marker, and any line
  with no Arabic letter (banners, English headings, translations) are dropped
  automatically.
- Include **only text that is actually sung**. Repeat repeated lines. Omit
  unsung ad-libs/translations: extra words shift the forced path, and missing
  words cannot be recovered by the aligner.
- Punctuation at word edges is ignored. Timestamps are not used and will be
  treated as words — don't include them.

### Audio rules

- Anything `soundfile` can read (WAV/FLAC/MP3); stereo is fine and is downmixed
  to 16 kHz mono internally.
- Do **not** pre-separate: Demucs runs automatically and the stem is cached.
- If you already have a vocal stem, place it at
  `<stems-dir>/htdemucs/<wav stem>/vocals.wav` and pass `--skip-separation`.

## Rehearse first

```bash
.venv/bin/python scripts/a1_align_run.py --run-dir <run> --dry-run
```

`--dry-run` lists the audio/lyrics matched for each song and the output
directory; it does not load torch/Demucs or write anything. Use it to catch a
prompt-name mismatch before paying for separation.

## Useful flags

| flag | meaning |
|---|---|
| `--out DIR` | output directory (default `results/a1/<label>`) |
| `--force` | re-align even if `<song>.words.json` already exists |
| `--limit N` | only the first N songs (smoke test) |
| `--player-audio stem` | player plays the isolated vocal instead of the mix |
| `--no-player` | skip HTML (JSON/SRT/VTT still written) |
| `--skip-separation` | require existing stems, never run Demucs |
| `--model ID` / `--lock FILE` | override the production model / pins |
| `--device` / `--sep-device` | aligner and Demucs devices |

Re-running is cheap: existing `.words.json` files are reused and only the
derived VTT/SRT/player files are regenerated. `--force` re-aligns.

## Read the report like a sceptic

`A1-REPORT.md` carries the same checks B2 used to eliminate bad models:

1. **words aligned / words in** — should be equal. A collapse means the model's
   vocabulary lost the word delimiter (the A0 bug).
2. **coverage** — last word end / audio duration; ≈0.95–1.0 is healthy.
3. **on/off** — energy on word spans vs gaps; >1 means the words sit on the
   voice. Below 1 means the alignment is upside down.
4. **intro** — word 0 must not start inside the vocal stem's measured leading
   silence. A `VIOLATION` is the MMS-1b failure signature.
5. **warnings** — long gaps are expected at line boundaries; a runaway last word
   or non-monotone path is not.

Then **listen**. Numbers are necessary, not sufficient; open
`<song>.player.html` and check the first few lines for drift.

## Validation runs (2026-10-21)

### Smoke — A0a Suno track

No YuE2 render was staged locally at first, so the pipeline was smoke-tested on
the A0a proxy:

| | |
|---|---|
| Subject | `ajam_Lamiyat_Alshanfara_16082026_004_bb17d099` (198.9 s) |
| Words | 120/120, 24 line cues |
| First word | **21.481 s** (A0a: 21.48 s) |
| Coverage / on-off / intro | 0.971 / 2.15 / ok |
| Cost | Demucs 50 s + align ≈ 38 s on CPU |

Artifacts: `results/a1/suno-smoke/`.

### Real — a 4-song YuE2 run directory

Run `20261021-045926_ibn_zuraiq_21102026_backing_formatted`, fetched read-only
from the GCS backup into `/tmp`, one render per prompt:

| song | words | coverage | on/off | intro | cues |
|---|---|---|---|---|---|
| `01-العذل-والمطامع-وفلسفة-الرزق_3064210` | 172/172 | 0.945 | 2.41 | ok | 32 line |
| `02-استيداع-قمر-بغداد-ولوعة-الوداع_1072565065` | 125/125 | 1.000 | 2.89 | ok | 24 line |
| `03-تجرع-مرارة-البين-ووخز-الندم_1891521623` | 148/148 | 0.977 | 3.02 | ok | 26 line |
| `04-مناجاة-ديار-الأنس-ورجاء-التلاقي_2395913353` | 125/125 | 0.912 | 1.73 | ok | 24 line |

**4/4 songs, 570 words, 0 silent-intro violations.** Artifacts and the
per-song players: `results/a1/yue2-real/`.

Warnings are the expected ones, not path loss:

- Long gaps (up to 30 s) all fall on **low-energy instrumental sections**
  (gap RMS ~0.02–0.06 vs ~0.13 on word spans), i.e. the aligner correctly
  waited through intros/interludes.
- Song 1's last word absorbs the outro (**11.4 s**); songs 2's absorbs 6.5 s.
  This is the same A0a caveat — harmless for line display, wrong for exact
  word timing at the tail.
- Song 4 tracks words at on/off **1.73** (still on the voice) with coverage
  0.912 — the weakest of the four, worth a listen before trusting word-level
  precision there.

The real-data test also found and fixed two things the smoke track could not:
the sibling prompts' `///***///` banner was being counted as a lyric word, and
a vocabulary-dropped word shifted every later line (alignment now returns
`src_i`, so line mapping survives drops).
