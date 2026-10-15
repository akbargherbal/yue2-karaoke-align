# Benchmark results

6 models x 8 songs. Full data: `bench_results.json`.
Consensus dev = mean per-song median |dstart| to the other models (lower is better).

| model | ok/total | consensus dev (s) | gold MAE (s) | on/off | coverage | mono | translit | sec/audio-min | params (M) | peak mem |
|---|---|---|---|---|---|---|---|---|---|---|
| `jonatasgrosman/wav2vec2-large-xlsr-53-arabic` | 8/8 | 0.143 | — | 2.37 | 0.97 | 100% | none | 28.2 | 316 | 1816 MB |
| `elgeish/wav2vec2-large-xlsr-53-arabic` | 8/8 | 0.776 | — | 2.04 | 0.95 | 100% | buckwalter | 30.6 | 316 | 1348 MB |
| `rabah2026/wav2vec2-large-xlsr-53-arabic-quran-v_final` | 8/8 | 0.133 | — | 2.39 | 0.96 | 100% | none | 27.6 | 316 | 1341 MB |
| `MostafaMaroof/wav2vec2-arabic-phoneme-asr` | 8/8 | 1.104 | — | 0.44 | 0.96 | 100% | buckwalter | 27.8 | 316 | 1274 MB |
| `facebook/mms-1b-all` | 8/8 | 0.240 | — | 2.25 | 1.00 | 100% | none | 66.5 | 965 | -1203 MB |
| `IbrahimAmin/egyptian-arabic-wav2vec2-xlsr-53` | 8/8 | — | — | 479.22 | 0.97 | 100% | none | 27.1 | 316 | 1330 MB |

## Per-song difficulty (mean pairwise |dstart| across models)

If this dominates the model differences, song choice matters more than model choice.

| song | mean pairwise (s) |
|---|---|
| `A_diction_mix_04-القطا-ومضجع-العراء_3770232818` | 2.244 |
| `B_consonants_04-القطا-ومضجع-العراء_1437953371` | 2.146 |
| `A_diction_mix_06-ليلة-الرعب_1743552974` | 0.648 |
| `A_diction_mix_01-الوداع-والالتحاق-بالرفاق-الحقيقيين_462805571` | 0.342 |
| `A_diction_mix_03-مشهد-الذئاب_2019749659` | 0.214 |
| `A_diction_mix_02-تنزه-عما-ليس-فيه-الجلد-في-الظلام-والجوع_1053902951` | 0.204 |
| `B_consonants_03-مشهد-الذئاب_2867769501` | 0.204 |
| `A_diction_mix_05-الطريد-وهموم-ملازمة-العزة-والزهد_552458420` | 0.142 |

## Ranking

*primary: gold MAE when available, else cross-model consensus (lower is better)*

1. `rabah2026/wav2vec2-large-xlsr-53-arabic-quran-v_final` — consensus 0.133s, on/off 2.39, cover 0.963, 27.6s/min, 316M params
2. `jonatasgrosman/wav2vec2-large-xlsr-53-arabic` — consensus 0.143s, on/off 2.37, cover 0.971, 28.2s/min, 316M params
3. `facebook/mms-1b-all` — consensus 0.240s, on/off 2.25, cover 1.0, 66.5s/min, 965M params
4. `elgeish/wav2vec2-large-xlsr-53-arabic` — consensus 0.776s, on/off 2.04, cover 0.954, 30.6s/min, 316M params
5. `MostafaMaroof/wav2vec2-arabic-phoneme-asr` — consensus 1.104s, on/off 0.44, cover 0.957, 27.8s/min, 316M params
6. `IbrahimAmin/egyptian-arabic-wav2vec2-xlsr-53` — single-model run (no consensus yet), on/off 479.22, cover 0.974, 27.1s/min, 316M params

> Consensus deviation is a *proxy*: it measures agreement between
> models, not agreement with truth — two models can share a bias.
> Rank with it, then confirm the winner by ear or with `--gold` anchors.
> Quality and cost are two axes; a small model that ties on quality wins
> on efficiency (see the mem / params columns).

