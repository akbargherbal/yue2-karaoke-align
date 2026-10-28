# STEM-BATCH-RUNBOOK.md — one GPU pass, resumable, failure-isolated

Operational plan for the one-time Demucs pass over the **236 Elissa / Fadl
Shaker tracks**. Contract: [`STEM-CACHE.md`](STEM-CACHE.md). This runbook is
about **not losing work**: per-track failure isolation, a durable checkpoint
after every track, and a resume procedure that survives a Colab disconnect.

The target is two stems per track:

```
gs://akbar-december-2024-backup/STEMS/htdemucs/v1/
  manifest.jsonl          # one row per track, the resume authority
  run.log                 # append-only progress
  errors.log              # append-only tracebacks
  <source_sha256>/
    vocals.flac
    instrumental.flac
```

---

## 1. Guarantees

| Failure | Behaviour |
|---|---|
| One track fails (bad URI, OOM, corrupt audio) | logged to `errors.log`, `manifest` row `status=failed`, **batch continues** |
| Colab disconnects | every completed track is already on GCS; worst case is **the current track** |
| You set a soft time budget | `--max-minutes` stops cleanly and exits 0; completed tracks are durable |
| GCS/auth is down | after `--max-failures` consecutive failures the batch stops instead of burning GPU |
| Resume | same command; skips `status=ok` rows whose stems exist, retries failed rows |
| Manifest says ok but a stem object is missing | reported `stale` by `audit`, re-separated on the next run |
| Source audio changed | new `sha256` → new cache entry; old one is never overwritten |

**Checkpoint granularity is one track, not five minutes.** A five-minute timer
would not help: Demucs has no partial-result format that is worth saving. What
matters is that *finished* work is durable. With ~1–2 min per track on GPU, the
checkpoint interval is already below five minutes; on a slow/long track the
worst-case loss is that one track, never the batch.

---

## 2. Durable state vs ephemeral state

| State | Where | Survives Colab? |
|---|---|---|
| source audio | `DISCOGRAPHY/` (read-only) | yes |
| stems | `STEMS/htdemucs/v1/<sha>/` | **yes** |
| manifest | `STEMS/htdemucs/v1/manifest.jsonl` | **yes** |
| logs | `STEMS/htdemucs/v1/{run,errors}.log` | **yes** |
| local manifest copy | `work/stems-gcs/manifest.jsonl` | no |
| temp audio, Demucs WAVs | `/tmp/stems-*` | no |
| notebook, venv | Colab VM | no |

The script uploads the stems **before** marking the row `ok`, then rewrites the
manifest and uploads it. A disconnect between those steps can leave an orphan
stem object; the next run redoes that one track. It cannot corrupt completed
rows.

---

## 3. Per-track algorithm (what the script does)

```
for each inventory track not already ok+verified:
    optional time budget        -> stop cleanly if set and exceeded
    download source to temp
    sha256(source)              -> cache key
    if key already has both stems -> reuse row, skip GPU
    run Demucs (two stems) on GPU
    convert vocals/no_vocals to FLAC
    verify duration, channels, sample rate, non-silence
    upload vocals.flac + instrumental.flac
    write manifest row status=ok
    upload manifest.jsonl + logs      <-- checkpoint
    delete temp
    on any exception:
        write status=failed + error
        append traceback to errors.log
        upload manifest + logs
        continue to next track
```

Failure is **per track**. A `failed` row is retried automatically on the next
run; it never blocks the rest.

---

## 4. Colab setup (fresh runtime)

Use a **GPU runtime**. The free/Pro T4 is enough; htdemucs fits in 15 GB.

### 4.1 Authenticate

Default path: **user login**. The operator runs `gcloud auth login` in the
runtime before the session; the agent verifies it and proceeds. Auth lives in
the VM, so it is lost on disconnect — repeat it on resume.

```bash
# gcloud is not preinstalled on Colab; skip if already present
!command -v gcloud >/dev/null || \
    curl -sSL https://sdk.cloud.google.com | bash -s -- --disable-prompts
```

```python
# make gcloud visible to every later shell/subprocess in this kernel
import os
os.environ["PATH"] = "/root/google-cloud-sdk/bin:" + os.environ["PATH"]
```

```bash
!gcloud auth list --filter=status:ACTIVE --format="value(account)"
!gcloud config get-value project
!gcloud storage ls "gs://akbar-december-2024-backup/DISCOGRAPHY/" | head
```

If there is no ACTIVE account, the operator runs:

```bash
!gcloud auth login --no-launch-browser
```

