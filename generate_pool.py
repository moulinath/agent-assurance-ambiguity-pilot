"""
Generate a LABELED POOL of implementations, so validators can be scored WITHIN a task.

Why: in earlier runs the wrong-code samples and the correct-code samples came from
different tasks, so "P(accept | wrong) vs P(accept | correct)" was confounded with
task difficulty. This script draws several implementations of the SAME tasks and
labels each with the hidden oracle, so each task ends up with both wrong and
correct code (where the model produces both).

    python3 generate_pool.py                       # dry run: prints call count + cost, spends nothing
    python3 generate_pool.py --yes                 # real run (needs OPENAI_API_KEY)
    python3 generate_pool.py --mode mock           # plumbing check, no API

Two arms per task (both use NO clarification, so there is one implementation call each):
  ambiguous    -- the ambiguous ticket (signature pinned). Mostly produces the wrong code.
  unambiguous  -- the clarified ticket. Mostly produces the correct code.
The validators later judge ALL of them against the AMBIGUOUS ticket (what the requestor
actually wrote), via the existing replay mode:

    python3 run_pilot.py --provider openai --replay-code pool.csv \
        --independence-levels L0,L1,L3,L3a --chain-lengths 1 --out pool_judged.csv --yes
    python3 analyze_discrimination.py --pool pool.csv --results pool_judged.csv

Default tasks are the six that produced behavior-wrong code in Run B.
Makes no judgement calls itself; it only draws and labels.
"""
import argparse
import csv
import os
import sys

from tasks import TASKS_BY_ID
from harness import (run_implementation, AmbiguityCondition, ClarificationCondition,
                     classify_oracle_failure)

DEFAULT_TASKS = "T02,T04,T05,T08,T09,T13"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["mock", "real"], default="real")
    ap.add_argument("--openai-model", default="gpt-5.1")
    ap.add_argument("--tasks", default=DEFAULT_TASKS)
    ap.add_argument("--ambiguous-draws", type=int, default=6)
    ap.add_argument("--unambiguous-draws", type=int, default=4)
    ap.add_argument("--out", default="pool.csv")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    tasks = [TASKS_BY_ID[t.strip()] for t in args.tasks.split(",")]
    per_task = args.ambiguous_draws + args.unambiguous_draws
    calls = len(tasks) * per_task
    print(f"Pool: {len(tasks)} tasks x ({args.ambiguous_draws} ambiguous + "
          f"{args.unambiguous_draws} unambiguous) = {calls} implementation calls "
          f"(~${calls*0.004:.2f}-${calls*0.02:.2f}); judging is a separate run.", file=sys.stderr)

    if args.mode == "mock":
        from run_pilot import build_mock_clients
        implementer, _ = build_mock_clients()
    else:
        if not os.environ.get("OPENAI_API_KEY"):
            print("ERROR: OPENAI_API_KEY is not in the environment.", file=sys.stderr)
            return
        if not args.yes:
            print("Dry run only -- re-run with --yes to spend money.", file=sys.stderr)
            return
        from llm_clients import OpenAIClient
        implementer = OpenAIClient(model=args.openai_model)

    rows = []
    for task in tasks:
        n = 0
        for arm, cond, draws in (("ambiguous", AmbiguityCondition.AMBIGUOUS, args.ambiguous_draws),
                                 ("unambiguous", AmbiguityCondition.UNAMBIGUOUS, args.unambiguous_draws)):
            for _ in range(draws):
                n += 1
                print(f"  {task.task_id} sample {n} ({arm}) ...", file=sys.stderr, flush=True)
                o = run_implementation(task, implementer, cond, ClarificationCondition.NOT_ALLOWED)
                rows.append({
                    "task_id": task.task_id,
                    "repeat": n,
                    "arm": arm,
                    "ground_truth_correct": o.ground_truth_correct,
                    "oracle_failure_kind": classify_oracle_failure(
                        o.oracle_result.passed, o.oracle_result.failed_cases),
                    "oracle_failed_cases": " | ".join(o.oracle_result.failed_cases)[:600],
                    "code_under_test": o.code,
                })

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"\nWrote {len(rows)} samples to {args.out}\n")
    print(f"{'task':5s} {'pass':>5s} {'behavior':>9s} {'interface':>10s} {'crash':>6s}   usable for within-task comparison?")
    for task in tasks:
        rs = [r for r in rows if r["task_id"] == task.task_id]
        k = {x: sum(1 for r in rs if r["oracle_failure_kind"] == x)
             for x in ("pass", "behavior", "interface", "crash")}
        ok = k["pass"] >= 1 and k["behavior"] >= 1
        print(f"{task.task_id:5s} {k['pass']:>5d} {k['behavior']:>9d} {k['interface']:>10d} {k['crash']:>6d}   "
              f"{'YES' if ok else 'no (needs >=1 correct and >=1 behavior-wrong)'}")
    print("\nInterface/crash samples are ignored by the analysis. Tasks marked 'no' cannot be compared within-task.")


if __name__ == "__main__":
    main()
