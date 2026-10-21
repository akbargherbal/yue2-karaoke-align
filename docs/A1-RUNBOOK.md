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
HTML player.

## Run it on a YuE2 run directory

The sibling's `generate.py` / `run_one.sh` layout is:

```
out/<run>/
  <name>_<seed>.wav
  prompts/<name>_lyrics.txt
```

so:

```bash
cd yue2-karaoke-align
.venv/bin/python scripts/a1_align_run.py \
    --run-dir /path/to/out/20261014-093128_batch_02_formatted \
    --device cpu --sep-device cpu
```

The run directory is **read only**. Vocal stems are cached under
`work/stems/htdemucs/<wav stem>/vocals.wav` and reused on later runs.

## Other input modes

```bash
# a flat staged directory: <name>.mp3/.wav + <name>.txt
.venv/bin/python scripts/a1_align_run.py --data-dir data/yue2_songs --auto-songs

# explicit song list (same format as scripts/bench.py)
.venv/bin/python scripts/a1_align_run.py --songs bench/songs.json --data-dir data/yue2_songs
```

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

## Smoke result (2026-10-21)

The pipeline was smoke-tested on the A0a Suno track (no YuE2 render was local):

| | |
|---|---|
| Subject | `ajam_Lamiyat_Alshanfara_16082026_004_bb17d099` (198.9 s) |
| Words | 120/120, 24 line cues |
| First word | **21.481 s** (A0a: 21.48 s) |
| Coverage / on-off / intro | 0.971 / 2.15 / ok |
| Cost | Demucs 50 s + align ≈ 35 s on CPU |

Artifacts: `results/a1/suno-smoke/`.

The remaining real-data check is A1 on a **YuE2 run directory** (the B2 songs
were staged flat on the Colab box, not as a full run dir). The command is above.
