#!/usr/bin/env python3
"""A1 — turn a directory of rendered songs into karaoke assets.

This is the hardened, batch version of the A0 trial. One command consumes a
YuE2 run directory (`out/<run>/` with `<name>_<seed>.wav` and
`prompts/<name>_lyrics.txt`) or a staged data directory, and writes:

    <song>.words.json      word timings + QA + lyric lines (the source of truth)
    <song>.vtt             line-level WebVTT
    <song>.srt             line-level SRT
    <song>.player.html     self-contained word-highlighting player
    summary.json           machine-readable run summary
    A1-REPORT.md           human-readable summary (QA + links)
    index.html             one page linking every song

Alignment is **forced**: the lyrics are given, the search is constrained to
them. This is not ASR, so it cannot invent words — only mis-time them.

Usage
-----
    # a real YuE2 run (reads only; writes to results/a1/<run>/)
    python scripts/a1_align_run.py \
        --run-dir /path/to/out/20261014-093128_batch_02_formatted

    # a staged flat directory: <name>.wav/.mp3 + <name>.txt
    python scripts/a1_align_run.py --data-dir data/yue2_songs --auto-songs

    # rehearse without loading torch/Demucs
    python scripts/a1_align_run.py --run-dir ... --dry-run

The production model comes from `bench/models.txt` / `bench/models.lock`
(`akbargherbal/wav2vec2-large-xlsr-53-arabic`, pinned). Override with --model.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from a0_align import align_waveform, load_audio, load_model  # noqa: E402
from a0_compare import energy_tracking  # noqa: E402
from a0_lyrics import EDGE_PUNCT, lyric_lines  # noqa: E402
from a0_player import render_html  # noqa: E402
from a0_qa import qa  # noqa: E402
from bench import ensure_stem, load_lock, read_models, resolve_songs  # noqa: E402
from duel_verdict import lead_silence_s  # noqa: E402

AUDIO_SUFFIXES = {".wav", ".flac", ".mp3"}
PROMPT_SUFFIX = "_lyrics.txt"
SLUG_RE = re.compile(r"[^\w.-]+", re.UNICODE)
ARABIC_RE = re.compile(r"[\u0621-\u064a\u0671-\u06d3]")


# --- input discovery -------------------------------------------------------

def match_prompt(wav_stem: str, prompts: dict[str, Path]) -> Path | None:
    """Find the lyrics prompt for a `<name>_<seed>.wav`.

    YuE2 writes `prompts/<name>_lyrics.txt` and `<name>_<seed>.wav`, but the
    seed is not recorded in the prompt, so the match is by longest prompt name
    that prefixes the WAV stem (at an `_` boundary).
    """
    best: tuple[int, Path] | None = None
    for name, path in prompts.items():
        if wav_stem == name or wav_stem.startswith(name + "_"):
            if best is None or len(name) > best[0]:
                best = (len(name), path)
    return best[1] if best else None


def discover_run(run_dir: Path) -> list[dict]:
    prompts: dict[str, Path] = {}
    prompt_dir = run_dir / "prompts"
    if prompt_dir.is_dir():
        for p in sorted(prompt_dir.glob("*" + PROMPT_SUFFIX)):
            prompts[p.name[:-len(PROMPT_SUFFIX)]] = p
    songs: list[dict] = []
    for audio in sorted(run_dir.iterdir()):
        if not audio.is_file() or audio.suffix.lower() not in AUDIO_SUFFIXES:
            continue
        caption = match_prompt(audio.stem, prompts)
        songs.append({
            # Name by the WAV stem, not the prompt: a run dir has one WAV per
            # seed and naming by prompt would make the takes collide.
            "name": audio.stem,
            # absolute(), not resolve(): following a symlink would rename the
            # stem cache from the render's name to the target's ("vocals").
            "audio": str(audio.absolute()),
            "caption": str(caption.absolute()) if caption else None,
        })
    return songs


def resolve_inputs(args) -> tuple[list[dict], str]:
    """Return (songs, label) from --run-dir or --data-dir/--songs."""
    if args.run_dir:
        run = Path(args.run_dir).resolve()
        if not run.is_dir():
            sys.exit(f"--run-dir is not a directory: {run}")
        return discover_run(run), run.name
    data_dir = Path(args.data_dir) if args.data_dir else None
    songs = resolve_songs(Path(args.songs) if args.songs else None, data_dir,
                          args.auto_songs)
    label = data_dir.name if data_dir else Path(args.songs).stem
    return songs, label


# --- lyric lines -----------------------------------------------------------

def lyric_structure(text: str) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """Split a lyrics file into (display line, word tokens) and a flat word list.

    Tokenisation must match `a0_lyrics.words` (whitespace split, edge punctuation
    removed) so the aligned-word count lines up with the display lines.
    """
    per_line: list[tuple[str, list[str]]] = []
    for line in lyric_lines(text):
        # Sibling prompt files may carry a `///***///` banner or other
        # non-lyric header. Section tags are already removed; anything with no
        # Arabic letter is not sung text.
        if not ARABIC_RE.search(line):
            continue
        tokens = [t.strip(EDGE_PUNCT) for t in line.split()]
        tokens = [t for t in tokens if t]
        if tokens:
            per_line.append((line, tokens))
    flat = [w for _, tokens in per_line for w in tokens]
    return per_line, flat


def assign_lines(words: list[dict], per_line: list[tuple[str, list[str]]],
                 flat: list[str]) -> bool:
    """Attach a `line` index to every aligned word.

    Aligned words normally carry `src_i` (the index of the input word they came
    from), so a word dropped by the model vocab does not shift the rest. Older
    word lists have no `src_i`; those fall back to a sequential mapping, which
    is only valid when every word aligned.
    """
    src_line: list[int] = []
    for li, (_, tokens) in enumerate(per_line):
        src_line.extend([li] * len(tokens))

    if words and all("src_i" in w for w in words) and len(src_line) == len(flat):
        mapped = 0
        for w in words:
            si = w["src_i"]
            if 0 <= si < len(src_line):
                w["line"] = src_line[si]
                mapped += 1
        if mapped == len(words):
            return True
        for w in words:
            w.pop("line", None)
        return False

    expected = len(flat)
    if len(words) != expected:
        return False
    k = 0
    for li, (_, tokens) in enumerate(per_line):
        for _ in tokens:
            words[k]["line"] = li
            k += 1
    return True


# --- subtitles -------------------------------------------------------------

def _ts(seconds: float, sep: str) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", sep)


def line_cues(record: dict, min_dur: float = 0.2) -> list[dict]:
    """One cue per lyric line, from the word spans that belong to it.

    Cues are made non-overlapping so SRT/VTT players that reject overlaps are
    happy. The player itself uses the (unclamped) word timings.
    """
    words = record["words"]
    lines = record.get("lines") or []
    if not words or not lines or any("line" not in w for w in words):
        return []
    by_line: dict[int, list[dict]] = {}
    for w in words:
        by_line.setdefault(w["line"], []).append(w)
    cues: list[dict] = []
    for li in sorted(by_line):
        ws = by_line[li]
        start = ws[0]["start"]
        end = max(w["end"] for w in ws)
        end = max(end, start + min_dur)
        cues.append({"start": start, "end": end, "text": lines[li]["text"]})
    for i in range(len(cues) - 1):
        nxt = cues[i + 1]["start"]
        if cues[i]["end"] > nxt:
            cues[i]["end"] = max(cues[i]["start"] + min_dur, nxt - 0.001)
    return cues


def word_cues(record: dict) -> list[dict]:
    """Fallback when line mapping failed: one cue per word."""
    return [{"start": w["start"], "end": max(w["end"], w["start"] + 0.2),
             "text": w["word"]} for w in record["words"]]


def cues_for(record: dict) -> tuple[list[dict], str]:
    cues = line_cues(record)
    return (cues, "line") if cues else (word_cues(record), "word")


def write_vtt(cues: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("WEBVTT\n\n")
        for c in cues:
            fh.write(f"{_ts(c['start'], '.')} --> {_ts(c['end'], '.')}\n"
                     f"{c['text']}\n\n")


def write_srt(cues: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for i, c in enumerate(cues, 1):
            fh.write(f"{i}\n{_ts(c['start'], ',')} --> {_ts(c['end'], ',')}\n"
                     f"{c['text']}\n\n")


# --- per-song --------------------------------------------------------------

def slug(name: str) -> str:
    s = SLUG_RE.sub("_", name).strip("._")
    return s or "song"


def validate_record(record: dict, path: Path) -> None:
    """Refuse to reuse a JSON that is not an A1 output (e.g. an old a0 dump)."""
    required = {"song", "audio", "stem", "caption", "model", "duration_s",
                "n_words_in", "n_words_aligned", "qa", "energy", "warnings",
                "intro_violation", "lines", "words"}
    missing = sorted(required - set(record))
    if missing:
        raise ValueError(f"{path.name} is not an A1 word file "
                         f"(missing: {', '.join(missing)}); use --force")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_prompts(song: dict) -> tuple[list[tuple[str, list[str]]], list[str]]:
    if not song.get("caption"):
        raise ValueError("no lyrics prompt found "
                         "(expected prompts/<name>_lyrics.txt)")
    caption = Path(song["caption"])
    if not caption.is_file():
        raise ValueError(f"lyrics prompt missing: {caption}")
    return lyric_structure(caption.read_text(encoding="utf-8"))


def align_one(song: dict, proc, model, model_id: str, revision: str | None,
              device: str, stems_root: Path, sep_device: str,
              skip_separation: bool) -> dict:
    per_line, flat = load_prompts(song)
    if not flat:
        raise ValueError(f"no words in {song['caption']}")

    audio = Path(song["audio"])
    if song.get("stems_dir"):
        stem = Path(song["stems_dir"])
    else:
        stem = stems_root / "htdemucs" / audio.stem / "vocals.wav"
    if skip_separation:
        if not stem.is_file():
            raise ValueError(f"--skip-separation but stem missing: {stem}")
    else:
        stem = ensure_stem(audio, stems_root, sep_device)

    t0 = time.monotonic()
    res = align_waveform(proc, model, load_audio(stem), flat, None,
                         model_id, song["name"], device)
    elapsed = time.monotonic() - t0
    words = res["words"]
    mapped = assign_lines(words, per_line, flat)
    m, warns = qa(words, res["duration_s"], 2.0)
    lead = lead_silence_s(stem)
    first = words[0]["start"] if words else None

    return {
        "song": song["name"],
        "audio": str(audio),
        "stem": str(stem),
        "caption": song["caption"],
        "model": model_id,
        "revision": revision,
        "aligned_at": utc_now(),
        "align_s": round(elapsed, 1),
        "duration_s": res["duration_s"],
        "n_words_in": res["n_words_in"],
        "n_words_aligned": res["n_words_aligned"],
        "stripped_diacritics": res.get("stripped_diacritics"),
        "transliteration": res.get("transliteration"),
        "vocab": res["vocab"],
        "qa": m,
        "warnings": warns,
        "energy": energy_tracking(words, str(stem)),
        "lead_silence_s": lead,
        "intro_violation": (lead is not None and first is not None
                            and first < lead),
        "line_mapping": "line" if mapped else "word (count mismatch)",
        "lines": [{"i": i, "text": text} for i, (text, _) in enumerate(per_line)],
        "words": words,
    }


def write_assets(record: dict, out: Path, song_slug: str, player_audio: str,
                 make_player: bool) -> dict:
    """Write JSON/VTT/SRT/player for one already-aligned song."""
    words_json = out / f"{song_slug}.words.json"
    words_json.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")

    cues, granularity = cues_for(record)
    vtt = out / f"{song_slug}.vtt"
    srt = out / f"{song_slug}.srt"
    write_vtt(cues, vtt)
    write_srt(cues, srt)

    player = None
    if make_player and record["words"]:
        audio = record["stem"] if player_audio == "stem" else record["audio"]
        title = f"{record['song']} — {record['n_words_aligned']} words"
        player = render_html(record, audio, out / f"{song_slug}.player.html", title)

    return {"words_json": str(words_json), "vtt": str(vtt), "srt": str(srt),
            "player": str(player) if player else None,
            "subtitle_granularity": granularity, "cues": len(cues)}


# --- reports ---------------------------------------------------------------

def write_reports(out: Path, label: str, records: list[dict],
                  failures: list[dict], model_id: str,
                  revision: str | None, device: str) -> None:
    ok = [r for r in records if r.get("_ok")]
    songs_out = []
    for r in records:
        item = {"song": r["song"], "ok": bool(r.get("_ok"))}
        if r.get("_ok"):
            for key in ("n_words_in", "n_words_aligned", "duration_s", "align_s",
                        "lead_silence_s", "intro_violation", "line_mapping",
                        "warnings"):
                item[key] = r.get(key)
            item["coverage"] = r["qa"].get("coverage")
            item["monotone"] = r["qa"].get("monotone_starts")
            item["on_off_ratio"] = r["energy"].get("on_off_ratio")
        else:
            item["error"] = r.get("error")
        item["files"] = r.get("_files")
        songs_out.append(item)
    summary = {
        "run": label,
        "generated_at": utc_now(),
        "model": model_id,
        "revision": revision,
        "device": device,
        "songs": songs_out,
        "totals": {
            "songs": len(records),
            "ok": len(ok),
            "failed": len(failures),
            "words": sum(r["n_words_aligned"] for r in ok),
            "intro_violations": sum(1 for r in ok if r["intro_violation"]),
            "warned_songs": sum(1 for r in ok if r["warnings"]),
        },
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [f"# A1 alignment report — {label}", "",
             f"Model: `{model_id}`" + (f" @ `{revision[:12]}`" if revision else ""),
             f"Device: {device} · generated {summary['generated_at']}", "",
             "| song | words | coverage | on/off | intro | cues | warnings | player |",
             "|---|---|---|---|---|---|---|---|"]
    for r in records:
        if not r.get("_ok"):
            lines.append(f"| `{r['song']}` | — | — | — | — | — | FAILED: "
                         f"{r['error']} | — |")
            continue
        files = r.get("_files") or {}
        player = f"[open]({Path(files['player']).name})" if files.get("player") else "—"
        intro = "**VIOLATION**" if r["intro_violation"] else "ok"
        lines.append(
            f"| `{r['song']}` | {r['n_words_aligned']}/{r['n_words_in']} | "
            f"{(r['qa'].get('coverage') or 0):.2f} | "
            f"{r['energy'].get('on_off_ratio') or 0:.2f} | {intro} | "
            f"{files.get('cues', '—')} {files.get('subtitle_granularity', '')} | "
            f"{len(r['warnings'])} | {player} |")
    t = summary["totals"]
    lines += ["", "## Totals", "",
              f"- {t['ok']}/{t['songs']} songs aligned, {t['words']} words",
              f"- {t['intro_violations']} silent-intro violation(s), "
              f"{t['warned_songs']} song(s) with QA warnings",
              "", "## Files", "",
              "`<song>.words.json` is the source of truth; `.vtt`/`.srt` are "
              "line-level subtitles; `.player.html` is the karaoke page.",
              "The silent-intro check independently verifies that word 0 waits "
              "for the vocal stem (the failure that disqualified MMS-1b in B2).",
              "", "Subtitles are line-level by design. Word-level timing lives "
              "in the JSON and drives the player.", ""]
    (out / "A1-REPORT.md").write_text("\n".join(lines), encoding="utf-8")

    links = ["<!doctype html>", "<html lang=\"en\"><head><meta charset=\"utf-8\">",
             f"<title>A1 — {label}</title>",
             "<style>body{background:#12141a;color:#dfe3ea;font:16px/1.6 "
             "system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 18px}"
             "a{color:#ffd34d}li{margin:.35em 0}"
             ".err{color:#ff7b72}</style></head><body>",
             f"<h1>A1 alignment — {label}</h1>",
             "<p>Word-level karaoke timings from forced alignment of the known "
             "lyrics.</p><ul>"]
    for r in records:
        if r.get("_ok"):
            files = r.get("_files") or {}
            n = r["n_words_aligned"]
            if files.get("player"):
                links.append(f"<li><a href=\"{Path(files['player']).name}\">"
                             f"{r['song']}</a> — {n} words</li>")
            else:
                links.append(f"<li>{r['song']} — {n} words "
                             "(player not written)</li>")
        else:
            links.append(f"<li class=\"err\">{r['song']} — "
                         f"failed: {r['error']}</li>")
    links += ["</ul></body></html>", ""]
    (out / "index.html").write_text("\n".join(links), encoding="utf-8")

    print(f"\n[a1] wrote {out}/A1-REPORT.md, summary.json, index.html")
    print((out / "A1-REPORT.md").read_text(encoding="utf-8"))


# --- main ------------------------------------------------------------------

def _has_cuda() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:  # noqa: BLE001
        return False


def default_model(repo: Path) -> str:
    """First uncommented model in bench/models.txt (the production default)."""
    models_file = repo / "bench" / "models.txt"
    if models_file.is_file():
        models = read_models(models_file)
        if models:
            return models[0]
    return "akbargherbal/wav2vec2-large-xlsr-53-arabic"


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-dir", help="a YuE2 run directory (wavs + prompts/)")
    ap.add_argument("--songs", help="songs JSON (see scripts/bench.py)")
    ap.add_argument("--data-dir", help="flat dir of audio + captions")
    ap.add_argument("--auto-songs", action="store_true",
                    help="pair <name>.mp3/.wav with <name>.txt in --data-dir")
    ap.add_argument("--out", help="output dir (default: results/a1/<label>)")
    ap.add_argument("--model", default=default_model(repo),
                    help="acoustic model id (default: production model in bench/models.txt)")
    ap.add_argument("--lock", default=str(repo / "bench" / "models.lock"),
                    help="pinned revisions JSON")
    ap.add_argument("--device", default="cuda" if _has_cuda() else "cpu")
    ap.add_argument("--sep-device", default="cpu",
                    help="Demucs device (cpu is fine; the run is I/O light)")
    ap.add_argument("--stems-dir", default="work/stems")
    ap.add_argument("--player-audio", choices=("mix", "stem"), default="mix",
                    help="audio the karaoke page plays (timings hold for both)")
    ap.add_argument("--no-player", action="store_true", help="skip player HTML")
    ap.add_argument("--skip-separation", action="store_true",
                    help="assume vocal stems already exist")
    ap.add_argument("--force", action="store_true",
                    help="re-align even when <song>.words.json exists")
    ap.add_argument("--limit", type=int, help="only the first N songs")
    ap.add_argument("--dry-run", action="store_true",
                    help="list the plan; do not load models or write files")
    args = ap.parse_args(argv)

    if args.run_dir and (args.songs or args.data_dir):
        ap.error("--run-dir cannot be combined with --songs / --data-dir")
    if not (args.run_dir or args.songs or args.data_dir):
        ap.error("give --run-dir, --songs, or --data-dir")
    if args.auto_songs and not args.data_dir:
        ap.error("--auto-songs needs --data-dir")

    songs, label = resolve_inputs(args)
    if args.limit:
        songs = songs[:args.limit]
    if not songs:
        sys.exit("no songs found")

    out = Path(args.out) if args.out else repo / "results" / "a1" / label
    lock_path = Path(args.lock) if args.lock else None
    lock = load_lock(lock_path, repo / "bench" / "models.txt")
    revision = lock.get(args.model)
    stems_root = Path(args.stems_dir)

    print(f"[a1] {len(songs)} song(s) · model {args.model}"
          + (f"@{revision[:12]}" if revision else " (unpinned)"), flush=True)
    if args.dry_run:
        for s in songs:
            cap = s.get("caption") or "(NO LYRICS PROMPT)"
            print(f"  - {s['name']}\n      audio:   {s['audio']}\n"
                  f"      lyrics:  {cap}\n      out:     {out}/")
        print(f"[a1] dry run; would write to {out}/")
        return 0

    out.mkdir(parents=True, exist_ok=True)
    pending = [s for s in songs
               if args.force or not (out / f"{slug(s['name'])}.words.json").is_file()]
    proc = model = None
    if pending:
        print(f"[a1] loading model {args.model}"
              + (f"@{revision[:12]}" if revision else "") + " ...", flush=True)
        try:
            proc, model, _torch = load_model(args.model, args.device,
                                             revision=revision)
        except (Exception, SystemExit) as e:  # noqa: BLE001
            sys.exit(f"could not load model {args.model}: {e}")
    records: list[dict] = []
    failures: list[dict] = []

    for i, song in enumerate(songs, 1):
        s_slug = slug(song["name"])
        words_json = out / f"{s_slug}.words.json"
        print(f"\n[a1] {i}/{len(songs)}: {song['name']}", flush=True)
        try:
            if words_json.is_file() and not args.force:
                print(f"[a1]   reuse {words_json.name} (use --force to re-align)")
                record = json.loads(words_json.read_text(encoding="utf-8"))
                validate_record(record, words_json)
            else:
                record = align_one(song, proc, model, args.model, revision,
                                   args.device, stems_root, args.sep_device,
                                   args.skip_separation)
            files = write_assets(record, out, s_slug, args.player_audio,
                                 not args.no_player)
            record.update({"_ok": True, "_files": files})
            records.append(record)
            qm = record["qa"]
            print(f"[a1]   {record['n_words_aligned']}/{record['n_words_in']} words · "
                  f"cov {qm.get('coverage')} · on/off "
                  f"{record['energy'].get('on_off_ratio')} · "
                  f"intro {'VIOLATION' if record['intro_violation'] else 'ok'} · "
                  f"{files['cues']} {files['subtitle_granularity']} cues", flush=True)
            for w in record["warnings"]:
                print(f"[a1]   WARN {w}", flush=True)
        except (Exception, SystemExit) as e:  # noqa: BLE001
            msg = str(e) or type(e).__name__
            print(f"[a1]   FAILED: {msg}", flush=True)
            rec = {"song": song["name"], "_ok": False, "error": msg}
            records.append(rec)
            failures.append(rec)

    write_reports(out, label, records, failures, args.model, revision, args.device)
    t = sum(1 for r in records if r.get("_ok"))
    print(f"[a1] done: {t}/{len(records)} songs ok"
          + (f", {len(failures)} failed" if failures else ""))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
