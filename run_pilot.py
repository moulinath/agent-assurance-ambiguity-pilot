"""
Pilot run orchestrator.

Usage:
    python run_pilot.py --mode mock            # zero-cost dry run, validates harness logic
    python run_pilot.py --mode real --model claude-sonnet-4-6   # real API calls (costs money)

Sweeps the full pilot grid:
    13 tasks x 2 ambiguity conditions x 2 clarification conditions x
    4 independence levels x chain lengths {1,3} x 3 decision rules

Chain length 5 and the mixed L0+L3 secondary condition are deliberately left
out of the pilot sweep (design doc scopes those for the full 120-task study,
not the 13-task pilot) -- add them back once the pilot confirms the harness
and the ambiguity manipulation both work as intended.

Writes one row per (task, condition-combo, decision_rule) to pilot_results.csv,
matching the RunRecord field names in harness.py, plus a separate
clarification_recovery.csv that isolates the effect the user asked about: how
often clarification turns a would-be-wrong implementation into a correct one,
and how often it doesn't (because the requestor-side answer didn't actually
resolve anything either).
"""

from __future__ import annotations
import argparse
import csv
import itertools
import sys
from typing import List, Dict, Callable

from tasks import ALL_TASKS, TASKS_BY_ID, HEDGE_PREFIX
from llm_clients import AnthropicClient, MockLLMClient, LLMClient
from harness import (
    run_implementation, run_validator, aggregate_decision,
    decision_quality, decision_is_correct_default_policy,
    AmbiguityCondition, ClarificationCondition, IndependenceLevel, DecisionRule, Verdict,
    classify_oracle_failure,
)


_AMBIGUITY_MARKERS = (
    "ambigu", "unclear", "not clear", "under-specified", "underspecified",
    "under specified", "not specified", "doesn't specify", "does not specify",
    "doesn’t specify", "not defined", "unspecified", "open to interpretation",
    "multiple interpretations", "two readings", "either interpretation",
)


def reasoning_flags_ambiguity(text: str) -> bool:
    """
    Crude keyword heuristic: does a validator's reasoning mention an
    ambiguity / under-specification in the spec? It is a SCREENING flag only.
    It can false-positive (e.g. "no ambiguity here") and false-negative
    (an ambiguity described without any of these words). Before quoting any
    `ambiguity_flagged` / `accepted_despite_flag` number, hand-check the rows
    it selects (read `validator_reasoning`) and report the hand-coded result.
    """
    t = (text or "").lower()
    return any(m in t for m in _AMBIGUITY_MARKERS)