That needs a browser/code from the operator; the agent must ask and wait, then
re-check. Do not write to GCS until `gcloud storage ls` returns the folders.
If the project is unset, ask for the project id and set it once.

**Service account (optional, for unattended runs).** If the operator prefers a
key instead of user login: create a service account with
`roles/storage.objectAdmin` on the bucket, store the key JSON in Colab Secrets
as `GCS_SA_JSON`, then:

```python
from google.colab import userdata
open('/content/sa.json', 'w').write(userdata.get('GCS_SA_JSON'))
```

```bash
!chmod 600 /content/sa.json
!gcloud auth activate-service-account --key-file=/content/sa.json
!gcloud config set project $(python -c "import json;print(json.load(open('/content/sa.json'))['project_id'])")
!gcloud storage ls "gs://akbar-december-2024-backup/DISCOGRAPHY/" | head
```

Delete the key file at the end of the runtime. Do not put it in the repo.

### 4.2 Code and dependencies

```bash
!git clone https://github.com/akbargherbal/yue2-karaoke-align.git /content/yue2-karaoke-align
!git clone --depth 1 https://github.com/akbargherbal/music_captioning_oct_2026.git /content/music_captioning_oct_2026
%cd /content/yue2-karaoke-align
!pip install -q demucs soundfile numpy
```

Colab already provides a CUDA build of torch. Verify:

```python
import torch
print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))
```

```bash
!nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
```

### 4.3 Inventory

The inventory is already built (236 tracks) and can be rebuilt from GCS:

```bash
!python scripts/gcs_inventory.py \
    --caption-root /content/music_captioning_oct_2026/outputs \
    --out work/lyrics/inventory.csv
```

If the caption clone is not on the runtime, copy `inventory.csv` from Drive or
regenerate it from the committed caption JSONs. The script only needs
`track_id, artist, album, file, gcs_uri, duration_s`.

---

## 5. Benchmark before the long run (3 tracks)

```bash
!python -u scripts/gcs_stems.py separate \
    --inventory work/lyrics/inventory.csv \
    --device cuda --limit 3 --max-minutes 20
```

Record:

- separation seconds per track (and track duration);
- peak VRAM (`nvidia-smi` sampled in a second cell);
- FLAC bytes for `vocals` and `instrumental`;
- any failure.

Then audit:

```bash
!python scripts/gcs_stems.py audit \
    --inventory work/lyrics/inventory.csv \
    --out work/stems-gcs/audit.csv
```

If VRAM is tight, add `--segment 30` (recorded in the manifest). Do not change
`--shifts`/`--overlap` between runs without bumping the cache namespace.

---

## 6. The long run (unattended)

`--max-minutes` is **not** an estimate of the Colab session length. It is an
optional **clean-stop budget**. Colab does not guarantee a contiguous window:
the free tier has a daily compute quota (often quoted around 5 h, demand
dependent) but a session can end earlier; Pro gets priority and longer windows,
not a guarantee. The design therefore does **not** depend on any number.

Default command — no time stop; the per-track checkpoint is the protection:

```bash
!python -u scripts/gcs_stems.py separate \
    --inventory work/lyrics/inventory.csv \
    --device cuda --max-minutes 0 --max-failures 5
```

Use a budget only when you want a clean exit before a window you control:

| Situation | Suggested `--max-minutes` |
|---|---|
| Free tier, leave it running | `0` (rely on checkpoints) |
| Free tier, watched session | 10–15 min less than you plan to stay |
| Pro, watched session | 10–15 min less than you plan to stay |
| Any tier, uncertain | `0` + resume |

If Colab kills the VM at an arbitrary time, the worst case is still **the
current track**; completed tracks are already on GCS. A budget only turns that
into a clean `exit 0` and a log line.

For a browser-independent process (the budget below is just an example — set it
to your planned window, or use `0`):

```bash
!nohup python -u scripts/gcs_stems.py separate \
    --inventory work/lyrics/inventory.csv \
    --device cuda --max-minutes 0 --max-failures 5 \
    > /content/run.out 2>&1 &
```

Monitor from another cell:

```bash
!tail -n 20 /content/run.out
!gcloud storage cat gs://akbar-december-2024-backup/STEMS/htdemucs/v1/manifest.jsonl \
    | wc -l
```

- `--max-failures 5` is a circuit breaker for auth/network problems; one bad
  track never triggers it.
- Only **one writer** may run against the manifest. Do not start a second
  notebook on the same prefix.

---

