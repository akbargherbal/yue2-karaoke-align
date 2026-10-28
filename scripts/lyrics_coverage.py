#!/usr/bin/env python3
"""Lyrics coverage audit — which audio tracks have a usable lyric sheet?

This is the L0–L4 tool from `docs/CROSS-PROJECT.md`. It does **not** align
anything and it does not copy lyric text into its outputs. It builds:

    work/lyrics/inventory.csv     one row per audio track
    work/lyrics/lyrics_index.csv  one row per lyric sheet (metadata only)
    work/lyrics/title_map.csv     candidates + a human-editable decision column
    work/lyrics/coverage.csv      join + readiness flags
    work/lyrics/readiness.csv     matched tracks only (the review queue)
    work/lyrics/summary.json      aggregate counts

Inputs are read-only:

* an audio inventory: either the caption JSONs from
  `music_captioning_oct_2026/outputs/<ARTIST>/<album>.json`, or a CSV with
  `artist,album,file[,duration_s]`. Reading a caption tree defaults to the
  Elissa + Fadl Shaker scope; `--artists` changes it;
* the lyric scrape tree from `arabic-music-lyrics/lyrics/<source>/artists/`.

The hard part is that the audio filenames are Latin transliterations
(``01. Ayami Bik.mp3``) while the lyric titles are Arabic (``أيامي بيك``).
Matching therefore uses a consonant skeleton with variants for qaf (Egyptian
silent vs ``k``), long vowels (written in Arabic, dropped in Latin) and the
definite article. That produces **candidates**; ``title_map.csv`` is the
authority. Re-running preserves human edits in ``decision`` / ``lyric_title`` /
``lyric_source`` / ``notes`` keyed by ``track_id``.

Usage
-----
    python scripts/lyrics_coverage.py \
        --caption-root /path/to/music_captioning_oct_2026/outputs \
        --lyrics-root  /path/to/arabic-music-lyrics/lyrics \
        --out work/lyrics

    # or an explicit inventory
    python scripts/lyrics_coverage.py --inventory work/lyrics/inventory.csv \
        --lyrics-root ... --out work/lyrics

    # add the broad corpus as a CSV fallback (export the parquet once)
    python scripts/lyrics_coverage.py ... \
        --corpus-csv work/lyrics/arabic_lyrics.csv

Exit code is 0 even when tracks are missing; this is a report, not a gate.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import unquote, urlparse

# --- Arabic / Latin skeletons ------------------------------------------------

AR_MAP = {
    "ا": "", "أ": "", "إ": "", "آ": "", "ب": "b", "ت": "t", "ث": "t",
    "ج": "g", "ح": "h", "خ": "x", "د": "d", "ذ": "d", "ر": "r", "ز": "z",
    "س": "s", "ش": "x", "ص": "s", "ض": "d", "ط": "t", "ظ": "d", "ع": "",
    "غ": "g", "ف": "f", "ق": "k", "ك": "k", "ل": "l", "م": "m", "ن": "n",
    "ه": "h", "و": "w", "ي": "y", "ى": "", "ة": "", "ء": "", "ئ": "",
    "ؤ": "", "ٱ": "", "\u0640": "",
}
LONG_VOWELS = set("اوي")
LATIN_DIGRAPHS = [
    ("kh", "x"), ("ch", "x"), ("sh", "x"), ("th", "t"), ("dh", "d"),
    ("gh", "g"), ("ph", "f"), ("ck", "k"), ("qu", "k"), ("ou", "u"),
    ("ee", "i"), ("oo", "u"), ("aa", "a"), ("ai", "a"), ("ay", "a"),
    ("ei", "a"), ("ey", "a"), ("ea", "i"), ("ie", "i"),
]
LATIN_DIGITS = {"2": "k", "3": "", "7": "h", "9": "s", "5": "x", "6": "t", "0": ""}
HARAKAT = set("\u064b\u064c\u064d\u064e\u064f\u0650\u0651\u0652\u0653\u0654\u0670")
SECTION_RE = re.compile(r"^\s*\[.*\]\s*$")
LATIN_RE = re.compile(r"[A-Za-z]")
ARABIC_RE = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]")


def strip_marks(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text)
                   if c not in HARAKAT and c != "\u0640")


def norm_ar(text: str) -> str:
    t = strip_marks(text)
    return (t.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
             .replace("ى", "ي").replace("ة", "ه"))


def skel_ar(text: str, qaf: str = "k", keep_long: bool = True) -> str:
    text = norm_ar(text)
    out = []
    for c in text:
        if c.isspace():
            out.append(" ")
        elif c == "ق":
            out.append(qaf)
        elif c in LONG_VOWELS and not keep_long:
            continue
        elif c in AR_MAP:
            out.append(AR_MAP[c])
        elif c.isalpha():
            out.append(c)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def skel_lat(text: str, keep_semivowels: bool = True,
             strip_article: bool = True) -> str:
    t = unicodedata.normalize("NFKD", text or "").lower()
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("'", "").replace("’", "").replace("`", "")
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()

    # "El Hob El Qadeem" tokenizes the article separately; Arabic attaches it
    # ("الحب القديم"). Merge it before vowel removal, then strip the prefix.
    merged: list[str] = []
    toks = t.split()
    i = 0
    while i < len(toks):
        if toks[i] in ("al", "el", "il") and i + 1 < len(toks):
            merged.append(toks[i] + toks[i + 1])
            i += 2
        else:
            merged.append(toks[i])
            i += 1

    out: list[str] = []
    for tok in merged:
        if strip_article:
            tok = re.sub(r"^(al|el|il)", "", tok) or tok
        for a, b in LATIN_DIGRAPHS:
            tok = tok.replace(a, b)
        tok = "".join(LATIN_DIGITS.get(c, c) for c in tok)
        if keep_semivowels:
            tok = re.sub(r"[aeiou]", "", tok)
        else:
            tok = re.sub(r"[aeiouyw]", "", tok)
        tok = re.sub(r"(.)\1+", r"\1", tok)
        out.append(tok)
    return re.sub(r"\s+", " ", " ".join(out)).strip()


def variants_ar(title: str) -> set[str]:
    out = set()
    for qaf in ("k", ""):
        for keep_long in (True, False):
            s = skel_ar(title, qaf=qaf, keep_long=keep_long)
            if s:
                out.add(s)
                out.add(" ".join(sorted(s.split())))
    return out


def variants_lat(title: str) -> set[str]:
    out = set()
    for keep in (True, False):
        for strip in (True, False):
            s = skel_lat(title, keep_semivowels=keep, strip_article=strip)
            if s:
                out.add(s)
                out.add(" ".join(sorted(s.split())))
    return out


def ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() * 100.0


def url_slug(url: str | None) -> str:
    """Last URL path segment without .html (fnanen stores a Latin slug there)."""
    if not url:
        return ""
    name = unquote(urlparse(url).path.rstrip("/").split("/")[-1])
    if name.endswith(".html"):
        name = name[:-5]
    return name.replace("-", " ").strip()


def score_titles(lat: str, ar: str, slugs: list[str] | None = None) -> float:
    """Best skeleton similarity, with a token-count sanity guard.

    ``slugs`` are Latin transliterations from the source URL (fnanen). They are
    often closer to the audio filename than the Arabic title is, so they are
    scored Latin-to-Latin as an extra signal. Arabic slugs (kalimataghani) are
    ignored here.
    """
    best = 0.0
    for a in variants_lat(lat):
        for b in variants_ar(ar):
            if abs(len(a.split()) - len(b.split())) > 1:
                continue
            ca, cb = a.replace(" ", ""), b.replace(" ", "")
            if min(len(ca), len(cb)) < 2:
                continue
            # Reject collapse-into-nothing matches such as "Mali" -> "معقول"
            # ("ml" vs "mwl"): a huge length gap is not a transliteration.
            if min(len(ca), len(cb)) / max(len(ca), len(cb)) < 0.55:
                continue
            best = max(best, ratio(a, b))
    for slug in slugs or ():
        if not slug or ARABIC_RE.search(slug):
            continue
        for a in variants_lat(lat):
            for b in variants_lat(slug):
                if abs(len(a.split()) - len(b.split())) > 1:
                    continue
                best = max(best, ratio(a, b))
    return best


def strict_score(lat: str, ar: str) -> float:
    """Un-varied skeleton similarity, for human review (no vowel/qaf tricks)."""
    return ratio(skel_lat(lat, keep_semivowels=True),
                 skel_ar(ar, qaf="k", keep_long=True))


# --- title / text helpers ---------------------------------------------------

def clean_title(filename: str) -> str:
    s = Path(filename).stem
    s = re.sub(r"^\s*\d+\s*[.\-)]\s*", "", s)
    s = re.sub(r"\b(19|20)\d{2}\b", "", s)
    s = re.sub(r"[\(\)\[\]]", " ", s)
    s = re.sub(r"\b(mix|remix|remaster(ed)?|version|edit)\b", " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip(" .-")


def norm_key(title: str) -> str:
    t = norm_ar(title).lower()
    t = re.sub(r"[^\w\u0600-\u06ff\s]+", " ", t)
    t = re.sub(r"\b(feat|ft|featuring)\b.*$", "", t)
    return re.sub(r"\s+", " ", t).strip()


def harakat_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(c in HARAKAT for c in text) / len(text)


def lyric_stats(text: str) -> dict:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    words = [w for ln in lines if not SECTION_RE.match(ln) for w in ln.split()]
    latin = sum(bool(LATIN_RE.search(ln)) for ln in lines)
    return {
        "n_lines": len(lines),
        "n_words": len(words),
        "harakat_ratio": round(harakat_ratio(text), 5),
        "has_section_tags": any(SECTION_RE.match(ln) for ln in lines),
        "latin_lines": latin,
        "arabic_ratio": round(
            len(ARABIC_RE.findall(text)) / max(1, len(re.sub(r"\s", "", text))), 3),
    }


def readiness(row: dict, duration_s: float | None) -> tuple[str, list[str]]:
    flags: list[str] = []
    if row.get("status") != "matched":
        return row.get("status", "missing"), flags
    if row.get("instrumental"):
        return "not_applicable", ["instrumental"]
    if row.get("latin_lines"):
        flags.append("has_latin_lines")
    if row.get("has_translation"):
        flags.append("has_translation")
    if row.get("has_section_tags"):
        flags.append("has_section_tags")
    if row.get("latin_lines") and row.get("arabic_ratio", 1.0) < 0.5:
        return "reject", flags + ["not_arabic"]
    if row.get("n_words", 0) < 20:
        return "reject", flags + ["too_short"]
    if duration_s and row.get("n_words"):
        wpm = row["n_words"] / (duration_s / 60.0)
        if wpm < 50:
            flags.append(f"low_wpm:{wpm:.0f}")
        elif wpm > 220:
            flags.append(f"high_wpm:{wpm:.0f}")
    if "has_translation" in flags or "has_latin_lines" in flags:
        return "needs_cleanup", flags
    if any(f.startswith("low_wpm") for f in flags):
        # A compact sheet (chorus once, sung twice) looks like this.
        return "needs_expansion", flags
    return "ready", flags


# --- inventory / lyric loading ---------------------------------------------

ARTIST_LABELS = {
    "ELISSA": "Elissa", "FADL_SHAKER": "Fadl Shaker",
}
SLUG_LABELS = {
    "elissa": "Elissa", "fadhl-shaker": "Fadl Shaker",
    "fadl-shaker": "Fadl Shaker",
}
ARTIST_ALIASES = {
    "fadel chaker": "Fadl Shaker", "fadl shaker": "Fadl Shaker",
    "elissa": "Elissa",
}
# This plan is scoped to the commercial Elissa/Fadl discography. When reading a
# caption tree, keep only these artists unless --artists says otherwise.
SCOPE_ARTISTS = ("Elissa", "Fadl Shaker")


def load_inventory(args) -> list[dict]:
    if args.inventory:
        rows = []
        with open(args.inventory, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows.append({
                    "track_id": r.get("track_id") or
                    f"{r.get('artist','')}/{r.get('album','')}/{r.get('file','')}",
                    "artist": r.get("artist", ""), "album": r.get("album", ""),
                    "file": r.get("file", ""),
                    "duration_s": float(r["duration_s"]) if r.get("duration_s") else None,
                    "instrumental": str(r.get("instrumental", "")).lower()
                    in ("1", "true", "yes"),
                })
        return rows

    rows = []
    for album_json in sorted(Path(args.caption_root).glob("*/*.json")):
        if "/abc/" in str(album_json):
            continue
        artist_dir = album_json.parent.name
        artist = ARTIST_LABELS.get(artist_dir, artist_dir)
        data = json.loads(album_json.read_text(encoding="utf-8"))
        for t in data.get("tracks", []):
            file = t.get("file") or ""
            rows.append({
                "track_id": f"{artist}/{album_json.stem}/{file}",
                "artist": artist, "album": album_json.stem, "file": file,
                "duration_s": t.get("duration_s"),
                "instrumental": "(music)" in file.lower(),
            })
    return rows


def load_lyrics(root: Path) -> list[dict]:
    out: list[dict] = []
    for path in sorted(root.glob("*/artists/*/*.json")):
        source = path.parents[2].name
        slug = path.parent.name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[coverage] skip {path}: {exc}", file=sys.stderr)
            continue
        rows = data["songs"] if isinstance(data, dict) and "songs" in data else data
        if not isinstance(rows, list):
            continue
        artist = SLUG_LABELS.get(slug)
        if artist is None and isinstance(data, dict):
            artist = (data.get("dataset") or {}).get("artist_en")
        artist = artist or slug
        for r in rows:
            text = r.get("lyrics") or ""
            stats = lyric_stats(text)
            out.append({
                "lyric_id": f"{source}/{slug}/{r.get('id') or r.get('slug') or r.get('title')}",
                "artist": artist, "title_ar": r.get("title", ""),
                "title_norm": norm_key(r.get("title", "")),
                "source": source, "source_url": r.get("url"),
                "slug": url_slug(r.get("url")),
                "year": r.get("year"), "notes": r.get("notes"),
                **stats,
            })
    return out


def load_corpus_csv(path: str) -> list[dict]:
    """Optional broad-corpus fallback: a CSV export of arabic_lyrics.parquet.

    Required columns: ``artist``, ``title``, ``lyrics``. Optional: ``source``,
    ``source_url``/``url``. The per-artist scrapes are preferred; this loader
    exists so the tool can see artists that have no scrape.
    """
    out: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for i, r in enumerate(csv.DictReader(fh)):
            text = r.get("lyrics") or ""
            artist = (r.get("artist") or r.get("artist_origin") or "").strip()
            artist = ARTIST_ALIASES.get(artist.lower(), artist)
            stats = lyric_stats(text)
            out.append({
                "lyric_id": f"corpus/{r.get('source', 'csv')}/{i}",
                "artist": artist, "title_ar": r.get("title", ""),
                "title_norm": norm_key(r.get("title", "")),
                "source": f"corpus:{r.get('source', 'csv')}",
                "source_url": r.get("source_url") or r.get("url"),
                "slug": url_slug(r.get("source_url") or r.get("url")),
                "year": r.get("year"), "notes": "",
                **stats,
            })
    return out


def dedupe_lyrics(rows: list[dict]) -> list[dict]:
    """One row per (artist, normalized title); union sources, keep longest."""
    best: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r["artist"], r["title_norm"])
        if key not in best:
            best[key] = dict(r, sources=[r["source"]],
                             slugs=[r["slug"]] if r.get("slug") else [])
            continue
        keep = best[key]
        slugs = sorted(set(keep.get("slugs", []) +
                           ([r["slug"]] if r.get("slug") else [])))
        if r["n_words"] > keep["n_words"]:
            sources = sorted(set(keep["sources"] + [r["source"]]))
            keep = dict(r, sources=sources, slugs=slugs)
        else:
            keep["sources"] = sorted(set(keep["sources"] + [r["source"]]))
            keep["slugs"] = slugs
        best[key] = keep
    return sorted(best.values(), key=lambda x: (x["artist"], x["title_norm"]))


# --- main -------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--caption-root", help="music_captioning outputs/ directory")
    ap.add_argument("--inventory", help="CSV with artist,album,file[,duration_s]")
    ap.add_argument("--lyrics-root", required=True,
                    help="arabic-music-lyrics/lyrics/ directory")
    ap.add_argument("--out", default="work/lyrics", help="output directory")
    ap.add_argument("--corpus-csv",
                    help="optional CSV export of arabic_lyrics.parquet "
                         "(artist,title,lyrics); a fallback for artists with no scrape")
    ap.add_argument("--artists", nargs="+", metavar="LABEL", default=None,
                    help='inventory artists to keep (default when reading a '
                         'caption tree: Elissa, Fadl Shaker)')
    ap.add_argument("--max-duration", type=float, default=0.0,
                    help="keep only tracks whose duration_s <= this many seconds "
                         "(0 = no limit); matches gcs_inventory.py's scope flag")
    ap.add_argument("--top", type=int, default=3, help="candidates per track")
    ap.add_argument("--auto-threshold", type=float, default=93.0,
                    help="score for an automatic 'match' (still needs review)")
    ap.add_argument("--ambiguous-threshold", type=float, default=80.0)
    args = ap.parse_args(argv)

    if not args.caption_root and not args.inventory:
        ap.error("give --caption-root or --inventory")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    inventory = load_inventory(args)
    # Explicit --artists always wins. Otherwise, a caption tree defaults to the
    # two artists this plan is about; an explicit --inventory is taken as-is.
    wanted = (set(args.artists) if args.artists
              else (set(SCOPE_ARTISTS) if args.caption_root and not args.inventory
                    else set()))
    if wanted:
        inventory = [r for r in inventory if r["artist"] in wanted]
        if not inventory:
            seen = sorted({r["artist"] for r in load_inventory(args)})
            ap.error(f"artist filter matched nothing; labels seen: {seen}")
    if not inventory:
        ap.error("inventory is empty (check --caption-root / --inventory)")
    if args.max_duration > 0:
        kept, excluded = [], []
        for row in inventory:
            duration = row.get("duration_s")
            if duration in ("", None):
                print(f"[coverage] keeping {row['track_id']}: no duration to check",
                      file=sys.stderr)
                kept.append(row)
            elif float(duration) <= args.max_duration:
                kept.append(row)
            else:
                excluded.append(row)
        if not kept:
            ap.error(f"no tracks <= {args.max_duration:g}s")
        if excluded:
            by_artist: dict[str, int] = {}
            for row in excluded:
                by_artist[row["artist"]] = by_artist.get(row["artist"], 0) + 1
            print(f"[coverage] excluded {len(excluded)} tracks longer than "
                  f"{args.max_duration:g}s {by_artist}", file=sys.stderr)
        inventory = kept
    lyrics = load_lyrics(Path(args.lyrics_root))
    if args.corpus_csv:
        lyrics += load_corpus_csv(args.corpus_csv)
    lyrics = dedupe_lyrics(lyrics)
    print(f"[coverage] inventory: {len(inventory)} tracks; "
          f"lyrics: {len(lyrics)} unique titles", file=sys.stderr)

    lyric_by_artist: dict[str, list[dict]] = {}
    for row in lyrics:
        lyric_by_artist.setdefault(row["artist"], []).append(row)

    previous = {}
    title_map_path = out / "title_map.csv"
    if title_map_path.exists():
        with open(title_map_path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                previous[r["track_id"]] = r

    candidates: dict[str, list[tuple[float, dict]]] = {}
    for track in inventory:
        title_lat = clean_title(track["file"])
        pool = lyric_by_artist.get(track["artist"], [])
        scored = [(score_titles(title_lat, x["title_ar"], x.get("slugs")), x)
                  for x in pool]
        scored.sort(key=lambda z: -z[0])
        candidates[track["track_id"]] = scored[:args.top]

    # write inventory
    with open(out / "inventory.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["track_id", "artist", "album", "file",
                                           "duration_s", "instrumental"])
        w.writeheader()
        for r in inventory:
            w.writerow({k: r.get(k) for k in w.fieldnames})

    # write lyric index (metadata only, no lyric text)
    with open(out / "lyrics_index.csv", "w", newline="", encoding="utf-8") as fh:
        cols = ["lyric_id", "artist", "title_ar", "source", "source_url", "slug", "year",
                "n_lines", "n_words", "harakat_ratio", "has_section_tags",
                "latin_lines", "arabic_ratio", "sources"]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in lyrics:
            row = dict(r)
            row["sources"] = ",".join(r.get("sources", [r["source"]]))
            w.writerow(row)

    # write title map, preserving human decisions
    map_rows = []
    per_status = {"match": 0, "ambiguous": 0, "missing": 0}
    for track in inventory:
        cands = candidates[track["track_id"]]
        best_score, best = (cands[0] if cands else (0.0, None))
        second = cands[1][0] if len(cands) > 1 else 0.0
        title_lat = clean_title(track["file"])
        strict = strict_score(title_lat, best["title_ar"]) if best else 0.0
        # A 1-2 consonant title ("Mali" -> "ml") can collapse onto anything.
        # Never auto-accept those; send them to the human queue.
        short = len(skel_lat(title_lat).replace(" ", "")) < 3
        if best is None:
            auto = "missing"
        elif (best_score >= args.auto_threshold and best_score - second >= 3.0
              and strict >= 45.0 and not short):
            auto = "match"
        elif best_score >= args.ambiguous_threshold:
            auto = "ambiguous"
        else:
            auto = "missing"
        per_status[auto] = per_status.get(auto, 0) + 1
        prev = previous.get(track["track_id"], {})
        decision = prev.get("decision") or auto
        map_rows.append({
            "track_id": track["track_id"], "artist": track["artist"],
            "album": track["album"], "file": track["file"],
            "title_lat": clean_title(track["file"]),
            "duration_s": track["duration_s"],
            "instrumental": track["instrumental"],
            "candidate_1": best["title_ar"] if best else "",
            "candidate_1_source": best["source"] if best else "",
            "candidate_1_score": f"{best_score:.1f}" if best else "",
            "strict_score": f"{strict:.1f}" if best else "",
            "candidate_2": cands[1][1]["title_ar"] if len(cands) > 1 else "",
            "candidate_2_score": f"{cands[1][0]:.1f}" if len(cands) > 1 else "",
            "auto_decision": auto,
            "decision": decision,
            "lyric_title": prev.get("lyric_title") or (best["title_ar"] if auto == "match" else ""),
            "lyric_source": prev.get("lyric_source") or (best["source"] if auto == "match" else ""),
            "reviewer": prev.get("reviewer", ""),
            "reviewed_at": prev.get("reviewed_at", ""),
            "notes": prev.get("notes", ""),
        })

    with open(title_map_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(map_rows[0].keys()))
        w.writeheader()
        w.writerows(map_rows)

    # coverage + readiness
    coverage = []
    status_counts = {"matched": 0, "ambiguous": 0, "missing": 0, "not_applicable": 0}
    ready_counts: dict[str, int] = {}
    for row in map_rows:
        decision = row["decision"]
        if row["instrumental"]:
            status = "not_applicable"
        elif decision == "match":
            status = "matched"
        elif decision == "ambiguous":
            status = "ambiguous"
        else:
            status = "missing"
        status_counts[status] = status_counts.get(status, 0) + 1

        lyric = None
        if status == "matched":
            title = row["lyric_title"] or row["candidate_1"]
            pool = lyric_by_artist.get(row["artist"], [])
            lyric = next((x for x in pool if x["title_ar"] == title), None)
        rec = dict(row)
        rec["status"] = status
        if lyric:
            rec.update({k: lyric[k] for k in ("n_lines", "n_words", "harakat_ratio",
                                              "has_section_tags", "latin_lines",
                                              "arabic_ratio")})
            rec["lyrics_source"] = lyric["source"]
            rec["lyrics_url"] = lyric["source_url"]
        else:
            rec.update({k: "" for k in ("n_lines", "n_words", "harakat_ratio",
                                        "has_section_tags", "latin_lines",
                                        "arabic_ratio", "lyrics_source", "lyrics_url")})
        state, flags = readiness(rec, row["duration_s"])
        rec["readiness"] = state
        rec["flags"] = ",".join(flags)
        rec["wpm"] = (round(float(rec["n_words"]) / (float(row["duration_s"]) / 60.0), 1)
                      if rec.get("n_words") and row["duration_s"] else "")
        ready_counts[state] = ready_counts.get(state, 0) + 1
        coverage.append(rec)

    with open(out / "coverage.csv", "w", newline="", encoding="utf-8") as fh:
        cols = ["track_id", "artist", "album", "file", "title_lat", "duration_s",
                "status", "readiness", "lyrics_source", "lyrics_url",
                "n_lines", "n_words", "wpm", "harakat_ratio", "has_section_tags",
                "latin_lines", "arabic_ratio", "flags", "decision", "notes"]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(coverage)

    # A focused review queue: only matched tracks, with the readiness verdict.
    with open(out / "readiness.csv", "w", newline="", encoding="utf-8") as fh:
        cols = ["track_id", "artist", "title_lat", "duration_s", "readiness",
                "n_lines", "n_words", "wpm", "flags", "lyrics_source", "lyrics_url"]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(r for r in coverage if r["status"] == "matched")

    summary = {
        "inventory_tracks": len(inventory),
        "lyrics_unique_titles": len(lyrics),
        "lyrics_by_artist": {k: len(v) for k, v in lyric_by_artist.items()},
        "auto_status": per_status,
        "status": status_counts,
        "readiness": ready_counts,
        "thresholds": {"match": args.auto_threshold,
                       "ambiguous": args.ambiguous_threshold},
        "note": "title_map.csv decisions are authoritative; auto status is a candidate.",
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
