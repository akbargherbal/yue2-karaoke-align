# index/lyrics — the lyric identity index (v1, machine-seeded)

Status: **committed snapshot, 2026-10-28.** This is the L0–L4 output of
[`docs/CROSS-PROJECT.md`](../../docs/CROSS-PROJECT.md) §6, kept in git because
`work/` is gitignored and these tables are expensive to reproduce from the
external clones.

It contains **no lyric text**. Only titles, source URLs, counts and match
decisions. The lyric bodies stay in `arabic-music-lyrics` (see §"What is not
here").

| File | Rows | What it is |
|---|---:|---|
| `inventory.csv` | 236 | audio identity: `track_id`, artist, album, file, duration, instrumental flag |
| `lyrics_index.csv` | 221 | one row per unique lyric title (metadata only) |
| `title_map.csv` | 236 | audio track → lyric sheet map, with machine candidates and the human decision column |
| `coverage.csv` | 236 | join of inventory × title map × readiness |
| `readiness.csv` | 125 | matched tracks only: is the sheet alignable as written? |
| `summary.json` | — | aggregate counts (the numbers quoted in the docs) |

`inventory.csv` is the **236-track superset**. The pipeline scope is
**214 tracks (≤ 360 s)**, derived with a filter, not a new scrape:

```bash
python scripts/lyrics_coverage.py \
    --inventory index/lyrics/inventory.csv \
    --lyrics-root /path/to/arabic-music-lyrics/lyrics \
    --out work/lyrics --max-duration 360
```

That writes the same five tables for the active scope into `work/lyrics/`
(gitignored). Verified counts on 2026-10-28:

| Scope | match | ambiguous | missing | ready | needs_cleanup | needs_expansion |
|---|---:|---:|---:|---:|---:|---:|
| 236 | 125 | 59 | 52 | 9 | 3 | 113 |
| **214** | **118** | **54** | **42** | **9** | **3** | **106** |

## State of `title_map.csv` — read this before treating it as verified

The map is a **candidate generator output**, not an authority:

- `decision` is pre-filled with `auto_decision` (`match` / `ambiguous` /
  `missing`);
- `lyric_title` / `lyric_source` are filled for the 125 auto-matches;
- `reviewer` and `reviewed_at` are **empty on every row** → no human has
  adjudicated any track yet.

Adjudication (L2) means setting `decision`, `lyric_title`, `lyric_source`,
`reviewer`, `reviewed_at`, `notes` by hand. Rules are in
[`docs/CROSS-PROJECT.md`](../../docs/CROSS-PROJECT.md) §5.2. Never edit
`auto_decision`; never fix a title by rewriting lyric text.

The 54 `ambiguous` rows are the review queue. They do **not** enter L4/L5.

## Provenance

Inputs (read-only clones, not vendored here):

- `music_captioning_oct_2026/outputs/{ELISSA,FADL_SHAKER}` — durations, audio identity
- `arabic-music-lyrics/lyrics/{fnanen,kalimataghani}/artists/{elissa,fadhl-shaker}` — titles + text
- `gs://akbar-december-2024-backup/DISCOGRAPHY/` — authoritative audio listing

Built with `scripts/gcs_inventory.py` then `scripts/lyrics_coverage.py`
(skeleton matching with qaf/vowel/article variants, `--auto-threshold 93`,
`--ambiguous-threshold 80`). Measured 2026-10-28; scalar counts are reproducible,
the exact ordering may shift if the upstream scrapes are re-pulled.

## What is not here

- **No lyric bodies.** `scripts/lyrics_coverage.py` writes metadata only; the
  text stays with its source until a gated data location exists.
- **No audio, stems or alignments.** Those live in
  `gs://akbar-december-2024-backup/STEMS/htdemucs/v1/` and `work/srt/`.
- **No human decisions yet.** This snapshot is v1-machine; the first adjudicated
  version bumps to a new file (or a `v2` directory), it does not overwrite v1.
