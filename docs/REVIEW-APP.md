# REVIEW-APP.md — browser review of the title map (L2), on Colab

Status: **plan, 2026-10-28.** Nothing here is built yet except the review sheet
(`scripts/lyrics_review.py`). This document is the spec for the app.

Goal: turn human adjudication of `title_map.csv` from "edit a CSV" into
**one question per card, one click per answer**, with the audio in the same tab
and every answer persisted and exportable.

Scope: the **54 `ambiguous` rows** on the 214-track scope (Elissa 22,
Fadl Shaker 32). The same app can serve any later queue (`missing`, re-reviews)
by changing one flag — it is queue-driven, not hard-coded to 54.

---

## 1. What the reviewer does (the only thing)

Each card asks exactly one question:

> **Is this audio track the song in candidate 1?**

with four answers:

| Key | Button | `decision` | Meaning |
|---|---|---|---|
| `1` | نعم، الأول | `match` | candidate 1 is the song |
| `2` | لا، الثاني | `match` | candidate 2 is the song (`lyric_title` = candidate 2) |
| `3` | لا هذا ولا ذاك | `no_match` | neither; ship as `no_lyrics` |
| `4` | غير متأكد | `ambiguous` | skip, decide later |

Nothing else is asked. Text quality, chorus repetition, diacritics and timing
are **not** reviewer questions — they belong to readiness (L4) and QA.

---

## 2. Architecture

```
index/lyrics/title_map.csv        committed, read-only input
work/lyrics/review/ambiguous.csv  excerpts (lyric text lives here, gitignored)
        │
        ▼
scripts/review_build.py  ──▶  work/review/items.jsonl   (one row per card)
                                      │
                                      ▼
scripts/review_app.py  (Flask)  ──▶  work/review/answers.db  (SQLite, authoritative)
        │                              work/review/events.jsonl (append-only audit)
        │
        ├─ GET  /                card view (current unanswered)
        ├─ GET  /audio/<id>      audio, HTTP Range (seeking works)
        ├─ POST /answer          one decision + optional note
        ├─ GET  /audit            finished: table of all answers, editable
        ├─ GET  /export.csv      title_map-shaped export
        └─ GET  /health          liveness for the tunnel check
```

Three files, three responsibilities, no framework beyond Flask:

| Script | In | Out |
|---|---|---|
| `scripts/review_build.py` | `index/lyrics/title_map.csv` + review excerpts | `work/review/items.jsonl` |
| `scripts/review_app.py` | `items.jsonl` | `answers.db`, `events.jsonl`, CSV |
| `scripts/review_export.py` | `answers.db` + `title_map.csv` | `title_map_v2.csv` + diff + counts |

`review_export.py` never writes the committed map directly; it emits a v2 file
and a diff, and a human (me) reviews that diff before it becomes authoritative.

---

## 3. Card content (`items.jsonl`)

```json
{
  "id": "0561c8e",                      // short hash of track_id, stable
  "track_id": "Elissa/Elissa - 2004 Ahla Donya/02.Ahla Donya.mp3",
  "artist": "Elissa",
  "album": "Elissa - 2004 Ahla Donya",
  "file": "02.Ahla Donya.mp3",
  "duration_s": 257.7,
  "gcs_uri": "gs://…/DISCOGRAPHY/ELISSA/Elissa - 2004 Ahla Donya/02.Ahla Donya.mp3",
  "candidates": [
    {"n": 1, "title": "أحلى دنيا", "source": "fnanen", "score": 100.0,
     "excerpt": "احترت اشكي ولا احكي…", "url": "https://fnanen.com/…"},
    {"n": 2, "title": "حليلي دنيايا", "source": "fnanen", "score": 100.0,
     "excerpt": "ويلي ويلي يابا…", "url": "https://fnanen.com/…"}
  ]
}
```

