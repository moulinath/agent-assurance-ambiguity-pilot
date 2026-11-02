# Changelog

## 1.1.0 (2026-11-02)

Data
- Added `data/pool_judged_gpt51.csv`: the gpt-5.1 reviewer verdicts behind the paper's gpt-5.1
  table. They were missing from 0.1.0.
- Added `data/pilot_results_l3a_gpt51.csv`: two reviewer runs per sample, the source of the
  gpt-5.1 noise figure.
- Replaced `data/pilot_results.csv` and `data/clarification_recovery.csv`, which in 0.1.0 were
  mock-mode outputs (`mock-validator`, `mock-implementer`), with the real gpt-5.1 runs.
- Added `data/clarification_recovery_v2.csv` and `data/clarification_recovery_v3.csv`, the two
  earlier clarification runs (59 runs in total across the three files).
- Rewrote `data/README.md` with one line per file: model, rows and the table it backs.

Code
- `run_pilot.py`: the bad_merge summary now divides by wrong-code rows instead of all rows. No
  number in the paper or any post came from this printout; the paper's tables come from
  `analyze_discrimination.py`, which was already correct.

Docs and metadata
- README links the paper DOI (10.5281/zenodo.23234730) and gives one command per table.
- `CITATION.cff` and `.zenodo.json` set to 1.1.0 and link the paper.

No change to the harness, tasks, oracles, pool or analysis scripts.

## 0.1.0 (2026-10-08)

First release.
