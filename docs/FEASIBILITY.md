# FEASIBILITY.md — doable or headache?

A grounded assessment. Claims are marked **[verified]** (read in source/docs) or
**[assumed]** (plausible, untested). Sources are the sibling repo
(`maqamrock-yue2-lora-finetuning`) and the YuE2 paper (§ refs) unless noted.

## 1. What the feature needs

For each of the **known** lyrics, a time range → highlight in a player. That is
a monotone map from lyric tokens to audio time. Nothing more.

## 2. What the model gives us, and what it doesn't

YuE2 generates in stages: AR (symbolic score + 25 Hz semantic tokens) → NAR
(acoustic flow) → VAE (48 kHz stereo).

- The AR stream is conditioned on the style + diacritized lyric tokens and emits
  one semantic ID every 40 ms at **25 Hz / 375 bit/s**. **[verified]**
  (paper §3.3; sibling `DECISIONS.md:64`).
- That stream is the informational bottleneck: the phonemic plan must be written
  into it for the NAR to render it. **[verified]** (architecture) /
  **[assumed]** (that nothing is additionally carried only by the NAR's own text
  attention — the paper's mask lets the NAR attend to the text too, eq. 4).
- The model distinguishes harakat (e.g. `سِ` vs `سٍ`), so a per-syllable
  lyric→frame correspondence **causally exists**. **[assumed from the reported
  pronunciation quality]** — and it is exactly what Phase 0/2 will test.
- **No alignment is ever emitted.** The runtime exports no attention; the
  `yue2` skill states the model "exposes no ... phoneme-alignment ... argument".
  **[verified]** So the alignment is *never materialized*, not *deleted*.

## 3. Why "it must keep a hash map that gets discarded" is close but not right

There is no table. Nothing maps `سٍ` → "nunation" as a lookup. The mapping is a
computation: token → vector → adapted weights → a 32768-way distribution per
frame. What exists at runtime and then vanishes:

- the **KV cache** (the model's in-context representation of each lyric token),
- the **attention matrices** (which frame attends to which token) — computed per
  step, never written.

So the correction is: the alignment is never *produced*, hence never *discarded*.
It is recoverable only by re-running the computation with a tap on it.

Two useful consequences:

- **It is per-track, but cheap to redo.** The runtime is deterministic per
  request + seed **[verified]** (audio.cpp doc), so an already-rendered track can
  be *replayed* through an instrumented run to recover that track's timing.
- **It is the same readout as the pronunciation question.** Where the diacritic
  knowledge is applied, frame by frame, *is* the alignment trace.

## 4. The two routes, and why B is the expensive one

**Route A — post-hoc forced alignment.** Separate vocals, align known lyrics.
Bypasses the model entirely; robust because the text is known. ~1 day,
near-certain. **Out of scope by decision** — kept only as the fallback.

**Route B — native extraction.** Requires running the model with instrumentation.
The costs stack in this order:

1. **Capture** — the production runtime exposes nothing; you need the Python
   reference (tractable to hook) or a C++ patch of `audio.cpp` (faithful but
   heavy). **[verified]** that no dump exists; **[assumed]** that the reference
   is hookable.
2. **Monotonicity** — unproven. If attention is diffuse, the cheap route dies
   and you fall back to training a probe. **[assumed]**
3. **Parity** — the reference and `audio.cpp` are different runtimes; the
   reference may not load our *converted, unfused* LoRA. Getting timings for
   *our* tracks may mean patching and rebuilding the C++ runtime. **[assumed,
   but the largest single risk]**

## 5. Verdict

| Question | Answer |
|---|---|
| Is the UX achievable today? | **Yes** — via Route A, ~1 day. |
| Is it achievable natively? | **Yes, as a demo** — base-model render, ~3–5 days. |
| Faithful to our LoRA tracks? | **Headache** — ~1–2 weeks, real C++/CUDA risk. |
| Is the player hard? | **No** — ~½ day, and independent of the above. |
| Is it worth starting? | **The P0–P2 slice is** — cheap, high-information, dual-value. P3+ is a project, not a feature. |

## 6. How this could be falsified cheaply

- **P0:** if harakat don't survive tokenization, the handle is coarser than
  assumed — decide then whether to continue.
- **P2:** if the attention trace is not monotone on the base model, the native
  route fails before any parity work — the cheapest possible kill.
