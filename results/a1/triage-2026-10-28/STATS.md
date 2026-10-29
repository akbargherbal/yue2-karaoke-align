# Triage-2026-10-28 — measurement, text side

Source: the 148 `.words.json` published to `gs://akbar-december-2024-backup/SRT/v1/triage-2026-10-28/`, joined to the index by `track_id` from the backfilled manifest. No audio was needed for the table below; the sampled dB audit at the end did use the vocal stems.

## Alignment quality (all 148)

- words aligned/in: **16939/16954** (0.9991)
- `line_mapping = line`: **148/148**
- `intro_violation = true`: **2**
  - `15.The_Best_Of__Al_Rawae3_.2010_-_01.Rouh`
  - `Elissa_-_2006_Bastannak_-_07.Taa`
- coverage (last cue / duration): min **0.76**, p10 **0.87**, median **0.97**, max **1.00**
- tracks with coverage < 0.85: **8**

## Gaps between cues (text only)

- median largest internal gap: **16.4s**; > 10s: **87**; > 20s: **64**; worst **53.5s**

A large gap is **not** by itself evidence of missing text: the 13 accepted tracks have largest gaps of 3.5–40.8 s (median ≈ 19 s). Text cannot separate an instrumental break from skipped singing; the dB rule in `scripts/a1_audit.py` can, and it needs the vocal stem.

## Sampled dB audit (7 tracks, the 4 worst gaps + 3 with none)

| track | seconds of loud singing inside gaps | worst |
|---|---:|---|
| Elissa / Aisha Welsalam | 25.0 | 8 gaps, largest 6.4s @ +2 dB |
| Fadl Shaker / Baya'a Al Qoloub (Hafla 2004) | 2.0 | 1 gap of 2.0s @ -2 dB |
| Fadl Shaker / Hakhally Baly | 30.0 | 7 gaps, largest 9.5s @ +1 dB |
| Fadl Shaker / Bafakkar Bel Sa'at | 11.3 | 4 gaps, largest 3.8s @ +6 dB |
| Fadl Shaker / Allah Alam | 0.0 | no gap > 2s at all |
| Fadl Shaker / Allah We'allam | 0.0 | no gap > 2s at all |
| Elissa / Albi Hasis Fik | 7.5 | 3 gaps, largest 2.6s @ +5 dB |

Reading:

- The four 50 s+ gaps are **silent** (-34 to -50 dB): instrumental intros/interludes, exactly like the accepted set. Large gaps alone are a false alarm.
- What separates the tracks is *loud* singing inside gaps: 0 s for the two `Allah Alam` tracks, 7–30 s for four others. Those are the expansion candidates.
- `inter_line_gap_env_median` in `.words.json` tracks this (0.000 for the clean pair, 0.11–0.21 for the four) and can rank all 148 for free, but it is a proxy, not the audit: rank with it, then confirm with the dB rule.
- Missing: the triage run has no `summary.json`/`run.json` in the prefix, so the 148 have no committed per-track metrics; this file is the metrics.
