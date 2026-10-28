# CROSS-PROJECT.md — timed lyric transcripts across three repos

Status: **planning note, 2026-10-28.** This document contains no new accuracy
claims. Numbers marked *measured* were observed in the clones on 2026-10-28 and
are reproducible from the stated inputs; everything else is a plan.

Related repos (read-only for this project):

- `music_captioning_oct_2026` — audio → Suno-style tags, plus a SheetSage2 ABC
  transcription pass over the Elissa / Fadl Shaker discography.
- `arabic-music-lyrics` — a 12,463-track Arabic lyrics corpus and per-artist
  scrapers.
- this repo (`yue2-karaoke-align`) — a validated **forced-alignment** pipeline
  (A1) that turns audio + known lyrics into word timings, SRT/VTT and a player.

---

## 0. The product we actually want

Not karaoke. The product is a **timed lyric transcript** for every track we can
support, and from that a **training dataset**:

```
audio (GCS)  ─┐
              ├─▶ forced alignment (A1) ─▶ words.json ─▶ SRT / VTT / JSONL
lyric sheet ──┘                                  │
                                                 └─▶ dataset manifest + QA
```

Why **forced** alignment and not ASR: the text is known, so the aligner cannot
invent words. The failure mode is wrong timing, not wrong content. That is the
whole argument in [`ROUTE-A.md`](ROUTE-A.md), and it is what makes this usable as
a *transcription* source: every emitted token is a token we supplied.

The player (`a0_player.py`) is a **debugging/inspection surface**, not the goal.
The real artifacts are:

1. `<track>.words.json` — text + word spans + QA (source of truth),
2. `<track>.srt` / `<track>.vtt` — line-level timed text,
3. a dataset row joining audio identity, text and time,
4. a manifest that says which tracks passed which gate.

Downstream uses that motivate the schema: singing-ASR fine-tuning, training a
singing-oriented forced aligner, lyric↔audio retrieval, pronunciation/dialect
studies, subtitle generation, and evaluation anchors for future aligners.

### 0.1 Output contract

The deliverable for this plan is **one `.srt` per track, for as many of the 214
in-scope Elissa/Fadl tracks (≤ 360 s) as the lyrics allow**. Nothing else is the headline.

```
work/srt/<artist>/<album>/<track>.srt      accepted, line-level
work/srt/<artist>/<album>/<track>.vtt      same cues (optional)
work/srt/<artist>/<album>/<track>.words.json   word timings + QA (debug)
work/srt/manifest.csv                      one row per inventory track
```

`manifest.csv` has a row for **every** track, including the ones that fail:
`status = accepted | no_lyrics | compact_sheet | failed_qa`, plus the reason and
the QA numbers. A track without an SRT is not silently dropped; it is counted.
The headline metric is the number of `accepted` rows. Tracks whose lyric sheet
is compact are **not** accepted with guessed timings — they wait for expansion
(§5.1) or are reported as `compact_sheet`.

---

## 1. The three repos, recast for this goal

| Repo | Role in the timed-lyrics pipeline | Input | Output | What we consume |
|---|---|---|---|---|
| `music_captioning_oct_2026` | **audio inventory + metadata + vocal gating + symbolic melody** | audio | caption JSON, ABC/MIDI, tags | track list, `vocals`/`instrumental`, `notes_vocal`, tempo/key/sections, `audio_sha256` |
| `arabic-music-lyrics` | **text supply** | web lyrics | parquet + per-artist JSON/CSV | lyric sheets, artist/title/year, source URL |
| `yue2-karaoke-align` | **alignment + QA + export** | audio + text | `words.json`, SRT/VTT, report | the A1 pipeline, aligner pin, QA gates |

The important consequence: **no single repo owns the dataset.** Captioning owns
the audio identity and the vocal/instrumental split; the lyrics repo owns the
text; this repo owns the time axis. The dataset is the join.

---

## 2. What is already true (measured)

### 2.1 This repo

- A1 is a working batch pipeline: `scripts/a1_align_run.py`.
- Production aligner: `akbargherbal/wav2vec2-large-xlsr-53-arabic`, pinned in
  `bench/models.lock` at `4ca1c9c6ac85…`.
