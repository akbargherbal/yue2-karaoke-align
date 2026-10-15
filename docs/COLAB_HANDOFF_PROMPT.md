# COLAB_HANDOFF_PROMPT.md

Copy the block below into the agent session on Colab. It is self-contained: the
Colab session has no memory of the local one.

---

## Prompt (copy from here)

Context: we are benchmarking acoustic models for **word-level forced alignment**
of Arabic lyrics to sung audio — i.e. karaoke-style timings for songs generated
by our YuE2 LoRA. Side investigation; **personal/non-commercial use, so licence
is out of scope**. The mechanism is already proven on one track (A0a PASS).

Repo (public — clone it): `https://github.com/akbargherbal/yue2-karaoke-align`
**Read first:** `docs/BENCH-PLAN.md` (metrics, shortlist, decision rule) and
`results/A0-FINDINGS.md` (what we already know).

Environment: this Colab box, GPU. **Before any GPU work run `nvidia-smi`.** If an
ai-toolkit training run is active, do NOT start anything — stop and tell me.

Task, in order:

1. `git clone https://github.com/akbargherbal/yue2-karaoke-align && cd yue2-karaoke-align`
2. `bash scripts/colab_setup.sh` — installs demucs/transformers/soundfile and
   downloads the baseline aligner once.
3. **Stage songs.** I need a folder of `<name>.mp3` + `<name>.txt` pairs, where
   the `.txt` is the caption carrying the lyrics. Two options:
   - the sibling v3 dataset (Suno renders, 438 pairs; zip path is in the
     notebook's env — look for `GCP_DATASET_ZIP`), or
   - real YuE2 renders (`audiocpp_inference/out/<run>/*.wav`) + their lyrics
     from `out/<run>/prompts/*_lyrics.txt`.
   **Ask me which** if it is not obvious. Prefer ~8 tracks, 2 per maqam.
   For the final decision, real YuE2 renders beat the Suno proxy.
4. **B1 smoke** (one model, one song) — confirm the harness runs end to end:
   `python scripts/bench.py --models bench/models.txt --songs bench/songs.json --data-dir <DIR> --out results/bench --device cuda --sep-device cuda --limit-models 1 --limit-songs 1`
5. **B2 sweep** — all core models × the song set (drop `--limit-*`). This is
   long-running (~1–2 h): run it detached with a log, e.g.
   `nohup python scripts/bench.py ... > results/bench/bench.log 2>&1 &`
   It is resumable: vocal stems are cached and `bench_raw.jsonl` is written per
   model.
6. **Report back** and push `results/bench/` to the repo if you can authenticate:
   the model table, the ranking, the per-song difficulty table, any model that
   failed to load, and your recommendation.

Facts already established — do not re-derive these:

- **Only CTC models can be used** (`torchaudio.functional.forced_align`).
  Whisper, Parakeet/TDT, Voxtral, Qwen3-ASR and zipformer need *different*
  aligners; they are listed in `bench/models.txt` but excluded from the CTC
  table. Do not merge them into the same comparison.
- The word delimiter `|` is inside the tokenizer's `all_special_ids`. `a0_align`
  already handles this. **Never strip special tokens from the CTC target** — it
  deletes every word boundary and collapses the whole song to one word.
- There is **no ground truth**. Quality is judged by (a) cross-model consensus
  deviation and (b) energy tracking against the vocal stem. The decisive check:
  if the vocal stem is silent at the start, **no words may land there**.
- Separation is cached per song — never re-paid. On Colab pass `--sep-device cuda`.
- Run **one model at a time** (a memory ceiling, not a style choice).
- **Do not polish the tooling.** Fix bugs only, keep runs minimal, report.
  (This is why the local session ran long — don't repeat it.)

Report back: the markdown table, the ranking, per-song difficulty, load failures,
and which model you would pick and why.

## Prompt (to here)
