# STEM-CACHE.md — two-stem cache contract (vocals + instrumental)

Status: **decision record, 2026-10-28.** Applies to the Elissa / Fadl Shaker
SRT pipeline and to any future reuse of the separated audio.

This document exists so the decision is not re-litigated. If a future task needs
four stems, it gets a **new namespace and a new doc section**; it does not
overwrite or reinterpret `v1`.

---

## 1. Decision

- The default split is **two stems**, not four:
  - `vocals.flac` — Demucs `vocals`;
  - `instrumental.flac` — Demucs `no_vocals` (the mixture minus the vocal
    estimate).
- Separation happens **once per source audio**, on a GPU, and the result is
  cached on GCS.
- The cache key is **content-addressed** (`sha256` of the source object), not
  the filename.
- The cache is **private**. It is a derivative of copyrighted audio.
- Four stems (`drums`, `bass`, `other`) are **deferred**. If they are ever
  needed, they go under `htdemucs-4stem/v1/`; `htdemucs/v1/` is never rewritten.

## 2. Semantics — do not mislabel the second stem

Demucs with `--two-stems=vocals` produces:

| Demucs output | Cache name | Meaning |
|---|---|---|
| `vocals.wav` | `vocals.flac` | the model's vocal estimate |
| `no_vocals.wav` | `instrumental.flac` | the **mixture minus the vocal estimate** |

`instrumental` is an **accompaniment**, not a guarantee of a clean instrumental:
vocal bleed, backing vocals, reverb tails and artefacts can remain. It is also
not a license-free karaoke track. The manifest records
`"demucs_stem": "no_vocals"` for it so the provenance is explicit.

## 3. Why two stems

- The current consumer is **forced alignment**, which needs `vocals` only.
- `instrumental` is the cheapest useful second product: it is already emitted by
  the same Demucs run, and it enables future work (karaoke/mixing, ASR
  augmentation, source-separation evaluation, lyric-free references).
- Four stems cost more GPU time and roughly double the storage with **no current
  consumer**. Adding them later is a namespace bump, not a reason to redo the
  audio today.
- One GPU pass removes Demucs from the critical path of every future alignment
  run. That is the point: **separate once, align many times.**

## 4. GCS layout

```
gs://akbar-december-2024-backup/STEMS/htdemucs/v1/
  manifest.jsonl
  <source_sha256>/
    vocals.flac
    instrumental.flac
```

- `<source_sha256>` is the SHA-256 of the stored source object bytes (the MP3 as
  it sits in `DISCOGRAPHY/`), not a hash of the decoded waveform.
- The manifest is one JSON object per line (JSONL), one row per track.
- A human-readable mirror is optional; the manifest is the authority.

## 5. Key and versioning

The effective cache key is:

```
source_sha256
+ separator family          ("htdemucs")
+ cache version             ("v1")
+ model / params family     (two_stems=vocals, model=htdemucs)
```

Rules:

- If the source object changes (re-encode, remaster, replacement), the hash
  changes and a **new entry** is created. Never overwrite an existing hash path.
- If the Demucs model, major version, or separation parameters change, bump the
  namespace (`v2`). Do not mix versions in one folder.
- Renames, album moves and duplicate titles do **not** invalidate the cache; the
  hash is stable. This is why filenames are not the key.
- The manifest row stores `source_md5` as well, so a cheap GCS listing can detect
  a source change without downloading.

## 6. Format and measured size

Demucs on this corpus writes **44.1 kHz, stereo, PCM_16** WAV. The cache stores
lossless FLAC at the same rate/channels/subtype.

Measured on the pilot (`Bastanak`, 236.09 s):

| Stem | WAV | FLAC | Ratio |
|---|---:|---:|---:|
| `vocals` | 41.6 MB | **19.7 MB** | 0.47 |
| `instrumental` | 41.6 MB | **23.8 MB** | 0.57 |
| both | 83.2 MB | **43.5 MB** | 0.52 |

Projection for the 236-track corpus (19.188 h):

- both stems: **~13–15 GB** (varies with vocal density; a second measured track,
  `Baddy Doub`, was 14.5 MB vocals + 25.3 MB instrumental);
- vocals only: **~6 GB**.

GCS storage cost is negligible at this size. Egress is the only recurring cost
if the cache is pulled repeatedly; download it once per machine and reuse the
local copy.

Do not assume the sample rate: read it from the produced file and store
`sample_rate`, `channels`, `subtype`, `duration_s` and `bytes` in the manifest.

## 7. Manifest schema

`manifest.jsonl`, one row per successfully cached track:

