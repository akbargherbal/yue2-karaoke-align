# Benchmark results

2 models x 2 songs. Full data: `bench_results.json`.
Consensus dev = mean per-song median |dstart| to the other models (lower is better).

| model | ok/total | consensus dev (s) | gold MAE (s) | on/off | coverage | mono | translit | sec/audio-min | params (M) | peak mem |
|---|---|---|---|---|---|---|---|---|---|---|
| `akbargherbal/wav2vec2-large-xlsr-53-arabic-quran-v_final` | 2/2 | 0.020 | — | 2.02 | 0.95 | 100% | none | 32.5 | 316 | 1756 MB |
| `akbargherbal/wav2vec2-large-xlsr-53-arabic` | 2/2 | 0.020 | — | 2.21 | 0.95 | 100% | none | 30.1 | 316 | 1264 MB |

## Per-song difficulty (mean pairwise |dstart| across models)

If this dominates the model differences, song choice matters more than model choice.

| song | mean pairwise (s) |
|---|---|
| `A_diction_mix_01-الوداع-والالتحاق-بالرفاق-الحقيقيين_462805571` | 0.020 |
| `A_diction_mix_02-تنزه-عما-ليس-فيه-الجلد-في-الظلام-والجوع_1053902951` | 0.020 |

## Ranking

*primary: gold MAE when available, else cross-model consensus (lower is better)*

1. `akbargherbal/wav2vec2-large-xlsr-53-arabic-quran-v_final` — consensus 0.020s, on/off 2.02, cover 0.95, 32.5s/min, 316M params
2. `akbargherbal/wav2vec2-large-xlsr-53-arabic` — consensus 0.020s, on/off 2.21, cover 0.95, 30.1s/min, 316M params

> Consensus deviation is a *proxy*: it measures agreement between
> models, not agreement with truth — two models can share a bias.
> Rank with it, then confirm the winner by ear or with `--gold` anchors.
> Quality and cost are two axes; a small model that ties on quality wins
> on efficiency (see the mem / params columns).