- Validated on 8 real YuE2 renders (100 % monotone, 8/8, no silent-intro
  violations). See [`ROUTE-A.md`](ROUTE-A.md) §5 and `results/`.
- One real pop song was already aligned outside the YuE2 corpus:
  `results/a1/elissa/elissa_maktooba_leek.*` — 204/204 words, coverage 1.00,
  on/off 1.74, 2 QA warnings, 87.2 s alignment for a 317.1 s track (CPU).
- Outputs already include **line-level SRT and VTT** plus word JSON. So the
  export format for a dataset mostly exists; the missing pieces are inventory,
  coverage, readiness and a dataset schema.

#### Pilot: a real SRT from GCS (2026-10-28)

One track was run end-to-end: `Bastanak` (Elissa, *2006 Bastannak*), audio
downloaded from `gs://…/DISCOGRAPHY/ELISSA/…/01.Bastanak.mp3`, lyrics from
`fnanen/artists/elissa` (`bstnak.html`), then A1 on CPU.

| Metric | Result |
|---|---|
| words | 201 / 201 |
| coverage | 0.971 |
| line cues | 41 |
| silent-intro | ok (first word 38.56 s, lead 37.5 s) |
| QA | monotone; 4 gaps > 2 s (max 3.96 s) |
| wall | 63 s Demucs + 54 s alignment for 236 s audio |

The SRT is a normal line-level file, kept under `work/srt-pilot/` (gitignored;
it contains lyrics, so it is not committed). This proves the path
audio (GCS) → lyric sheet → `.srt`; it is one track, not a corpus claim.

### 2.2 `music_captioning_oct_2026`

- 23 albums / **236 tracks** of Elissa + Fadl Shaker transcribed with SheetSage2:
  **224 `ok`, 3 `flagged`, 9 `failed (no-abc)`**, **19.188 h** of audio, RTF
  `5.423×` on a T4. See its `docs/TRANSCRIPTION_RESULTS.md`.
- Captions for **230** Elissa/Fadl tracks (`outputs/ELISSA`,
  `outputs/FADL_SHAKER`); **6** more were skipped by the 480 s cap but their
  audio exists on GCS. The real audio inventory is therefore **236 tracks**
  (96 Elissa + 140 Fadl), which matches the ABC count.
- The ABC dataset builder already has a `lyrics` column — and it is hard-coded
  to `None`:
  `scripts/build_abc_dataset.py` → `"lyrics": None,  # §11 deferred`.
  The transcription plan says the same: *“No lyrics. SheetSage2 emits no words.”*
  (`docs/TRANSCRIPTION.md`).
- **Hard constraint discovered:** the packed ABC JSONs are **not in git**; only
  `outputs/abc_index.json` is. The JSONs must be restored from
  `gs://akbar-december-2024-backup/TRANSCRIPTS/<ARTIST>/abc/` before any ABC-side
  join. The caption JSONs *are* in git.

### 2.3 `arabic-music-lyrics`

- **12,463 tracks / 18.5 M characters / 2,771 artists** (1921–2023) in
  `data/processed/arabic_lyrics.parquet`.
- Per-artist scrapes, which are the useful tier for this job:

| Artist | fnanen | kalimataghani | union of titles (normalized) |
|---|---:|---:|---:|
| Elissa | 112 | 46 | **141** |
| Fadl Shaker | 76 | 26 | **86** |

- The merged corpus has Elissa = 70 tracks, but Fadl Shaker is split across two
  artist strings: `Fadel Chaker` = 17 and `Fadl Shaker` = 6. The alias merge is
  **not** complete — relevant whenever the corpus is used as the index.
- The sheets are essentially **undiacritized**: harakat ratio measured at
  `0.0011–0.0067` in the artist scrapes (a 200-row corpus sample: `0.0023`).
  Corpus-wide ratio is **not measured** (the minimal env has no parquet engine).
  This is not a blocker: the pinned aligner has 6 harakat in its vocabulary and
  `stripped_diacritics: false`, and the Elissa run succeeded without diacritics.
  It does raise G2P ambiguity relative to fully diacritized text.

