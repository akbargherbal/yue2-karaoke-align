#!/usr/bin/env python3
"""Phase 0 — does the YuE2 tokenizer preserve Arabic harakat?

One question, one answer: do diacritic-marked forms tokenize *differently* from
their undiacritized skeleton? If they do not, then "diacritic-level" behaviour
is the model reconstructing the vowel from context, not reading the mark — which
changes both the mechanism and how fine an alignment can ever be.

Usage
-----
    # The real check (needs the model's tokenizer, from the sibling project):
    python tokenizer_check.py --tokenizer /path/to/yue2-qwen.tiktoken

    # Smoke-test this script itself, with a built-in tiktoken encoding
    # (this is NOT the model's tokenizer and says nothing about YuE2):
    python tokenizer_check.py --encoding cl100k_base

The model's tokenizer is a tiktoken BPE file (`sidecars/yue2-qwen.tiktoken`,
staged from `audio-cpp/Yue2-3B-GGUF`). Its tokenization regex (`pat_str`) and
special tokens live in the model's sidecar config; pass them with `--pat-str`
and `--specials-file` if the defaults below don't reproduce the model's output.

Exit code 0 always (this is a measurement, not a test); the summary line states
whether each pair is distinct.
"""
from __future__ import annotations

import argparse
import json
import sys

# Qwen2-family tokenizer regex (the family YuE2's text encoder derives from).
# If the model's actual pattern differs, read it from the sidecar config and
# pass it with --pat-str.
QWEN2_PAT_STR = (
    r"(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}"
    r"| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"
)

# (label, a, b) — "distinct?" asks whether a and b tokenize to different ids.
PAIRS: list[tuple[str, str, str]] = [
    ("kasra vs kasratan",            "سِ", "سٍ"),
    ("kasra vs damma",               "سِ", "سُ"),
    ("fatha vs kasra",               "سَ", "سِ"),
    ("plain vs shadda",              "س",  "سّ"),
    ("undiacritized vs diacritized", "كتاب", "كِتَاب"),
    ("undiacritized vs diacritized", "الله", "اللّٰه"),
]

# Standalone forms worth eyeballing (not pair comparisons).
SINGLES: list[tuple[str, str]] = [
    ("seen + kasra",     "سِ"),
    ("seen + kasratan",  "سٍ"),
    ("seen (bare)",      "س"),
]


class Tokenizer:
    """Minimal wrapper so both a .tiktoken file and a built-in encoding fit."""

    def __init__(self, name: str, enc):
        self.name = name
        self._enc = enc

    def ids(self, text: str) -> list[int]:
        return self._enc.encode(text)

    def pieces(self, text: str) -> list[str]:
        out: list[str] = []
        for i in self.ids(text):
            b = self._enc.decode_single_token_bytes(i)
            try:
                out.append(b.decode("utf-8"))
            except UnicodeDecodeError:
                out.append("b'" + b.hex(" ") + "'")  # partial UTF-8 byte piece
        return out


def _load_tiktoken_file(path: str, pat_str: str, specials: dict):
    try:
        import tiktoken
        from tiktoken.load import load_tiktoken_bpe
    except ImportError:
        sys.exit("tiktoken is not installed — run: pip install tiktoken")
    try:
        ranks = load_tiktoken_bpe(path)
    except Exception as e:  # noqa: BLE001 - surface whatever the loader says
        sys.exit(f"could not load tiktoken BPE file {path!r}: {e}")
    try:
        enc = tiktoken.Encoding(
            name="yue2-qwen",
            pat_str=pat_str,
            mergeable_ranks=ranks,
            special_tokens=specials,
        )
    except Exception as e:  # noqa: BLE001
        sys.exit(
            f"could not build an Encoding from {path!r}: {e}\n"
            "Hint: the pattern/special tokens may differ — pass --pat-str / "
            "--specials-file from the model's sidecar config."
        )
    return Tokenizer(path, enc), enc


def _load_builtin(name: str):
    try:
        import tiktoken
    except ImportError:
        sys.exit("tiktoken is not installed — run: pip install tiktoken")
    enc = tiktoken.get_encoding(name)
    return Tokenizer(f"builtin:{name}", enc), enc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--tokenizer", help="path to a .tiktoken BPE file (the model's)")
    src.add_argument("--encoding", help="built-in tiktoken encoding name (smoke test only)")
    ap.add_argument("--pat-str", default=QWEN2_PAT_STR, help="tokenization regex")
    ap.add_argument("--specials-file", help="JSON file: {'<token>': id, ...}")
    args = ap.parse_args(argv)

    specials: dict = {}
    if args.specials_file:
        with open(args.specials_file, encoding="utf-8") as fh:
            specials = json.load(fh)

    if args.tokenizer:
        tok, enc = _load_tiktoken_file(args.tokenizer, args.pat_str, specials)
    else:
        print("[note] using a BUILT-IN encoding — this is a script smoke test,")
        print("[note] not the YuE2 tokenizer, and proves nothing about the model.\n")
        tok, enc = _load_builtin(args.encoding)

    print(f"tokenizer: {tok.name}  (vocab {getattr(enc, 'n_vocab', '?')})\n")

    print("== singles ==")
    for label, s in SINGLES:
        ids = tok.ids(s)
        print(f"  {label:18} {s!r:12} ids={ids} pieces={tok.pieces(s)}")

    print("\n== pairs (do the two forms differ?) ==")
    distinct = 0
    for label, a, b in PAIRS:
        ia, ib = tok.ids(a), tok.ids(b)
        same = ia == ib
        distinct += (not same)
        verdict = "same ids (NOT distinct)" if same else "distinct"
        print(f"  {label:30} {a!r} {ia}  vs  {b!r} {ib}  -> {verdict}")

    print(f"\nsummary: {distinct}/{len(PAIRS)} pairs tokenize distinctly")
    if args.tokenizer:
        if distinct == 0:
            print("=> harakat appear to be STRIPPED/MERGED: the diacritic is not "
                  "reaching the model as a distinct input. Reassess the mechanism.")
        else:
            print("=> at least some harakat survive as distinct tokens: the "
                  "diacritic reaches the model as a handle. Continue to Phase 1.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