```json
{
  "track_id": "Elissa/Elissa - 2006 Bastannak/01.Bastanak.mp3",
  "artist": "Elissa",
  "album": "Elissa - 2006 Bastannak",
  "file": "01.Bastanak.mp3",
  "source_uri": "gs://akbar-december-2024-backup/DISCOGRAPHY/ELISSA/...",
  "source_sha256": "…",
  "source_md5": "…",
  "source_duration_s": 236.09,
  "stems": {
    "vocals": {
      "uri": "gs://…/STEMS/htdemucs/v1/<sha>/vocals.flac",
      "sha256": "…",
      "bytes": 19700000,
      "format": "flac",
      "sample_rate": 44100,
      "channels": 2,
      "subtype": "PCM_16",
      "duration_s": 236.09,
      "demucs_stem": "vocals"
    },
    "instrumental": {
      "uri": "gs://…/STEMS/htdemucs/v1/<sha>/instrumental.flac",
      "sha256": "…",
      "bytes": 23800000,
      "format": "flac",
      "sample_rate": 44100,
      "channels": 2,
      "subtype": "PCM_16",
      "duration_s": 236.09,
      "demucs_stem": "no_vocals"
    }
  },
  "separator": {
    "name": "demucs",
    "model": "htdemucs",
    "version": "4.x",
    "two_stems": "vocals",
    "device": "cuda",
    "params": {"shifts": 1, "overlap": 0.25}
  },
  "status": "ok",
  "created_at": "2026-10-28T…Z"
}
```

`status` is `ok`, `partial` or `failed`. Only `ok` rows are reused. A failed row
records the error and never blocks a retry.

## 8. Write protocol (atomicity)

1. Download the source to a temp path.
2. Compute `source_sha256` and `source_md5`.
3. If the hashed path already has both stems, add/reuse the manifest row and
   stop (no GPU).
4. Run Demucs into a temp directory.
5. Convert both WAVs to FLAC; verify duration, channels, sample rate, non-silence
   and absence of NaNs.
6. Upload `vocals.flac` and `instrumental.flac` first.
7. Compute each stem's SHA-256 and size; **only then** mark `status=ok`.
8. Append the row to `manifest.jsonl`, rewrite the object atomically, and keep a
   local copy.
9. Delete temp audio/stems unless `--keep-temp`.

If step 6 or 7 fails, write `status=partial`/`failed`; do not let a later run
treat a missing object as cached. A re-run is idempotent: it checks the manifest
and the object paths.

## 9. Pipeline integration

Tool (L0.5 of the SRT plan): `scripts/gcs_stems.py`. The unattended Colab
procedure, checkpoint/resume protocol and failure drills are in
[`STEM-BATCH-RUNBOOK.md`](STEM-BATCH-RUNBOOK.md).

```bash
# What is already cached? no downloads, no GPU
python scripts/gcs_stems.py audit \
    --inventory work/lyrics/inventory.csv

# One-time GPU pass (benchmark 3 tracks first)
python scripts/gcs_stems.py separate \
    --inventory work/lyrics/inventory.csv \
    --device cuda --limit 3

# Full pass (resumable; 0 = no time stop, checkpoints protect finished tracks)
python scripts/gcs_stems.py separate \
    --inventory work/lyrics/inventory.csv \
    --device cuda --max-minutes 0 --max-failures 5

# Pull cached stems for an alignment batch
python scripts/gcs_stems.py fetch \
    --inventory work/lyrics/inventory.csv \
    --local work/stems --limit 12
```

Local layout for A1:

```
work/stems/htdemucs/<audio-stem>/vocals.flac
work/stems/htdemucs/<audio-stem>/instrumental.flac
```

A1/`bench.ensure_stem` must accept `vocals.flac` as well as `vocals.wav` (small
patch), then align with `--skip-separation`. The cache is the only separation
path for the batch; A1 does not re-run Demucs when the stem is present.

## 10. Cost, rights, and access

- **Storage:** ~13 GB for the full two-stem corpus. Standard class is fine.
- **Egress:** one download per machine/batch; avoid repeated full pulls.
- **Rights:** stems are derivatives of copyrighted recordings. Keep the bucket
  private; do not publish stems or derived SRTs without a rights decision.
- **Access:** the same service account/user that reads `DISCOGRAPHY/`; no public
  ACLs.
- **Retention:** keep `v1` even after `v2` appears; it is the reproducibility
  anchor for runs that used it.

## 11. Future uses the two stems already enable

- SRT / forced alignment (current goal).
- Singing-ASR training with vocal-only and accompaniment-only variants.
- Karaoke/mixing experiments (with the caveat in §2: accompaniment ≠ clean
  instrumental).
- Source-separation evaluation and vocal-presence gating.
- Lyric-conditioned retrieval and pronunciation studies.

If a future task genuinely needs drums/bass/other, add
`STEMS/htdemucs-4stem/v1/` and a section here. Do not mutate `htdemucs/v1/`.

## 12. Open decisions (record, do not silently change)

- **PCM_16 vs 24-bit:** Demucs emits PCM_16 on this corpus; the cache preserves
  it. Revisit only if a downstream task needs more headroom.
- **44.1 kHz vs 16 kHz:** the cache keeps 44.1 kHz for reuse; the aligner
  resamples to 16 kHz at load time.
- **`instrumental` naming:** cache name is `instrumental`; the Demucs output name
  is `no_vocals`. Both are recorded in the manifest.
- **Four stems:** deferred; new namespace if ever needed.