`gcs_uri` is **reconstructed**, because the committed inventory drops the
column: `root/{ELISSA|FADL_SHAKER}/{album}/{file}`. Reuse the literal-URI
escaping already proven in `scripts/gcs_stems.py` (`gcs_glob_escape`) — two real
tracks contain `[ ]` and broke an earlier batch.

`items.jsonl` carries lyric excerpts, so it lives under `work/` and is never
committed. Only titles and decisions are exportable.

---

## 4. Audio strategy

| Step | Detail |
|---|---|
| Fetch | `gcloud storage cp <escaped gcs_uri> /content/review/audio/<id>.mp3` on first view |
| Cache | file exists → serve; never re-download within the session |
| Prefetch | after serving card *n*, a background thread fetches *n+1*, *n+2* |
| Serve | `send_file(path, conditional=True)` → HTTP Range, so the `<audio>` element can seek |
| Failure | fetch error → card still renders, audio area shows "الصوت غير متاح" and a retry button; never block a decision |
| Source | original MP3 mix, not the separated stem: ~4 MB vs ~25 MB FLAC, and the mix is what a listener recognizes |

Disk: 54 × ~5 MB ≈ 300 MB. Colab has room; nothing is uploaded back.

---

## 5. Colab hosting (the part that usually breaks)

Colab gives no inbound port, so:

```python
# cell 1
!pip -q install flask
!wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
      -O /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared
```

```python
# cell 2 — app in a thread, tunnel in the foreground
import threading, subprocess, re, time, sys
threading.Thread(target=app.run, kwargs=dict(host="127.0.0.1", port=5000,
                                             threaded=True), daemon=True).start()
time.sleep(2)
p = subprocess.Popen(["cloudflared", "tunnel", "--url", "http://127.0.0.1:5000"],
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
for line in p.stdout:
    sys.stdout.write(line); sys.stdout.flush()
    m = re.search(r"https://[-\w]+\.trycloudflare\.com", line)
    if m: print("\nOPEN:", m.group(0) + "/?k=" + TOKEN, flush=True)
```

- `threaded=True` is mandatory: audio Range requests and the UI share the process.
- A random `TOKEN` (`secrets.token_urlsafe`) in the query string keeps the URL
  from being guessable; the tunnel itself is public, so this is hygiene, not security.
- Fallback if `trycloudflare` is blocked: `google.colab.output.serve_kernel_port_as_window(5000)`
  (works, but Range/seek is less reliable — accept no-seek for that session).
- The cell must stay in the foreground; that is fine, the user is reviewing anyway.

---

## 6. Persistence — the app must survive a disconnect

- **SQLite** (`answers.db`, WAL) is the source of truth: `track_id` primary key,
  `decision`, `chosen`, `lyric_title`, `lyric_source`, `note`, `answered_at`,
  `reviewer`.
- **`events.jsonl`** is append-only: every answer and every change, with a
  timestamp. Used to reconstruct history and to prove nothing was overwritten.
- **GCS mirror after each answer** (cheap, ~1 KB):
  `gs://akbar-december-2024-backup/REVIEW/title_map/v1/answers.jsonl`.
  On a new Colab session: read the mirror back, merge by `track_id`, continue.
  Single writer, this app only — the same rule as the stem manifest.
- Re-running the Colab cell is always safe: the app resumes at the first
  unanswered card, and `?all=1` reopens answered ones.

---

## 7. UI (keep it boring)

```
┌───────────────────────────────────────────────┐
│ 5 / 54        Elissa · 2004 Ahla Donya        │
│ 02.Ahla Donya.mp3                257.7 s      │
│                                               │
│ ▶ ━━━━━━━━━━━━━━━━ 0:12 / 4:17               │
│                                               │
│ هذه الأغنية هي:                                │
│ ┌───────────────────────────────────────────┐ │
│ │ (1) أحلى دنيا · 100 · fnanen              │ │
│ │     "احترت اشكي ولا احكي…"        [المصدر]│ │
│ └───────────────────────────────────────────┘ │
│ ┌───────────────────────────────────────────┐ │
│ │ (2) حليلي دنيايا · 100 · fnanen           │ │
│ │     "ويلي ويلي يابا…"              [المصدر]│ │
│ └───────────────────────────────────────────┘ │
│                                               │
│ [1 نعم الأول] [2 لا الثاني] [3 لا هذا ولا ذاك] [4 غير متأكد] │
│                                               │
│ ملاحظة (اختياري): ___________________         │
│ ← السابق            تخطي غير المجابة →        │
└───────────────────────────────────────────────┘
```

