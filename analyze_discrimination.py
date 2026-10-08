"""
Within-task discrimination: does a validator accept correct code more often than wrong
code of the SAME task?  Read-only; no API calls.

    python3 analyze_discrimination.py --pool pool.csv --results pool_judged.csv

For each independence level (majority rule, chain length 1 by default) and each task that
has BOTH behavior-wrong and correct samples:
    P(accept | wrong), P(accept | correct), and their difference (the discrimination).
Then the average difference over those tasks, with a task-level sign count. The unit of
analysis is the TASK (about 6), not the row, because rows within a task are not independent.

Also reports P(reject | correct) = false_reject and P(hold | correct) = costly_hold, so a
validator that "wins" by rejecting everything is visible.
Interface and crash samples are excluded. Validator repeats are averaged within sample.
"""
import argparse
import csv
from collections import defaultdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="pool.csv")
    ap.add_argument("--results", default="pool_judged.csv")
    ap.add_argument("--chain", default="1")
    ap.add_argument("--rule", default="majority")
    args = ap.parse_args()

    from harness import _run_oracle_safely, classify_oracle_failure
    from tasks import TASKS_BY_ID
    pool = {(r["task_id"], r["repeat"]): r for r in csv.DictReader(open(args.pool))}
    # Re-label every sample with the CURRENT oracle (free), instead of trusting the stored label,
    # so an oracle fix takes effect without regenerating the pool.
    changed = []
    for key, p in pool.items():
        res = _run_oracle_safely(TASKS_BY_ID[p["task_id"]], p["code_under_test"])
        new = classify_oracle_failure(res.passed, res.failed_cases)
        if new != p["oracle_failure_kind"]:
            changed.append((key, p["oracle_failure_kind"], new))
        p["oracle_failure_kind"] = new
    if changed:
        print(f"Re-labelled {len(changed)} samples with the current oracle (stored -> current):")
        for key, o, n in changed:
            print(f"  {key[0]} sample {key[1]}: {o} -> {n}")
        print()
    rows = [r for r in csv.DictReader(open(args.results))
            if r["decision_rule"] == args.rule and r["chain_length"] == args.chain]

    # label: wrong / correct / skip, from the POOL's oracle label
    def label(key):
        p = pool.get(key)
        if not p:
            return None
        k = p["oracle_failure_kind"]
        return "correct" if k == "pass" else "wrong" if k == "behavior" else None

    levels = sorted({r["independence_level"] for r in rows})
    short = {l: l.split("_")[0] for l in levels}

    # cell[(level, task, label)] -> list of decisions (one per sample x validator_run)
    cell = defaultdict(list)
    for r in rows:
        lab = label((r["task_id"], r["repeat"]))
        if lab:
            cell[(r["independence_level"], r["task_id"], lab)].append(r["aggregated_decision"])

    tasks = sorted({t for (_, t, _) in cell})
    both = [t for t in tasks if any((l, t, "wrong") in cell for l in levels)
            and any((l, t, "correct") in cell for l in levels)]
    print(f"Tasks with both behavior-wrong and correct samples: {both}")
    print(f"Tasks lacking one side (excluded from discrimination): {[t for t in tasks if t not in both]}\n")

    def rate(decs, what):
        return (sum(1 for d in decs if d == what) / len(decs)) if decs else None

    for l in levels:
        print(f"=== {short[l]} ===")
        print(f"{'task':5s} {'n_wrong':>7s} {'n_corr':>6s} {'acc|wrong':>10s} {'acc|corr':>9s} {'diff':>6s} "
              f"{'rej|corr':>9s} {'hold|corr':>10s}")
        diffs = []
        for t in both:
            w, c = cell.get((l, t, "wrong"), []), cell.get((l, t, "correct"), [])
            if not w or not c:
                continue
            aw, ac = rate(w, "accept"), rate(c, "accept")
            d = ac - aw
            diffs.append(d)
            print(f"{t:5s} {len(w):>7d} {len(c):>6d} {aw:>10.0%} {ac:>9.0%} {d:>+6.0%} "
                  f"{rate(c,'reject'):>9.0%} {rate(c,'hold'):>10.0%}")
        if diffs:
            pos = sum(1 for d in diffs if d > 0)
            neg = sum(1 for d in diffs if d < 0)
            print(f"  mean discrimination over {len(diffs)} tasks: {sum(diffs)/len(diffs):+.0%}   "
                  f"(tasks where correct code was accepted MORE: {pos}, LESS: {neg}, tie: {len(diffs)-pos-neg})")
        print()

    print("How to read: discrimination = P(accept|correct) - P(accept|wrong), same task.")
    print("  ~0 or negative  -> the validator cannot tell them apart (accepting is not evidence of correctness).")
    print("  clearly positive -> it discriminates. Check rej|corr: a high value means it gets there by rejecting")
    print("  correct code too, which is a cost (false_reject), not a free win.")
    print("With ~6 tasks this is directional, not significant. Report per-task rows, not just the mean.")


if __name__ == "__main__":
    main()
