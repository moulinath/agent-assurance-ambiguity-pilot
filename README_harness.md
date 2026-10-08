# CL10 Pilot Harness

Reference implementation of the Phase 1 pilot pipeline from the experimental
design doc: implementer -> (optional clarifying-question exchange) -> hidden
oracle -> validators at varying independence levels -> aggregated
accept/reject decision -> compare to ground truth.

Tasks are insurance-contextualized (claims, underwriting, policy
administration) -- see `pilot_task_pool_insurance.md` for the human-readable
version of the 13 tasks, and `tasks.py` for the executable version with
hidden oracles.

## Files

- `tasks.py` -- the 13 pilot tasks (12 original + T13, the unresolved-intent case): unambiguous spec, ambiguous spec
  (ticket-history style), a `clarifying_answer_fn` simulating a requestor who
  knows the true intent but only answers what's asked, and a hidden oracle.
- `analyze_results.py` -- read-only analysis of a finished run (no API calls): data-quality checks,
  per-task table, `bad_merge` as a share of wrong-code runs with Wilson intervals, paired
  level-vs-level comparison, clarification behavior, and `hand_check.csv` (every accepted-wrong run,
  with the validator's full reasoning). Excludes runs whose oracle failure is `interface`/`crash`.
- `audit_oracle.py` -- re-runs the hidden oracles on code saved in a results CSV and labels each
  failure `interface` / `behavior` / `crash` (no API calls). Use it on any older results file.
- `llm_clients.py` -- pluggable model client interface (`AnthropicClient`,
  a stubbed `OpenAIClient` for a genuinely independent substrate, and
  `MockLLMClient` for zero-cost dry runs).
- `harness.py` -- the core pipeline: implementation phase (with the
  clarification fork), validation phase (independence levels L0-L3),
  decision aggregation (majority / unanimity / any-reject).
- `run_pilot.py` -- orchestrator that sweeps the pilot grid and writes
  `pilot_results.csv` and `clarification_recovery.csv`.

## The clarification fork (why it's here)

This is the piece that turns "does validator independence help" into "does
*alignment between requestor and implementer* help" -- which is the more
fundamental question. Concretely:

- **`ClarificationCondition.NOT_ALLOWED`**: implementer must commit to code
  in one shot, exactly like the original 13-task pilot spec.
- **`ClarificationCondition.ALLOWED`**: implementer may ask up to
  `MAX_CLARIFYING_TURNS` (default 2) questions first. Each question is
  answered by `task.clarifying_answer_fn` -- a simulated requestor who
  *does* know the true business intent, but only answers what's actually
  asked, and gives a non-committal answer if the question doesn't touch the
  real ambiguity.

This setup can produce four distinct outcomes worth telling apart in
analysis, not just "clarification helped" / "didn't help" — the harness
computes this automatically via `run_pilot.py::classify_failure_mode`:

1. **Recovery**: implementer asks the right question, gets a clear answer,
   applies it correctly. (T01, T04 in the mock script.)
2. **Wrong question asked**: implementer asks something that doesn't touch
   the actual ambiguity, so the requestor's answer doesn't resolve anything.
   (T05 in the mock script.)
3. **Right answer, not applied**: implementer asks the right question, gets
   a clear answer, but the answer doesn't make it into the code correctly.
   (T08 in the mock script.)
4. **Unresolved intent**: implementer asks exactly the right question, but
   the requestor genuinely has no resolved answer to give — the honest
   response is a hedge ("use your judgment for now, legal hasn't decided").
   No amount of asking fixes this, because the ambiguity was never actually
   resolved on the business side. (T13 in the mock script — see its entry
   in `tasks.py` and `pilot_task_pool_insurance.md`.)

Only (1) is "harmony between requestor and implementer" working as
intended. (2), (3), and (4) all look identical from the outside
("still wrong after clarification was allowed") but have different causes
and different fixes — (2) is a requirements-elicitation-skill problem on the
implementer's side, (3) is an instruction-following problem downstream of
understanding, and (4) is a problem no coding agent or validator chain can
solve at all, because it originates upstream of the entire evidence chain.
`clarification_recovery.csv` records the failure-mode classification and
full transcript for each task specifically so these can be distinguished
when coding real (non-mock) transcripts by hand.

## Independence levels (L0-L3)

See `harness.py` docstring and the design doc sec. 4 for full definitions.
Summary:

| Level | What differs from the implementer |
|---|---|
| L0 | Nothing -- same session, "check your own work" |
| L1 | Fresh session, same model family, same spec/tests |
| L2 | Different model family, same spec/tests |
| L3 | Different model family, independently re-derives understanding from raw spec, not shown the implementer's reasoning |

**L3 as implemented is "derive-then-judge", not independent derivation:** the validator is shown the
code in the same message as the instruction to derive the behavior itself, so its derivation can be
anchored on the implementation (see Before trusting real results, item 4).

Real L2/L3 runs require an actually different model family. `OpenAIClient`
in `llm_clients.py` is stubbed for this -- fill in credentials and swap it
into `build_real_clients()` / the validator factory in `run_pilot.py`
before treating L2/L3 results as meaningful. Running both implementer and
validator on the same provider (as `build_real_clients()` does by default)
only genuinely realizes L0/L1; do not report L2/L3 numbers from that
configuration.

## Validator verdicts: accept / reject / hold

Validators are no longer forced into a binary accept/reject choice. A third
verdict, **hold**, means "the evidence doesn't support confidently doing
either — flag for review." This matters because forcing a binary choice
hides exactly the information this study cares about: how confident was
this signal, really? The three decision rules now produce genuinely
different aggregated outcomes (see `harness.py::aggregate_decision`'s
docstring for the exact semantics of each), and every run is scored via a
six-way `decision_quality` classification (`correct_accept`, `bad_merge`,
`correct_reject`, `false_reject`, `safe_hold`, `costly_hold`) rather than a
flat correct/incorrect boolean. **`bad_merge`** — a confidently wrong
accept — is the outcome this whole study is ultimately trying to measure
the rate of.

Collapsing that six-way outcome into a single correct/incorrect number (for
a headline P(correct decision) figure) requires a policy decision about how
to score a hold — there's no single objectively right answer. The default
policy is implemented and explicitly flagged as a policy choice, not a
settled fact, in `harness.py::decision_is_correct_default_policy` — read
that docstring before quoting a single accuracy number in any writeup.

## Running it

```bash
# Zero-cost dry run -- validates the harness logic with scripted mock models.
# Always run this first, and after any change to tasks.py or harness.py.
python run_pilot.py --mode mock

# Real run on Anthropic only (costs money, requires ANTHROPIC_API_KEY).
# Only genuinely realizes independence levels L0/L1 -- see above.
pip install anthropic
export ANTHROPIC_API_KEY=...
python run_pilot.py --mode real --model claude-sonnet-5

# Real run on OpenAI only (needs OPENAI_API_KEY, no Anthropic account).
# Every role uses one provider, so this tests L0 vs L1 and L3's
# independent-derivation prompt, NOT substrate independence; do not report
# L2 from this configuration as substrate-independent.
pip install openai
export OPENAI_API_KEY=...
python run_pilot.py --mode real --provider openai --tasks T04,T13 --independence-levels L0,L3 --chain-lengths 1      # preview only
python run_pilot.py --mode real --provider openai --tasks T04,T13 --independence-levels L0,L3 --chain-lengths 1 --yes  # spends money

# Two providers (L2/L3 genuinely substrate-independent): needs both keys.
python run_pilot.py --mode real --cross-provider --tasks T04,T13 --independence-levels L0,L3 --chain-lengths 1 --yes
```

`OpenAIClient` has been exercised only against a local fake of the `openai`
package (plumbing check), not the live API -- the first real call is its real
test. Pass `--openai-model` to use a model other than the `gpt-5.1` default.

Repeats and the per-decision table:

```bash
# Each task is run end to end N times (fresh implementer draw each time).
python3 run_pilot.py --mode real --provider openai --repeats 3 --independence-levels L0,L1,L3 --chain-lengths 1          # preview
python3 run_pilot.py --mode real --provider openai --repeats 3 --independence-levels L0,L1,L3 --chain-lengths 1 --yes    # spends money
```

Real models are non-deterministic (T13's L3 verdict flipped between two identical
runs), so a single run is one sample. The run ends with a *per-decision table*
(majority rule only, 95% Wilson intervals). Use that table, not the all-rows
counts: the three decision rules re-score the same validator verdicts, so the
all-rows counts triple-count every case. With one validator per chain, chain
length 1 vs 3 also differ only in how many validators you pay for.

New CSV columns: `repeat`; `validator_parse_ok` (False means the reply could not
be parsed and was defaulted to hold -- not a genuine hold); `validator_reasoning`;
`code_under_test`; `ambiguity_flagged` and `accepted_despite_flag`. The last two
are a keyword heuristic (`reasoning_flags_ambiguity` in `run_pilot.py`): hand-check
the rows it selects before quoting any number from it.

Other options: `--recovery-only` runs only the three implementation arms per task (unambiguous /
ambiguous / ambiguous+clarification) and skips the validators; it writes only the recovery CSV, so pass
`--recovery-out` to avoid overwriting earlier output. It is about 10x cheaper and is the right way to
re-test the clarification fork or to check a task change before a full run.

**Ambiguous tickets carry a pinned signature.** `harness.py::interface_text` appends the call signature
(taken from the first backticked `name(...)` in the unambiguous spec) and, where set, a task's
`interface_note` (e.g. T12's return shape) to every ambiguous ticket. The ambiguity under study is
behavioral; the call/return contract is not. Without this, many "wrong" implementations were just
interface mismatches (18 of 28 in the first run). Pinning can leak the answer (T04 names `max_retries`;
T13 is a no-argument function), so re-check each task's wrong-code rate after changing a signature.

Typical workflow (macOS/Linux shown; use `python`/`pip` on Windows):

```bash
python3 run_pilot.py --mode real --provider openai --recovery-only --recovery-out recovery_check.csv --yes   # cheap gate
python3 run_pilot.py --mode real --provider openai --repeats 3 --tasks T01,T02,T03,T04,T05,T06,T08,T09,T11,T12,T13 \
    --independence-levels L0,L1,L3 --chain-lengths 1 --yes
python3 analyze_results.py                                  # read the output, then open hand_check.csv
python3 audit_oracle.py --results pilot_results.csv         # classify oracle failures in any saved run
```

Output:
- `pilot_results.csv` -- one row per (task, independence_level, chain_length,
  decision_rule), for the ambiguous+no-clarification condition (this isolates
  the independence/chain-length/decision-rule variables against a fixed
  evidence base, per the design doc's factorial structure).
- `clarification_recovery.csv` -- one row per (task, repeat), comparing
  unambiguous / ambiguous-no-clarification / ambiguous-with-clarification
  outcomes, with the full question-answer transcript, the final raw implementer reply
  (`final_raw_response`), `asked_but_unparsed` (looks like a question that was not recognized as one),
  and `*_failure_kind` (`pass` / `interface` / `behavior` / `crash`) for each arm.

Columns added to `pilot_results.csv` beyond the original set: `repeat`; `validator_parse_ok` (False means
a reply could not be parsed and was defaulted to hold -- not a genuine hold); `validator_reasoning`;
`code_under_test`; `ambiguity_flagged` and `accepted_despite_flag` (a KEYWORD SCREEN in
`run_pilot.py::reasoning_flags_ambiguity` -- hand-check before quoting any number from it);
`oracle_failure_kind` and `oracle_failed_cases` (why the code failed the oracle).

`correct_without_asking` (clarification-arm label): the code was wrong without clarification and right
with it, but NO question was asked. That is sampling variation, not a recovery; `recovered` now requires
at least one question.

## Replay, L3a, the labeled pool, and discrimination (added v1.4)

**IMPORTANT: `run_pilot.py` defaults to `--mode mock`.** A real run needs `--mode real` (plus `--provider openai --yes`). Without it you get plausible-looking mock numbers (L0 accepts everything, L1/L3 hold everything, L3a rejects everything) and spend nothing. A real run prints a "Real-mode pre-flight estimate" block first; if you do not see it, you are in mock.

- **L3a** (`--independence-levels L3a`): the validator derives the expected behavior in a separate call *before* seeing any code, then judges the code against its own derivation. Two calls per validator.
- **Replay** (`--replay-code file.csv`): no implementer calls; saved `code_under_test` is re-scored by the oracle and only validators run, so the evidence is held constant across levels. `--replay-only-wrong` / `--replay-only-correct` filter by oracle label (the second is the control: it shows how often a level holds or rejects correct code).
- **Validator repeats** (`--validator-repeats N`): re-run validators on the same code to measure verdict noise (about 1 in 5 verdicts flipped in the pilot).
- **Labeled pool** (`generate_pool.py`): draws several implementations per task from the ambiguous and the unambiguous ticket, labels each with the oracle, writes `pool.csv`. Judge it with replay:

```bash
python3 generate_pool.py            # preview, no spend
python3 generate_pool.py --yes      # ~60 implementation calls
python3 run_pilot.py --mode real --provider openai --replay-code pool.csv --tasks T02,T04,T05,T09,T13 \
    --independence-levels L0,L1,L3,L3a --chain-lengths 1 --out pool_judged.csv --yes
python3 analyze_discrimination.py --pool pool.csv --results pool_judged.csv
```

`analyze_discrimination.py` reports, per level and task, P(accept | wrong) against P(accept | correct) on the *same task*, plus how often correct code is rejected or held. A validator that rejects everything lowers `bad_merge` without discriminating, so read both.

**Different-family validators (added v1.5).** `--validator-provider openrouter --validator-model <id>` makes every validator a model from another family (ids from openrouter.ai/models; needs `OPENROUTER_API_KEY` and `pip install openai`). Use it with `--replay-code` so the saved code is judged with no implementer calls. `--validator-provider gemini --gemini-model <id>` uses the native Gemini API (`pip install google-genai`, `GEMINI_API_KEY`; run `python3 gemini_check.py` to list ids). Google's newer `AQ.`-prefix keys returned 401 on every route when we tried them, so OpenRouter was used for the pilot. The pre-flight text for these runs still starts with an OpenAI-only block; trust the line that says "VALIDATORS: ...".

**Oracles are re-run on read.** `analyze_results.py` and `analyze_discrimination.py` re-score saved code with the *current* `tasks.py` oracle, so an oracle fix needs no re-run of any API call. (`analyze_results.py --no-relabel` shows the stored labels.) This matters because the T08 oracle used to require a particular storage layout the ticket never specified; it now checks only that an existing quote is returned. Read each task's oracle failure messages before trusting a task.

New scripts: `generate_pool.py`, `analyze_discrimination.py`. Pilot status and the Run C results are in `CL10_experimental_design.md` §8a.

## Before trusting real results

1. **The code runner is not a security sandbox.** `_run_oracle_safely` in
   `harness.py` runs candidate code in a separate subprocess with a hard
   timeout (an infinite loop is killed), but it has no network or filesystem
   isolation. You are executing LLM-generated code on your own machine. For
   larger runs, use an isolated container with no network access.
2. **Wrong-code rates are task-determined, and repeats are not independent evidence.** In the fixed
   pilot run each task was wrong on all 3 repeats or on none, so 18 wrong-code runs carried about 6
   independent cases. The printed Wilson intervals treat task-runs as independent and are therefore too
   narrow; use them descriptively. Precision on between-level contrasts comes from adding TASKS that
   produce behavior-wrong code, not from more repeats.
2b. **Confirm the ≥20% wrong-but-plausible threshold per task** (design doc
   sec. 3c) using real model outputs before promoting any task from this
   13-task pilot into the full 120-task pool. Run each task's ambiguous
   variant through several different implementer models/seeds and check
   the oracle-fail rate directly.
3. **Get real L2/L3 independence** by wiring in a second model provider --
   see the OpenAIClient stub. Reporting L2/L3 results using the same
   provider for implementer and validator misrepresents what's actually
   being measured.
4. **Strengthen L3's "independent derivation" framing.** The current
   reference implementation reframes the prompt to ask the validator to
   reason independently before judging, but still shows it the implementer's
   code directly. A stronger L3 would have the validator produce its own
   full implementation first and compare *behaviors* (e.g. run both against
   a shared set of inputs and diff outputs) rather than showing it the
   implementer's code at all -- left as an extension point since it requires
   the validator to also be run through the sandboxed execution path.
5. **T13's ground truth is a modeling assumption, not a settled fact --
   report it as such.** T13 (grace period) is the one task whose hidden
   oracle encodes a researcher-assumed conservative default rather than
   something confirmable from the task's own spec/clarification history.
   This is deliberate and necessary to make an "unresolved intent" task
   scoreable at all, but it means T13's result is conditional on that
   assumed default matching whatever a real compliance/legal decision (a
   "hard gate") would eventually land on. Call this out explicitly in any
   writeup rather than presenting T13's ground truth with the same
   epistemic status as T01-T12's.
6. **Scoring a HOLD as correct or incorrect is a policy choice, not a
   settled fact -- report it as such.** See the "Validator verdicts" section
   above and `harness.py::decision_is_correct_default_policy`'s docstring.
   The default policy counts `safe_hold` as correct and `costly_hold` as
   incorrect, which rewards avoiding a bad merge above all else; a different,
   equally defensible policy excludes HOLD from the correct/incorrect
   calculation entirely. Report the full six-way `decision_quality`
   breakdown alongside whatever single accuracy number this policy produces,
   the same way T13's ground truth (item 5) needs its own explicit call-out
   rather than being folded silently into an aggregate.
