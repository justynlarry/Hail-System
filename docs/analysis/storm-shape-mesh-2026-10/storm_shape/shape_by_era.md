# Shape by era

Local start year decides the era. `ok` = at least 5 reports, at least 30 min, linear-fit R² >= 0.5. Cells are median [Q1, Q3].

## Volume

| Era | years | analysed reports | reports/yr | clusters | storm-sized (>=5 rep, >=30 min) | ok fits | ok share of storm-sized | median reports/ok storm |
|---|---|---|---|---|---|---|---|---|
| 2004-2012 | 9.0 | 2621 | 291 | 913 | 99 | 52 | 53% | 7 |
| 2013-2019 | 7.0 | 2406 | 344 | 790 | 103 | 64 | 62% | 8 |
| 2020-present | 6.8 | 2745 | 405 | 791 | 106 | 64 | 60% | 8 |

## Shape distributions per era (ok-fit storms)

| Metric | 2004-2012 (n=52) | 2013-2019 (n=64) | 2020-present (n=64) | Kruskal-Wallis H | p |
|---|---|---|---|---|---|
| n_reports | 7.0 [6.0, 11.0] | 8.0 [6.0, 11.0] | 8.0 [6.0, 13.0] | 1.5 | 0.462 |
| duration (min) | 79.0 [57.0, 104.0] | 79.5 [54.8, 114.8] | 70.0 [50.0, 95.2] | 1.3 | 0.516 |
| along length (km) | 41.3 [28.9, 60.3] | 42.0 [28.3, 56.4] | 40.6 [29.9, 52.4] | 0.1 | 0.945 |
| cross SD (km) | 3.8 [2.8, 5.7] | 3.6 [2.0, 5.3] | 4.0 [2.3, 5.9] | 1.7 | 0.436 |
| cross p90 (km) | 5.3 [3.7, 7.8] | 4.5 [2.8, 7.3] | 5.2 [3.3, 7.5] | 1.4 | 0.488 |
| aspect (length / 2*p90) | 3.7 [2.7, 6.0] | 4.4 [3.0, 6.6] | 4.4 [3.1, 6.2] | 1.5 | 0.484 |
| aspect (SD along / SD cross) | 3.3 [2.9, 5.3] | 4.1 [2.8, 6.4] | 4.0 [2.7, 5.7] | 1.7 | 0.426 |
| PCA elongation | 3.3 [2.9, 5.3] | 4.2 [2.8, 6.6] | 4.2 [2.7, 5.8] | 1.4 | 0.487 |
| wind share of reports | 0.0 [0.0, 0.1] | 0.0 [0.0, 0.1] | 0.0 [0.0, 0.3] | 3.3 | 0.188 |

## Report-count confound (aspect and length grow with the number of reports)

Spearman-style check without scipy: median aspect (length / 2*p90) by report-count bin, pooled and by era.

| reports | pooled n | pooled median aspect | 2004-2012 n / median | 2013-2019 n / median | 2020-present n / median |
|---|---|---|---|---|---|
| 5-7 | 84 | 4.51 | 28 / 3.32 | 29 / 4.60 | 27 / 4.72 |
| 8-12 | 55 | 4.24 | 13 / 4.24 | 23 / 4.73 | 19 / 3.11 |
| 13-25 | 32 | 3.79 | 10 / 3.22 | 9 / 3.74 | 13 / 4.29 |
| 26+ | 9 | 4.87 | 1 / 5.52 | 3 / 3.33 | 5 / 4.87 |

## Is pooling justified?

For the shape question, yes, with limits. Across the three eras no shape metric differs at conventional significance
(all Kruskal-Wallis p >= 0.19; along length p = 0.95). Medians are close: along length 41 / 42 / 41 km, cross p90 5.3 / 4.5 / 5.2 km,
aspect 3.7 / 4.4 / 4.4. Aspect does not trend with report count either (table above), so rising density has not visibly
changed the measured shape of the storms that pass the fit filter.

What did change: reports per year rose from about 291 to 344 to 405, and wind's share of all analysed reports rose from about 11% to 25%
in 2020-present (all clusters, not only ok storms). Among ok storms the wind share is not significantly different (p = 0.19), but that is
a test on 52 to 64 storms per era and has little power to detect a modest shift. The earliest era has the lowest aspect median (3.7 against 4.4),
which could be real or noise; with 52 storms I can not tell. If a downstream decision turns on era-specific shape, repeat it with hail-only reports.
