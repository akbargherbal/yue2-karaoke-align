# ROUTE-A.md — post-hoc alignment, done properly

Status: **Track A is validated and has a batch pipeline** (A0a pass → B2 on 8
real YuE2 renders → A1 in `scripts/a1_align_run.py`). Route A has already
failed once in this project's experience; this document is why that failure was
not conclusive, and what was actually tested.

## 1. The failure that already happened, named correctly

Prior experience: WhisperX on the rendered songs **hallucinated** — invented or
looped words — and the quality was not acceptable.

That is the expected behaviour of the **ASR family** on music:

- WhisperX / stable-ts run a *speech recogniser* first and align to *its* output.
  Whisper has well-documented repetition/hallucination failures on music and
  long silence. When the recogniser invents words, the alignment is built on
  invented words. Separation does not fix the recogniser.
- So the negative result is about **ASR-based alignment**, not about
  alignment **per se**.

## 2. The distinction that matters

| | ASR-based (WhisperX, stable-ts) | Forced alignment (MFA, Charsiu, aeneas) |
|---|---|---|
| Input | audio only | audio **+ the known text** |
| How | transcribe, then align to the transcript | constrained search over the given text |
| Failure mode | **wrong content** (hallucination, loops) | only **wrong timing** |
| Diagnostic | output has words you never wrote | output has *your* words in the wrong place |

For karaoke, the text is **known** — it is our own prompt. Handing it to the
aligner removes the failure mode that burned us. That is the whole argument for
retrying with the other family.

## 3. Why our corpus is unusually favourable

- **The lyrics are fully diacritized.** Harakat encode the vowels, so
  grapheme→phoneme is close to deterministic — exactly the lexicon a forced
  aligner needs. (Contrast English karaoke, where G2P is genuinely ambiguous.)
- **Arabic is phonemically shallow**; letter↔phone is far closer to 1:1 than
  English, which is also what makes letter-level output plausible *if* the
  finer granularity is ever wanted.
- A pronunciation lexicon can be derived from the text itself rather than
  sourced externally.

## 4. What is still genuinely risky (do not hand-wave these)

1. **Vocal separation quality.** Demucs/MDX on a dense maqam-rock mix
   (distorted guitars, drums, reverb) can leave bleed and artefacts; the aligner
   inherits every defect. *This is probably the single biggest risk.*
2. **No singing-trained Arabic acoustic model.** MFA/Charsiu models are
   speech-trained; singing (sustained vowels, vibrato, consonants softened)
   is out of distribution. A custom model could be trained from our own
   audio+text pairs (the v3 set; and the ~9.5k fully-diacritized Quran clips),
   but that is a larger project than "run an aligner".
3. **Melisma / madd.** One syllable may span many frames/notes; forced aligners
   handle this by design, but the *interpolation* between anchors is where
   error concentrates.
4. **Granularity.** Line/phrase-level is far more forgiving than word-level,
   which is far more forgiving than letter-level. The quality bar moves with
   the ambition.

## 5. Track A phases

- **A0 — one-track trial (decisive, cheap). DONE.** Took one existing rendered
  WAV, separated vocals, ran a *forced* aligner with the known diacritized
  lyrics, and inspected the result. A0a (Suno proxy) passed; B2 repeated it on
  **8 real YuE2 renders** with 6 models, eliminating MMS-1b (silent-intro
  violations 8/8), the Egyptian model (collapsed) and the phoneme model
  (inverted energy). Results: [../results/A0-FINDINGS.md](../results/A0-FINDINGS.md),
  [../results/BENCH-FINDINGS.md](../results/BENCH-FINDINGS.md).
- **A1 — harden. DONE.** Batch over a run directory, QA gates, line-level
  WebVTT/SRT + word JSON, player attached, run report. `scripts/a1_align_run.py`;
  protocol [A1-RUNBOOK.md](A1-RUNBOOK.md).
- **A2 — only if needed.** Improve the lexicon/acoustic model; or train a
  domain (singing) aligner from our own audio+text pairs. Still open, and still
  optional: the failure mode it would fix is accuracy at melisma, not method.
- **Gold anchors — the remaining honest gap.** The base/Quran tie was broken on
  cost, not accuracy. A small hand-checked anchor set is the only way to settle
  it.

## 6. Dual value

Even if Track A's quality is mediocre, a coarse alignment is a **reference** to
validate Track B against — the native trace needs *something* to be checked
against. A0 therefore has value beyond "does Route A work".

## 7. Not this

This document does not reopen **whisper-based** alignment, and does not claim
A0 will succeed. It claims only that the prior failure tested one family, and
the other family is untested here.