---

## 3. Intersections (concrete)

### 3.1 The same discography, two views

`music_captioning_oct_2026/outputs/ELISSA` and `outputs/FADL_SHAKER` hold the
**230-track commercial discography** this plan targets, and `abc_index.json`
covers **236** of the same tracks at the ABC level. The audio is on GCS; the
captions and the lyrics point at the same songs.

### 3.2 Elissa / Fadl Shaker is the shared axis

These two artists are the only place where all three repos already overlap:

- captioning: 96 + 140 audio tracks (230 captioned); 236 ABC transcriptions;
- lyrics corpus: 141 + 86 unique titles across the two scrapers;
- this repo: one completed Elissa alignment (`maktooba leek`).

### 3.3 The missing `lyrics` field is the same hole in both schemas

`build_abc_dataset.py` emits `lyrics: null` and `lyrics_source: null`; the
transcription plan defers §11. The lyrics repo is the natural supplier of that
field; A1 is the natural supplier of its **time axis**. This is the cleanest
functional intersection in the three projects.

### 3.4 The data contracts already compose — after one mapping

| Object | Key | Has audio hash? | Has text? | Has time? |
|---|---|---|---|---|
| caption JSON track | `artist` + `album` + `file` | yes (ABC path) | no | no |
| ABC packed track | `track_id` + `audio_sha256` | yes | **no (`null`)** | no (ABC only) |
| lyric song | `title` + `artist` | **no** | yes | no |
| A1 `words.json` | WAV stem | no | yes | **yes** |

There is **no shared key**. `audio_sha256` is the right long-term key, but the
lyrics repo has no audio at all. The bridge must be a **verified title/artist
mapping** (see §5), after which `track_id` becomes the dataset key.

### 3.5 Arabic text logic is implemented three times

- lyrics repo: Arabic-ratio filter, Persian/Urdu/Kurdish exclusion, dedupe,
  artist-alias merge (`scripts/merge_corpus.py`);
- this repo: `[Lyrics]` extraction, section-tag removal, one-sung-line-per-line
  rules (`scripts/a0_lyrics.py`, `docs/A1-RUNBOOK.md`);
- captioning: lyric eligibility / instrumental flags.

Three implementations of “turn an Arabic lyric document into clean sung text”.
Worth one shared, tested module rather than three drifting copies.

### 3.6 Same operating model

All three are docs-first, agent-operated, Colab/GCS-aware, with explicit
“do not touch production” boundaries. That makes a read-only join cheap.

---

## 4. Differences that decide the design

| Dimension | captioning | lyrics | this repo |
|---|---|---|---|
| Signal | audio → tags/symbols | text → corpus | audio + text → time |
| Output | JSON / ABC | parquet / JSON | `words.json` / SRT / VTT |
| Compute | GPU T4, 4-bit, ~8 GiB | CPU | CPU or GPU (Demucs dominates; **GPU much faster**) |
| Scale | 236 tracks (Elissa + Fadl) | 12,463 tracks | 10 tracks (incl. pilot) |
| Provenance | `audio_sha256`, GCS URI | source URL, no audio | WAV stem |
| Maturity | transcription complete | published | 8/8 validated |
| Code shared with the others | **none** | **none** | **none** |

Design consequences:

1. **Do not merge the repos.** Add one thin bridge (inventory + mapping +
   readiness + dataset export), and keep each repo authoritative for its own
   artifact.
2. **Copy no lyrics into git here.** The bridge should store only track IDs,
   match decisions, aggregate counts and offsets; text stays with its source or
   in a gated data location.
3. **Treat the audio as the identity.** The lyric sheet is an attribute of the
   audio track, not the other way round.

---

## 5. The hard part: Latin filenames vs Arabic titles

The Elissa/Fadl audio filenames are Latin transliterations
(`01. Ayami Bik.mp3`, `03. El Hob El Qadeem.mp3`), while the lyric titles are
Arabic (`أسعد واحده`, `الحب القديم`). A normalized exact match gets:

> **0 / 96 Elissa tracks and 0 / 140 Fadl tracks.**

