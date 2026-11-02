# Agent assurance ambiguity pilot

Harness, tasks, labeled code pool and raw verdicts for a pilot study:
**when several reviewers (LLMs) agree to merge agent-written code, are they right,
and does making them more "independent" help?**

Author: Moulinath Chakrabarty (independent researcher), moulinath@yahoo.com,
ORCID 0009-0002-3084-2930.

Paper: *When Reviewers Agree on the Wrong Reading* (preprint),
https://doi.org/10.5281/zenodo.23234730. Version 1.1.0 of this release; see `CHANGELOG.md`.

## Headline (pilot, read the limits)
Across gpt-5.1, Claude Haiku 5.5 and Claude Sonnet 5.5 as validators, no configuration
reliably accepted correct code more often than wrong code on the same task. Shared blind
spots were task-determined (T09, T05, T13). Five tasks, one implementer family, single-run
cells; this is directional, not significant. Full design, threats and limits:
`docs/experimental_design_v1.5.md`.

## Contents
- `harness.py`, `run_pilot.py`, `tasks.py`, `llm_clients.py`: pipeline and task oracles
- `generate_pool.py`, `analyze_results.py`, `analyze_discrimination.py`, `noise_floor.py`: pool and analysis
- `data/`: raw results (see `data/README.md`)
- `docs/`: design document and task pool description
- `README_harness.md`: detailed harness usage (mock mode is the default and costs nothing)

## Reproduce
Replay analysis needs no API key. Each command rebuilds one table of the paper:

- gpt-5.1 as reviewer (Table 4.1): `python3 analyze_discrimination.py --pool data/pool.csv --results data/pool_judged_gpt51.csv`
- Claude Haiku 5.5 as reviewer (Table 4.2): `python3 analyze_discrimination.py --pool data/pool.csv --results data/pool_judged_haiku.csv`
- Claude Sonnet 5.5 (subset): the same command with `data/pool_judged_sonnet.csv` and `data/pool_judged_sonnet2.csv`
- Reviewer noise (section 4.5): `python3 noise_floor.py --results data/pool_judged_haiku2.csv` (Haiku) and
  `python3 noise_floor.py --results data/pilot_results_l3a_gpt51.csv` (gpt-5.1)
Real runs need your own provider keys in environment variables; never commit keys.

## Disclosure
Claude (Anthropic) helped build this harness and several Claude models were validators in
the study. The raw data is released so that every number can be checked.

## License
MIT for code. Data in `data/` is released under CC BY 4.0.
