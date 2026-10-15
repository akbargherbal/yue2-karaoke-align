# ROUTE-A.md — post-hoc alignment, done properly

Status: **reopened as Track A**, gated by a one-track trial (A0). Route A has
already failed once in this project's experience; this document is why that
failure is not conclusive, and what would make it conclusive.

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

- **A0 — one-track trial (decisive, cheap).** Take **one existing rendered
  WAV** from the sibling project. Separate vocals, run a *forced* aligner with
  the known diacritized lyrics, and inspect the result (listen at the anchors,
  look at the trace). No new generation.
  **Gate:** if line boundaries land convincingly, Route A is viable at
  line/phrase granularity — ship it. If the anchors are mush, Route A is
  probably dead for this corpus and the effort pivots to Track B.
- **A1 — harden.** If A0 passes: batch it, handle failure cases, emit
  WebVTT/SRT/JSON, attach to the player (Phase 5).
- **A2 — only if needed.** Improve the lexicon/acoustic model; or train a
  domain (singing) aligner from our own audio+text pairs.

## 6. Dual value

Even if Track A's quality is mediocre, a coarse alignment is a **reference** to
validate Track B against — the native trace needs *something* to be checked
against. A0 therefore has value beyond "does Route A work".

## 7. Not this

This document does not reopen **whisper-based** alignment, and does not claim
A0 will succeed. It claims only that the prior failure tested one family, and
the other family is untested here.