That is not a data shortage; it is a script/orthography mismatch. The coverage tool
(`scripts/lyrics_coverage.py`) uses a consonant skeleton with variants for qaf
(Egyptian silent vs `k`), long vowels (written in Arabic, dropped in Latin) and
the definite article. On the 230 Elissa/Fadl caption tracks it produced:

| | Elissa | Fadl Shaker |
|---|---:|---:|
| inventory | 96 | 140 |
| auto-`match` (≥93, margin ≥3, strict ≥45) | **68** | **57** |
| `ambiguous` (≥80) | 22 | 37 |
| `missing` | 6 | 46 |
| `not_applicable` | 0 | 0 |

Those are **candidates, not verified coverage**. Both error directions occur:

- ✅ `Bastanak` ↔ `بستناك`, `Waddaatak` ↔ `ودعتك`, `Akhedny Ma'ak` ↔ `اخدني معاك`;
- ❌ `Mali` ↔ `معقول` scored 100 under the permissive variants (both collapse to
  `ml`); the short-title guard now sends it to `ambiguous`, but this is why the
  decisions are human;
- ⚠️ `Ayami Bik` ↔ `ايامي بيك` is a true match that the strict skeleton scores
  only 66.7 because long vowels are written in Arabic and dropped in Latin;
- ⚠️ `Alouli El Eid` ↔ `قالولي العيد` loses the silent Egyptian qaf.

**Conclusion: automated matching is a candidate generator, not an authority.**
The deliverable is a **human-verified title map**, seeded by the matcher. At 230
rows this is a few hours, once, and it becomes the join key for everything else.

### 5.1 The second surprise: matched text is usually *compact*

Running the readiness check over the 125 auto-matched rows gives only **9
`ready`**, **3 `needs_cleanup`** and **113 `needs_expansion`** at a 50 wpm floor.
The word rate of the matched sheets is median **27.2 wpm** (Elissa) and
**24.3 wpm** (Fadl) against a typical sung rate of 60–150 wpm. That is not
necessarily a bad match: it is the compact-sheet problem above (chorus written
once, sung two or three times). **Text availability is not the bottleneck; text
completeness in sung order is.** The floor is a screen, not a verdict: a
genuinely slow ballad can sit near it, so treat the list as a review queue.

### 5.2 Title-map contract

`work/lyrics/title_map.csv` (gitignored) — one row per audio track:

| column | meaning |
|---|---|
| `track_id` | stable id (artist/album/file or audio hash) |
| `artist`, `album`, `file`, `duration_s` | inventory identity |
| `title_lat` | cleaned filename title |
| `candidate_1`, `candidate_1_source`, `candidate_1_score` | top machine candidate |
| `candidate_2`, `candidate_2_score` | runner-up, for the review decision |
| `strict_score` | un-varied skeleton score (robustness check) |
| `auto_decision` | what the matcher would do; never edit |
| `decision` | **human**: `match` / `no_match` / `ambiguous` / `not_applicable` |
| `lyric_title`, `lyric_source` | adjudicated answer |
| `reviewer`, `reviewed_at`, `notes` | audit trail |

Rules:

- one lyric sheet per track; if two sources disagree, keep both and mark
  `ambiguous` until a human picks one;
- `not_applicable` for `(Music)`, instrumentals, and non-song entries;
- never “fix” a title by rewriting the lyric text — record the mapping instead;
- freeze the map before alignment; changes require a new map version.

### 5.3 Committed index (v1, machine-seeded)

L0–L4 outputs are committed under [`index/lyrics/`](../index/lyrics/) because
`work/` is gitignored and the tables are costly to rebuild: `inventory.csv`,
`lyrics_index.csv`, `title_map.csv`, `coverage.csv`, `readiness.csv`,
`summary.json` (236-track superset; no lyric text, titles only). See that
directory's `README.md` for provenance and the filter command.

Active scope is **214 tracks (≤ 360 s)**, not 236: 22 long live/concert tracks
are excluded by `--max-duration 360` (already applied to the stem cache). After
the human review (v2, 2026-10-28) that scope reads: **163 `match`, 0
`ambiguous`, 51 without lyrics**; readiness **10 `ready`, 3 `needs_cleanup`,
150 `needs_expansion`**.

