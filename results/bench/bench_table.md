# Benchmark results

1 models x 1 songs. Full data: `bench_results.json`.
Consensus dev = mean per-song median |dstart| to the other models (lower is better).

| model | ok/total | consensus dev (s) | gold MAE (s) | on/off | coverage | mono | translit | sec/audio-min | params (M) | peak mem |
|---|---|---|---|---|---|---|---|---|---|---|
| `jonatasgrosman/wav2vec2-large-xlsr-53-arabic` | 1/1 | — | — | 1.95 | 0.95 | 100% | none | 30.3 | 316 | 1688 MB |

## Per-song difficulty (mean pairwise |dstart| across models)

If this dominates the model differences, song choice matters more than model choice.

| song | mean pairwise (s) |
|---|---|

## Ranking

*primary: gold MAE when available, else cross-model consensus (lower is better)*

1. `jonatasgrosman/wav2vec2-large-xlsr-53-arabic` — single-model run (no consensus yet), on/off 1.95, cover 0.954, 30.3s/min, 316M params

> Consensus deviation is a *proxy*: it measures agreement between
> models, not agreement with truth — two models can share a bias.
> Rank with it, then confirm the winner by ear or with `--gold` anchors.
> Quality and cost are two axes; a small model that ties on quality wins
> on efficiency (see the mem / params columns).

