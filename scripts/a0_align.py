#!/usr/bin/env python3
"""A0 — forced alignment of *known* lyrics to a vocal stem (no ASR).

This is the "forced" family, not the WhisperX/ASR family: the transcript is
given, the search is constrained to it, so the output can only mis-*time* the
lyrics — it cannot invent words.

Method: a wav2vec2 CTC acoustic model + `torchaudio.functional.forced_align`
(Viterbi over the known target), which is the standard dictionary-free way to
do this. Requires: torch, torchaudio, transformers.

Usage
-----
    # inspect what the model can represent (no audio needed):
    python scripts/a0_align.py --check --model <hf-id>

    # align:
    python scripts/a0_align.py \
        --audio separated/htdemucs/<name>/vocals.wav \
        --text  caption.txt \
        --out   results/<name>.words.json \
        --vtt   results/<name>.vtt

`--text` may be a caption file (the lyric block is extracted) or a plain
lyrics/words file.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_MODEL = "jonatasgrosman/wav2vec2-large-xlsr-53-arabic"
# Arabic harakat + tatweel — combining marks a speech-trained vocab usually lacks.
DIACRITICS = re.compile(
    "[\u064b-\u065f\u0670\u06d6-\u06dc\u06df-\u06e8\u06ea-\u06ed\u0640]"
)


def strip_diacritics(s: str) -> str:
    return DIACRITICS.sub("", s)


# Some Arabic ASR checkpoints (e.g. elgeish/*) are trained on Buckwalter
# *transliteration*, not Arabic script, so their vocab has no Arabic letters and
# raw lyrics match nothing. Map to Buckwalter when the script's own chars fail.
BUCKWALTER = {
    "\u0621": "'", "\u0622": "|", "\u0623": ">", "\u0624": "&", "\u0625": "<",
    "\u0626": "}", "\u0627": "A", "\u0628": "b", "\u0629": "p", "\u062a": "t",
    "\u062b": "v", "\u062c": "j", "\u062d": "H", "\u062e": "x", "\u062f": "d",
    "\u0630": "*", "\u0631": "r", "\u0632": "z", "\u0633": "s", "\u0634": "$",
    "\u0635": "S", "\u0636": "D", "\u0637": "T", "\u0638": "Z", "\u0639": "E",
    "\u063a": "g", "\u0640": "_", "\u0641": "f", "\u0642": "q", "\u0643": "k",
    "\u0644": "l", "\u0645": "m", "\u0646": "n", "\u0647": "h", "\u0648": "w",
    "\u0649": "Y", "\u064a": "y", "\u064b": "F", "\u064c": "N", "\u064d": "K",
    "\u064e": "a", "\u064f": "u", "\u0650": "i", "\u0651": "~", "\u0652": "o",
    "\u0670": "`", "\u0671": "{",
}


def to_buckwalter(s: str) -> str:
    return "".join(BUCKWALTER.get(ch, ch) for ch in s)


# --- text ------------------------------------------------------------------

def load_words(path: Path) -> list[str]:
    """Words to align, in sung order. Caption files are narrowed to lyrics."""
    text = path.read_text(encoding="utf-8")
    if "[Lyrics]" in text:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from a0_lyrics import lyric_lines, words as split_words  # noqa: E402
        return split_words(lyric_lines(text))
    out: list[str] = []
    for ln in text.splitlines():
        out.extend(ln.split())
    return [w for w in out if w]


# --- model -----------------------------------------------------------------

def load_model(model_id: str, device: str):
    import torch
    from transformers import AutoModelForCTC, AutoProcessor

    proc = AutoProcessor.from_pretrained(model_id)
    model = AutoModelForCTC.from_pretrained(model_id).to(device).eval()
    return proc, model, torch


def vocab_info(proc) -> dict:
    tok = proc.tokenizer
    vocab = tok.get_vocab()
    chars = {k: v for k, v in vocab.items() if len(k) == 1}
    blank = getattr(tok, "pad_token_id", None)
    if blank is None:
        blank = vocab.get("<pad>")
    sep = "|" if "|" in vocab else (" " if " " in vocab else None)
    has_diacritics = sum(1 for ch in DIACRITICS.pattern if ch in vocab)
    return {
        "vocab_size": len(vocab),
        "blank_id": blank,
        "word_separator": sep,
        "single_char_tokens": len(chars),
        "diacritic_chars_in_vocab": has_diacritics,
    }


# --- alignment -------------------------------------------------------------

def _keep_in_vocab(word: str, vocab: dict, strip_diac: bool, transform=None) -> str:
    if strip_diac:
        word = strip_diacritics(word)
    if transform is not None:
        word = transform(word)
    return "".join(ch for ch in word if ch in vocab)


def load_audio(audio_path) -> "torch.Tensor":
    """Read any audio file as 16 kHz mono float32."""
    import soundfile as sf
    import torch
    import torchaudio

    wav, sr = sf.read(str(audio_path), always_2d=True)
    wav = wav.mean(axis=1).astype("float32")
    if sr != 16000:
        wav = torchaudio.functional.resample(torch.from_numpy(wav), sr, 16000).numpy()
    return torch.from_numpy(wav)


def align_waveform(proc, model, waveform, words: list[str], strip_diac: bool | None,
                   model_id: str = "", audio_label: str = "", device: str = "cpu") -> dict:
    """Align already-loaded 16 kHz mono audio.

    Split out from `align()` so a batch harness can load each acoustic model
    **once** and reuse it across many songs.
    """
    import torch
    import torchaudio

    tok = proc.tokenizer
    vocab = tok.get_vocab()
    info = vocab_info(proc)
    sep = info["word_separator"]
    if sep is None:
        sys.exit(f"model vocab has no word separator ('|' or ' '): {model_id}")
    # The word delimiter is itself a *special* token in these tokenizers, so a
    # naive "drop all_special_ids" filter deletes every word boundary and the
    # whole song collapses to one word. Never strip it.
    sep_id = tok.convert_tokens_to_ids(sep)
    if strip_diac is None:
        strip_diac = info["diacritic_chars_in_vocab"] == 0

    def attempt(transform) -> list[tuple[str, str]]:
        kept = [_keep_in_vocab(w, vocab, strip_diac, transform) for w in words]
        return [(o, k) for o, k in zip(words, kept) if k]

    pairs = attempt(None)
    translit = "none"
    if len(pairs) < max(1, len(words) // 2):
        alt = attempt(to_buckwalter)
        if len(alt) > len(pairs):
            pairs, translit = alt, "buckwalter"
    if not pairs:
        sys.exit("no words survive the model vocab — wrong model for this text?")
    if translit != "none":
        print(f"[a0] model vocab needs {translit} transliteration "
              f"({len(pairs)}/{len(words)} words matched)", file=sys.stderr)
    target_text = sep.join(k for _, k in pairs)

    inputs = proc(waveform, sampling_rate=16000, return_tensors="pt")
    with torch.no_grad():
        logits = model(inputs.input_values.to(device)).logits
    log_probs = torch.log_softmax(logits, dim=-1).cpu()

    enc = tok(target_text)
    specials = set(getattr(tok, "all_special_ids", []) or []) - {sep_id}
    ids = [i for i in enc.input_ids if i not in specials]
    if not ids:
        sys.exit("empty target after tokenization")
    target = torch.tensor(ids, dtype=torch.int64)

    aligned, scores = torchaudio.functional.forced_align(
        log_probs, target.unsqueeze(0), blank=info["blank_id"]
    )
    spans = torchaudio.functional.merge_tokens(aligned[0], scores[0])

    ratio = waveform.shape[0] / log_probs.shape[1]
    def t(frame: int) -> float:
        return frame * ratio / 16000.0

    # walk spans; a separator span ends the current word
    out_words: list[dict] = []
    wi = 0
    cur_start = None
    cur_end = None

    def flush():
        nonlocal wi, cur_start, cur_end
        if wi < len(pairs) and cur_start is not None:
            orig = pairs[wi][0]
            out_words.append(
                {"i": wi, "word": orig, "start": round(cur_start, 3),
                 "end": round(cur_end, 3)}
            )
            wi += 1
        cur_start = cur_end = None

    for sp in spans:
        if sp.token == info["blank_id"]:
            continue
        if sp.token == sep_id:
            flush()
            continue
        if wi >= len(pairs):
            break
        if cur_start is None:
            cur_start = t(sp.start)
        cur_end = t(sp.end)
    flush()

    return {
        "audio": audio_label,
        "model": model_id,
        "device": device,
        "vocab": info,
        "stripped_diacritics": bool(strip_diac),
        "transliteration": translit,
        "n_words_in": len(words),
        "n_words_aligned": len(out_words),
        "duration_s": round(waveform.shape[0] / 16000.0, 2),
        "words": out_words,
    }


def align(audio_path: Path, words: list[str], model_id: str, device: str,
          strip_diac: bool | None) -> dict:
    """One-shot: load the model, load the audio, align."""
    proc, model, _ = load_model(model_id, device)
    waveform = load_audio(audio_path)
    return align_waveform(proc, model, waveform, words, strip_diac,
                          model_id, str(audio_path), device)


def to_vtt(rows: list[dict], path: Path) -> None:
    def ts(s: float) -> str:
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{int(h):02d}:{int(m):02d}:{sec:06.3f}"
    with path.open("w", encoding="utf-8") as fh:
        fh.write("WEBVTT\n\n")
        for r in rows:
            fh.write(f"{ts(r['start'])} --> {ts(r['end'])}\n{r['word']}\n\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--audio", help="vocal stem (wav/flac)")
    ap.add_argument("--text", help="caption file or lyrics/words file")
    ap.add_argument("--out", help="word-timings JSON (default: stdout)")
    ap.add_argument("--vtt", help="also write a WebVTT file")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--strip-diacritics", action=argparse.BooleanOptionalAction,
                    default=None, help="default: strip iff the model lacks harakat")
    ap.add_argument("--check", action="store_true",
                    help="load the model and print vocab info; no audio needed")
    args = ap.parse_args(argv)

    if args.check:
        proc, _model, _ = load_model(args.model, args.device)
        print(json.dumps({"model": args.model, **vocab_info(proc)},
                         ensure_ascii=False, indent=2))
        return 0

    if not (args.audio and args.text):
        ap.error("--audio and --text are required (or use --check)")

    words = load_words(Path(args.text))
    print(f"[a0] {len(words)} words from {args.text}", file=sys.stderr)
    result = align(Path(args.audio), words, args.model, args.device,
                   args.strip_diacritics)
    print(f"[a0] aligned {result['n_words_aligned']}/{result['n_words_in']} words "
          f"over {result['duration_s']}s", file=sys.stderr)

    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"[a0] wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(payload)
    if args.vtt:
        Path(args.vtt).parent.mkdir(parents=True, exist_ok=True)
        to_vtt(result["words"], Path(args.vtt))
        print(f"[a0] wrote {args.vtt}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