The review settled identity, not completeness: matched went **118 → 163** but
`ready` only **9 → 10**, because the 45 recovered sheets are compact (median
**24.5 wpm** against 60–150 sung). **The lyric expander (§5.1), not title
matching, is now the binding constraint.**

`title_map.csv` in that snapshot is **v2 (human-adjudicated)**: 54 rows
reviewed, `reviewer`/`reviewed_at` filled, 45 `match` + 9 `no_match`; the 5
remaining `ambiguous` rows are long tracks outside the scope. The browser flow
that produced it is specified in [`REVIEW-APP.md`](REVIEW-APP.md).

---

## 6. Coverage plan (L0–L6)

The question this plan answers: **which audio tracks have a lyric sheet we can
actually align?** A track is “covered” only when the text is (a) the right song,
(b) complete enough, (c) in sung order, and (d) alignable. Text existence alone
is not coverage.

### 6.0 Scope: Elissa and Fadl Shaker only

This plan is deliberately narrow: **236 audio tracks (230 captioned), 236 ABC
tracks, two artists**, of which **214 (≤ 360 s) are in the active SRT scope**
(the 22 longer live/concert tracks were dropped with the stem-cache pass). No
arabmaqamrock tracks, no YuE2 prompts, no new scraping outside
these two artists. The reason is that this pair is the only place where the
audio inventory, the ABC pass and the lyric scrapes already overlap; expanding
scope before the title map and readiness gates are proven would multiply the
manual work without proving the pipeline.

The tool defaults to these two artists when it reads the caption tree. If the
inventory ever grows, pass `--artists` explicitly.

### L0 — Build the audio inventory *(~1 h, CPU)*

Sources of truth, in order:

1. `gcloud storage ls -l -r gs://akbar-december-2024-backup/DISCOGRAPHY/` — the
   real inventory, with sizes and paths. **This is authoritative.**
2. the caption JSONs (`outputs/ELISSA`, `outputs/FADL_SHAKER`) — per-track
   duration, bitrate, tags, caption;
3. `outputs/abc_index.json` — which albums/tracks have ABC packed (JSON restore
   still needed from `TRANSCRIPTS/`).

Fields: `track_id`, `artist`, `album`, `file`, `gcs_uri`, `duration_s`,
`bitrate_kbps`, `audio_sha256` (if available), `has_caption`, `has_abc`,
`instrumental`.

```bash
python scripts/gcs_inventory.py \
    --caption-root /path/to/music_captioning_oct_2026/outputs \
    --out work/lyrics/inventory.csv
```

It lists GCS, joins durations from the caption JSONs (including the 6 tracks the
captioner skipped as too long), and writes the CSV. Nothing is downloaded.

**Deliverable:** `work/lyrics/inventory.csv` (the 2026-10-28 run: **236
tracks**, 96 + 140).
**Gate:** every GCS object appears exactly once; instrumentals marked.

### L1 — Build one lyrics index *(~2 h, CPU)*

Normalize all lyric sources into one table without changing the text:

- `data/processed/arabic_lyrics.parquet` (broad, undiacritized, section tags).
  It **exists** in the lyrics repo (12,463 rows) but the coverage tool does not
  read parquet: it has no pandas/pyarrow dependency. Export it once to CSV if
  you want it as a fallback (`--corpus-csv`). For Elissa/Fadl the per-artist
  scrapes are larger, so the parquet mainly helps fill specific missing titles;
- `lyrics/fnanen/artists/{elissa,fadhl-shaker}/*.json` (rich metadata);
- `lyrics/kalimataghani/artists/{elissa,fadhl-shaker}/*.json` (some diacritized
  songs, `notes` field).

