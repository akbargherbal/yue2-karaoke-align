# A1 alignment report — a1suno

Model: `akbargherbal/wav2vec2-large-xlsr-53-arabic` @ `4ca1c9c6ac85`
Device: cpu · generated 2026-10-21T15:34:54Z

| song | words | coverage | on/off | intro | cues | warnings | player |
|---|---|---|---|---|---|---|---|
| `ajam_Lamiyat_Alshanfara_16082026_004_bb17d099` | 120/120 | 0.97 | 2.15 | ok | 24 line | 1 | [open](ajam_Lamiyat_Alshanfara_16082026_004_bb17d099.player.html) |

## Totals

- 1/1 songs aligned, 120 words
- 0 silent-intro violation(s), 1 song(s) with QA warnings

## Files

`<song>.words.json` is the source of truth; `.vtt`/`.srt` are line-level subtitles; `.player.html` is the karaoke page.
The silent-intro check independently verifies that word 0 waits for the vocal stem (the failure that disqualified MMS-1b in B2).

Subtitles are line-level by design. Word-level timing lives in the JSON and drives the player.
