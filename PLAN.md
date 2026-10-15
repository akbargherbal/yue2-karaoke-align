# PLAN.md — phased plan

Goal: produce **word/phrase-level timestamps for the known lyrics** of a YuE2
song, good enough to drive a browser highlight player, **without** modifying the
sibling project.

Non-goals: changing training, datasets, `INFERENCE/run_one.sh`, `generate.py`,
GCS, or the production inference path. Nothing here is wired into production
unless Phase 6 is explicitly opened.

## The two solution families

| | A — post-hoc forced alignment | B — native extraction (this spike) |
|---|---|---|
| Idea | Separate the vocal stem, force-align the known lyrics to it (MFA / stable-ts / WhisperX) | Tap the AR stream during generation; the lyric→frame attention *is* the alignment |
| Cost | ~1 day | ~3–5 days (demo) … ~1–2 weeks (production-faithful) |
| Certainty | Near-certain | Unproven; one hard gate (monotonicity) and one parity gate |
| Fidelity to our tracks | Exact (runs on our WAVs) | Depends on runtime parity (Phase 3) |
| Extra deps | Demucs-like separator + aligner | The YuE2 Python reference + hooks, or a C++ patch |
| Status | **Out of scope by decision** — the documented fallback | The thing being investigated |

The honest headline: **the UX you want is cheap through A and expensive through
B.** B is pursued because it is native, not because it is easier — and because
the Phase 2 readout also answers a separate question (where the model's
pronunciation knowledge is applied). Phase 0–2 is therefore worth running even
if the feature is later shelved.

## Hardware

- **CPU:** Phase 0, Phase 4, Phase 5 (and the probe training in 2b).
- **GPU:** Phase 1, 2, 3. This is unavoidable — the readout *is* a forward pass
  over the model.
- **Rule (inherited):** one shared/rented GPU. Before any GPU work run
  `nvidia-smi`; never overlap with a training run; this is out-of-project work,
  so it runs in its own session. See the sibling `AGENTS.md` §8.

---

## Phase 0 — Tokenizer reality check  *(CPU, ~30 min)*

**Question:** does the model's tokenizer preserve the harakat as distinct
tokens, or does it merge/drop them?

**Why it's first:** it decides whether the "handle" exists. If `سِ` and `سٍ`
tokenize identically, then "diacritic-level knowledge" is really the model
*reconstructing* the vowel from the undiacritized skeleton — which changes both
the mechanism and how fine an alignment is even possible.

**Steps**
1. `pip install tiktoken` (and, if needed, `transformers`).
2. Get the model's tokenizer: `sidecars/yue2-qwen.tiktoken` from the sibling's
   model dir (`/content/audiocpp_inference/models/Yue2-3B-GGUF/sidecars/`, or
   `hf download audio-cpp/Yue2-3B-GGUF`).
3. `python scripts/tokenizer_check.py --tokenizer <path>`
   (smoke-test the script itself with `--encoding cl100k_base`).

**Artifact / proof:** a printed table of token IDs + decoded pieces for the
diacritic pairs, checked into `results/`.

**Gate:** harakat survive ⇒ continue. Harakat dropped ⇒ document it, and treat
every later "alignment" as word-level at best.

---

## Phase 1 — Stand up the reference and prove we can capture  *(GPU, ~½–1 day)*

**Goal:** run *one* generation through the **Python reference**
(`m-a-p/YuE` / the `yue2` package) and prove attention (or hidden states) can be
captured at all.

**Why the reference:** `audio.cpp` (our production runtime) exposes no attention
dump. The reference is where hooking/instrumentation is tractable.

**Steps**
1. Install the reference in an isolated venv; download `m-a-p/YuE2-3B` + VAE.
2. Reproduce a minimal `cot=off` generation (short lyrics, small cap).
3. Hook the AR stream and dump, for a few generated semantic positions, the
   attention over the lyric prefix (or the hidden states, which is the cheaper,
   more portable capture).

**Artifact / proof:** a saved tensor + a short note on what was captured and
how.

**Gate:** capture is possible ⇒ continue. If the reference exposes nothing and
cannot be patched within a day ⇒ **stop or switch to a C++ patch (Phase 3)
early.**