Fields: `lyric_id`, `artist`, `title_ar`, `title_norm`, `source`, `source_url`,
`n_lines`, `n_words`, `harakat_ratio`, `has_section_tags`, `has_translation`,
`year`, `notes`. The broad parquet has no per-song URL and uses alias-split
artist strings (`Fadel Chaker` vs `Fadl Shaker`); the tool applies a small alias
map, but the per-artist scrapes are the better tier. **The lyric text itself is
not copied into this repo**; the
index carries a pointer (`lyric_id`, `source_url`) and the text stays with its
source or in a gated data location.

**Deliverable:** `work/lyrics/lyrics_index.csv` (metadata only) + a per-source
count table.
**Gate:** every lyric row has provenance; duplicates are marked, not silently
dropped.

### L2 — Resolve identity (the title map) *(~3–4 h human, CPU assists)*

1. Run the candidate generator over the inventory (skeleton + variants, top-3):

   ```bash
   python scripts/lyrics_coverage.py \
       --caption-root /path/to/music_captioning_oct_2026/outputs \
       --lyrics-root  /path/to/arabic-music-lyrics/lyrics \
       --out work/lyrics
   ```

   When it reads a caption tree the tool keeps only Elissa + Fadl Shaker by
   default; `--artists` overrides that. For artists with no per-artist scrape, export the
   broad parquet once to CSV and pass
   `--corpus-csv work/lyrics/arabic_lyrics.csv`; the scrapes win when both exist.

   It writes `inventory.csv`, `lyrics_index.csv`, `title_map.csv`,
   `coverage.csv`, `readiness.csv` and `summary.json`. Re-running preserves human edits in
   `title_map.csv` (`decision`, `lyric_title`, `lyric_source`, `notes`) keyed by
   `track_id`.
2. Human adjudication of all 230 Elissa/Fadl rows.
3. Use album/year as a second signal wherever the source has them, and the
   `strict_score` column as a robustness check.
4. For stubborn cases, a 10-second ASR probe of the first vocal phrase can
   confirm a title; do not use ASR to *generate* the lyric text.
5. Freeze `work/lyrics/title_map.csv` v1.

**Deliverable:** verified `title_map.csv` + the `coverage.csv` join.
**Gate:** every inventory row has a decision; no `ambiguous` row enters L4.

### L3 — Coverage audit *(~1 h)*

Join inventory × title map × lyrics index and report:

- matched / ambiguous / missing / not-applicable, by artist and album;
- multi-source disagreements;
- tracks whose only lyric source is the broad parquet (higher cleanup risk).

**Deliverable:** `work/lyrics/coverage.csv` + a committed aggregate summary
(counts only, no lyric text).
**Gate:** coverage is measured, not estimated; missing list is explicit.

### L4 — Lyric-sheet readiness *(~1 h automated + review)*

For each matched track, decide whether the sheet can be aligned **as written**:

| Check | Rule of thumb | Flag |
|---|---|---|
| completeness | words/min in a sane band (≈50–220) vs duration | `short` / `long` |
| translation leakage | English or bilingual blocks | `has_translation` |
| section tags | `[Verse]`, `[اللازمة]`, `(…)` ad-libs | `needs_cleanup` |
| repetition | chorus appears once but is sung twice or more | `needs_expansion` |
| language | Arabic ratio; Persian/Urdu/Kurdish markers | `reject_language` |
| script/diacritics | harakat ratio, Arabizi rows | `undiacritized` / `arabizi` |
| duplicates | live/remix/compilation copies | `duplicate_of` |

The compact-sheet problem is real and measured: kalimataghani's *مكتوبة ليك* is
**18 lines**; the file used for the successful A1 run is **40 lines**. The extra
lines are repeated choruses plus orthographic normalization (`بقي/بقى`,
`بيا/بيّ`). That expansion was manual. Do not align a compact sheet and expect
line-level cues to line up; either expand it or accept word-level-only output.

**Deliverable:** `work/lyrics/readiness.csv` with
`ready` / `needs_cleanup` / `needs_expansion` / `reject`.
**Gate:** only `ready` rows move to L5.

### L5 — Alignment pass (reuse A1) *(CPU; measure on 3 tracks first)*

For each `ready` track:

1. stage `audio + <track>.txt` (one sung line per line, only sung text, repeats
   written out) under a scratch run directory;
