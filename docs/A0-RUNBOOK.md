# A0-RUNBOOK.md — the one-track trial that decides Track A

A0 answers one question: **can a *forced* aligner turn a rendered song + its
known diacritized lyrics into usable word timings?**

It is cheap (CPU, no GPU contention), and it produces the reference alignment
that Track B needs for validation regardless of which route wins.

## Scope and honesty

- **A0a — Suno proxy (available now).** The only audio on hand is the v3
  training corpus — *Suno* renders (`../v3_arabmaqamrock_dataset/`), not YuE2
  output. Same lyrics, same genre, cleaner production. A0a is therefore a
  **generous screen**: if forced alignment fails here, it will fail on YuE2
  output too (decisive negative). Passing here is **necessary, not sufficient**.
- **A0b — a real YuE2 render (needs a WAV).** Re-run the same pipeline on one
  `out/<run>/<name>_<seed>.wav` from the sibling project. Only A0b is the real
  answer; A0a just decides whether A0b is worth running.

## Prerequisites (once)

```bash
cd yue2-karaoke-align
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install demucs transformers
```

Then check what the acoustic model can represent (no audio needed):

```bash
.venv/bin/python scripts/a0_align.py --check
```

Look for `word_separator` (should be `|`), `blank_id`, and
`diacritic_chars_in_vocab`. If harakat are absent, alignment strips them and
matches on the undiacritized skeleton — fine for **word** timing (which is all
we need), and noted in the output.

## The trial (one track)

```bash
SUBJECT=../v3_arabmaqamrock_dataset/ajam_Lamiyat_Alshanfara_16082026_004_bb17d099

# 1. lyrics, in sung order (no deps)
python3 scripts/a0_lyrics.py "$SUBJECT.txt" --out work/lyrics.txt

# 2. isolate vocals (CPU, several minutes)
scripts/a0_separate.sh "$SUBJECT.mp3" work/separated

# 3. forced alignment against the KNOWN lyrics
.venv/bin/python scripts/a0_align.py \
    --audio work/separated/htdemucs/$(basename "$SUBJECT")/vocals.wav \
    --text  "$SUBJECT.txt" \
    --out   results/a0a.words.json \
    --vtt   results/a0a.vtt
```

## How to judge it (don't just read the numbers)

1. **Anchors.** Open the vocal stem beside `a0a.vtt`. Do the first few words
   land where they are actually sung? Drift *grows* with time if the aligner is
   losing the path.
2. **Monotonic sanity.** `start` should increase across words; large jumps or
   overlaps (> a second) are where it broke.
3. **The melisma test.** Long `end-start` on a single word is expected (madd);
   but if a *whole phrase* collapses into one word's span, the aligner gave up.
4. **Separation bleed.** Compare against `no_vocals.wav`: if the "vocal" stem is
   mostly guitar, any alignment result is about the guitar, not the voice.

## Decision

- **Line/phrase boundaries land convincingly** ⇒ Track A is viable at word
  granularity; go to A1 (harden + emit VTT/SRT + player). Then run A0b.
- **Anchors are mush, or drift runs away** ⇒ Route A is likely dead for this
  corpus; record it and pivot to Track B (native extraction).
- **Ambiguous** ⇒ try the other aligner family (MFA with an Arabic dictionary,
  or Charsiu) before concluding; only one aligner has been tested.

## Record

Write the outcome (pass/fail/ambiguous + the two or three numbers that decided
it) into `results/A0-FINDINGS.md` and link it from `PLAN.md`.
