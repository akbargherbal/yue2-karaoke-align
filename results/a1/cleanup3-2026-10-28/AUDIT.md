# A1 gap audit vs vocal stem

`delta_db` is the window's mean level minus the track's median vocal level.
Negative = quieter than singing. A gap counts as `skipped_singing` when >= 25% of it
sits within 6 dB of the median; otherwise it is an instrumental break.

| song | words | coverage | tail_s | tail_delta_db | gaps_gt2s | gaps_skipped | worst_gap_s | worst_gap_at | worst_gap_delta_db | worst_gap_verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| `06.Sidi Rohi.2003 - 01.Ya Habibi Ta_ala` | 129/129 | 1.0 | -0.0 | 0.0 | 2 | 1 | 29.222 | 118.07-147.292 | -46.3 | instrumental |
| `08.Hafla 2004 - 06.Ya Habibi Ta_ala` | 129/129 | 1.0 | 0.0 | 0.0 | 7 | 7 | 4.041 | 301.796-305.837 | 3.1 | skipped_singing |
| `15.The Best Of _Al Rawae3_.2010 - 15.Ya Habibi Ta_ala` | 129/129 | 1.0 | -0.0 | 0.0 | 3 | 2 | 29.201 | 118.126-147.327 | -46.6 | instrumental |
