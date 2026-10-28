# A1 cohorts — what to do with the rest

**Updated:** 2026-10-28. Source of truth: `index/lyrics/coverage.csv` (236 rows)
and `index/lyrics/readiness.csv` (174 matched rows).

## The whole inventory, in dispositions

| cohort | n | usable sheet | action | cost |
|---|---|---|---|---|
| accepted (10 `ready` + 3 `needs_cleanup`) | **13** | yes, proven | listen; timing gates already pass | human |
| `needs_expansion`, sheet proven by a ready/cleanup track | **2** | yes | align as-is, expect `complete` | 20 s GPU |
| `needs_expansion`, unproven sheet | **159** | yes, unvalidated | **triage** (design: `docs/A1-EXPANDER.md`) | ~30 min GPU |
| ↳ of those, stub sheets (< 8 lines) | 7 | no, broken | lyrics work; do not align | human |
| `missing` (no lyric candidate) | **57** | no | source lyrics; cannot align | human |
| `ambiguous` (title match unresolved) | **5** | maybe | `title_map.csv` decision | ~15 min human |

Total 236 = 13 + 2 + 159 + 57 + 5. The brief's "214 tracks → 167 matched,
47 without lyrics" does not reproduce on disk (22 more inventory rows, 7 more
matched, 10 more missing); that gap is still unexplained and should be settled
before anyone quotes totals.

## Two facts that shrink the work

1. **174 tracks use 152 unique lyric sheets** (18 sheets are shared, up to 4
   tracks each). For the expansion queue: 159 tracks on 143 unique sheets. A
   sheet-level verdict (`complete` / `compact` / `broken`) applies to every
   track carrying it, so triage needs **143 decisions, not 159** — and a
   decision on a shared sheet is reusable.
2. **Most of the 159 are not known to be missing text.** The queue is defined by
   `wpm < 50`; our 3 `needs_cleanup` tracks prove a full sheet can sit at
   28–39 wpm. Triage will move a large share to `complete` with no text added.

## What not to do

- Do not align the 57 `missing` + 5 `ambiguous` tracks. There is no text to
  force-align; a result would be invented.
- Do not expand on the `wpm` flag alone (see the expander draft §1).
- Do not re-run Demucs for any of them; the stem cache already holds all 214.
- Do not treat a shared sheet as aligned evidence for its other tracks — each
  release has its own timing and needs its own alignment (10 s each).

## Order of work, if the queue is picked up again

1. **Triage** the 159 (one alignment each, ~30 min GPU, detached), then classify
   per sheet. No expansion yet.
2. **Expand** only the tracks whose sheet is `compact`, starting with the 20
   pilot; validate with the compaction round-trip before the pilot.
3. **Content work in parallel, no GPU:** the 7 stubs (replace the sheet), the 5
   ambiguous (`title_map.csv`), the 57 missing (source lyrics; 4 already have
   reviewer URLs in `manual_sources.csv`).
4. **Accept nothing without an audit** — the first-cue screen and the dB gap
   rule are the two checks that caught real defects in the 13.