2. run `scripts/a1_align_run.py --data-dir … --auto-songs`;
3. keep `words.json`, `.srt`, `.vtt`, `summary.json`;
4. apply the dataset QA gates in §8.

Known cost: 87.2 s alignment + Demucs for a 317 s song on CPU; Demucs dominates
and is cached per stem. **Measure 3 tracks before scheduling the batch.**

CPU is sufficient, but it is not the fast path. Demucs is the dominant cost and
is much faster on a GPU: pass `--sep-device cuda --device cuda` (and use a CUDA
build of torch). The local box used for this plan is CPU-only
(`torch 2.14.1+cpu`, no `nvidia-smi`), so the measured 63 s separation for a
236 s track is the CPU fallback, not a GPU number. For the full 236-track batch,
GPU is the single biggest wall-clock lever; measure one track on the target GPU
before extrapolating.

**Separate once, align many times.** The one-time GPU pass and its storage
contract are in [`STEM-CACHE.md`](STEM-CACHE.md): **two stems** (`vocals` +
`instrumental`), content-addressed, FLAC, private GCS, with a JSONL manifest.
A1 must consume the cache with `--skip-separation`; it must not re-run Demucs
for every alignment run.

**Deliverable:** per-track timings + QA.
**Gate:** §8 alignment gates.

### L6 — Dataset export *(~1 day)*

Emit one JSONL row per accepted track, plus a manifest and optional SRT/VTT.
Splits by **album** (never by track) to avoid leakage; duplicate groups stay in
one split. Keep the aligner pin, prompt hash and QA in every row.

**Deliverable:** `work/datasets/<name>/{train,val,test}.jsonl` + `manifest.json`.

### L7 — Gold anchors *(the honest gap)*

Hand-check 10–20 tracks at line level (word level for 3–5) and measure timing
error. `ROUTE-A.md` §5 already names this as the remaining accuracy gap; a
training dataset without an anchor set cannot state its own quality.

---

## 7. Track status model

```
inventory
  ├─ not_applicable        instrumental / (Music) / non-song
  └─ candidate
       ├─ missing          no lyric sheet found
       ├─ ambiguous        >1 plausible sheet, or low-confidence mapping
       └─ matched
            ├─ reject     language / translation-only / unusable
            ├─ needs_cleanup
            ├─ needs_expansion
            └─ ready ──▶ aligned ──▶ accepted | failed_qa
```

Only `accepted` rows enter a training split. `failed_qa` rows stay in the
manifest with their failure reason; they are not deleted.

---

## 8. Dataset schema (draft)

One JSON object per track (JSONL), plus a manifest:

```json
{
  "track_id": "ELISSA/Elissa - 2008 Ayami Beek/01. Ayami Bik.mp3",
  "artist": "Elissa",
  "album": "Elissa - 2008 Ayami Beek",
  "title": "Ayami Bik",
  "audio_uri": "gs://…/DISCOGRAPHY/…/01. Ayami Bik.mp3",
  "audio_sha256": "…",
  "duration_s": 279.7,
  "lyrics_source": "fnanen",
  "lyrics_url": "https://…",
  "lyric_sheet_sha256": "…",
  "text": "البيت الأول\n…",
  "lines": [{"i": 0, "text": "…", "start": 12.31, "end": 18.02}],
  "words": [{"w": "…", "start": 12.31, "end": 12.77}],
  "aligner": {"model": "akbargherbal/wav2vec2-large-xlsr-53-arabic",
               "revision": "4ca1c9c6ac85d839eebc530ec85b6a89f697e4e8"},
  "pipeline": {"name": "a1", "version": "…", "aligned_at": "…"},
  "qc": {"coverage": 1.0, "monotone": true, "on_off_ratio": 1.74,
          "intro_violation": false, "max_gap_s": 2.34,
          "max_word_s": 42.18, "warnings": ["…"]},
  "split": "train",
  "duplicate_of": null
}
```

Also export `.srt` and `.vtt` per track (line-level) and a `manifest.json` with
counts, the title-map version, and the license/provenance flags.

---

## 9. QA and acceptance

### Alignment gates (per track, before acceptance)

