"""
How often does the SAME validator give the SAME decision on the SAME code when run twice?
Needs a results CSV produced with --validator-repeats 2 (or more). Read-only; no API calls.

    python3 noise_floor.py --results pool_judged_haiku2.csv

Groups majority-rule rows by (task, sample, level) and compares the decision across
validator runs. Reports exact agreement per level and overall. Disagreement here is
validator noise, so differences between conditions smaller than this are not evidence.
"""
import argparse
import csv
from collections import defaultdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--chain", default="1")
    args = ap.parse_args()
    cell = defaultdict(list)
    for r in csv.DictReader(open(args.results)):
        if r["decision_rule"] != "majority" or r["chain_length"] != args.chain:
            continue
        cell[(r["independence_level"].split("_")[0], r["task_id"], r["repeat"])].append(
            (r.get("validator_run", "1"), r["aggregated_decision"]))
    per = defaultdict(lambda: [0, 0])
    for (lv, t, rp), v in cell.items():
        decs = [d for _, d in sorted(v)]
        if len(decs) < 2:
            continue
        per[lv][1] += 1
        per[lv][0] += len(set(decs)) == 1
    if not per:
        print("No cells with 2+ validator runs. Re-run with --validator-repeats 2.")
        return
    tot = [0, 0]
    for lv in sorted(per):
        a, n = per[lv]
        tot[0] += a; tot[1] += n
        print(f"  {lv}: same decision on both runs {a}/{n} ({a/n:.0%})")
    print(f"  ALL: {tot[0]}/{tot[1]} ({tot[0]/tot[1]:.0%}); the rest is validator noise.")


if __name__ == "__main__":
    main()
