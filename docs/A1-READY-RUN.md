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
| GPU, Tesla T4, `torch` 2.11.0+cu130 — **measured 2026-10-28** | **6.3–14.5 s** align (`align_s`, mean 10.1 s) | **150.6 s wall** (`EXIT=0`), 41.8 min audio in ≈ 16.6× real-time |

The GPU row is measured, not estimated: ten tracks in 150.6 s of wall clock,
of which 100.9 s is `align_s` summed over the tracks; the remaining ~50 s is
model load from cache + audio decode + report writing. The model itself (1.2 GB)
is fetched once — the smoke paid ~45 s for that, the batch paid only the load.
Staging the 181 MB pack took 22 s.

The CPU row is measured, not estimated: the smoke had used 4.5 min of
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

### 6.1 Measured outcome (2026-10-28, Tesla T4)

`work/srt/ready10/summary.json`: **10/10 accepted, 0 failed_qa**, 2162 words,
0 silent-intro violations, 382 line cues. All ten pass every gate in §4:
`n_words_aligned / n_words_in` = 1.000, `monotone` true, `intro_violation`
false, `line_mapping` `line`. SRT sanity: 0 cues out of order, 0 zero-length,
0 past the audio, 0 empty.

| track | words | coverage | max gap | lead silence |
|---|---|---|---|---|
| Elissa — 06. Sho Ma Sar | 181/181 | 0.953 | 27.3 s | 4.0 s |
| Elissa — 03. Kol Youm Beomre | 298/298 | **0.840** | 20.8 s | 40.0 s |
| Elissa — 01. Bastanak | 201/201 | 0.971 | 3.5 s | 37.5 s |
| Fadl — 04. Law Faker (Sidi Rohi) | 143/143 | 1.000 | 18.8 s | 27.0 s |
| Fadl — 01. Law Faker (Hafla 2004) | 143/143 | 1.000 | 17.4 s | 18.5 s |
| Fadl — 02. Sidi Rohi (Hafla 2004) | 182/182 | 1.000 | 20.8 s | 19.0 s |
| Fadl — 04. Tadri Lieh | 250/250 | 0.931 | 10.8 s | 19.5 s |
| Fadl — 13. Ya Hayat El-Roh | 274/274 | 1.000 | 40.8 s | 22.5 s |
| Fadl — 04. Ya Hyat Al Roh (Best Of) | 274/274 | 1.000 | 40.8 s | 23.5 s |
| Fadl — 12. Dehket El Donya | 216/216 | 1.000 | 12.6 s | 26.5 s |

**Coverage is not a confidence score.** `a0_qa.py` defines it as
`last_word_end / duration`, and it only warns below 0.60, so a lyric sheet that
stops early would still report `ok: true`. The two low rows above (0.84, 0.93)
were checked against the cached vocal stem, not guessed: every gap > 2 s and
each tail window was compared to the track's median vocal level (1 s frames,
dBFS). The 20.8 s gap on *Kol Youm Beomre* sits **10.7 dB below** the singing
level and its 51.6 s tail **27.5 dB below** — instrumental intro/outro, not
missing lyrics. Across the ten there are 31 gaps > 2 s: the 14 gaps of 7 s or
more are all quiet (**10.7–41.5 dB** below singing, including the two 40.8 s
Ya Hayat gaps at 25.8 and 23.0 dB down; the 34.8 s one on the Saharni copy is
the misplaced word below, not a break), and the 14 gaps within 6 dB of singing
level are all **≤ 3.8 s** — holds and breaths inside a phrase, not skipped
sections.

Gap sizes are reported, not hidden; none of these gaps rejects a track.

**One real defect survived the gates.** On *13. Ya Hayat El-Roh* (Saharni
copy) word 0 is anchored at 28.86 s while word 1 starts at 63.96 s, so the
opening cue runs **43 s** (28.862 → 71.844) and lights up 35 s before the line
is sung. The same lyric on the Best Of copy — same 301 s duration, same 274
words — puts that word at 63.78 s and its first cue at 63.784 → 71.905.
Across the 274 words the median start difference between the two copies is
**0.10 s** and the max is 34.9 s, i.e. that single word. `monotone` stays true
because the path jumps once and never goes backwards, and the word count is
1:1, so every §4 gate passes it. The fix belongs in the aligner (drop or clamp
a leading word isolated from word 1), not in post-processing — the SRT was left
as produced.

Line-level cues also span quiet breaks by design: the same file has a 49 s cue
(137.268 → 186.290) across the 40.8 s instrumental gap. That is granularity,
not a misalignment.

### 6.2 Still open

- The 3 `needs_cleanup` tracks are unaligned — cleaning the sheet or leaving
  them out is still a separate decision (§4).
- `needs_expansion` is untouched; the expander is the next gate, and the
  honest number to carry into it is 10/10 accepted on compact sheets.
