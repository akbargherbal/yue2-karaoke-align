# BENCH-PLAN.md — pick the best acoustic model for our songs

Goal: choose the acoustic model (and settings) that gives the **best word-level
alignment on our audio for the least hardware**, using evidence from our own
songs rather than model cards.

Run on Colab (GPU) — but the harness also runs on CPU. Personal/non-commercial
use is assumed, so **licence is out of scope** and non-commercial models (MMS,
Voxtral, Cohere) are fair game.

## 0. The one hard constraint

Only **CTC** models can be forced-aligned with our mechanism
(`torchaudio.functional.forced_align`). Everything else needs a different
aligner and must be measured separately:

| Family | Examples | Usable now? |
|---|---|---|
| Wav2Vec2-CTC | jonatasgrosman, elgeish, rabah2026-quran, MMS-1b, phoneme-asr | **yes** |
| Hubert / WavLM-CTC | (if an Arabic fine-tune exists) | yes |
| Whisper (enc-dec) | whisper-large-v3-turbo, Arabic dialectal fine-tunes | no — DTW/WhisperX path |
| Parakeet / TDT / RNNT | nvidia/parakeet-tdt-0.6b-v3, Nawah-60M | no — NeMo aligner |
| Zipformer / k2 | Quran-Lab/zipformer_p-arabic-v3 | no — icefall toolchain |
| LLM-ASR (enc-dec) | Qwen3-ASR, Voxtral, Cohere Transcribe | no — no CTC |

So Phase B2 measures the CTC set; Phase B4 optionally measures one non-CTC
family through its own aligner, to answer "are we leaving quality on the table?"

## 1. How we measure **accuracy** without ground truth

No human-labelled timings exist for our songs, so accuracy has three layers,
cheapest first:

1. **Sanity** (`a0_qa`): monotone, no overlaps, coverage ≈ 1, plausible word
   durations, gaps > 2 s landing on line boundaries.
2. **Energy tracking** (`a0_compare`): word spans vs the vocal-stem envelope.
   The decisive test is the *silent intro* — a model that invents positions will
   smear words into it. Reported as on/off ratio.
3. **Cross-model consensus**: two independently-trained models that agree are
   probably both right. Per song, median `|dstart|` between every model pair;
   a model's score is its mean distance to the others. **This is the primary
   ranking metric.**
4. **Gold anchors** (optional, `--gold`): 15–25 hand-checked word starts on one
   song turn the proxy into a real MAE. Cheap and worth it before committing.

> Consensus is a proxy, not truth — two models can share a bias. Rank with it,
> then confirm the winner by ear or with gold anchors.

## 2. How we measure **efficiency**

Per model, per song: wall time, seconds per audio-minute, parameter count, and
**peak VRAM** on GPU (exact, reset per model) or **model RSS** on CPU. Separation
(Demucs) is measured once per song and cached, so it never contaminates the
per-model numbers.

Quality and cost are **two axes**. A model that ties on quality but uses 60M
params beats one using 1B. Look for the Pareto front, not a single number.

## 3. Model shortlist (`bench/models.txt`)

### Core CTC set — drops straight into the harness

| Model | Params | Notes |
|---|---|---|
| `jonatasgrosman/wav2vec2-large-xlsr-53-arabic` | 316M | baseline; Arabic script, full harakat in vocab |
| `elgeish/wav2vec2-large-xlsr-53-arabic` | 316M | **Buckwalter** input; lower reported WER |
| `rabah2026/wav2vec2-large-xlsr-53-arabic-quran-v_final` | ~316M | **Quran-trained** — recitation ≈ singing (madd, sustained vowels); best guess for our domain |
| `MostafaMaroof/wav2vec2-arabic-phoneme-asr` | ~316M | **phoneme-level** vocab — alignment-native |
| `facebook/mms-1b-all` | 965M | multilingual CTC; load with `target_lang="ara"` |
| `IbrahimAmin/egyptian-arabic-wav2vec2-xlsr-53` | ~316M | dialectal control (expect weaker on fusha) |

### Stretch / different mechanism

