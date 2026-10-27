# A1 alignment report — baghdad_moon

Model: `akbargherbal/wav2vec2-large-xlsr-53-arabic` @ `4ca1c9c6ac85`
Device: cpu · generated 2026-10-21T16:16:12Z

| song | words | coverage | on/off | intro | cues | warnings | player |
|---|---|---|---|---|---|---|---|
| `baghdad_moon` | 125/125 | 0.98 | 2.52 | ok | 24 line | 1 | [open](baghdad_moon.player.html) |

## Totals

- 1/1 songs aligned, 125 words
- 0 silent-intro violation(s), 1 song(s) with QA warnings

## Files

`<song>.words.json` is the source of truth; `.vtt`/`.srt` are line-level subtitles; `.player.html` is the karaoke page.
The silent-intro check independently verifies that word 0 waits for the vocal stem (the failure that disqualified MMS-1b in B2).

Subtitles are line-level by design. Word-level timing lives in the JSON and drives the player.