- The leading-word anchor above is the first concrete aligner bug the
  post-batch audit caught; until it is fixed, any batch should be screened for
  first-cue duration (a first cue much longer than its line is the signature).
- The gap audit rule above is reproducible from `summary.json` + the stem cache;
  the throwaway script lives in `work/audit/align_audit.py` (not committed,
  `work/` is ignored). Promote it to `scripts/` if the expansion batch needs the
  same check.

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
mkdir -p work/logs
gcloud storage cat gs://akbar-december-2024-backup/STEMS/htdemucs/v1/manifest.jsonl \
    | head -1            # permission check; stop if this errors
setsid nohup bash -c 'python scripts/ready_pack.py --lyrics-root /tmp/lyrics/lyrics \
    --out work/ready-pack --fetch; echo "EXIT=$? $(date -Is)" >> work/logs/ready_pack.log' \
    > work/logs/ready_pack.log 2>&1 < /dev/null &
disown; echo $! > work/logs/ready_pack.pid
tail -f work/logs/ready_pack.log     # Ctrl-C is safe; the job keeps running
```

Staging is a download, not a separation, but it is ~181 MB: detached like
everything else, so the session stays answerable.

Expected in the log after `EXIT=0`: `staged 10 track(s)`, 0 skipped. This is a
re-download of `vocals.flac` (not a re-separation); Demucs is still untouched.

### 9.3 Smoke, then the ten

```bash
mkdir -p work/logs

# smoke: one track. On GPU this is seconds; the detached form costs nothing.
setsid nohup bash -c 'python scripts/a1_align_run.py \
    --songs work/ready-pack/pack.json --data-dir work/ready-pack \
    --skip-separation --no-player --out work/srt/smoke --limit 1 \
    --device cuda; echo "EXIT=$? $(date -Is)" >> work/logs/a1_smoke.log' \
    > work/logs/a1_smoke.log 2>&1 < /dev/null &
disown; echo $! > work/logs/a1_smoke.pid

# the ten, same flags, own output dir and log
setsid nohup bash -c 'python scripts/a1_align_run.py \
    --songs work/ready-pack/pack.json --data-dir work/ready-pack \
    --skip-separation --no-player --out work/srt/ready10 \
    --device cuda; echo "EXIT=$? $(date -Is)" >> work/logs/a1_ready10.log' \
    > work/logs/a1_ready10.log 2>&1 < /dev/null &
disown; echo $! > work/logs/a1_ready10.pid
```

Only after `EXIT=0` in `work/logs/a1_smoke.log`, launch the second block. One
track failing must not stop the batch; `failed_qa` is counted, not deleted.

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

---

## 10. Resume after a disconnect

Colab's disk is the VM, not a mount: a recycled runtime takes `/content` and
`/tmp` with it. Nothing here is worth backing up except the record, because the
expensive parts are all caches with a known rebuild cost.

| thing | where | survives? | rebuild |
|---|---|---|---|
| repo, index, scripts, docs | GitHub `main` | yes, once pushed | `git pull` |
| this run's metrics + audit table | `results/a1/ready10-2026-10-28/` | yes, once pushed | — |
| `/tmp/lyrics` clone | `/tmp` | no | `git clone --depth 1`, ~10 s |
| wav2vec2 model, 1.2 GB | `~/.cache/huggingface` | no | ~45 s download, once |
| `work/ready-pack`, 181 MB | `work/` (ignored) | no | `ready_pack --fetch`, 22 s |
| 10 SRTs + `words.json` | `work/srt/ready10` | no | align, 150 s |
| gap audit + cue sheets | `work/audit` | no | `scripts/a1_audit.py`, 8 s |

Total rebuild from a cold VM: **~4 minutes**, of which ~45 s is the model.

```bash
cd /content/yue2-karaoke-align && git pull
nvidia-smi && python -c "import torch; print(torch.cuda.is_available())"
test -d /tmp/lyrics || git clone --depth 1 \
    https://github.com/akbargherbal/arabic-music-lyrics /tmp/lyrics
mkdir -p work/logs
python scripts/ready_pack.py --lyrics-root /tmp/lyrics/lyrics --out work/ready-pack --fetch
python scripts/a1_align_run.py --songs work/ready-pack/pack.json \
    --data-dir work/ready-pack --skip-separation --no-player \
    --out work/srt/ready10 --device cuda
python scripts/a1_audit.py --out work/srt/ready10
```

Rules that make this work:

- `work/` is never pushed — it holds lyric text. Metrics and the audit table go
  to `results/`, which is committed; that is the durable record.
- Run the long steps detached (`setsid nohup ... &`). A closed browser tab does
  not kill them; only a recycled VM does, so do not idle for long with a batch
  pending.
- Commit after every passing step. An uncommitted fix on a dead VM is the only
  loss that costs thinking rather than compute.