- Keys `1–4` answer; `Backspace`/`←` goes back; `Space` plays/pauses. No key
  handler fires while the note field has focus.
- Auto-advance ~400 ms after an answer so a fast reviewer can go 1→1→1 without
  touching the mouse. Undo = go back one card.
- Progress bar and a per-artist count, because 54 in one sitting is the target.
- The candidate block is a `<label>` wrapping a radio input, so touch works too.

`/audit` (the "preview and correct" pass the reviewer asked for) is a table:
`idx · file · answer · lyric_title · note · [تعديل]`, plus a filter for
"changed my mind" rows, and the CSV download. Editing there writes a new event,
never a silent overwrite.

---

## 8. Export and merge

`GET /export.csv` and `scripts/review_export.py` emit the title-map columns:

```
track_id,decision,lyric_title,lyric_source,reviewer,reviewed_at,notes
```

`review_export.py` then prints a **pre-merge report**:

- answered / unanswered / skipped counts;
- decisions by artist;
- how many chose candidate 2 (a signal that the matcher's ranking is weak);
- **conflicts**: two audio tracks mapped to the same lyric sheet (legitimate for
  album duplicates, suspicious otherwise);
- **`no_match` list** — these leave the pipeline as `no_lyrics`;
- a unified diff of `title_map.csv` → `title_map_v2.csv`.

Only after that diff is accepted does the committed index change (`index/lyrics/`,
one commit, `reviewer` + `reviewed_at` filled). Then readiness is re-run on the
214 scope and the new `ready` count is the input to the A1 batch.

---

## 9. Build and test order (do not skip the local step)

| # | Step | Gate |
|---|---|---|
| 1 | `review_build.py` on the committed index | 54 items, every `gcs_uri` exists (`gcloud storage ls`) |
| 2 | app locally, 3 hard-coded items | keys, audio seek, notes, undo |
| 3 | app locally, full 54, **no Colab** | resume after `kill -9`, export CSV parses |
| 4 | Colab, 5 items | tunnel URL opens, Range works (seek mid-track) |
| 5 | Colab, kill the cell mid-review, reopen | answers survive; resumes at the right card |
| 6 | Colab, full 54 | ~30–40 min of review |

Local first is deliberate: debugging a Flask app through a tunnel is the slow
way, and the audio path is identical either way.

---

## 10. Effort and risks

Effort: build ~2 h (build + app + export), test ~30 min, review ~30–40 min.

| Risk | Mitigation |
|---|---|
| Tunnel blocked or slow | `serve_kernel_port_as_window` fallback; local cache removes WAN from the loop |
| Colab disconnect mid-review | SQLite + GCS mirror; resume by re-running the cell |
| Audio seek fails through the tunnel | Range enabled; if it still fails, review without seeking |
| Wrong answer slips through | `/audit` pass + export diff + A1 coverage gate |
| Copyright | audio in Colab temp disk + private GCS only; nothing committed; `work/` gitignored |
| Reviewer fatigue | one question, four keys, auto-advance, progress count |

---

## 11. Decisions (resolved 2026-10-28)

1. **Audio source: original MP3** — ~5 MB per track, natural listening
   reference, no `sha256` indirection. The separated `vocals.flac` stays for
   alignment only.
2. **Arabic-only UI** — RTL, file names stay Latin. Confirmed with the reviewer.
3. **Answers destination** — local `work/review/` + per-answer mirror to
   `gs://…/REVIEW/title_map/v1/answers.jsonl`; `title_map_v2.csv` reaches
   `index/lyrics/` only after the diff is read.

---

## 12. Runbook — exact cells (built and tested locally 2026-10-28)

Local pre-flight (once, on the box that has the lyric clones):

```bash
python scripts/lyrics_review.py --title-map work/lyrics/title_map.csv \
    --lyrics-root /path/to/arabic-music-lyrics/lyrics --out work/lyrics/review
python scripts/review_build.py --title-map work/lyrics/title_map.csv \
    --review-csv work/lyrics/review/ambiguous.csv --out work/review/items.jsonl
# 54 cards, 54/54 gcs_uri verified against the real listing
gcloud storage cp work/review/items.jsonl \
    gs://akbar-december-2024-backup/REVIEW/title_map/v1/items.jsonl
```

Colab, cell 1 — setup and audio access:

```python
!pip -q install flask
!wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
      -O /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared
from google.colab import auth; auth.authenticate_user()
!gcloud storage ls gs://akbar-december-2024-backup/DISCOGRAPHY/ELISSA/ | head -2
```

If the last line fails, `gcloud auth login` interactively once (the project's
default auth path) — do not use a service-account key for an attended session.

Colab, cell 2 — fetch the queue, start the app, open the tunnel:

```python
%cd /content
!git clone -q https://github.com/akbargherbal/yue2-karaoke-align repo 2>/dev/null || true
%cd /content/repo
!mkdir -p work/review
!gcloud storage cp gs://akbar-december-2024-backup/REVIEW/title_map/v1/items.jsonl \
     work/review/items.jsonl

import subprocess, re, time, secrets
TOKEN = secrets.token_urlsafe(9)
MIRROR = "gs://akbar-december-2024-backup/REVIEW/title_map/v1/answers.jsonl"

app = subprocess.Popen(
    ["python", "-u", "scripts/review_app.py",
     "--items", "work/review/items.jsonl",
     "--db", "work/review/answers.db",
     "--audio-dir", "work/review/audio",
     "--token", TOKEN, "--mirror-uri", MIRROR],
    stdout=open("/content/app.log", "w"), stderr=subprocess.STDOUT)
time.sleep(3)
print(subprocess.run(["curl", "-s", "http://127.0.0.1:5000/health"],
                     capture_output=True, text=True).stdout)

tun = subprocess.Popen(["cloudflared", "tunnel", "--url", "http://127.0.0.1:5000"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
for line in tun.stdout:
    print(line, end="")
    m = re.search(r"https://[-\w]+\.trycloudflare\.com", line)
    if m:
        print("\n\nOPEN →", f"{m.group(0)}/?k={TOKEN}\n", flush=True)
```

The cell stays open (the tunnel must keep running). Review happens in the
browser tab; the Colab cell is not touched again.

Colab, cell 3 — when done:

```python
!ls -la work/review/answers.db work/review/events.jsonl
!sqlite3 work/review/answers.db "select decision,count(*) from answers group by 1"
```

Then download `http://127.0.0.1:5000/export.csv` from the browser tab, or run
locally:

```bash
python scripts/review_export.py --db work/review/answers.db \
    --title-map work/lyrics/title_map.csv --out work/review/title_map_v2.csv
```

---

## 13. Implementation status

| Piece | State |
|---|---|
| `scripts/lyrics_review.py` (excerpt sheet) | done, 54 rows, candidate excerpts resolved |
| `scripts/review_build.py` (cards + URI check) | done, 54 cards, 54/54 audio objects verified |
| `scripts/review_app.py` (Flask) | done, tested locally: token gate, Range 206, answer/redirect, audit edit, export, restart-resume |
| `scripts/review_export.py` (v2 + report) | done, flags unanswered, overrides, duplicate sheets |
| Colab end-to-end | **not yet run** — pending the queue upload to GCS |
