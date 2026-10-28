# A1 expander — design draft (not implemented)

**Status:** draft for review, 2026-10-28. Nothing here is code yet.
**Scope:** the `needs_expansion` queue only. The 13 accepted tracks are done.

## 1. What the queue actually is

`index/lyrics/readiness.csv` has **161** rows marked `needs_expansion` (the
brief said 154; the on-disk index is 7 rows larger — unresolved, see
`docs/A1-STATUS.md`). Every one of them carries exactly one flag family,
`low_wpm`, and that flag comes from one line of `lyrics_coverage.py`:

```python
if wpm < 50:
    flags.append(f"low_wpm:{wpm:.0f}")
```

So `needs_expansion` means **"the sheet has fewer words than 50 wpm implies"**,
nothing more. Median sheet is 116 words / 24 wpm over 18 lines; median track is 4.7 min, so
at 45 wpm it wants ~211 words. Median 116 against 211 is the whole queue in one
number.

| bucket (from `n_lines`, `wpm`) | rows |
|---|---|
| ≥ 8 lines, wpm < 30 (`long_but_slow`) | 113 |
| ≥ 8 lines, wpm ≥ 30 (`compact_plausible`) | 41 |
| 3–7 lines (`very_short`) | 5 |
| ≤ 2 lines (`sheet_stub`) | 2 |

**Evidence that `wpm` is a weak proxy:** the three `needs_cleanup` tracks we
just aligned sit at 28.5–39.4 wpm — under the same 50 threshold — and they
aligned 129/129 words with coverage 1.000 and no loud gap. A full sheet can
legitimately be slow. Conversely a fast song with a compact sheet lands above
50 and never enters this queue. Therefore the queue is a **suspicion list**, not
a diagnosis, and the expander must not treat membership as proof that text is
missing.

## 2. What "expansion" means

The sheet states each line once; the performance repeats sections. Forced
alignment maps each input word to exactly one span, so a compact sheet either
drifts (if the section repeats early) or leaves **loud gaps** (if the aligner
refuses to place text over an untexted repeat). Our ten ready tracks show the
distinction cleanly: `Kol Youm Beomre` has 0.84 coverage from a 40 s intro and a
51 s outro, both **27 dB below** the singing level — breaks, not missing text.
No accepted track has more than 3.8 s of gap at singing level. That is the
signature the expander has to detect, and the same rule `scripts/a1_audit.py`
already implements.

## 3. Architecture: triage → propose → verify

**Stage 0 — triage, no new code.** Align the sheet as-is, run the gap audit,
and classify each track:

| class | rule | action |
|---|---|---|
| `complete` | no loud gap > 2.5 s, coverage ≥ 0.90 or quiet tail | accept, **no expansion** |
| `compact` | loud-gap seconds > 2.5 s, or coverage < 0.90 with a loud tail | send to Stage 1 |
| `broken_sheet` | < 8 lines, < 40 words, or words/sec > 220 | reject to lyrics work — alignment cannot help |

Expect a large share of the 113 `long_but_slow` rows to be `complete`. Triage
is one alignment per track (~10 s), so it is cheaper than designing around a
queue that may halve itself.

**Stage 1 — propose, per loud gap.** For a gap of length `G`:

1. Local speech rate: words/min over the 60 s of accepted words around the gap
   (fallback 45 wpm). Expected new words `N ≈ G × rate / 60`.
2. Candidates: contiguous line ranges from the sheet, 1–4 lines, total words
   within ±25 % of `N`, duration fit `|sum(line_dur) - G| / G` where
   `line_dur = words / rate`.
3. Prior: prefer a block that already occurs immediately before or after the
   gap's neighbourhood (chorus repeats around a verse), scored by exact line
   match, then by token overlap. A candidate with no structural prior needs a
   duration fit within 10 % to be proposable at all.
4. One insertion per gap per iteration. Never invent text; a candidate is
   always a copy of existing lines.

**Stage 2 — verify, or revert.** Re-align with the insertion and accept only if
all hold:

- total loud-gap seconds drop by at least half of the inserted duration;
- `monotone` true, `words_ratio` 1.0 (true by construction), intro unchanged;
- no new loud gap appears elsewhere;
- first cue still passes the `--max-lead-gap` screen.

Otherwise revert and record `failed_expansion` with the attempted insert and
its score. Max 3 iterations per track, then stop and report — oscillation is a
reject, not a retry loop.

## 4. Provenance (the "did not invent cues" requirement)

Every insertion writes `<track>.expand.json`:

```json
{"iteration": 1, "gap": [106.0, 126.8], "gap_s": 20.8,
 "inserted_lines": [12, 13], "text_sha": "...", "words": 19,
 "score": {"duration_fit": 0.94, "prior": "repeat_after_verse_2"},
 "loud_gap_s_before": 34.1, "loud_gap_s_after": 12.2,
 "accepted": true}
```

The lyric text itself stays in `work/`; the journal references line indices and
a hash, so it can be archived in `results/` without shipping lyrics. A track
with any `accepted: false` entry is `failed_expansion` and is counted, never
silently dropped.

## 5. Validation: compaction round-trip, then a pilot

We can test the expander against ground truth for free. Take the 13 accepted
tracks, detect repeated line blocks by exact match, delete the **second**
occurrence of each repeat, and align the compacted sheet. That recreates the
failure. Run the expander; compare the restored alignment to the known-good
one.

- pass: median `|Δstart| ≤ 0.25 s`, p95 `≤ 1.0 s`, no word lost or reordered;
- fail: anything worse, or a wrong block inserted (detectable because the
  restored text differs from the known-good sheet).

Only after the round-trip passes, run a **20-track pilot** from the real queue,
prioritising `compact_plausible` (41 rows), and audit it with the same dB rule.

## 6. Gates for the pilot

Per track: the §4 gates of `A1-READY-RUN.md`, plus no loud gap > 2.5 s,
coverage explained (either ≥ 0.90 or a quiet tail), and the first-cue screen.
Report `accepted / expanded / failed_expansion` with the reason and the
attempted insert for every failure.

## 7. Cost

| stage | per track | 161 tracks |
|---|---|---|
| triage (1 alignment) | ~10 s | ~30 min GPU |
| expansion (≤3 more alignments) | ≤ 40 s | add ≤ 1.5 h on the `compact` subset only |

Fits one GPU sitting if triage runs first and the subset is small. Do not run
the whole queue blind.

## 8. Risks

- **Wrong block** (chorus inserted where a verse repeats): the duration fit
  cannot see text identity. Mitigated by the structural prior and the
  round-trip test; not eliminated.
- **Live/medley versions**: structure differs from the studio sheet in ways no
  repetition model captures. Expect these in `failed_expansion`.
- **Stub sheets** (7 rows with < 8 lines): not an expansion problem at all;
  they need source work.
- **Threshold creep**: `wpm < 50` already mislabels slow full sheets. Resist
  adding new numeric gates without the gap audit behind them.
- **Silent acceptance**: the failure mode to design against is a plausible
  insertion that places text 3 s off. Hence the revert rule and the journal.

## 9. Decisions needed before implementation

1. **Triage the 161 first** (recommended) instead of expanding on the `wpm`
   flag. This is the only step that can shrink the queue honestly.
2. Confirm an insertion is never accepted on a **quiet** gap — quiet means
   instrumental, and text there would be invented.
3. Pilot size 20, `compact_plausible` first.
4. ASR as a structure hint: recommend **no** for timing (the ASR family already
   failed here); if ever used, only to propose blocks, never to time words.
