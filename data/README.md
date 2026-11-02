Raw result files from the pilot. Released under CC BY 4.0.
Every file below is a real model run; no mock output is included (v1.1.0).

| File | Rows | Model(s) | What it is | Backs |
|---|---|---|---|---|
| pool.csv | 60 | gpt-5.1 (implementer) | Labelled implementation pool: task, arm, oracle label, code | All tables |
| pool_judged_gpt51.csv | 600 | gpt-5.1 | Reviewer verdicts on the pool, levels L0, L1, L3, L3a | Table 4.1 |
| pool_judged_haiku.csv | 600 | Claude Haiku 5.5 | Reviewer verdicts on the pool, levels L0, L1, L3, L3a | Table 4.2 |
| pool_judged_haiku2.csv | 1,200 | Claude Haiku 5.5 | Same, two runs per sample | Haiku noise (4.5) |
| pool_judged_sonnet.csv | 180 | Claude Sonnet 5.5 | Verdicts on T02, T04, T09 at L0 and L3 | Table 4.3, 4.4 |
| pool_judged_sonnet2.csv | 120 | Claude Sonnet 5.5 | Verdicts on T05, T13 at L0 and L3 | Table 4.3, 4.4 |
| trial_or.csv | 120 | Claude Haiku 5.5 | Trial run on T04 only, all four levels | Not used in the paper |
| pilot_results_l3a_gpt51.csv | 432 | gpt-5.1 | End-to-end run, two reviewer runs per sample | gpt-5.1 noise (4.5) |
| pilot_results.csv | 297 | gpt-5.1 | End-to-end pilot run (implementer and reviewer), L0, L1, L3 | Earlier pilot run |
| clarification_recovery.csv | 33 | gpt-5.1 | Implementer runs with clarifying questions allowed (final run) | "No question asked" (section 5) |
| clarification_recovery_v2.csv | 13 | gpt-5.1 | Earlier clarification run | Same |
| clarification_recovery_v3.csv | 13 | gpt-5.1 | Earlier clarification run | Same |

The three clarification files hold 59 runs where the implementer was allowed to ask; it asked
in none of them. `pool.csv` stores labels from the original oracle; the analysis scripts
re-label every sample with the current oracle at run time (five T08 samples change; T08 is
not among the five analysed tasks).
