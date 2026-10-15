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

- **A0b** — repeat on a real YuE2 render (`out/<run>/<name>_<seed>.wav` from the
  sibling project). This is the actual answer; A0a only earned it.
- If A0b passes → A1 (batch, SRT/VTT emit, attach the player).
- If A0b fails but A0a passed, the difference is our audio, not the method —
  worth one more aligner family before abandoning Track A.