- coverage ≥ 0.95 of the audio;
- monotone starts, no overlaps, no zero-length words;
- no silent-intro violation;
- longest single word ≤ 8 s (or explicitly flagged as melisma and reviewed);
- gaps > 2 s counted and reported, not hidden;
- line mapping is one cue per lyric line.

The Elissa run passed coverage/monotonicity but produced two warnings
(3 gaps > 2 s, one 42.2 s word). That is exactly the kind of row that must be
**accepted-with-flags or rejected by policy**, not silently shipped.

### Corpus gates

- 100 % of inventory rows have a decision;
- ≥ 90 % of non-instrumental tracks matched, or the missing list is explained;
- ≥ 70 % of matched tracks ready without manual text surgery;
- accepted tracks have provenance, audio hash and a frozen title-map version;
- a gold set with measured error exists before any accuracy claim.

### Rights gate

Audio and lyrics are copyrighted. Decide **before** publishing: internal
research use vs redistribution. The lyrics repo already warns that its data is a
research compilation, not a redistribution license. This is a stop-and-ask item,
not an engineering detail.

---

## 10. Risks and open decisions

1. **Transliteration ambiguity** — solved by the manual title map, but it is the
   schedule risk. 230 rows is tractable; 2,771 artists is not.
2. **Compact lyric sheets** — repeated choruses are the main cause of bad
   line-level output. Expand before aligning, or restrict the first dataset to
   sheets that already contain repeats.
3. **Singing vs canonical text** — live versions, ad-libs and altered lyrics
   differ from the sheet. Forced alignment will time the *sheet*, not the
   performance. A downstream ASR dataset needs a “sung text” check.
4. **Undiacritized text** — workable with the current model, but accuracy at
   melisma is the known weak point; a gold set will quantify it.
5. **Instrumentals and `(Music)` entries** — exclude via caption `instrumental`
   and ABC `notes_vocal`, not by filename heuristics alone.
6. **Duplicates** — live/compilation copies share hashes or titles; keep groups
   in one split.
7. **GCS egress and CPU time** — Demucs dominates; cache stems, measure first.
8. **ABC is a bonus, not a dependency** — the packed ABC has no absolute note
   timings (key/tempo/sections/note counts only). Raw SheetSage2 `.lab`/events
   might carry timing; that is **unverified** and must not be assumed.

---

## 11. Recommended first slice (5 working days)

**Day 1 — inventory + lyric index.** L0 and L1. Confirm the counts (236 audio,
230 captions, 236 ABC) against GCS. Produce `inventory.csv` and
`lyrics_index.csv`. The 236-track inventory is already built; the remaining
work is the title map.

**Day 2 — title map.** L2. Run `scripts/lyrics_coverage.py`, adjudicate Elissa
first (96 rows, richest lyric coverage), then Fadl (134). Freeze v1.

**Day 3 — coverage + readiness.** L3 and L4. Produce the coverage table and the
first `ready` list. Expect the compact-sheet flag to remove a meaningful share;
do not paper over it.

**Day 4 — extend the pilot.** One `ready` track is already done (`Bastanak`,
201/201 words, coverage 0.971, 41 cues). Add 4 more on a deliberately mixed
sample: 1 `ready` track, 1 compact-sheet track (to show the failure mode), 1
track whose title needed a manual map decision, and 1 instrumental control.
Measure wall time and QA.

**Day 5 — export + verdict.** L6 for the pilot, write the manifest, and decide:
scale to the full Elissa/Fadl set, or first invest in the lyric expander.

**Exit criterion for the slice:** a coverage number we trust, a frozen title
map, five aligned tracks with SRT/VTT/JSONL, and a measured cost per track.

---

## 12. What not to do

- Do not treat “a row exists in the lyrics corpus” as coverage.
- Do not align a compact sheet and call the line-level SRT correct.
- Do not use ASR to generate the lyric text; use it only as a title-confirmation
  probe.
- Do not commit lyric text, ABC text or audio to this repo.
- Do not publish a “dataset” before the rights decision and the gold anchors.
- Do not merge the three repos; keep the bridge thin and read-only.
