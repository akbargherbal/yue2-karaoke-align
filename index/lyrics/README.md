# index/lyrics — the lyric identity index (v2)

Status: **committed snapshot, 2026-10-28, v2 = human review applied.** The 54
`ambiguous` rows were adjudicated in the browser app (`docs/REVIEW-APP.md`):
**45 `match`, 9 `no_match`**, 0 left unsure. The 5 remaining `ambiguous` rows are
long live/concert tracks outside the active scope.

It contains **no lyric text**. Only titles, source URLs, counts and match
decisions. The lyric bodies stay in `arabic-music-lyrics`.

| File | Rows | What it is |
|---|---:|---|
| `inventory.csv` | 236 | audio identity: `track_id`, artist, album, file, duration, instrumental flag |
| `lyrics_index.csv` | 221 | one row per unique lyric title (metadata only) |
| `title_map.csv` | 236 | audio track → lyric sheet map: machine candidates + the human decision |
| `coverage.csv` | 236 | join of inventory × title map × readiness |
| `readiness.csv` | 170 | matched tracks only: is the sheet alignable as written? |
| `summary.json` | — | aggregate counts (the numbers quoted in the docs) |

`inventory.csv` is the **236-track superset**. The active scope is **214 tracks
(≤ 360 s)**; the 22 long live/concert tracks are outside it (the stem cache
already dropped them). Filter, do not re-scrape:

```bash
python scripts/lyrics_coverage.py \
    --inventory index/lyrics/inventory.csv \
    --lyrics-root /path/to/arabic-music-lyrics/lyrics \
    --out work/lyrics --max-duration 360
```

Verified counts, 2026-10-28:

| Scope | match | ambiguous | no lyrics | ready | needs_cleanup | needs_expansion |
|---|---:|---:|---:|---:|---:|---:|
| 236 | 170 | 5 | 61 | 10 | 3 | 157 |
| **214** | **163** | **0** | **51** | **10** | **3** | **150** |

"no lyrics" counts both `no_match` (9, reviewed) and `missing` (no candidate).

## What the review changed

- matched: **118 → 163** on the 214 scope (+45);
- alignable as written (`ready`): **9 → 10**. Identity was not the blocker —
  completeness is: the 45 new matches are median **24.5 wpm** (sung is 60–150),
  i.e. compact sheets with the chorus written once;
- 3 of the 45 overrode the matcher's ranking (candidate 2 was right);
- 5 decisions had their `lyric_source` normalised (`fnanen → kalimataghani`)
  because the index keeps one row per title; the note column records it.

## Contract for `title_map.csv`

- `auto_decision` is what the matcher would do — **never edit**;
- `decision` / `lyric_title` / `lyric_source` / `reviewer` / `reviewed_at` /
  `notes` are the human record; a re-run of `lyrics_coverage.py` preserves them
  keyed by `track_id`;
- a changed decision means a new revision of this file (git is the version),
  never an in-place rewrite of history;
- `not_applicable` is for instrumentals and `(Music)` entries only.

Rules are in [`docs/CROSS-PROJECT.md`](../../docs/CROSS-PROJECT.md) §5.2.

## Provenance

Inputs (read-only, not vendored here):

- `music_captioning_oct_2026/outputs/{ELISSA,FADL_SHAKER}` — durations, audio identity
- `arabic-music-lyrics/lyrics/{fnanen,kalimataghani}/artists/{elissa,fadhl-shaker}` — titles + text
- `gs://akbar-december-2024-backup/DISCOGRAPHY/` — authoritative audio listing

Built with `scripts/gcs_inventory.py` → `scripts/lyrics_coverage.py` (skeleton
matching with qaf/vowel/article variants, `--auto-threshold 93`,
`--ambiguous-threshold 80`), reviewed through `scripts/review_app.py`, merged
with `scripts/review_export.py`.

## What is not here

- **No lyric bodies.** `lyrics_coverage.py` writes metadata only.
- **No audio, stems or alignments.** Those live in
  `gs://akbar-december-2024-backup/STEMS/htdemucs/v1/` and `work/srt/`.
- **No long-track decisions.** The 5 `ambiguous` rows are >360 s; they were
  never in the review queue.
