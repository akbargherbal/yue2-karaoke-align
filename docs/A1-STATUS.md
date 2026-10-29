# A1 status — resume point

**Updated:** 2026-10-28 ~21:55 UTC on a Colab T4 (ephemeral disk).
This file is the checkpoint. Update it and push it at the end of every step; a
recycled runtime then costs ~4 minutes of rebuild, not the work.

## Where we are

| step | state |
|---|---|
| index v2 (`index/lyrics/`) | 236 rows on disk. The 214 scope is the same file filtered to `duration_s <= 360`, so nothing was lost: 174-7=**167** matched, 57-10=**47** missing, 5-5=**0** ambiguous, 161-7=**154** expansion. The 22 long tracks are the entire gap (reconciled 2026-10-28, `docs/A1-COHORTS.md`) |
| stem cache | complete, 214 tracks / 428 FLAC, `status=ok`; never re-separated |
| staging (`work/ready-pack`) | done, 10 tracks, 181 MB, `EXIT=0` |
| smoke (1 track) | `EXIT=0`, 181/181 words, 54.8 s |
| A1 batch, 10 ready tracks | **10/10 accepted, 0 failed_qa**, 150.6 s, `EXIT=0` |
| gap audit vs vocal stem | done; two low-coverage tracks cleared as instrumental intros/outros |
| known defect | leading word of `10.Saharni...13. Ya Hayat El-Roh` anchored 34.9 s early → 43 s opening cue |
| fix for that defect | **done** — `--max-lead-gap` guard; re-run 10/10 ok, other nine bit-identical, opening cue 63.964 → 71.844 |
| 3 `needs_cleanup` tracks | **done: 3/3 accepted** (129/129 words, coverage 1.0); latin lines are dropped by the vocab filter, low wpm is tempo — nothing to clean for timing |
| publish (step 4) | **done 2026-10-28** — `SRT/v1/{ready10,cleanup3,exp2}-2026-10-28/` = 31/10/7 objects, `verify: ok` on all three; manifests committed (`f1ded0a`), SRTs no longer only in `work/` |
| triage (`needs_expansion`), align | **148/148 ok, 0 failed**, `EXIT=0`, 40m33s. 161 rows → 150 stageable; 7 are >360 s and have no stem in the 214 cache, 4 have no sheet for the title; 2 were already aligned in exp2 |
| triage, durability | `gs://…/SRT/v1/triage-2026-10-28/` — 444 objects, **manifest backfilled 2026-10-29** (444/444 with `track_id` and md5; sha256 is not recoverable once the local dir is gone). Text-side metrics in `results/a1/triage-2026-10-28/STATS.md` |
| expander | design draft in `docs/A1-EXPANDER.md`; **not implemented**; triage-first, 161 rows on disk (154 in the brief) |

## Next action

Triage is aligning-done; what remains is measurement, not GPU:

1. **Audit the 148** with `scripts/a1_audit.py` (loud-gap seconds per track), then
   classify **per sheet**, not per track (150 tracks sit on 143 sheets).
2. **Publish** `work/srt/triage` — done, and the manifest was backfilled from
   the prefix (`srt_publish.py --backfill`), so the checkpoint is a complete run.
3. Only then pick the expansion pilot from the sheets that come out `compact`.
   The `broken`/`complete` buckets need no text work.

Still open, unchanged: the 7 stubs, 5 ambiguous titles, 57 `missing` rows.

## Rebuild from a cold VM (~4 min)

See `docs/A1-READY-RUN.md` §10 for the full table. The short version:

```bash
cd /content/yue2-karaoke-align && git pull
nvidia-smi && python -c "import torch; print(torch.cuda.is_available())"
test -d /tmp/lyrics || git clone --depth 1 \
    https://github.com/akbargherbal/arabic-music-lyrics /tmp/lyrics
mkdir -p work/logs
python scripts/ready_pack.py --lyrics-root /tmp/lyrics/lyrics --out work/ready-pack --fetch
```

## Remaining cohorts

See `docs/A1-COHORTS.md`: 2 expansion tracks on a proven sheet (align now), 159
on unproven sheets (triage first, 143 unique sheets), 7 stubs and 57 missing +
5 ambiguous tracks that need lyrics work, not alignment.

## Durable vs ephemeral

- Durable: this repo, `results/a1/ready10-2026-10-28/{run.json,AUDIT.md}`
  (metrics only), `scripts/a1_audit.py`, the three `SRT/v1/…` prefixes
  (`ready10` 31 objects, `cleanup3` 10, `exp2` 7) and the triage checkpoint
  `SRT/v1/triage-2026-10-28/` (444 objects, manifest pending).
- Ephemeral: `work/` (SRTs, cue sheets, pack — lyric text, never pushed),
  `/tmp/lyrics`, the 1.2 GB HF model.
- Do not push `work/`; do not leave a passing step uncommitted.
