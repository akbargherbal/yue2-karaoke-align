# A1-READY-RUN.md — align the `ready` set from the stem cache

Status: **staging done, alignment moving to GPU, 2026-10-28.** Ten tracks are
`ready` (sheet complete enough to align as written). This is the first batch
that turns the cache into SRT files without touching Demucs.

Prerequisite: the stem cache is complete (`docs/STEM-RUN-2026-10-28.md`) and the
title map is v2 (`index/lyrics/`).

Run log so far (UTC):

| When | Step | Result |
|---|---|---|
| 18:00–18:01 | `ready_pack.py --fetch` | `EXIT=0`, 10/10 staged, 0 skipped, 181 MB |
| 18:02 | smoke (`--limit 1`, CPU) | launched; model loaded, still aligning at 5 min |
| — | 10-track batch | **not started**; waiting for the GPU runtime |

The CPU smoke is the reason for the move: `torch 2.11.0+cpu`, no CUDA device,
2 vCPUs and `torch.get_num_threads()=1`, so a 3.6-min track had not finished
after 5 min. See §9.

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
| `needs_expansion` | 154 |
| matched on the 214-track scope | 167 |
| no lyrics on the 214-track scope | 47 |

`readiness.csv` has 174 rows because it is the 236-row title map's view; 7 of
those rows are longer than 360 s and are out of scope (no cached stem), leaving
167 matched on the 214-track scope.

---

## 2. Three commands

```bash
# 1. stage: lyrics text + cached vocal stem per track, plus pack.json
python scripts/ready_pack.py \
    --lyrics-root /tmp/lyrics/lyrics \
    --out work/ready-pack --fetch

# 2. smoke first: one track, same flags, separate output dir
python scripts/a1_align_run.py --songs work/ready-pack/pack.json \
    --data-dir work/ready-pack --skip-separation --no-player \
    --out work/srt/smoke --limit 1

# 3. only if the smoke exits 0: the ten
python scripts/a1_align_run.py --songs work/ready-pack/pack.json \
    --data-dir work/ready-pack --skip-separation --no-player \
    --out work/srt/ready10

# 4. read the verdict
cat work/srt/ready10/summary.json
sed -n 1,60p work/srt/ready10/A1-REPORT.md
ls work/srt/ready10/*.srt | wc -l
```

Any step expected to take more than ~30 s (the fetch, either alignment) is run
detached, so the session stays responsive:

```bash
mkdir -p work/logs
setsid nohup bash -c 'python scripts/a1_align_run.py … ; echo "EXIT=$? $(date -Is)" >> work/logs/a1_ready10.log' \
    > work/logs/a1_ready10.log 2>&1 < /dev/null &
disown; echo $! > work/logs/a1_ready10.pid
```

A step is finished only when the log has an `EXIT=` line (or the PID is gone).

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

Alignment only: no Demucs, and the audio comes from the stem cache.

| Box | Per 4-min track | Ten tracks (41.8 min audio) |
|---|---|---|
| CPU, 2 vCPU, `torch` 1 thread (this session) | **> 5 min** measured on track 1; ~1.5–3× real-time | **45–90 min**, not the 60–90 s first assumed |
| GPU (CUDA wheel, model on device) | ~5–15 s | a few minutes including model load |

The CPU numbers above are measured, not estimated: the smoke had used 4.5 min of
CPU time on a 3.6-min track and had not produced timings. The single-thread
default is the main culprit; `OMP_NUM_THREADS=2` helps but does not close the
gap.

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

---

## 9. GPU switch (the plan as of 2026-10-28)

The CPU box cannot finish this batch in a sitting: no CUDA device, 2 vCPUs and
`torch` limited to one thread. Move the run to a GPU runtime; the stem cache and
the lyrics stay remote, so nothing is lost but local `work/`.

### 9.1 On the new runtime, before any work

```bash
nvidia-smi
python -c "import torch; print(torch.__version__, torch.cuda.is_available(),
          torch.version.cuda, torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
```

- If `cuda.is_available()` is `False`, the installed torch is a CPU wheel: install
  the CUDA build for the driver (e.g. `pip install --index-url
  https://download.pytorch.org/whl/cu124 torch torchaudio`) and re-check. Do not
  start aligning until this prints `True`.
- `a1_align_run.py` defaults `--device` to `cuda` when CUDA is visible (§ code,
  line ~490), so no extra flag is needed; pass `--device cuda` only to make the
  choice explicit in the log.

### 9.2 Stage again (work/ is local, gitignored)

```bash
cd /content/yue2-karaoke-align && git pull
test -d /tmp/lyrics || git clone --depth 1 \
    https://github.com/akbargherbal/arabic-music-lyrics /tmp/lyrics
gcloud storage cat gs://akbar-december-2024-backup/STEMS/htdemucs/v1/manifest.jsonl \
    | head -1            # permission check; stop if this errors
python scripts/ready_pack.py --lyrics-root /tmp/lyrics/lyrics \
    --out work/ready-pack --fetch
```

Expected: `staged 10 track(s)`, 0 skipped, ~181 MB. This is a re-download of
`vocals.flac` (not a re-separation); Demucs is still untouched.

### 9.3 Smoke, then the ten

```bash
# smoke: one track, foreground is still too long on CPU but fine on GPU;
# keep the detached form anyway so the session never blocks
setsid nohup bash -c 'python scripts/a1_align_run.py \
    --songs work/ready-pack/pack.json --data-dir work/ready-pack \
    --skip-separation --no-player --out work/srt/smoke --limit 1 \
    --device cuda; echo "EXIT=$? $(date -Is)" >> work/logs/a1_smoke.log' \
    > work/logs/a1_smoke.log 2>&1 < /dev/null &
disown; echo $! > work/logs/a1_smoke.pid
```

Only after `EXIT=0` in `work/logs/a1_smoke.log`, launch the batch with the same
flags and `--out work/srt/ready10`. One track failing must not stop the batch;
`failed_qa` is counted, not deleted.

### 9.4 Verdict

Per track from `summary.json` / `A1-REPORT.md`: `n_words_aligned/n_words_in`
(≥ 0.95), `qa.monotone` (true), `intro_violation` (false), `line_mapping`
(`line`), `qa.max_gap_s` (report). Then `accepted` vs `failed_qa`, the failing
track and its reason from the report, and the wall time from the `EXIT=` line.

### 9.5 What not to do on the switch

- Do not re-run Demucs; `--skip-separation` and the cached `vocals.flac` are the
  whole point.
- Do not push `work/` (ignored; it contains lyric text).
- Do not expand to the 154 `needs_expansion` tracks before the 10-track batch
  has an honest accept/fail number.