**Big unknown already visible:** the reference may not load our *converted,
unfused* LoRA the way `audio.cpp` does. That tension is deferred to Phase 3;
Phase 1–2 deliberately work on the **base model** to isolate the harder
question first.

---

## Phase 2 — Monotonicity test  *(GPU, ~½ day)*  ← **KILL GATE**

**Question:** is the AR attention from generated semantic frames back to lyric
tokens *monotonic* (a readable left-to-right trace), or diffuse?

**Steps**
1. Extract the semantic→lyric attention for the short Phase 1 run.
2. Score it: fraction of frames whose argmax lyric position is ≥ the previous
   frame's (monotone fraction), plus a diagonalness/DTW-cost measure.
3. Visualise: frame index (x) vs lyric token index (y), one point per frame.

**Decision**
- Clear monotone band ⇒ continue to Phase 3.
- Diffuse / non-monotone ⇒ the attention route fails. Options:
  - **Phase 2b (contingency, GPU training):** train a small alignment probe
    (linear or CTC) on the captured hidden states, leveraging the fact that the
    semantic tokenizer was itself CTC-trained against lyrics. This is where the
    work stops being cheap.
  - Or stop, and bank the negative result.

**Artifact / proof:** the alignment plot + the two numbers, checked in.

---

## Phase 3 — Parity decision  *(GPU, ~1–4 days)* ← **SCOPE GATE**

**Question:** do the timings have to be for *our* LoRA renders, or is a
base-model render acceptable as a proof?

- **Accept base-model scope** ⇒ done instrumenting; go to Phase 4. (Cheap.)
- **Require our renders** ⇒ the alignment must come from the runtime that
  produced them (`audio.cpp`), which means patching C++/CUDA to dump attention
  and rebuilding for the target GPU arch (~21 min cold build per the sibling's
  `docs/audiocpp_gpu_arch_builds.md`). This is the real cost and the real risk.

**Artifact / proof:** a decision, recorded here.

---

## Phase 4 — Trace → timestamps  *(CPU, ~½ day)*

**Steps**
1. Collapse the (possibly soft) frame→token path into per-token frame ranges.
2. Frames → seconds at **25 fps**.
3. Map tokenizer tokens back onto the user-facing lyric string (tiktoken
   offsets), then merge sub-word tokens into words/phrases.
4. Emit `WebVTT` + `SRT` + a small JSON.

**Artifact / proof:** a subtitle file that plays in sync.

---

## Phase 5 — Browser player  *(CPU, ~½ day; parallelisable)*

A single HTML page: audio element + lyrics with `<span>`s, a `timeupdate`
handler that toggles a highlight class. Independent of alignment — build it now
against hand-written timings to de-risk the fun part.

---

## Phase 6 — Integration  *(optional, do not start without a decision)*

An opt-in producer that consumes the sibling's outputs (`out/<run>/*.wav` +
`prompts/*_lyrics.txt`) and writes a sidecar next to them. It must **read only**,
and must not alter `run_one.sh` / `generate.py`.

---

## Effort summary

| Path | Effort | Certainty |
|---|---|---|
| A — post-hoc | ~1 day | high (out of scope by decision) |
| B — native, base-model demo | ~3–5 days | medium |
| B — native, our-LoRA faithful | ~1–2 weeks | low (C++/CUDA risk) |
| Player only | ~½ day | high |

**Verdict:** doable as a demo; a headache as a feature. The P0–P2 slice is the
cheap, high-information part (and has dual value: it also reveals *where* the
pronunciation knowledge is applied). Phases 3+ are where it becomes a project.

## Risk register

| Risk | Where | Mitigation |
|---|---|---|
| Harakat not preserved by the tokenizer | P0 | Gate; reframes the question, doesn't block the plan |
| Reference won't load our converted LoRA | P1/P3 | Deferred; base-model scope, or C++ patch |
| Attention is diffuse, not monotone | P2 | Kill gate → probe (2b) or stop |
| C++/CUDA patch + arch build cost | P3 | Scope decision; only if faithful timings are required |
| The two runtimes don't reproduce each other | P3 | Accepted limitation, documented |
| Grabbing the GPU while training runs | any GPU phase | `nvidia-smi` first; own session |
