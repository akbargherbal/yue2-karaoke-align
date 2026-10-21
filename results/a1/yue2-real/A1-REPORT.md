# A1 alignment report — a1real

Model: `akbargherbal/wav2vec2-large-xlsr-53-arabic` @ `4ca1c9c6ac85`
Device: cpu · generated 2026-10-21T16:10:25Z

| song | words | coverage | on/off | intro | cues | warnings | player |
|---|---|---|---|---|---|---|---|
| `01-العذل-والمطامع-وفلسفة-الرزق_3064210` | 172/172 | 0.94 | 2.41 | ok | 32 line | 2 | [open](01-العذل-والمطامع-وفلسفة-الرزق_3064210.player.html) |
| `02-استيداع-قمر-بغداد-ولوعة-الوداع_1072565065` | 125/125 | 1.00 | 2.89 | ok | 24 line | 1 | [open](02-استيداع-قمر-بغداد-ولوعة-الوداع_1072565065.player.html) |
| `03-تجرع-مرارة-البين-ووخز-الندم_1891521623` | 148/148 | 0.98 | 3.02 | ok | 26 line | 1 | [open](03-تجرع-مرارة-البين-ووخز-الندم_1891521623.player.html) |
| `04-مناجاة-ديار-الأنس-ورجاء-التلاقي_2395913353` | 125/125 | 0.91 | 1.73 | ok | 24 line | 1 | [open](04-مناجاة-ديار-الأنس-ورجاء-التلاقي_2395913353.player.html) |

## Totals

- 4/4 songs aligned, 570 words
- 0 silent-intro violation(s), 4 song(s) with QA warnings

## Files

`<song>.words.json` is the source of truth; `.vtt`/`.srt` are line-level subtitles; `.player.html` is the karaoke page.
The silent-intro check independently verifies that word 0 waits for the vocal stem (the failure that disqualified MMS-1b in B2).

Subtitles are line-level by design. Word-level timing lives in the JSON and drives the player.
