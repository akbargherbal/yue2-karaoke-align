# A0-FINDINGS.md — Track A0a result (Suno proxy)

**Verdict: PASS on the objective screen, at word level.** Not yet the real
answer — A0b (a YuE2 render) is still required — but it is a clear positive, and
the failure we feared (WhisperX-style hallucination) did not appear.

Date 2026-10-15. Run on the local CPU box (no GPU). Method and protocol:
[docs/A0-RUNBOOK.md](../docs/A0-RUNBOOK.md).

## Setup

| | |
|---|---|
| Subject | `v3_arabmaqamrock_dataset/ajam_Lamiyat_Alshanfara_16082026_004_bb17d099` (Suno v3 render, 192.6 s) |
| Lyrics | 24 lines / 120 words, fully diacritized (from the caption `.txt`) |
| Separation | Demucs 4.1.0 `htdemucs --two-stems=vocals`, CPU, **58 s** |
| Aligner | `jonatasgrosman/wav2vec2-large-xlsr-53-arabic` + `torchaudio.forced_align`, CPU, **40 s** |

**This is a *forced* aligner** (fixed transcript, constrained search) — not ASR.
It cannot invent words; the earlier WhisperX failure was the ASR family.

Side finding: the aligner's tokenizer **represents the full harakat set**
(fathatan, dammatan, kasratan, fatha, damma, kasra, shadda, sukun, tatweel).
Only maddah and the hamza marks are absent. So alignment runs on the
*diacritized* skeleton, not a stripped one.

## Result

| Metric | Value |
|---|---|
| Words aligned | **120 / 120** |
| Monotone starts | **yes** (0 backwards, 0 overlaps, 0 zero-length) |
| Coverage | **1.000** (first word 21.48 s → last 192.65 s) |
| Word duration | median 0.76 s, max 6.56 s (final word, bleeds into the outro) |
| Inter-word gap | median 0.26 s |
| Gaps > 2 s | 11 (max 3.56 s) — these fall on **line boundaries** |

### Independent check: does it track the voice?

Against the vocal stem's energy envelope (the alignment was *not* fit to this —
it is an independent test):

| Region | RMS |
|---|---|
| Instrumental intro, 0.5–21 s | **0.0002** (silent) |
| First aligned word | **starts at 21.48 s** |
| Word spans (mean) | 0.1111 |
| Inter-line gaps (mean) | 0.0600 → **1.85×** lower |
| Frame-level on-word vs off-word | 0.1045 vs 0.0485 → **2.16×** |

The decisive one: **the vocal stem is silent for the first 21 s and the aligner
put no words there.** A hallucinating aligner would have smeared words into the
intro; this one waited for the voice. The inter-line gaps also land on low
energy. That is what a real alignment looks like.

## Bug found (recorded so it doesn't recur)

First run: **1 / 120 words**. Cause: the word delimiter `|` is itself in the
tokenizer's `all_special_ids`, so a naive "drop special tokens from the CTC
target" filter **deleted every word boundary** and the whole song collapsed to a
single word. Fix: never strip the word-delimiter token. (Also: `merge_tokens`
returns *vocabulary* ids, not target-sequence indices — worth knowing.)

## Dependencies and licensing

| Component | Source | Size | License | Token? |
|---|---|---|---|---|
| Aligner ("ears") `jonatasgrosman/wav2vec2-large-xlsr-53-arabic` | HuggingFace | ~1.2 GB | **Apache-2.0** | no (public, not gated) |
| Separation Demucs 4.1.0 `htdemucs` | pip + weights | ~80 MB | MIT (code) | no |
| torch / torchaudio / transformers | PyPI | ~1 GB (CPU) | BSD/Apache | no |

Downloaded once, then fully offline (HF cache ~2.5 GB). The dependency is a
*pretrained Arabic acoustic model*, not HuggingFace-the-service — HF is just the
easiest source; MFA's own Arabic models or a model trained from our own
audio+text pairs would work too.

**If this ever ships**, these become runtime dependencies (bundle or fetch on
first use), and the Apache-2.0 attribution should travel with them.

## Model comparison (A/B on the same track)

Two independently-trained Arabic wav2vec2 checkpoints, same audio, same lyrics,
same pipeline — only `--model` changed. Full output:
`results/a0a.compare.txt`.

| | jonatasgrosman | elgeish |
|---|---|---|
| Params / size / license | 315M / 1.2 GB / Apache-2.0 | 315M / 1.2 GB / Apache-2.0 |
| Input format | **Arabic script** (full harakat in vocab) | **Buckwalter transliteration** required |
| Words aligned | 120/120 | 120/120 |
| Monotone / overlaps | yes / 0 | yes / 0 |
| Coverage | 1.00 | 0.97 |
| Median word | 0.76 s | 0.68 s |
| Energy on/off ratio | 2.16× | 1.90× |
| First word | 21.48 s | 21.46 s |

**Agreement between the two models** (the sharpest evidence without ground truth —
two independent models agreeing means the audio really constrains it):

| |dstart| | value |
|---|---|
| median | **0.02 s** (≈ one 20 ms frame) |
| mean | 0.17 s |
| p90 | 0.40 s |
| within 0.3 s / 0.5 s / 1.0 s | 87% / 94% / 95% |

The worst disagreements cluster on four words (indices 54, 85–87, 110; up to
2.6 s), consistent with melisma or repeated phrasing.

**Two conclusions:**

1. **They are equivalent for our task.** And the reported *ASR* WER difference
   (39.6 vs 26.6) **did not translate into an alignment difference** — a concrete
   confirmation that WER is the wrong yardstick here. Pick on integration cost:
   `jonatasgrosman` takes Arabic script directly; `elgeish` needs a
   transliteration step, so it is the more fragile dependency.
2. **Agreement to ~one frame** is the strongest signal so far that the timings
   are real rather than plausible-looking.

Both players are committed for ear comparison:
`results/a0a.player.html` (jonatasgrosman) and `results/a0a.elgeish.player.html`.

## Caveats (do not over-read this)

1. **Suno proxy.** Same lyrics/genre, but cleanly produced. A0a is a *generous*
   screen. Passing means "worth trying on our own output", not "solved".
2. **One track, one aligner, one model.** No second family tried yet.
3. **Numbers are not ears.** Monotone + on-energy is necessary, not sufficient;
   within-word boundaries still need a listen. Player pages:
   `results/a0a.player.html` (vocals stem) and `results/a0a.player.mix.html`
   (full mix, same timings).
4. **Final word stretched to the end** (6.56 s) — the outro is being absorbed.
   Harmless for line display; wrong for word-level accuracy at the end.
5. The aligner is speech-trained; this result says it survived *this* mix.

## Next

- **A0b — satisfied at scale on 2026-10-15.** The B2 benchmark re-ran this
  pipeline on **8 real YuE2 renders** with 6 candidate models; three survived
  the silent-intro test and agreed to ~0.02 s. See
  [BENCH-FINDINGS.md](BENCH-FINDINGS.md). A0a's positive result did transfer.
- **A1 — done (2026-10-21).** `scripts/a1_align_run.py` batches a run directory
  into word JSON + line-level VTT/SRT + player + QA report. A smoke run on this
  same track reproduced word 0 at **21.481 s** (A0a: 21.48 s), 120/120 words.
  See [docs/A1-RUNBOOK.md](../docs/A1-RUNBOOK.md) and `results/a1/suno-smoke/`.
- **Still open:** A1 on a full YuE2 run directory (B2's audio was staged flat),
  and gold anchors if the model choice should be decided by accuracy.
