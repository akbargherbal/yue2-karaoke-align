# A1 gap audit vs vocal stem

`delta_db` is the window's mean level minus the track's median vocal level.
Negative = quieter than singing. A gap counts as `skipped_singing` when >= 25% of it
sits within 6 dB of the median; otherwise it is an instrumental break.

| song | words | coverage | tail_s | tail_delta_db | gaps_gt2s | gaps_skipped | worst_gap_s | worst_gap_at | worst_gap_delta_db | worst_gap_verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| `Elissa - 1998 Baddy Doub - 06. Sho Ma Sar` | 181/181 | 0.953 | 10.0 | -26.0 | 6 | 5 | 27.321 | 14.961-42.282 | -22.9 | instrumental |
| `Elissa - 2004 Ahla Donya - 03.Kol Youm Beomre` | 298/298 | 0.84 | 51.6 | -27.5 | 4 | 3 | 20.801 | 85.185-105.986 | -10.7 | skipped_singing |
| `Elissa - 2006 Bastannak - 01.Bastanak` | 201/201 | 0.971 | 6.9 | -36.9 | 4 | 4 | 3.5 | 204.673-208.173 | -0.5 | skipped_singing |
| `06.Sidi Rohi.2003 - 04.Law Faker` | 143/143 | 1.0 | 0.0 | 0.0 | 4 | 3 | 18.841 | 87.305-106.146 | -40.1 | instrumental |
| `08.Hafla 2004 - 01.Law Faker` | 143/143 | 1.0 | -0.0 | 0.0 | 3 | 2 | 17.382 | 85.83-103.212 | -15.6 | instrumental |
| `08.Hafla 2004 - 02.Sidi Rohi` | 182/182 | 1.0 | -0.0 | 0.0 | 2 | 1 | 20.843 | 196.241-217.084 | -26.9 | instrumental |
| `10.Saharni El Shoq.2005 - 04.Tadri Lieh` | 250/250 | 0.931 | 18.2 | -51.1 | 3 | 2 | 10.761 | 150.952-161.713 | -15.4 | skipped_singing |
| `10.Saharni El Shoq.2005 - 13.Ya Hayat El-Roh` | 274/274 | 1.0 | 0.0 | 0.0 | 2 | 0 | 40.802 | 137.468-178.27 | -25.8 | instrumental |
| `15.The Best Of _Al Rawae3_.2010 - 04.Ya Hyat Al Roh` | 274/274 | 1.0 | 0.0 | 0.0 | 1 | 0 | 40.803 | 137.569-178.372 | -23.0 | instrumental |
| `15.The Best Of _Al Rawae3_.2010 - 12.Dehket El Donya` | 216/216 | 1.0 | 0.0 | 0.0 | 2 | 1 | 12.56 | 167.131-179.691 | -26.3 | instrumental |
