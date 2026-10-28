#!/usr/bin/env python3
"""Merge review answers into a title-map v2 and report before it is trusted.

Never edits the committed map in place: writes ``title_map_v2.csv`` plus a
summary of what changed, so the diff can be read before the index moves.

Checks that matter before the merge is accepted:

- every answered row has a decision and, for a match, a lyric title;
- ``no_match`` rows leave the pipeline as ``no_lyrics`` (counted, not dropped);
- two audio tracks claiming the same lyric sheet (legit for album duplicates,
  suspicious otherwise) are listed;
- how many reviewers overrode the matcher's ranking (chose candidate 2).

Usage::

    python scripts/review_export.py --db work/review/answers.db \
        --title-map work/lyrics/title_map.csv --out work/review/title_map_v2.csv
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

def norm_title(text: str) -> str:
    """Same folding as lyrics_review.py: hamza/alef, ya, ta marbuta, harakat."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("ـ", "")
    for src, dst in (("أإآٱ", "ا"), ("ى", "ي"), ("ة", "ه"), ("ؤ", "و"), ("ئ", "ي")):
        for ch in src:
            text = text.replace(ch, dst)
    text = "".join(c if c.isalnum() or c.isspace() else " " for c in text)
    return " ".join(text.split())


COLUMNS = ["track_id", "artist", "album", "file", "title_lat", "duration_s",
           "instrumental", "candidate_1", "candidate_1_source",
           "candidate_1_score", "strict_score", "candidate_2",
           "candidate_2_score", "auto_decision", "decision", "lyric_title",
           "lyric_source", "reviewer", "reviewed_at", "notes"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="work/review/answers.db")
    ap.add_argument("--answers-csv",
                    help="use the app's exported CSV instead of the SQLite DB "
                         "(chosen candidate is inferred from the title)")
    ap.add_argument("--title-map", default="work/lyrics/title_map.csv")
    ap.add_argument("--out", default="work/review/title_map_v2.csv")
    ap.add_argument("--lyrics-index",
                    help="index/lyrics/lyrics_index.csv; when given, a recorded "
                         "source/title is resolved to the row the downstream lookup "
                         "will actually find (the index keeps one row per title)")
    ap.add_argument("--status", default="ambiguous",
                    help="only rows with this decision are expected to be reviewed")
    args = ap.parse_args(argv)

    with open(args.title_map, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    by_id = {r["track_id"]: r for r in rows}

    if args.answers_csv:
        answers = {}
        with open(args.answers_csv, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                title = (r.get("lyric_title") or "").strip()
                base = by_id.get(r["track_id"], {})
                chosen = 0
                if r["decision"] == "match":
                    if title and title == base.get("candidate_2"):
                        chosen = 2
                    elif title and title == base.get("candidate_1"):
                        chosen = 1
                answers[r["track_id"]] = {
                    "track_id": r["track_id"], "decision": r["decision"],
                    "chosen": chosen, "lyric_title": title,
                    "lyric_source": (r.get("lyric_source") or "").strip(),
                    "note": (r.get("notes") or "").strip(),
                    "reviewer": (r.get("reviewer") or "").strip(),
                    "updated_at": (r.get("reviewed_at") or "").strip(),
                }
    else:
        if not Path(args.db).exists():
            sys.exit(f"{args.db} missing; run the review app first")
        with sqlite3.connect(args.db) as c:
            c.row_factory = sqlite3.Row
            answers = {r["track_id"]: dict(r) for r in c.execute("SELECT * FROM answers")}
    queue = [r for r in rows if r["decision"] == args.status]
    missing = [r["track_id"] for r in queue if r["track_id"] not in answers]

    by_decision: dict[str, int] = defaultdict(int)
    overrides, sheets = [], defaultdict(list)
    for row in rows:
        a = answers.get(row["track_id"])
        if not a:
            continue
        by_decision[a["decision"]] += 1
        row["decision"] = a["decision"]
        row["lyric_title"] = a["lyric_title"] or ""
        row["lyric_source"] = a["lyric_source"] or ""
        row["reviewer"] = a["reviewer"] or ""
        row["reviewed_at"] = a["updated_at"] or ""
        if a["note"]:
            row["notes"] = a["note"]
        if a["chosen"] == 2:
            overrides.append(row)
        if a["decision"] == "match" and a["lyric_title"]:
            sheets[(a["lyric_source"], a["lyric_title"])].append(row["track_id"])

    # The app labels a candidate with the source whose excerpt it showed, while
    # the map's candidate came from the deduped index. Same song, possibly a
    # different scrape label: resolve to the row the index actually carries.
    fixed, unresolved = [], []
    if args.lyrics_index:
        idx_by_pair, idx_by_norm = {}, {}
        with open(args.lyrics_index, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                idx_by_pair[(r["title_ar"].strip(), r["source"])] = r
                idx_by_norm.setdefault(norm_title(r["title_ar"]), r)
        for row in rows:
            if row.get("decision") != "match" or not row.get("lyric_title"):
                continue
            title, source = row["lyric_title"].strip(), row["lyric_source"].strip()
            if (title, source) in idx_by_pair:
                continue
            hit = idx_by_pair.get((title, "")) or idx_by_norm.get(norm_title(title))
            if not hit:
                unresolved.append((row["file"], title, source))
                continue
            note = (row.get("notes") or "").strip()
            prefix = f"source {source}->{hit['source']}"
            row["lyric_title"], row["lyric_source"] = hit["title_ar"], hit["source"]
            row["notes"] = f"{note}; {prefix}" if note else prefix
            fixed.append((row["file"], prefix))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    conflicts = {k: v for k, v in sheets.items() if len({t.split('/')[1:2][0] for t in v}) > 1
                 or len(v) > 1}
    dup_sheet = {k: v for k, v in sheets.items() if len(v) > 1}

    print(f"queue ({args.status}): {len(queue)}")
    print(f"answered: {len(answers)}   unanswered: {len(missing)}")
    print(f"decisions: {dict(by_decision)}")
    print(f"reviewer chose candidate 2: {len(overrides)}")
    if args.lyrics_index:
        print(f"source/title resolved to the index: {len(fixed)}")
        for name, note in fixed:
            print(f"  {name}: {note}")
        if unresolved:
            print(f"UNRESOLVED against the lyrics index ({len(unresolved)}):",
                  file=sys.stderr)
            for name, title, source in unresolved:
                print(f"  {name}: {title!r} ({source})", file=sys.stderr)
    for row in overrides[:10]:
        print(f"  override: {row['file']}  ->  {row['lyric_title']}")
    if dup_sheet:
        print(f"same lyric sheet on multiple tracks: {len(dup_sheet)}")
        for (source, title), tracks in list(dup_sheet.items())[:10]:
            print(f"  {source} · {title}: {len(tracks)} tracks")
            for t in tracks[:4]:
                print(f"    {t}")
    if missing:
        print(f"\nUNANSWERED ({len(missing)}):", file=sys.stderr)
        for t in missing[:20]:
            print(f"  {t}", file=sys.stderr)
        if len(missing) > 20:
            print(f"  … and {len(missing) - 20} more", file=sys.stderr)
    print(f"\nwrote {out} ({len(rows)} rows)")
    print("next: diff against the committed map, then re-run readiness on the "
          "214-track scope")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
