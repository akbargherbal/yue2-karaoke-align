#!/usr/bin/env python3
"""Benchmark acoustic models for word-level forced alignment on our songs.

Runs N acoustic models x M songs, one model at a time (a memory ceiling, not a
style choice), and reports:

  * quality  — cross-model *consensus deviation* (no ground truth needed: two
    independent models that agree are probably both right), plus each model's
    energy-tracking against the vocal stem and the sanity metrics from a0_qa.
  * cost     — wall time, seconds per audio-minute, parameters, peak VRAM (GPU)
    or RSS (CPU).

Vocal separation happens **once per song** and is cached, so every model
aligns the identical stems (fair comparison) and Demucs is not re-paid.

Usage
-----
    python scripts/bench.py --models bench/models.txt --songs bench/songs.json \\
        --data-dir <dir> --out results/bench --device cuda

    # smoke first: one model, one song
    python scripts/bench.py ... --limit-models 1 --limit-songs 1

Songs JSON: [{"name": "...", "audio": "a.mp3", "caption": "a.txt"}] where the
audio/caption paths are relative to --data-dir. Or use --auto-songs to pair
`<name>.mp3` with `<name>.txt` in --data-dir.

Gold (optional) for a real accuracy number:
    {"<song name>": [{"word": "<arabic word>", "start": 12.34}, ...]}
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from a0_align import align_waveform, load_audio, load_model, load_words  # noqa: E402
from a0_compare import energy_tracking  # noqa: E402
from a0_qa import qa  # noqa: E402

GAP_THRESHOLD = 2.0


# --- helpers ---------------------------------------------------------------

def read_models(path: Path) -> list[str]:
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.split("#", 1)[0].strip()
        if ln:
            out.append(ln)
    return out


def load_lock(path: Path | None, models_file: Path) -> dict[str, str]:
    """Map model id -> pinned commit SHA from a models.lock JSON.

    Default: a `models.lock` sitting next to the models file. Missing lock is
    fine (unpinned run); a malformed lock is a hard error so a typo never
    silently degrades into "load whatever main is today".
    """
    if path is None:
        path = models_file.with_name("models.lock")
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for mid, meta in data.get("models", {}).items():
        rev = meta.get("revision") if isinstance(meta, dict) else meta
        if rev:
            out[mid] = rev
    return out


def resolve_songs(path: Path | None, data_dir: Path | None,
                  auto: bool) -> list[dict]:
    if auto:
        if data_dir is None:
            sys.exit("--auto-songs needs --data-dir")
        songs = []
        for txt in sorted(data_dir.glob("*.txt")):
            audio = txt.with_suffix(".mp3")
            if not audio.is_file():
                audio = txt.with_suffix(".wav")
            if audio.is_file():
                # Resolve against --data-dir, not CWD: the non-auto branch below
                # does the same, and downstream code (Demucs, load_words) opens
                # these paths as given.
                songs.append({"name": txt.stem,
                              "audio": str(audio.resolve()),
                              "caption": str(txt.resolve())})
        return songs
    if path is None:
        sys.exit("give --songs or --auto-songs")
    data = json.loads(path.read_text(encoding="utf-8"))
    base = data_dir if data_dir else path.resolve().parent
    out = []
    for s in data:
        if isinstance(s, str):                 # shorthand: a bare basename
            s = {"name": s, "audio": s + ".mp3", "caption": s + ".txt"}
        out.append({**s,
                    "audio": str((base / s["audio"]).resolve()),
                    "caption": str((base / s["caption"]).resolve())})
    return out


def ensure_stem(audio: Path, stems_root: Path, device: str = "cpu") -> Path:
    """Demucs vocal stem, cached. Layout: <stems_root>/htdemucs/<name>/vocals.wav"""
    stem = stems_root / "htdemucs" / audio.stem / "vocals.wav"
    if stem.is_file():
        return stem
    print(f"[sep] {audio.name} on {device} ...", flush=True)
    t0 = time.monotonic()
    subprocess.run([sys.executable, "-m", "demucs", "--two-stems=vocals",
                    "-n", "htdemucs", "-d", device, "-o", str(stems_root),
                    str(audio)], check=True)
    print(f"[sep] done in {time.monotonic()-t0:.0f}s -> {stem}", flush=True)
    return stem


def _rss_mb() -> float:
    """Current RSS in MB. Uses /proc/self/statm so it can go *down*.

    `resource.getrusage().ru_maxrss` is a high-water mark and never decreases, so
    a delta across models is meaningless after the first (largest) model.
    """
    try:
        with open("/proc/self/statm") as f:
            pages = int(f.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") / 1e6
    except (OSError, ValueError, IndexError):
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def params_m(model) -> float:
    return round(sum(p.numel() for p in model.parameters()) / 1e6, 1)


# --- main ------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", required=True, help="file with one model id per line")
    ap.add_argument("--lock", help="pinned revisions JSON (default: models.lock "
                                   "beside --models)")
    ap.add_argument("--songs", help="songs JSON")
    ap.add_argument("--data-dir", help="directory holding the audio + captions")
    ap.add_argument("--auto-songs", action="store_true",
                    help="pair every <name>.mp3 with <name>.txt in --data-dir")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--stems-dir", default="work/stems")
    ap.add_argument("--sep-device", default="cpu",
                    help="device for Demucs separation (cuda on Colab)")
    ap.add_argument("--device", default="cuda" if _has_cuda() else "cpu")
    ap.add_argument("--gold", help="optional gold anchor timings JSON")
    ap.add_argument("--limit-models", type=int)
    ap.add_argument("--limit-songs", type=int)
    ap.add_argument("--skip-separation", action="store_true",
                    help="assume stems already exist (fail if not)")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stems_root = Path(args.stems_dir)
    data_dir = Path(args.data_dir) if args.data_dir else None

    models = read_models(Path(args.models))
    if args.limit_models:
        models = models[:args.limit_models]
    lock = load_lock(Path(args.lock) if args.lock else None, Path(args.models))
    songs = resolve_songs(Path(args.songs) if args.songs else None, data_dir,
                          args.auto_songs)
    if args.limit_songs:
        songs = songs[:args.limit_songs]
    if not models or not songs:
        sys.exit("nothing to do (no models or no songs)")

    gold = json.loads(Path(args.gold).read_text(encoding="utf-8")) if args.gold else {}

    print(f"[bench] {len(models)} models x {len(songs)} songs on {args.device}")

    # 1. stems once
    for s in songs:
        audio = Path(s["audio"])
        stem = Path(s["stems_dir"]) if s.get("stems_dir") else (
            Path(args.stems_dir) / "htdemucs" / audio.stem / "vocals.wav")
        if args.skip_separation:
            if not stem.is_file():
                sys.exit(f"--skip-separation but stem missing: {stem}")
        else:
            stem = ensure_stem(audio, stems_root, args.sep_device)
        s["stem"] = str(stem)

    # 2. align
    rows: list[dict] = []
    word_lists: dict = {}
    jsonl = out / "bench_raw.jsonl"
    with jsonl.open("w", encoding="utf-8") as fh:
        for mi, model_id in enumerate(models, 1):
            revision = lock.get(model_id)
            pin = f"@{revision[:12]}" if revision else ""
            print(f"\n[bench] model {mi}/{len(models)}: {model_id}{pin}", flush=True)
            try:
                t_load = time.monotonic()
                rss0 = _rss_mb()
                proc, model, torch = load_model(model_id, args.device,
                                                revision=revision)
                load_s = time.monotonic() - t_load
            except Exception as e:  # noqa: BLE001
                print(f"[bench]   LOAD FAILED: {type(e).__name__}: {e}", flush=True)
                for s in songs:
                    rec = {"model": model_id, "song": s["name"], "ok": False,
                           "revision": revision,
                           "error": f"load: {type(e).__name__}: {e}"}
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    rows.append(rec)
                continue
            if args.device.startswith("cuda"):
                torch.cuda.reset_peak_memory_stats()
            p_m = params_m(model)
            model_recs: list[dict] = []

            for s in songs:
                words = load_words(Path(s["caption"]))
                wave = load_audio(s["stem"])
                t0 = time.monotonic()
                try:
                    res = align_waveform(proc, model, wave, words, None,
                                         model_id, s["name"], args.device)
                except SystemExit as e:            # vocab mismatch etc.
                    model_recs.append({"model": model_id, "song": s["name"],
                                       "ok": False, "error": f"align: {e}"})
                    print(f"[bench]   {s['name']}: FAILED ({e})", flush=True)
                    continue
                align_s = time.monotonic() - t0
                if args.device.startswith("cuda"):
                    torch.cuda.empty_cache()

                m, warns = qa(res["words"], res["duration_s"], GAP_THRESHOLD)
                rec = {
                    "model": model_id, "song": s["name"], "ok": True,
                    "revision": revision,
                    "params_m": p_m, "load_s": round(load_s, 1),
                    "align_s": round(align_s, 1),
                    "audio_min": round(res["duration_s"] / 60.0, 2),
                    "sec_per_audio_min": round(align_s / max(res["duration_s"], 1) * 60, 1),
                    "transliteration": res.get("transliteration"),
                    "stripped_diacritics": res.get("stripped_diacritics"),
                    "vocab_size": res["vocab"]["vocab_size"],
                    "diacritic_chars_in_vocab": res["vocab"]["diacritic_chars_in_vocab"],
                    "n_words_in": res["n_words_in"],
                    "n_words_aligned": res["n_words_aligned"],
                    "coverage": m.get("coverage"),
                    "monotone": m.get("monotone_starts"),
                    "overlaps": m.get("overlaps"),
                    "max_gap_s": m.get("max_gap_s"),
                    "word_dur_median_s": m.get("word_dur_median_s"),
                    "warnings": len(warns),
                    **energy_tracking(res["words"], s["stem"]),
                    **_maybe_gold(s["name"], res["words"], gold),
                }
                word_lists[(model_id, s["name"])] = res["words"]
                model_recs.append(rec)
                print(f"[bench]   {s['name']}: {res['n_words_aligned']}/{res['n_words_in']} "
                      f"mono={m.get('monotone_starts')} cov={m.get('coverage')} "
                      f"on/off={rec.get('on_off_ratio')} {align_s:.1f}s", flush=True)

            # Memory for THIS model, measured after it has actually run: the
            # weights are lazily mmap'd, so RSS has not grown yet at load time.
            if args.device.startswith("cuda"):
                mem = {"peak_vram_mb": round(torch.cuda.max_memory_allocated() / 1e6, 1)}
            else:
                mem = {"model_rss_mb": round(_rss_mb() - rss0, 1)}
            for rec in model_recs:
                if rec.get("ok"):
                    rec.update(mem)
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            rows.extend(model_recs)
            del model

    write_reports(out, rows, models, [s["name"] for s in songs], word_lists)
    return 0


def _maybe_gold(song: str, words: list[dict], gold: dict) -> dict:
    if song not in gold:
        return {}
    errs = []
    for item in gold[song]:
        if "i" in item and 0 <= item["i"] < len(words):
            errs.append(abs(words[item["i"]]["start"] - item["start"]))
            continue
        for w in words:                     # fall back to first text match
            if w["word"] == item.get("word"):
                errs.append(abs(w["start"] - item["start"]))
                break
    if not errs:
        return {}
    return {"gold_n": len(errs), "gold_mae_s": round(statistics.mean(errs), 3)}


def _consensus(rows: list[dict], word_lists: dict) -> dict:
    """Per-model mean of (per-song median |dstart| to every other model)."""
    ok = [r for r in rows if r.get("ok")]
    by_song: dict[str, list[dict]] = {}
    for r in ok:
        by_song.setdefault(r["song"], []).append(r)
    per_model: dict[str, list[float]] = {r["model"]: [] for r in ok}
    for song, rs in by_song.items():
        if len(rs) < 2:
            continue
        for r in rs:
            others = [o for o in rs if o["model"] != r["model"]
                      and o["n_words_aligned"] == r["n_words_aligned"]]
            ds: list[float] = []
            for o in others:
                a = word_lists[(r["model"], song)]
                b = word_lists[(o["model"], song)]
                ds += [abs(x["start"] - y["start"]) for x, y in zip(a, b)]
            if ds:
                per_model[r["model"]].append(statistics.median(ds))
    return {m: (round(statistics.mean(v), 3) if v else None)
            for m, v in per_model.items()}


def _song_difficulty(rows: list[dict], word_lists: dict) -> dict:
    """Mean pairwise |dstart| per song — which songs are hard, not just models."""
    ok = [r for r in rows if r.get("ok")]
    by_song: dict[str, list[dict]] = {}
    for r in ok:
        by_song.setdefault(r["song"], []).append(r)
    out: dict[str, float] = {}
    for song, rs in by_song.items():
        ds = []
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                a = word_lists.get((rs[i]["model"], song))
                b = word_lists.get((rs[j]["model"], song))
                if a and b and len(a) == len(b):
                    ds.append(statistics.median([abs(x["start"] - y["start"])
                                                 for x, y in zip(a, b)]))
        if ds:
            out[song] = round(statistics.mean(ds), 3)
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def write_reports(out: Path, rows: list[dict], models: list[str],
                  songs: list[str], word_lists: dict) -> None:
    cons = _consensus(rows, word_lists)
    for r in rows:
        if r.get("ok"):
            r["consensus_dev_s"] = cons.get(r["model"])

    by_model: dict[str, list[dict]] = {}
    for r in rows:
        by_model.setdefault(r["model"], []).append(r)

    lines = ["# Benchmark results", "",
             f"{len(models)} models x {len(songs)} songs. Full data: `bench_results.json`.",
             "Consensus dev = mean per-song median |dstart| to the other models "
             "(lower is better).", "",
             "| model | ok/total | consensus dev (s) | gold MAE (s) | on/off | coverage | "
             "mono | translit | sec/audio-min | params (M) | peak mem |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    summary = {}
    for m in models:
        rs = by_model.get(m, [])
        ok = [r for r in rs if r.get("ok")]
        if not ok:
            err = (rs[0].get("error", "?") if rs else "not run")
            lines.append(f"| `{m}` | 0/{len(rs)} | — | — | — | — | — | — | — | — | "
                         f"FAILED: {err} |")
            summary[m] = {"ok": 0, "error": err}
            continue
        c = [r["consensus_dev_s"] for r in ok if r.get("consensus_dev_s") is not None]
        gd = [r["gold_mae_s"] for r in ok if r.get("gold_mae_s") is not None]
        onoff = statistics.mean([r["on_off_ratio"] for r in ok if r.get("on_off_ratio")])
        cov = statistics.mean([r["coverage"] for r in ok if r.get("coverage") is not None])
        mono = sum(1 for r in ok if r.get("monotone")) / len(ok)
        tl = ",".join(sorted({str(r.get("transliteration")) for r in ok}))
        sec = statistics.mean([r["sec_per_audio_min"] for r in ok])
        memkey = "peak_vram_mb" if "peak_vram_mb" in ok[0] else "model_rss_mb"
        mem = max(r.get(memkey, 0) for r in ok)
        lines.append(
            f"| `{m}` | {len(ok)}/{len(rs)} | "
            f"{(f'{statistics.mean(c):.3f}' if c else '—')} | "
            f"{(f'{statistics.mean(gd):.3f}' if gd else '—')} | "
            f"{onoff:.2f} | {cov:.2f} | {mono:.0%} | {tl} | {sec:.1f} | "
            f"{ok[0]['params_m']:.0f} | {mem:.0f} MB |")
        summary[m] = {"ok": len(ok),
                      "consensus_dev_s": round(statistics.mean(c), 3) if c else None,
                      "gold_mae_s": round(statistics.mean(gd), 3) if gd else None,
                      "on_off": round(onoff, 2), "coverage": round(cov, 3),
                      "monotone_frac": round(mono, 3),
                      "sec_per_audio_min": round(sec, 1),
                      "params_m": ok[0]["params_m"], memkey: round(mem, 1)}

    lines += ["", "## Per-song difficulty (mean pairwise |dstart| across models)", "",
              "If this dominates the model differences, song choice matters more "
              "than model choice.", "",
              "| song | mean pairwise (s) |", "|---|---|"]
    for song, d in _song_difficulty(rows, word_lists).items():
        lines.append(f"| `{song}` | {d:.3f} |")

    ranked = sorted([kv for kv in summary.items() if kv[1].get("ok")],
                    key=lambda kv: (kv[1].get("gold_mae_s")
                                    if kv[1].get("gold_mae_s") is not None
                                    else kv[1].get("consensus_dev_s") or 9e9))
    lines += ["", "## Ranking", "",
              "*primary: gold MAE when available, else cross-model consensus "
              "(lower is better)*", ""]
    for i, (m, s) in enumerate(ranked, 1):
        prim = (f"gold MAE {s['gold_mae_s']:.3f}s" if s.get("gold_mae_s") is not None
                else (f"consensus {s['consensus_dev_s']:.3f}s"
                      if s.get("consensus_dev_s") is not None
                      else "single-model run (no consensus yet)"))
        lines.append(f"{i}. `{m}` — {prim}, on/off {s['on_off']}, cover {s['coverage']}, "
                     f"{s['sec_per_audio_min']}s/min, {s['params_m']:.0f}M params")
    lines += ["", "> Consensus deviation is a *proxy*: it measures agreement between",
              "> models, not agreement with truth — two models can share a bias.",
              "> Rank with it, then confirm the winner by ear or with `--gold` anchors.",
              "> Quality and cost are two axes; a small model that ties on quality wins",
              "> on efficiency (see the mem / params columns).", ""]

    (out / "bench_table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "bench_results.json").write_text(
        json.dumps({"models": models, "songs": songs, "summary": summary, "rows": rows},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n[bench] wrote {out}/bench_table.md, bench_results.json, bench_raw.jsonl")
    print((out / "bench_table.md").read_text(encoding="utf-8"))


def _has_cuda() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    raise SystemExit(main())
