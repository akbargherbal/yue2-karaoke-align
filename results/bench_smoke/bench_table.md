# Benchmark results

2 models x 2 songs. Full data: `bench_results.json`.
Consensus dev = mean per-song median |dstart| to the other models (lower is better).

| model | ok/total | consensus dev (s) | gold MAE (s) | on/off | coverage | mono | translit | sec/audio-min | params (M) | peak mem |
|---|---|---|---|---|---|---|---|---|---|---|
| `jonatasgrosman/wav2vec2-large-xlsr-53-arabic` | 2/2 | 2.650 | — | 2.08 | 0.98 | 100% | none | 15.1 | 316 | 1529 MB |
| `elgeish/wav2vec2-large-xlsr-53-arabic` | 2/2 | 2.650 | — | 1.83 | 0.96 | 100% | buckwalter | 16.2 | 316 | 1285 MB |

## Per-song difficulty (mean pairwise |dstart| across models)

If this dominates the model differences, song choice matters more than model choice.

| song | mean pairwise (s) |
|---|---|
| `ajam_amro_bin_kalthoum_20082026_007_5ee592b5` | 5.280 |
| `ajam_Lamiyat_Alshanfara_16082026_004_bb17d099` | 0.020 |

## Ranking

*primary: gold MAE when available, else cross-model consensus (lower is better)*

1. `jonatasgrosman/wav2vec2-large-xlsr-53-arabic` — consensus 2.650s, on/off 2.08, cover 0.982, 15.1s/min, 316M params
2. `elgeish/wav2vec2-large-xlsr-53-arabic` — consensus 2.650s, on/off 1.83, cover 0.96, 16.2s/min, 316M params

> Consensus deviation is a *proxy*: it measures agreement between
> models, not agreement with truth — two models can share a bias.
> Rank with it, then confirm the winner by ear or with `--gold` anchors.
> Quality and cost are two axes; a small model that ties on quality wins
> on efficiency (see the mem / params columns).

