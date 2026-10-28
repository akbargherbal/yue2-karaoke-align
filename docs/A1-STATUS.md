# A1 status — resume point

**Updated:** 2026-10-28 ~18:40 UTC on a Colab T4 (ephemeral disk).
This file is the checkpoint. Update it and push it at the end of every step; a
recycled runtime then costs ~4 minutes of rebuild, not the work.

## Where we are

| step | state |
|---|---|
| index v2 (`index/lyrics/`) | on disk: 174 matched rows (161 `needs_expansion`); the brief said 167/154 — still unexplained, 10 `ready` / 3 `needs_cleanup` agree |
| stem cache | complete, 214 tracks / 428 FLAC, `status=ok`; never re-separated |
| staging (`work/ready-pack`) | done, 10 tracks, 181 MB, `EXIT=0` |
| smoke (1 track) | `EXIT=0`, 181/181 words, 54.8 s |
| A1 batch, 10 ready tracks | **10/10 accepted, 0 failed_qa**, 150.6 s, `EXIT=0` |
| gap audit vs vocal stem | done; two low-coverage tracks cleared as instrumental intros/outros |
| known defect | leading word of `10.Saharni...13. Ya Hayat El-Roh` anchored 34.9 s early → 43 s opening cue |
| fix for that defect | **done** — `--max-lead-gap` guard; re-run 10/10 ok, other nine bit-identical, opening cue 63.964 → 71.844 |
| 3 `needs_cleanup` tracks | **done: 3/3 accepted** (129/129 words, coverage 1.0); latin lines are dropped by the vocab filter, low wpm is tempo — nothing to clean for timing |
| expander / `needs_expansion` | not started, gated |

## Next action

Nothing queued that is safe to start without a decision. The two open paths:

1. **Review the 13 accepted tracks** with a player and a human ear — the only
   remaining accuracy question is musical, not statistical.
2. **The expander** for `needs_expansion` — design work, gated: it needs a rule
   for repeating a compact sheet across a song's structure and a way to prove
   it did not invent cues. Do not start it as a command.

13/13 tracks accepted on compact sheets is the number to carry in.

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

## Durable vs ephemeral

- Durable: this repo, `results/a1/ready10-2026-10-28/{run.json,AUDIT.md}`
  (metrics only), `scripts/a1_audit.py`.
- Ephemeral: `work/` (SRTs, cue sheets, pack — lyric text, never pushed),
  `/tmp/lyrics`, the 1.2 GB HF model.
- Do not push `work/`; do not leave a passing step uncommitted.