def wilson_interval(k: int, n: int, z: float = 1.96):
    """95% Wilson score interval for a proportion k/n. Returns (lo, hi)."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def classify_failure_mode(outcome_amb_noclar, outcome_amb_clar) -> str:
    """
    Four-way classification of the clarification-allowed outcome, per design
    doc sec. 9a. Distinguishes causes that all look identical from the
    outside ("still wrong after clarification was allowed") but imply
    different fixes:

      - "not_applicable" -- already correct without clarification; this
        task doesn't test recovery at all.
      - "recovered" -- was wrong without clarification, correct with it, AND at
        least one clarifying question was actually asked.
      - "correct_without_asking" -- was wrong without clarification, correct with
        it, but NO question was asked: sampling variation, not a recovery.
      - "no_clarification_sought" -- clarification was allowed but the
        implementer never asked anything.
      - "wrong_question" -- a question was asked, but every answer received
        was None (the clarifying_answer_fn judged the question irrelevant
        to the actual ambiguity).
      - "unresolved_intent" -- at least one answer carries the HEDGE_PREFIX
        marker: the question WAS on point, but the requestor genuinely had
        no resolved answer to give (see T13).
      - "answer_not_applied" -- at least one real, non-hedge, non-None
        answer was given, yet the implementation is still wrong -- an
        instruction-following failure, not an understanding failure.
    """
    if outcome_amb_noclar.ground_truth_correct:
        return "not_applicable"
    if outcome_amb_clar.ground_truth_correct:
        # Correct with clarification allowed. Only a RECOVERY if the implementer
        # actually asked something; with zero questions asked the difference from
        # the no-clarification arm is just run-to-run variation in the draw.
        if not outcome_amb_clar.clarifying_transcript:
            return "correct_without_asking"
        return "recovered"

    transcript = outcome_amb_clar.clarifying_transcript
    if not transcript:
        return "no_clarification_sought"

    answers = [a for _, a in transcript]
    if all(a is None for a in answers):
        return "wrong_question"
    if any(a is not None and a.startswith(HEDGE_PREFIX) for a in answers):
        return "unresolved_intent"
    return "answer_not_applied"


def build_mock_clients():
    """
    Scripted mock behavior demonstrating the three outcomes that matter for
    the clarification-fork story:
      - T04 (retry off-by-one): WRONG under ambiguous+no-clarification,
        CORRECT under ambiguous+clarification (because the requestor's answer
        directly resolves the ambiguity).
      - T05 (tie-break): WRONG under ambiguous+no-clarification even with
        clarification requested-but-not-asked-about-the-right-thing -- to
        illustrate that clarification only helps if the implementer asks
        about the actual ambiguous point, not just "any" question.
      - T08 (idempotent quote): the requestor GIVES a clear, correct answer
        when asked -- but the scripted mock implementer still doesn't apply
        it to the code. This models a distinct and equally real failure mode
        from T05's: it's not that clarification was unavailable or that the
        wrong question was asked, it's that asking-and-receiving-a-good-answer
        does not guarantee the answer gets correctly incorporated. In a real
        (non-mock) run this shows up as an instruction-following failure
        rather than an ambiguity-resolution failure, and the two should be
        coded separately when analyzing real transcripts -- "clarification
        happened and was clear" vs "clarification happened and was applied"
        are different claims.
      - T05 (tie-break): the mock implementer asks an irrelevant question
        ("what language should I use?") that never touches the actual
        ambiguity, so clarification is available but doesn't help -- modeling
        the "wrong question asked" failure mode, distinct from T08's
        "right answer, not applied" failure mode above.
    Unscripted tasks fall back to a correct-under-unambiguous /
    wrong-under-ambiguous default via the naive implementer script below.
    """

    def naive_implementer(task_id):
        def _impl(spec_text, is_ambiguous, got_clarification):
            # Extremely naive per-task "buggy vs correct" toy implementations,
            # only meant to exercise the harness plumbing, NOT to model real
            # model behavior. Replace with real API calls for actual results.
            correct_map = {
                "T01": "def total_premium(premiums):\n"
                       "    from decimal import Decimal, ROUND_HALF_UP\n"
                       "    s = sum(Decimal(str(p)) for p in premiums)\n"
                       "    return float(s.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))",
                "T04": "def submit_with_retry(submit_fn, max_retries=3):\n"
                       "    last_exc = None\n"
                       "    for _ in range(max_retries + 1):\n"
                       "        try:\n"
                       "            return submit_fn()\n"
                       "        except Exception as e:\n"
                       "            last_exc = e\n"
                       "    raise last_exc",
                "T08": "def get_or_create_quote(store, application_id, premium):\n"
                       "    if application_id in store:\n"
                       "        return store[application_id]\n"
                       "    store[application_id] = premium\n"
                       "    return premium",
                "T13": "def grace_period_days():\n    return 31",
            }
            buggy_map = {
                "T01": "def total_premium(premiums):\n    return round(sum(premiums), 2)",
                "T04": "def submit_with_retry(submit_fn, max_retries=3):\n"
                       "    last_exc = None\n"
                       "    for _ in range(max_retries):\n"
                       "        try:\n"
                       "            return submit_fn()\n"
                       "        except Exception as e:\n"
                       "            last_exc = e\n"
                       "    raise last_exc",
                "T05": "def rank_claims(claims):\n"
                       "    return [c for c, _ in sorted(claims, key=lambda x: (-x[1], x[0]))]",
                "T08": "def get_or_create_quote(store, application_id, premium):\n"
                       "    store[application_id] = premium\n"
                       "    return premium",
                "T13": "def grace_period_days():\n    return 10",
            }
            if not is_ambiguous:
                return correct_map.get(task_id, buggy_map.get(task_id, "def PLACEHOLDER(*a,**k): pass"))
            if is_ambiguous and got_clarification and task_id in ("T01", "T04"):
                return correct_map.get(task_id, buggy_map.get(task_id))
            return buggy_map.get(task_id, correct_map.get(task_id, "def PLACEHOLDER(*a,**k): pass"))
        return _impl

    script = {t.task_id: naive_implementer(t.task_id) for t in ALL_TASKS}

    def clarifying_question_fn(spec_text):
        # Mock implementer "asks a question" only for T01/T04/T05/T08, and for
        # T05 asks the WRONG question (doesn't touch the actual ambiguity) to
        # demonstrate that clarification isn't automatically effective.
        # Once a CLARIFICATION answer has already come back, stop asking and
        # move to implementing -- otherwise this scripted mock re-triggers on
        # keywords still present in its own prior answer text and never commits.
        if "clarification:" in spec_text.lower():
            return None
        if "premium" in spec_text.lower() and "round" in spec_text.lower():
            return "What rounding mode should be used?"
        if "retr" in spec_text.lower():
            return "Does '3 retries' mean 3 total attempts or 3 plus the original?"
        if "severity" in spec_text.lower():
            return "What programming language should I use?"  # deliberately irrelevant
        if "quote" in spec_text.lower():
            return "Should re-running overwrite the existing quote?"
        if "grace" in spec_text.lower() or "lapse" in spec_text.lower():
            return "How many days should the grace period be?"
        return None

    def validator_script_default(prompt_text):
        # Deterministic heuristic mock -- NOT a stand-in for real model
        # judgment. Only meant to exercise all three verdict paths
        # (accept/reject/hold) through the pipeline so the aggregation logic
        # can be tested end-to-end without live API calls. Real validators
        # must come from actual model calls; see build_real_clients().
        if "PLACEHOLDER" in prompt_text or "NotImplementedError" in prompt_text:
            return "reject", "mock validator: implementation is an obvious stub/placeholder"
        if "you wrote this implementation yourself" in prompt_text:
            # L0 framing: rubber-stamp, no real independent scrutiny --
            # this is the mechanism the whole study is trying to detect.
            return "accept", "mock validator (L0): rechecking own work, no issue found"
        if "YOUR OWN independently-derived understanding" in prompt_text:
            # L3 framing: check a couple of known bug signatures directly,
            # simulating a validator that actually reasoned from the raw
            # spec rather than trusting the implementer's framing.
            if "range(max_retries):" in prompt_text and "range(max_retries + 1)" not in prompt_text:
                return "reject", "mock validator (L3): retry count looks off-by-one against my own derivation"
            if ("store[application_id] = premium\n    return premium" in prompt_text
                    and "if application_id in store" not in prompt_text):
                return "reject", "mock validator (L3): this overwrites an existing quote -- not idempotent per my own derivation"
            return "hold", "mock validator (L3): can't fully confirm correctness from behavior alone, flagging for review"
        # L1/L2: sees the code framed as someone else's work, but without
        # L3's forced independent-derivation-first step. Scripted as a plain
        # hold -- "not confident enough to accept, not confident enough to
        # reject" -- rather than defaulting to either extreme.
        return "hold", "mock validator: insufficient independent basis to confidently accept or reject"

    implementer = MockLLMClient(
        name="mock-implementer",
        script=script,
        clarifying_question_fn=clarifying_question_fn,
    )

    def validator_factory(independence_level: IndependenceLevel = None) -> MockLLMClient:
        # A fresh instance per call, not one shared object reused n times.
        # Accepts (and ignores) independence_level for interface parity with
        # the real-mode factory below, which genuinely branches on it to pick
        # a provider. The mock's behavior already varies by level via prompt
        # framing text (see validator_script_default), not by which client
        # object answers, so the level argument has no effect here.
        return MockLLMClient(
            name="mock-validator",
            validator_script=validator_script_default,
        )

    return implementer, validator_factory


def build_real_clients(model: str) -> LLMClient:
    return AnthropicClient(model=model)


def load_replay_code(path: str) -> Dict[str, list]:
    """
    Read previously generated implementations from a results CSV
    (`task_id`, `repeat`, `code_under_test`) so that validators can be re-run on
    EXACTLY the same code with no new implementer calls. Returns
    {task_id: [(repeat, code), ...]} with one entry per distinct (task, repeat).
    """
    out: Dict[str, list] = {}
    seen = set()
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            key = (r["task_id"], r.get("repeat", "1"))
            code = r.get("code_under_test") or ""
            if key in seen or not code:
                continue
            seen.add(key)
            out.setdefault(r["task_id"], []).append((r.get("repeat", "1"), code))
    return out


def run_sweep(implementer: LLMClient, validator_factory, out_csv: str, recovery_csv: str,
              tasks: List = None, independence_levels: List[IndependenceLevel] = None,
              chain_lengths: List[int] = None, repeats: int = 1,
              recovery_only: bool = False, replay: Dict[str, list] = None,
              replay_only_wrong: bool = False, validator_repeats: int = 1):
    """
    repeats: how many times each task is run end to end (fresh implementer draw,
    then validators on that draw). Real models are not deterministic, so a
    single run is one sample; repeats give a rate with some spread. Every row
    in both CSVs carries a `repeat` number (1..repeats).

    replay: {task_id: [(repeat, code), ...]} from `load_replay_code`. When given,
    NO implementation calls are made: the saved code is re-scored by the oracle
    and only the validators run. This holds the evidence constant across
    validator conditions (and is cheaper). replay_only_wrong keeps only samples
    whose oracle failure is a behavior failure.

    validator_repeats: run the validator sweep this many times on the same code
    (rows carry `validator_run`), to separate validator noise from task effects.

    tasks / independence_levels / chain_lengths: optional scope filters for a
    controlled (cheaper, bounded) run. Default to the full pilot grid when
    omitted. Filtering here is what makes "run a controlled shot" concrete --
    it's the same pipeline, just deliberately pointed at a small slice of it.
    """
    from types import SimpleNamespace
    from harness import _run_oracle_safely

    rows: List[dict] = []
    recovery_rows: List[dict] = []

    tasks = tasks if tasks is not None else ALL_TASKS
    chain_lengths = chain_lengths if chain_lengths is not None else [1, 3]
    decision_rules = [DecisionRule.MAJORITY, DecisionRule.UNANIMITY, DecisionRule.ANY_REJECT]
    independence_levels = independence_levels if independence_levels is not None else [
        IndependenceLevel.L0, IndependenceLevel.L1, IndependenceLevel.L2, IndependenceLevel.L3]

    if replay is not None:
        work = [(t, rp, code) for t in tasks for (rp, code) in replay.get(t.task_id, [])]
    else:
        work = [(t, rp, None) for t, rp in itertools.product(tasks, range(1, repeats + 1))]

    for task, rep, replay_code in work:
        print(f"  running {task.task_id} repeat {rep} ...", file=sys.stderr, flush=True)
        if replay_code is None:
            # --- clarification-recovery isolation (independent of independence level) ---
            outcome_amb_noclar = run_implementation(
                task, implementer, AmbiguityCondition.AMBIGUOUS, ClarificationCondition.NOT_ALLOWED)
            outcome_amb_clar = run_implementation(
                task, implementer, AmbiguityCondition.AMBIGUOUS, ClarificationCondition.ALLOWED)
            outcome_unamb = run_implementation(
                task, implementer, AmbiguityCondition.UNAMBIGUOUS, ClarificationCondition.NOT_ALLOWED)

            recovery_rows.append({
                "task_id": task.task_id,
                "repeat": rep,
                "unambiguous_correct": outcome_unamb.ground_truth_correct,
                "ambiguous_no_clarification_correct": outcome_amb_noclar.ground_truth_correct,
                "ambiguous_with_clarification_correct": outcome_amb_clar.ground_truth_correct,
                "num_questions_asked": outcome_amb_clar.num_clarifying_questions_asked,
                "unambiguous_failure_kind": classify_oracle_failure(
                    outcome_unamb.oracle_result.passed, outcome_unamb.oracle_result.failed_cases),
                "noclar_failure_kind": classify_oracle_failure(
                    outcome_amb_noclar.oracle_result.passed, outcome_amb_noclar.oracle_result.failed_cases),
                "clar_failure_kind": classify_oracle_failure(
                    outcome_amb_clar.oracle_result.passed, outcome_amb_clar.oracle_result.failed_cases),
                "failure_mode": classify_failure_mode(outcome_amb_noclar, outcome_amb_clar),
                "transcript": outcome_amb_clar.clarifying_transcript,
                # Final raw implementer reply in the clarification-allowed arm, and a
                # flag for the case "it looked like a question but was not recognized
                # as one" (no code fence, contains a '?', zero questions logged).
                "asked_but_unparsed": (outcome_amb_clar.num_clarifying_questions_asked == 0
                                       and "```" not in (outcome_amb_clar.raw_response or "")
                                       and "?" in (outcome_amb_clar.raw_response or "")),
                "final_raw_response": (outcome_amb_clar.raw_response or "")[:1500],
            })
            if recovery_only:
                continue
        else:
            res = _run_oracle_safely(task, replay_code)
            if replay_only_wrong and classify_oracle_failure(res.passed, res.failed_cases) != "behavior":
                continue
            outcome_amb_noclar = SimpleNamespace(
                code=replay_code, ground_truth_correct=res.passed, oracle_result=res)

        # --- full validator sweep (using the ambiguous+no-clarification
        #     implementation as the fixed evidence base for the independence
        #     x chain-length x decision-rule grid, to isolate that variable) ---
        code_under_test = outcome_amb_noclar.code
        ground_truth = outcome_amb_noclar.ground_truth_correct

        for vrun, level, n in itertools.product(range(1, validator_repeats + 1),
                                                independence_levels, chain_lengths):
            validators = [validator_factory(level) for _ in range(n)]
            verdicts = [
                run_validator(task, v, code_under_test, AmbiguityCondition.AMBIGUOUS, level, i)
                for i, v in enumerate(validators)
            ]
            for rule in decision_rules:
                decision = aggregate_decision(verdicts, rule)
                quality = decision_quality(decision, ground_truth)
                correct = decision_is_correct_default_policy(decision, ground_truth)
                reasonings = [str(v.reasoning) for v in verdicts]
                flagged = any(reasoning_flags_ambiguity(t) for t in reasonings)
                rows.append({
                    "task_id": task.task_id,
                    "repeat": rep,
                    "validator_run": vrun,
                    "ambiguity_condition": AmbiguityCondition.AMBIGUOUS.value,
                    "clarification_condition": ClarificationCondition.NOT_ALLOWED.value,
                    "independence_level": level.value,
                    "chain_length": n,
                    "decision_rule": rule.value,
                    "ground_truth_correct": ground_truth,
                    "validator_verdicts": [v.verdict.value for v in verdicts],
                    "validator_models": [v.validator_model for v in verdicts],
                    "aggregated_decision": decision.value,
                    "decision_quality": quality,
                    "correct_decision": correct,
                    "num_clarifying_questions_asked": 0,
                    "implementer_model": implementer.name if replay_code is None else "replayed",
                    "validator_model": validators[0].name if validators else "",
                    "notes": "" if all(v.parse_ok for v in verdicts)
                             else "PARSE_FAILURE: a validator reply was unparseable and defaulted to hold",
                    "validator_parse_ok": [v.parse_ok for v in verdicts],
                    "validator_reasoning": " || ".join(reasonings),
                    "validator_derivation": " || ".join(v.derivation for v in verdicts if v.derivation),
                    "ambiguity_flagged": flagged,
                    "accepted_despite_flag": flagged and decision == Verdict.ACCEPT,
                    "code_under_test": code_under_test,
                    "oracle_failure_kind": classify_oracle_failure(
                        outcome_amb_noclar.oracle_result.passed, outcome_amb_noclar.oracle_result.failed_cases),
                    "oracle_failed_cases": " | ".join(outcome_amb_noclar.oracle_result.failed_cases)[:600],
                })

    if rows:
        with open(out_csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    if recovery_rows:
        with open(recovery_csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(recovery_rows[0].keys()))
            writer.writeheader()
            writer.writerows(recovery_rows)

    if rows:
        print(f"Wrote {len(rows)} rows to {out_csv}")
    if recovery_rows:
        print(f"Wrote {len(recovery_rows)} rows to {recovery_csv}")
        _print_recovery_summary(recovery_rows)
    if rows:
        _print_decision_quality_summary(rows)
    if not rows and not recovery_rows:
        print("Nothing to do: no samples matched (check --tasks / --replay-code / --replay-only-wrong).")
    unparsed = sum(1 for r in recovery_rows if r.get("asked_but_unparsed"))
    if unparsed:
        print(f"\n!! {unparsed} clarification-arm runs look like an unrecognized question "
              f"(see asked_but_unparsed / final_raw_response in {recovery_csv}).")


def _print_decision_quality_summary(rows: List[dict]):
    from collections import Counter
    print("\n--- Decision quality summary (all rows) ---")
    overall = Counter(r["decision_quality"] for r in rows)
    for cat in ["correct_accept", "bad_merge", "correct_reject", "false_reject",
                "safe_hold", "costly_hold"]:
        print(f"  {cat:16s}: {overall.get(cat, 0)}")
    print("\n  ('bad_merge' is the outcome this whole study is about -- a confidently")
    print("   wrong accept. Compare its rate across independence_level below; the")
    print("   headline result would be bad_merge concentrated at L0/L1 and largely")
    print("   absent at L3.)")

    print("\n--- bad_merge rate by independence level (all rows; the three decision rules")
    print("    are NOT independent samples -- see the per-decision table below) ---")
    by_level: Dict[str, Counter] = {}
    for r in rows:
        by_level.setdefault(r["independence_level"], Counter())[r["decision_quality"]] += 1
    for level, counts in by_level.items():
        total = sum(counts.values())
        bad = counts.get("bad_merge", 0)
        print(f"  {level:45s}: bad_merge {bad}/{total} ({bad/total:.0%})" if total else f"  {level}: n/a")

    # One decision per (task, repeat, level, chain_length) using the majority
    # rule. This is the honest unit of analysis: the three rules re-score the
    # SAME validator verdicts, so counting all three triples-counts every case.
    print("\n--- Per-decision table (majority rule only; one row = one task-run) ---")
    print("    n = decisions; bad_merge shown with 95% Wilson interval; 'wrong code' = share of")
    print("    runs where the implementer's code failed the oracle (no wrong code, no bad_merge).")
    groups: Dict[tuple, list] = {}
    for r in rows:
        if r["decision_rule"] != DecisionRule.MAJORITY.value:
            continue
        groups.setdefault((r["independence_level"], r["chain_length"]), []).append(r)
    for (level, chain), rs in sorted(groups.items()):
        n = len(rs)
        c = Counter(r["decision_quality"] for r in rs)
        bad = c.get("bad_merge", 0)
        lo, hi = wilson_interval(bad, n)
        wrong = sum(1 for r in rs if not r["ground_truth_correct"])
        flagged = sum(1 for r in rs if r.get("ambiguity_flagged"))
        despite = sum(1 for r in rs if r.get("accepted_despite_flag"))
        print(f"  {level.split('_')[0]} chain={chain}: n={n}  wrong code {wrong}/{n}  bad_merge {bad}/{n} "
              f"({bad/n:.0%}, 95% CI {lo:.0%}-{hi:.0%})  "
              f"safe_hold {c.get('safe_hold',0)}  correct_reject {c.get('correct_reject',0)}  "
              f"correct_accept {c.get('correct_accept',0)}  costly_hold {c.get('costly_hold',0)}  "
              f"false_reject {c.get('false_reject',0)}")
        print(f"          ambiguity flagged in reasoning: {flagged}/{n}   accepted despite flag: {despite}/{n}  "
              f"(keyword heuristic -- hand-check before quoting)")


def _print_recovery_summary(recovery_rows: List[dict]):
    n = len(recovery_rows)
    from collections import Counter
    counts = Counter(r["failure_mode"] for r in recovery_rows)
    unamb_wrong = sum(1 for r in recovery_rows if not r["unambiguous_correct"])
    print("\n--- Clarification recovery summary ---")
    print(f"Tasks where unambiguous spec alone was still wrong (harness/task bug check): {unamb_wrong}/{n}")
    print(f"\nFour-way outcome breakdown (n={n} tasks):")
    for category in ["not_applicable", "recovered", "correct_without_asking", "no_clarification_sought",
                      "wrong_question", "unresolved_intent", "answer_not_applied"]:
        print(f"  {category:24s}: {counts.get(category, 0)}")
    print("\n(In a real run, cross-reference against the transcript column, especially")
    print(" for 'unresolved_intent' and 'answer_not_applied' rows, since those are the")
    print(" two failure modes that persist even when the implementer does everything")
    print(" right on its side of the exchange -- see design doc sec. 9a.)")


def estimate_call_count(tasks: List, independence_levels: List[IndependenceLevel],
                         chain_lengths: List[int], max_clarifying_turns: int = 2,
                         repeats: int = 1, replay_samples: int = None,
                         validator_repeats: int = 1) -> dict:
    """
    Rough pre-flight estimate of API call volume for a given scope, so a real
    run can be sized and confirmed before it spends anything. Implementer
    call count assumes worst case (every clarifying turn gets used) for a
    conservative (over-, not under-) estimate. L3a costs two calls per
    validator (derive, then judge). In replay mode (replay_samples given) there
    are no implementer calls at all.
    """
    calls_per_validator_slot = sum(2 if l == IndependenceLevel.L3A else 1 for l in independence_levels)
    if replay_samples is not None:
        validator_calls = replay_samples * validator_repeats * calls_per_validator_slot * sum(chain_lengths)
        return {"tasks": len(tasks), "repeats": repeats, "implementer_calls_max": 0,
                "validator_calls": validator_calls, "total_calls_max": validator_calls}
    n_tasks = len(tasks) * repeats  # each repeat re-runs every task end to end
    # 3 implementation runs per task (unambiguous, ambiguous-no-clar,
    # ambiguous-with-clar); the last one costs up to max_clarifying_turns+1 calls.
    implementer_calls = n_tasks * (1 + 1 + (max_clarifying_turns + 1))
    validator_calls = n_tasks * calls_per_validator_slot * sum(chain_lengths) * validator_repeats
    return {
        "tasks": len(tasks),
        "repeats": repeats,
        "implementer_calls_max": implementer_calls,
        "validator_calls": validator_calls,
        "total_calls_max": implementer_calls + validator_calls,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["mock", "real"], default="mock")
    parser.add_argument("--model", default="claude-sonnet-5",
                         help="Anthropic model, used as implementer always, and as validator for L0/L1 "
                              "(and for L2/L3 too unless --cross-provider is set).")
    parser.add_argument("--openai-model", default="gpt-5.1",
                         help="OpenAI model. Used for every role when --provider openai, or as the "
                              "L2/L3 validator when --cross-provider is set. Change this if you want a "
                              "different OpenAI model than the default.")
    parser.add_argument("--provider", choices=["anthropic", "openai"], default="anthropic",
                         help="Which provider runs EVERY role (implementer and all validators). "
                              "'openai' needs only OPENAI_API_KEY and no Anthropic account. Because one "
                              "provider fills every role, only the session/prompt-level independence "
                              "contrasts (L0 vs L1, and L3's independent-derivation framing) are "
                              "tested -- NOT substrate independence. Cannot be combined with "
                              "--cross-provider.")
    parser.add_argument("--cross-provider", action="store_true",
                         help="Use OpenAIClient (--openai-model) for L2/L3 validators instead of the "
                              "Anthropic model, so L2/L3 are genuinely substrate-independent rather than "
                              "same-provider-in-name-only. Requires OPENAI_API_KEY. Without this flag, "
                              "--mode real uses Anthropic for every role at every level -- only L0/L1 are "
                              "then honestly tested (see README).")
    parser.add_argument("--out", default="pilot_results.csv")
    parser.add_argument("--recovery-out", default="clarification_recovery.csv")
    parser.add_argument("--tasks", default=None,
                         help="Comma-separated task IDs for a controlled/scoped run, e.g. T04,T10,T13. "
                              "Default: all 13.")
    parser.add_argument("--independence-levels", default=None,
                         help="Comma-separated subset of L0,L1,L2,L3,L3a. Default: L0,L1,L2,L3. "
                              "L3a = derive the behavior in a separate call BEFORE seeing the code "
                              "(2 calls per validator).")
    parser.add_argument("--chain-lengths", default=None,
                         help="Comma-separated chain lengths, e.g. 1 or 1,3. Default: 1,3.")
    parser.add_argument("--repeats", type=int, default=1,
                         help="Run each task this many times end to end (fresh implementer draw each "
                              "time). Real models are non-deterministic, so one run is one sample. "
                              "Cost scales linearly with this. Default 1.")
    parser.add_argument("--recovery-only", action="store_true",
                         help="Run only the three implementation arms per task (unambiguous / ambiguous / "
                              "ambiguous+clarification) and skip the validator sweep. Writes only the "
                              "recovery CSV (use --recovery-out so the earlier results are not overwritten). "
                              "Much cheaper; use it to re-test the clarification fork on its own.")
    parser.add_argument("--replay-code", default=None,
                         help="Path to an earlier results CSV (needs task_id, repeat, code_under_test). "
                              "No implementer calls are made: the saved code is re-scored by the oracle and "
                              "only the validators run. Holds the evidence constant across validator "
                              "conditions and is cheaper. Writes only the results CSV.")
    parser.add_argument("--replay-only-wrong", action="store_true",
                         help="With --replay-code: keep only samples whose oracle failure is a behavior "
                              "failure (the only runs where a bad_merge can occur).")
    parser.add_argument("--replay-only-correct", action="store_true",
                         help="With --replay-code: keep only samples that PASS the oracle. This is the control "
                              "for --replay-only-wrong: it measures how often each validator level wrongly "
                              "holds or rejects code that is actually correct (costly_hold / false_reject).")
    parser.add_argument("--validator-repeats", type=int, default=1,
                         help="Run the validator sweep this many times on the same code (rows carry "
                              "validator_run) to separate validator noise from task effects. Default 1.")
    parser.add_argument("--validator-provider", choices=["same", "gemini", "openrouter"], default="same",
                         help="With --provider openai: 'gemini' makes every VALIDATOR a Gemini model "
                              "(different family from the gpt-5.1 implementer = a real substrate-independent "
                              "contrast). Best used with --replay-code so no implementer calls are made. "
                              "Needs GEMINI_API_KEY and --gemini-model.")
    parser.add_argument("--validator-model", default=None,
                         help="Model id for --validator-provider openrouter, e.g. a google/... or "
                              "anthropic/... id taken from openrouter.ai/models. Needs OPENROUTER_API_KEY.")
    parser.add_argument("--gemini-model", default=None,
                         help="Gemini model id (find one with `python3 gemini_check.py`).")
    parser.add_argument("--yes", action="store_true",
                         help="Required to actually spend money in --mode real. Without it, "
                              "prints a cost/call-count preview and exits without calling any API.")
    args = parser.parse_args()

    level_map = {"L0": IndependenceLevel.L0, "L1": IndependenceLevel.L1,
                 "L2": IndependenceLevel.L2, "L3": IndependenceLevel.L3,
                 "L3A": IndependenceLevel.L3A}
    tasks = ([TASKS_BY_ID[t.strip()] for t in args.tasks.split(",")] if args.tasks else ALL_TASKS)
    independence_levels = ([level_map[l.strip().upper()] for l in args.independence_levels.split(",")]
                            if args.independence_levels else
                            [IndependenceLevel.L0, IndependenceLevel.L1, IndependenceLevel.L2, IndependenceLevel.L3])
    chain_lengths = ([int(c.strip()) for c in args.chain_lengths.split(",")]
                      if args.chain_lengths else [1, 3])

    if args.repeats < 1:
        parser.error("--repeats must be >= 1")
    if args.validator_repeats < 1:
        parser.error("--validator-repeats must be >= 1")
    replay = None
    replay_samples = None
    if args.replay_code:
        from harness import _run_oracle_safely
        replay = load_replay_code(args.replay_code)
        replay = {t.task_id: replay.get(t.task_id, []) for t in tasks}
        if args.replay_only_wrong and args.replay_only_correct:
            parser.error("--replay-only-wrong and --replay-only-correct are mutually exclusive")
        if args.replay_only_wrong or args.replay_only_correct:
            kept = {}
            for tid, items in replay.items():
                keep = []
                for rp, code in items:
                    r = _run_oracle_safely(TASKS_BY_ID[tid], code)
                    kind = classify_oracle_failure(r.passed, r.failed_cases)
                    if (args.replay_only_wrong and kind == "behavior") or \
                            (args.replay_only_correct and kind == "pass"):
                        keep.append((rp, code))
                kept[tid] = keep
            replay = kept
        replay_samples = sum(len(v) for v in replay.values())
    elif args.replay_only_wrong or args.replay_only_correct:
        parser.error("--replay-only-wrong / --replay-only-correct need --replay-code")
    est = estimate_call_count(tasks, independence_levels, chain_lengths, repeats=args.repeats,
                              replay_samples=replay_samples, validator_repeats=args.validator_repeats)
    l23_in_scope = any(l in (IndependenceLevel.L2, IndependenceLevel.L3) for l in independence_levels)
    cross_provider = args.cross_provider and l23_in_scope

    if args.provider == "openai" and args.cross_provider:
        parser.error("--provider openai already uses OpenAI for every role; --cross-provider would "
                     "mix in Anthropic and needs both keys. Use one or the other.")

    if args.mode == "mock":
        implementer, validator_factory = build_mock_clients()
    elif args.provider == "openai":
        # OpenAI-only: every role (implementer and all validators) uses one
        # provider/model. Rate used for the rough estimate: GPT-5.1 $1.25/$10
        # per MTok (confirmed earlier in this project; re-check the current
        # pricing page if you switch --openai-model to a different model).
        import os as _os
        total = est["total_calls_max"]
        # Calibrated on observed spend (~$0.01/call early in the pilot); real cost varies
        # with reasoning length, so treat this as an order-of-magnitude range.
        low, high = total * 0.004, total * 0.02
        print(f"\n--- Real-mode pre-flight estimate (OpenAI-only) ---", file=sys.stderr)
        print(f"  Tasks: {est['tasks']} x repeats: {est['repeats']}  |  Independence levels: "
              f"{[l.value for l in independence_levels]}  |  Chain lengths: {chain_lengths}", file=sys.stderr)
        if replay is not None:
            print(f"  REPLAY: {replay_samples} saved code samples from {args.replay_code}"
                  f"{' (behavior-wrong only)' if args.replay_only_wrong else ''}"
                  f"{' (correct code only)' if args.replay_only_correct else ''}; no implementer calls; "
                  f"validator repeats: {args.validator_repeats}", file=sys.stderr)
        print(f"  Estimated API calls: ~{total} "
              f"({est['implementer_calls_max']} implementer + {est['validator_calls']} validator, "
              f"worst case on clarifying turns)", file=sys.stderr)
        print(f"  Model for ALL roles: {args.openai_model}  |  rough cost ~${low:.2f}-${high:.2f} "
              f"(actual depends on real token usage, not just call count)", file=sys.stderr)
        print(f"  NOTE: one provider fills every role, so this run does NOT test substrate "
              f"independence. L0 vs L1 and L3's independent-derivation prompt are testable; L2 is "
              f"effectively a repeat of L1 here and must not be reported as substrate-independent.",
              file=sys.stderr)
        use_gemini = args.validator_provider == "gemini"
        if use_gemini:
            if not args.gemini_model:
                parser.error("--validator-provider gemini needs --gemini-model (run gemini_check.py to list ids)")
            if not (_os.environ.get("GEMINI_API_KEY") or _os.environ.get("GOOGLE_API_KEY")):
                print("\n  ERROR: GEMINI_API_KEY is not in the environment.", file=sys.stderr)
                return
            print(f"  VALIDATORS: Gemini ({args.gemini_model}) at every level; implementer family: OpenAI. "
                  f"This is a different-family validator on the same code. Gemini cost is not estimated "
                  f"here (check your plan); the OpenAI figure above does not apply to validator calls.",
                  file=sys.stderr)
        use_or = args.validator_provider == "openrouter"
        if use_or:
            if not args.validator_model:
                parser.error("--validator-provider openrouter needs --validator-model (see openrouter.ai/models)")
            if not _os.environ.get("OPENROUTER_API_KEY"):
                print("\n  ERROR: OPENROUTER_API_KEY is not in the environment.", file=sys.stderr)
                return
            print(f"  VALIDATORS: OpenRouter ({args.validator_model}) at every level; implementer family: "
                  f"OpenAI. Cost depends on that model's price (see its page); the OpenAI figure above "
                  f"does not apply to validator calls.", file=sys.stderr)
        need_openai = not ((use_gemini or use_or) and replay is not None)
        if need_openai and not _os.environ.get("OPENAI_API_KEY"):
            print("\n  ERROR: OPENAI_API_KEY is not in the environment. Set it before running.",
                  file=sys.stderr)
            return
        if not args.yes:
            print("\n  Dry run only -- no API calls made. Re-run with --yes to actually spend money.",
                  file=sys.stderr)
            return

        from llm_clients import OpenAIClient, GeminiClient, OpenRouterClient
        implementer = OpenAIClient(model=args.openai_model) if need_openai else None

        def validator_factory(independence_level: IndependenceLevel = None) -> LLMClient:
            if use_or:
                return OpenRouterClient(model=args.validator_model)
            if use_gemini:
                return GeminiClient(model=args.gemini_model)
            return OpenAIClient(model=args.openai_model)
    else:
        # Anthropic (Sonnet 5) $2/$10 per MTok; OpenAI (GPT-5.1) $1.25/$10 per
        # MTok -- both confirmed against current pricing pages as of this
        # writing. Split the validator call estimate by provider when
        # cross-provider is on, since the two rates aren't identical.
        anthropic_calls = est["implementer_calls_max"]
        openai_calls = 0
        if cross_provider:
            l23_frac = sum(1 for l in independence_levels if l in (IndependenceLevel.L2, IndependenceLevel.L3)) / len(independence_levels)
            openai_calls = round(est["validator_calls"] * l23_frac)
            anthropic_calls += est["validator_calls"] - openai_calls
        else:
            anthropic_calls += est["validator_calls"]

        anthropic_low, anthropic_high = anthropic_calls * 0.0003, anthropic_calls * 0.004
        openai_low, openai_high = openai_calls * 0.0002, openai_calls * 0.0035

        print(f"\n--- Real-mode pre-flight estimate ---", file=sys.stderr)
        print(f"  Tasks: {est['tasks']} x repeats: {est['repeats']}  |  Independence levels: "
              f"{[l.value for l in independence_levels]}  |  Chain lengths: {chain_lengths}", file=sys.stderr)
        print(f"  Estimated API calls: ~{est['total_calls_max']} "
              f"({est['implementer_calls_max']} implementer + {est['validator_calls']} validator, "
              f"worst case on clarifying turns)", file=sys.stderr)
        if cross_provider:
            print(f"  Cross-provider ON: L0/L1 -> Anthropic ({args.model}), L2/L3 -> OpenAI ({args.openai_model})",
                  file=sys.stderr)
            print(f"  Rough cost: Anthropic ~${anthropic_low:.2f}-${anthropic_high:.2f}  +  "
                  f"OpenAI ~${openai_low:.2f}-${openai_high:.2f}  "
                  f"=  total ~${anthropic_low+openai_low:.2f}-${anthropic_high+openai_high:.2f}",
                  file=sys.stderr)
            print(f"  L0/L1 AND L2/L3 are both genuinely substrate-tested with this configuration.",
                  file=sys.stderr)
            import os as _os
            if not _os.environ.get("OPENAI_API_KEY"):
                print("\n  ERROR: --cross-provider set but OPENAI_API_KEY is not in the environment. "
                      "Set it before running with --yes.", file=sys.stderr)
                return
        else:
            print(f"  Rough cost estimate: ~${anthropic_low:.2f}-${anthropic_high:.2f} at model='{args.model}' "
                  f"(actual depends on real token usage, not just call count)", file=sys.stderr)
            print(f"  NOTE: --cross-provider not set -- every role uses {args.model}. "
                  f"Only L0/L1 are genuinely tested; L2/L3 verdicts come from the same model/provider "
                  f"and should not be reported as substrate-independent. Add --cross-provider for a "
                  f"real L2/L3 test.", file=sys.stderr)
        if not args.yes:
            print("\n  Dry run only -- no API calls made. Re-run with --yes to actually spend money.",
                  file=sys.stderr)
            return

        implementer = build_real_clients(args.model)
        if cross_provider:
            from llm_clients import OpenAIClient

            def validator_factory(independence_level: IndependenceLevel = None) -> LLMClient:
                if independence_level in (IndependenceLevel.L2, IndependenceLevel.L3):
                    return OpenAIClient(model=args.openai_model)
                return AnthropicClient(model=args.model)
        else:
            def validator_factory(independence_level: IndependenceLevel = None) -> LLMClient:
                return AnthropicClient(model=args.model)

    run_sweep(implementer, validator_factory, args.out, args.recovery_out,
              tasks=tasks, independence_levels=independence_levels, chain_lengths=chain_lengths,
              repeats=args.repeats, recovery_only=args.recovery_only, replay=replay,
              replay_only_wrong=args.replay_only_wrong, validator_repeats=args.validator_repeats)


if __name__ == "__main__":
    main()