`cstr/wav2vec2-large-xlsr-53-arabic-GGUF` (GGUF — needs ggml);
`nvidia/parakeet-tdt-0.6b-v3` and `oddadmix/Nawah-ASR-60M-Max-Arabic` (TDT →
NeMo aligner; **60M params** is the efficiency play);
`Quran-Lab/zipformer_p-arabic-v3` (k2); `openai/whisper-large-v3-turbo`
(the ASR family that already failed — include once as a control);
`CohereLabs/cohere-transcribe-arabic-07-2026`, `Qwen/Qwen3-ASR-1.7B`,
`mistralai/Voxtral-Mini-4B-Realtime-Arabic` (trending enc-dec).

> The hypothesis to test: **Quran-trained models beat generic ones on our songs**,
> because recitation shares singing's sustained vowels, madd and full tashkeel.

## 4. Song set (`bench/songs.json`)

Eight tracks, two per maqam (Ajam / Hijaz / Kurd / Nahawand) — maqam changes
vocal delivery, so one song can mislead. `songs.json` is a list of dataset
basenames; `--data-dir` points at the folder holding `<name>.mp3` + `<name>.txt`.
Build your own set with `--auto-songs --data-dir <dir>`.

**Prefer real YuE2 renders** for the final decision (that is the target), using
the v3 dataset as the cheap proxy. Keep them in separate runs:
`--out results/bench_suno` vs `--out results/bench_yue2`.

## 5. Phases

| Phase | What | Cost | Gate |
|---|---|---|---|
| **B0** provision | venv + deps; stage songs | ~10 min | `--check` prints a vocab |
| **B1** smoke | `--limit-models 1 --limit-songs 1` | ~2 min | one row of the table |
| **B2** core sweep | 6 CTC models × 8 songs | ~1–2 h GPU | ranking table produced |
| **B3** decide | read ranking + Pareto; listen to top-2 | ~30 min | model chosen |
| **B4** stretch *(optional)* | 1 non-CTC family via its own aligner; gold anchors | ~2–4 h | quality ceiling confirmed |

## 6. Colab execution

```bash
git clone https://github.com/akbargherbal/yue2-karaoke-align && cd yue2-karaoke-align
pip install -q demucs transformers soundfile        # torch/torchaudio already on Colab

# stage songs: <name>.mp3 + <name>.txt pairs
#   (v3 dataset zip, or a folder of YuE2 renders + their prompt lyrics)
DATA=/content/songs

python scripts/bench.py --check 2>/dev/null || true      # sanity
python scripts/bench.py \
    --models bench/models.txt --songs bench/songs.json --data-dir "$DATA" \
    --out results/bench_yue2 --device cuda --sep-device cuda \
    --limit-models 1 --limit-songs 1                     # B1 smoke first
# then the real run: drop --limit-*
```

Notes:
- Stems are cached under `--stems-dir`, so re-running never re-pays Demucs.
- If VRAM is tight, run the big model (MMS-1b) in its own pass with
  `--limit-models`.
- Partial results survive: `bench_raw.jsonl` is appended per run, and the
  reports are rebuilt from it at the end.
- **Pinned revisions:** `bench/models.lock` maps each id to a commit SHA and the
  harness loads that SHA, so a run is reproducible even if a repo's `main` moves.
- **Self-hosted mirrors:** the two starred entries in `bench/models.txt` are
  byte-identical mirrors under our own namespace (`akbargherbal/*`), so the sweep
  does not depend on `jonatasgrosman`/`rabah2026` staying online. To mirror a new
  model: `hf repos duplicate <owner>/<name>`.

## 7. Decision rule

1. If any model has **gold MAE** on the anchor set, rank by that.
2. Otherwise rank by **consensus deviation**; discard any model with
   `monotone < 100%` or coverage < 0.9.
3. Among the top cluster (within ~0.05 s of the best), pick the **cheapest**:
   fewer params, less VRAM, fewer seconds/audio-minute.
4. Tie-break on **integration cost**: Arabic-script input beats Buckwalter;
   one dependency beats three.

Expected outcome: several models cluster within noise; the tie-break decides.
If they *don't* cluster, the disagreement localises the hard passages — which is
itself the useful result.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Consensus rewards shared bias | gold anchors (B4); listen to the top-2 |
| Songs too homogeneous | 2 per maqam; add YuE2 renders for the final call |
| A model fails to load / OOM | harness records the error and continues |
| Colab session loss | `bench_raw.jsonl` incremental; back up `results/` |
| Separation dominates runtime | cached stems; `--sep-device cuda` |
| Whisper-family results not comparable | measured separately, never merged into the CTC table |
