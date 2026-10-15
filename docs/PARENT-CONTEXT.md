# PARENT-CONTEXT.md — relationship to the sibling project

This repo is a **standalone sibling** of
[`maqamrock-yue2-lora-finetuning`](../maqamrock-yue2-lora-finetuning) (the YuE2
LoRA training + inference project). It exists so the karaoke investigation
never risks that project.

## Scope boundary

**We consume (read-only):**
- rendered audio `out/<run>/*.wav`
- the conditioning text `out/<run>/prompts/<name>_{style,lyrics}.txt`
  (or the source lyric file)
- the model's tokenizer sidecar `sidecars/yue2-qwen.tiktoken` (Phase 0)

**We never touch:**
- training configs, datasets, or `/content/yue2_dataset`
- `INFERENCE/run_one.sh`, `INFERENCE/generate.py`, the production inference path
- GCS, backups, or continuity tooling
- the sibling's `agent_notes/`, docs, or git history

If a change to the sibling project ever seems required, **stop and raise it** —
it is out of scope here by construction.

## Environment

- **Local box** unless stated otherwise: no `/content`, so the sibling's
  Colab-only surfaces (GCS, `nvidia-smi`-gated scripts, `vm-continuity`) do not
  apply. Follow the sibling's `AGENTS.md` §2 for that distinction.
- GPU phases run on the same shared/rented GPU the sibling uses: run
  `nvidia-smi` first, never overlap with a training run, use a separate session
  (sibling `AGENTS.md` §8).

## Where the parent's authority lives

Read these in the sibling, don't duplicate them here:

| Topic | Sibling file |
|---|---|
| Operating contract (agent) | `AGENTS.md` |
| One authority row per topic | `SOURCE_OF_TRUTH.md` |
| Inference runbook | `docs/INFERENCE.md` |
| Key decisions (cot, LoRA scoping, AR length) | `DECISIONS.md` |
| Pronunciation LoRA analysis | `docs/FUTURE_PRONUNCIATION_LORA.md`, `docs/QURAN_PRON_REVIEW.md` |
| The YuE2 model's own CLI/options | audio.cpp `docs/models/yue2.md` (upstream) |

## Provenance of the claims in this repo

The evidence behind [FEASIBILITY.md](FEASIBILITY.md) was gathered from the
sibling's docs/source and the YuE2 paper (§3.3, eq. 4) on 2026-10-15. Anything
marked **[assumed]** there is a hypothesis this repo is meant to test — not a
finding to rely on.
