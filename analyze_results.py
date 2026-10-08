"""
Read-only analysis of a finished pilot run. Needs only the two CSVs the harness
wrote (pilot_results.csv, clarification_recovery.csv); makes no API calls.

    python3 analyze_results.py
    python3 analyze_results.py --results pilot_results.csv --recovery clarification_recovery.csv

Prints:
  1. Data-quality checks (parse failures, tasks wrong even on the clear spec).
  2. Per-task table: how often the implementer's code was wrong, and what each
     independence level did with it. Shows whether a headline rate is carried
     by a few tasks (task runs within a task are NOT independent of each other).
  3. bad_merge as a share of WRONG-code runs (the only runs where it can occur),
     per level, with 95% Wilson intervals.
  4. A paired L-vs-L comparison on the same code (discordant pairs + exact sign test).
  5. Clarification behavior: how often the implementer asked anything at all.

It also writes hand_check.csv: every majority-rule row where the validator
accepted wrong code, with the full reasoning, for reading by hand. The
keyword-based `ambiguity_flagged` columns are a screen, not a result.
"""
import argparse
import csv
from collections import defaultdict
from math import comb


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (max(0.0, c - h), min(1.0, c + h))


def sign_test_p(b, c):
    """Two-sided exact binomial (p=0.5) on discordant pairs b vs c."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def truthy(x):
    return str(x).strip().lower() == "true"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="pilot_results.csv")
    ap.add_argument("--recovery", default="clarification_recovery.csv")
    ap.add_argument("--hand-check-out", default="hand_check.csv")
    ap.add_argument("--no-relabel", dest="relabel", action="store_false",
                    help="Use the stored ground-truth labels instead of re-running the current oracle.")
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.results)))
    try:
        rec = list(csv.DictReader(open(args.recovery)))
    except FileNotFoundError:
        rec = []   # e.g. a replay run, which makes no implementation calls
        print(f"(no recovery file at {args.recovery}; skipping clarification and clear-spec checks)\n")
    # Re-label every row with the CURRENT oracle (free: no API calls), so a fixed oracle
    # (e.g. the T08 storage-layout fix) flows through to ground truth and decision_quality
    # without re-running anything. Disable with --no-relabel to see the stored labels.
    if args.relabel:
        from harness import _run_oracle_safely, classify_oracle_failure, decision_quality, Verdict
        from tasks import TASKS_BY_ID
        cache, flips = {}, set()
        for r in rows:
            code = r.get("code_under_test")
            if not code or r["task_id"] not in TASKS_BY_ID:
                continue
            k = (r["task_id"], code)
            if k not in cache:
                res = _run_oracle_safely(TASKS_BY_ID[r["task_id"]], code)
                cache[k] = (res.passed, classify_oracle_failure(res.passed, res.failed_cases))
            passed, kind = cache[k]
            if truthy(r["ground_truth_correct"]) != passed or r.get("oracle_failure_kind") != kind:
                flips.add((r["task_id"], r.get("repeat", "1")))
            r["ground_truth_correct"] = str(passed)
            r["oracle_failure_kind"] = kind
            r["decision_quality"] = decision_quality(Verdict(r["aggregated_decision"]), passed)
        print(f"(relabelled with the current oracle: {len(flips)} task-run samples changed label)\n")
    maj = [r for r in rows if r["decision_rule"] == "majority"]
    # Runs whose "wrong code" is an INTERFACE mismatch or an oracle crash say nothing
    # about whether the validator caught a misread requirement, so they are excluded
    # from every rate below and reported separately. (Older CSVs without the column
    # are treated as all-valid; run audit_oracle.py on those instead.)
    invalid_kinds = ("interface", "crash")
    n_invalid = len({(r["task_id"], r.get("repeat", "1")) for r in maj
                     if r.get("oracle_failure_kind") in invalid_kinds})
    maj = [r for r in maj if r.get("oracle_failure_kind") not in invalid_kinds]

    # ---- 1. data quality -------------------------------------------------
    print("=== 1. Data-quality checks ===")
    pf = sum(1 for r in rows if "PARSE_FAILURE" in r.get("notes", ""))
    print(f"rows: {len(rows)}  |  rows containing a validator parse failure: {pf}")
    if pf:
        print("  -> those verdicts were defaulted to HOLD; do not read them as genuine holds.")
    print(f"task-runs EXCLUDED as invalid (oracle failed on interface/crash, not on the planted behavior): {n_invalid}")
    bad_unamb = [(r["task_id"], r.get("repeat", "?")) for r in rec if not truthy(r["unambiguous_correct"])]
    print(f"runs where the implementer was wrong even on the UNAMBIGUOUS spec: {len(bad_unamb)} {bad_unamb}")
    print("  -> each is either a task/oracle bug or a plain model error. Read these before trusting that task.")

    # ---- 2. per-task -----------------------------------------------------
    levels = sorted({r["independence_level"] for r in maj})
    chains = sorted({int(r["chain_length"]) for r in maj})
    short = {l: l.split("_")[0] for l in levels}
    for chain in chains:
        print(f"\n=== 2. Per-task table (majority rule, chain length {chain}) ===")
        hdr = f"{'task':5s} {'runs':>4s} {'wrong':>5s} " + " ".join(f"{short[l]+' BM/acc/hold/rej':>20s}" for l in levels)
        print(hdr)
        by_task = defaultdict(list)
        for r in maj:
            if int(r["chain_length"]) == chain:
                by_task[r["task_id"]].append(r)
        for t in sorted(by_task):
            rs = by_task[t]
            runs = len({r.get("repeat", "1") for r in rs})
            wrong = len({r.get("repeat", "1") for r in rs if not truthy(r["ground_truth_correct"])})
            cells = []
            for l in levels:
                lr = [r for r in rs if r["independence_level"] == l]
                bm = sum(1 for r in lr if r["decision_quality"] == "bad_merge")
                acc = sum(1 for r in lr if r["aggregated_decision"] == "accept")
                hold = sum(1 for r in lr if r["aggregated_decision"] == "hold")
                rej = sum(1 for r in lr if r["aggregated_decision"] == "reject")
                cells.append(f"{bm:>3d}/{acc:>3d}/{hold:>3d}/{rej:>3d}".rjust(20))
            print(f"{t:5s} {runs:>4d} {wrong:>5d} " + " ".join(cells))
        print("  BM = bad_merge, acc/hold/rej = aggregated decision counts. 'wrong' = runs with wrong code.")
        print("  Tasks with wrong=0 can never produce a bad_merge; they only show correct_accept vs costly_hold.")

        # ---- 3. conditional rate ----------------------------------------
        print(f"\n=== 3. bad_merge as a share of WRONG-code runs (chain length {chain}) ===")
        for l in levels:
            lr = [r for r in maj if r["independence_level"] == l and int(r["chain_length"]) == chain]
            wrong = [r for r in lr if not truthy(r["ground_truth_correct"])]
            right = [r for r in lr if truthy(r["ground_truth_correct"])]
            bm = sum(1 for r in wrong if r["decision_quality"] == "bad_merge")
            lo, hi = wilson(bm, len(wrong))
            ca = sum(1 for r in right if r["decision_quality"] == "correct_accept")
            ch = sum(1 for r in right if r["decision_quality"] == "costly_hold")
            print(f"  {short[l]}: bad_merge {bm}/{len(wrong)} wrong-code runs "
                  f"({(bm/len(wrong) if wrong else 0):.0%}, 95% CI {lo:.0%}-{hi:.0%})  |  "
                  f"on correct code: accepted {ca}/{len(right)}, held {ch}/{len(right)}")

        # ---- 4. paired --------------------------------------------------
        print(f"\n=== 4. Paired comparison on the SAME wrong code (chain length {chain}) ===")
        idx = {}
        for r in maj:
            if int(r["chain_length"]) == chain and not truthy(r["ground_truth_correct"]):
                # validator_run distinguishes repeated validator sweeps on the same code
                idx[(r["task_id"], r.get("repeat", "1") + "." + r.get("validator_run", "1"),
                     short[r["independence_level"]])] = (r["decision_quality"] == "bad_merge")
        pairs = [("L0", "L1"), ("L0", "L3"), ("L1", "L3"),
                 ("L3", "L3a"), ("L1", "L3a"), ("L0", "L3a")]
        for a, b in pairs:
            keys = {(t, rp) for (t, rp, lv) in idx if lv in (a, b)}
            both = [(t, rp) for (t, rp) in keys if (t, rp, a) in idx and (t, rp, b) in idx]
            if not both:
                continue
            only_b = sum(1 for k in both if idx[(*k, b)] and not idx[(*k, a)])
            only_a = sum(1 for k in both if idx[(*k, a)] and not idx[(*k, b)])
            p = sign_test_p(only_b, only_a)
            print(f"  {a} vs {b}: n={len(both)} paired runs | bad_merge only at {b}: {only_b} | only at {a}: {only_a} "
                  f"| exact sign-test p = {p:.3f}")
        print("  Caveat: treats task-runs as independent. Repeats of one task are correlated, so these p-values")
        print("  are optimistic. Use them to rank findings for follow-up, not to claim significance.")

    # ---- 5. clarification -----------------------------------------------
    print("\n=== 5. Clarification behavior (implementer allowed to ask) ===")
    if not rec:
        print("  (no recovery data in this run)")
    asked = defaultdict(int)
    for r in rec:
        asked[r["num_questions_asked"]] += 1
    print("  questions asked per run:", dict(sorted(asked.items())))
    modes = defaultdict(int)
    for r in rec:
        modes[r["failure_mode"]] += 1
    print("  failure modes:", dict(modes))
    wrong_with_clar = sum(1 for r in rec if not truthy(r["ambiguous_with_clarification_correct"]))
    print(f"  runs still wrong with clarification allowed: {wrong_with_clar}/{len(rec)}")
    if rec and all(int(r["num_questions_asked"] or 0) == 0 for r in rec):
        print("  !! The implementer NEVER asked a question. Before concluding anything about asking behavior,")
        print("     check that this is not a prompt/format problem (see the transcript column, and how the")
        print("     implementer system prompt asks for an 'ask' reply).")

    # ---- hand-check file -------------------------------------------------
    out = [r for r in maj if r["aggregated_decision"] == "accept" and not truthy(r["ground_truth_correct"])]
    cols = ["task_id", "repeat", "independence_level", "chain_length", "ambiguity_flagged",
            "validator_reasoning", "code_under_test"]
    with open(args.hand_check_out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)
    print(f"\nWrote {len(out)} accepted-wrong-code rows to {args.hand_check_out} for hand review.")


if __name__ == "__main__":
    main()
