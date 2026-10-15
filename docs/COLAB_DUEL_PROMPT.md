# COLAB_DUEL_PROMPT.md

Copy the block below into the **active Colab agent session**. It is
self-contained — that session has no memory of the local one.

---

## Prompt (copy from here)

Context: we are picking a **word-level forced-alignment** model for Arabic
lyrics on our YuE2 renders (karaoke timings). The 8-song sweep is already done
(`results/BENCH-FINDINGS.md`): two models survived and they are **the same
lineage** — `...-arabic-quran-v_final` is a fine-tune of `...-arabic`. We now
self-host both as byte-identical mirrors under `akbargherbal/*`, revision-pinned
in `bench/models.lock`.

Repo: `https://github.com/akbargherbal/yue2-karaoke-align` (this box already has
it at `/content/yue2-karaoke-align`).

Task: run a **2-song head-to-head** of the two mirrors and return a verdict.
Keep it minimal — 2 models × 2 songs, nothing else. **This runtime is CPU-only**
(the terminal is `cpu_akbar`), so there is no `nvidia-smi` and no CUDA; run
everything on CPU.

1. Update the code:
   `cd /content/yue2-karaoke-align && git pull --ff-only`
2. Confirm the runtime is CPU (expected, not a problem): `python -c "import torch;
   print(torch.cuda.is_available())"` should print `False`. Do **not** pass any
   `cuda` flags.
3. Ensure deps (idempotent): `bash scripts/colab_setup.sh` (skips what is present).
4. Stage **2 songs**. Prefer the real YuE2 renders already used for the sweep
   (`data/yue2_songs`, files named `A_diction_mix_*.mp3` + `.txt`). Run exactly
   two:
   ```
   python scripts/bench.py --models bench/models_duel.txt --auto-songs \
       --data-dir data/yue2_songs --out results/duel \
       --device cpu --sep-device cpu --limit-songs 2
   ```
   (If `data/yue2_songs` is missing, tell me and I will re-stage it — do not
   substitute a different dataset silently.) Expect ~10–15 min total on CPU:
   Demucs ~2 min/song (cached after), each aligner ~28 s per audio-minute.
5. Decide:
   ```
   python scripts/duel_verdict.py --results results/duel/bench_results.json
   ```
   The script applies the decision rule and prints `VERDICT`.
6. Report back: the two-row table, the `VERDICT` block verbatim, and one sentence
   on whether you think the winner is real or a tie.

Decision rule the script uses (BENCH-PLAN §7, adapted to two models):
disqualify on any hard failure (load, ≤1 word/song collapse, non-monotone,
coverage < 0.9) or **any silent-intro violation** (word 0 inside the vocal
stem's leading silence); then energy tracking (`on/off`, >10% apart wins); then
agreement — a word-0 gap **≤ 40 ms** is an explicit **TIE** (both are below
listener perception), and the winner is then only the cheaper model.

Facts already established — do not re-derive:
- Both models are the **same lineage**, so with only two models "consensus" is
  meaningless. The script therefore relies on the physical checks, not consensus.
- The already-completed 8-song run shows them within **~0.02 s of word 0**
  everywhere. Expect a **tie on this 2-song duel too**; do not manufacture a
  winner. A tie is the correct, useful answer, and the cost tie-break picks the
  Quran mirror.
- Only a **gold set** (hand-checked word starts — a human listens) can break a
  real tie. That is the next step if I want one; not this run.
- Run one model at a time; stems are cached; keep everything on CPU
  (`--device cpu --sep-device cpu`) — this runtime has no GPU.

Do not polish tooling. Fix bugs only, keep the run to 2×2, report.

## Prompt (to here)
