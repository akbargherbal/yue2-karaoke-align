# A1-READY-RUN.md — align the `ready` set from the stem cache

Status: **runbook, 2026-10-28.** Ten tracks are `ready` (sheet complete enough
to align as written). This is the first batch that turns the cache into SRT
files without touching Demucs.

Prerequisite: the stem cache is complete (`docs/STEM-RUN-2026-10-28.md`) and the
title map is v2 (`index/lyrics/`).

---

## 1. What "ready" means here

`readiness.csv` = `ready` (10 tracks) means the lyric sheet has a plausible word
rate (~50 wpm+ against the sung 60–150) and no cleanup flags. It is a screen,
not a guarantee — the alignment QA gate is what accepts or rejects each track.

The other 153 matched tracks are `needs_expansion`: identity is settled, the
sheet is just too compact to align line-by-line.

| | Count |
|---|---:|
| `ready` | 10 |
| `needs_cleanup` | 3 |
| `needs_expansion` | 150 |
| no lyrics (`no_match` + `missing`) | 51 |

---

## 2. Three commands

```bash
# 1. stage: lyrics text + cached vocal stem per track, plus pack.json
python scripts/ready_pack.py \
    --lyrics-root /tmp/lyrics/lyrics \
    --out work/ready-pack --fetch

# 2. align: no separation, no player, outputs under gitignored work/
python scripts/a1_align_run.py --songs work/ready-pack/pack.json \
    --data-dir work/ready-pack --skip-separation --no-player \
    --out work/srt/ready10

# 3. read the verdict
cat work/srt/ready10/summary.json
sed -n 1,60p work/srt/ready10/A1-REPORT.md
```

`--lyrics-root` is the public clone:

```bash
git clone --depth 1 https://github.com/akbargherbal/arabic-music-lyrics /tmp/lyrics
```

`--fetch` downloads `vocals.flac` per track (~15–25 MB each) from
`gs://…/STEMS/htdemucs/v1/<sha>/`. Re-running skips what is already cached.

---

## 3. Why this shape

- **`--songs` instead of `--auto-songs`**: auto mode pairs `<name>.txt` with
  `<name>.mp3|wav`; the cache stores FLAC. The songs list can point
  `stems_dir` straight at `vocals.flac`, so alignment never re-runs Demucs.
- **`--no-player`**: the player embeds the audio path; for a batch whose only
  deliverable is SRT, skip it.
- **`--out work/srt/…`**: `results/` is not gitignored and A1 output contains
  lyric text. `work/` is ignored, so nothing leaks into git.

---

## 4. Gates to read before accepting the batch

Per track in `summary.json`:

| Field | Accept |
|---|---|
| `n_words_aligned / n_words_in` | ≥ 0.95 |
| `qa.monotone` | true |
| `intro_violation` | false |
| `line_mapping` | `line` (not `word (count mismatch)`) |
| `qa.max_gap_s` | report, do not hide |

A track that fails is **accepted-with-flags or rejected**, never silently
dropped. The three `needs_cleanup` tracks are a separate decision: clean the
sheet, or leave them out of the first batch.

---

## 5. Cost

Alignment only: no Demucs. A 4-minute track is ~60–90 s on CPU, ~5–10 s on a
GPU. Ten tracks: minutes, not hours.

---

## 6. After the batch

1. Count `accepted` vs `failed_qa` — that is the first honest batch number.
2. Those SRTs are the template for the expansion work: the same pipeline, with
   a fuller sheet, is what the other 150 tracks need.
3. Do **not** scale to the 150 before the expander exists; aligning a compact
   sheet produces confident-looking wrong cues.

---

## 7. Reviewer-supplied sheets

Four tracks had no candidate in either scrape; the reviewer recorded "the lyrics
are at this URL" in `notes`. Those are now `index/lyrics/manual_sources.csv` and
`scripts/manual_lyrics.py` turns them into `work/lyrics/manual/corpus.csv`
(gitignored — it holds text):

```bash
python scripts/manual_lyrics.py --fetch --cdp http://127.0.0.1:9222
```

- Genius answers a normal `requests` call with browser headers; a bare curl gets
  a JS challenge.
- wneen is an SPA, so it needs a rendered page: start Chrome with
  `--remote-debugging-port=9222` and pass `--cdp`. One navigation per URL, then
  the text is read out of `#lyricsText`.
- `ready_pack.py` reads that corpus automatically (`--corpus-csv`), so a staged
  pack can include these sheets with no extra step.

The same four tracks are all `needs_expansion` even after extraction (9–47 wpm):
the URLs settled *identity*, not *completeness*.

## 8. Scope caveat when staging

`readiness.csv` in the index is the **236-row** view, so it includes 7 tracks
longer than 360 s that the stem cache never separated (they are absent from the
manifest). `ready_pack.py` reports those as `no cached stems in the manifest`
and skips them; every in-scope expansion track stages.