## 7. Resume after a disconnect

Colab is ephemeral: the VM, venv, temp files and local manifest are gone. The
stems, manifest and logs are not.

1. Reopen a GPU runtime and repeat §4.1–§4.2 (auth + install).
2. Check the durable state:
   ```bash
   !gcloud storage cat gs://akbar-december-2024-backup/STEMS/htdemucs/v1/manifest.jsonl | wc -l
   ```
3. Run **the same command** as §6. The script downloads the remote manifest and
   logs, skips `status=ok` rows with both stems present, and retries failed
   rows.
4. Audit:
   ```bash
   !python scripts/gcs_stems.py audit --inventory work/lyrics/inventory.csv
   ```
5. Repeat until `cached ok = 236` (or only known-bad tracks remain).

No separate "resume mode" is needed; idempotence is the resume mode. If the last
track was killed mid-separation, it is the only one redone.

### What if a stem object is missing?

`audit` reports it as `stale`. The next `separate` run treats it as missing and
re-separates that track. Never hand-edit the manifest to mark it ok.

---

## 8. Failure drills (run once on a fresh cache)

**A. One bad track does not stop the batch**

```bash
# inventory with one bad URI followed by a good/cached track
python scripts/gcs_stems.py separate --inventory inv_bad.csv --limit 2
```

Expected: `status=failed` for the bad row, traceback in `errors.log`, the next
track is processed/skipped, exit code 1.

**B. Kill mid-run, resume**

```bash
python scripts/gcs_stems.py separate --limit 2     # let two finish
# Ctrl-C / kill during the third
python scripts/gcs_stems.py audit                  # 2 cached
python scripts/gcs_stems.py separate               # continues at #3
```

Expected: no duplicate work for the two finished tracks; the interrupted track
is redone.

**C. Stale object**

Delete one `vocals.flac` from a cached hash, run `audit` → `stale=1`, run
`separate` → that track is redone, the rest skipped.

**D. Time budget**

```bash
python scripts/gcs_stems.py separate --max-minutes 0.001 --limit 5 --dry-run
```

Expected: clean stop message, exit 0, no failed rows.

---

## 9. Acceptance checklist

- [ ] `audit` accounts for every inventory row:
      `cached + missing + failed + stale = 236`.
- [ ] `cached ok = 236`, or every remaining row has a logged reason.
- [ ] `manifest.jsonl` line count matches `cached ok` (plus failed rows).
- [ ] 3 random cached rows: `vocals.flac` and `instrumental.flac` exist, are
      FLAC 44.1 kHz stereo, and have the same duration as the source ±1 s.
- [ ] `fetch --limit 1` then A1 `--skip-separation` produces a non-empty SRT.
- [ ] `errors.log` contains no unexamined tracebacks.
- [ ] No service-account key, audio or stem is committed to git.

---

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| `gcloud: command not found` | install the SDK (§4.1), `source ~/.bashrc` |
| `401/403` on GCS | re-activate the service account; check bucket IAM |
| CUDA OOM | add `--segment 30`, close other notebooks, retry that track |
| `torch.cuda.is_available() == False` | Runtime → Change runtime type → GPU; reinstall if needed |
| All tracks failing in a row | circuit breaker stops at 5; check auth/network, then rerun |
| `audit` shows `stale` | run `separate`; it redoes only the stale tracks |
| Colab disconnected | repeat §7; do not start a second writer |
| Upload slow / throttled | keep going; each track is checkpointed; rerun later if needed |
| Wrong device recorded | stems are still valid; the manifest records what produced them |

---

## 11. Cost and time (order of magnitude)

- **Storage:** ~13–15 GB for both stems (236 tracks).
- **Network:** ~2 GB down (source) + ~10–15 GB up (stems).
- **GPU:** benchmark first. CPU was 63–66 s separation for a 236 s track; GPU is
  expected to be an order of magnitude faster, but do not extrapolate without
  the 3-track measurement.
- **Checkpoint overhead:** one manifest + two log uploads per track (a few
  hundred KB); negligible.

---

## 12. After the pass

The separation bottleneck is gone. The SRT batch then runs with:

```bash
python scripts/gcs_stems.py fetch --inventory work/lyrics/inventory.csv \
    --local work/stems --limit N
python scripts/a1_align_run.py --data-dir ... --auto-songs --skip-separation
```

The remaining bottleneck is the lyric side: title map + compact-sheet expansion
(see [`CROSS-PROJECT.md`](CROSS-PROJECT.md) §5–§6), not Demucs.
