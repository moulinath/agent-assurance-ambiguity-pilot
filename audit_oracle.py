"""
Re-run the hidden oracle on code samples already saved in a results CSV, and
classify WHY each one failed. Makes no API calls and costs nothing.

    python3 audit_oracle.py --results pilot_results.csv
    python3 audit_oracle.py --results hand_check.csv

Why this exists: a result of "wrong code" is only meaningful if the code is
wrong about the behavior the task plants as ambiguous. If the oracle fails the
code because the implementer chose a different parameter name or return shape
(the ambiguous ticket never stated a signature), that is an INTERFACE
mismatch, not a misreading of the requirement, and it should not be counted
as a bad merge.

Failure kinds (keyword heuristic on the oracle's failure messages -- read the
printed messages; they are the evidence, the label is only a screen):
  interface -- call failed on argument names/count, missing attribute, etc.
  behavior  -- the code ran and returned the wrong thing
  crash     -- the oracle child process died without a result
"""
import argparse
import csv
import re
from collections import defaultdict

from tasks import TASKS_BY_ID
from harness import _run_oracle_safely, classify_oracle_failure

INTERFACE_RE = re.compile(
    r"unexpected keyword|positional argument|required positional|missing \d+ required|"
    r"takes \d+ positional|not callable|has no attribute|NameError|is not defined|"
    r"not subscriptable|cannot unpack|not iterable|object has no|unhashable",
    re.I)


def classify(passed, failed_cases):
    if passed:
        return "pass"
    msg = " | ".join(failed_cases)
    if "child process exited without a result" in msg:
        return "crash"
    if INTERFACE_RE.search(msg):
        return "interface"
    return "behavior"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="pilot_results.csv")
    ap.add_argument("--out", default="oracle_audit.csv")
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.results)))
    seen = {}
    for r in rows:
        key = (r["task_id"], r.get("repeat", "1"))
        if key in seen or not r.get("code_under_test"):
            continue
        res = _run_oracle_safely(TASKS_BY_ID[r["task_id"]], r["code_under_test"])
        seen[key] = (classify_oracle_failure(res.passed, res.failed_cases), " | ".join(res.failed_cases)[:300])

    by_task = defaultdict(lambda: defaultdict(int))
    for (t, rp), (kind, msg) in sorted(seen.items()):
        by_task[t][kind] += 1
        print(f"{t} rep{rp} {kind:9s} {msg[:200]}")

    print("\n=== Summary by task (distinct code samples) ===")
    total = defaultdict(int)
    for t in sorted(by_task):
        d = by_task[t]
        for k, v in d.items():
            total[k] += v
        print(f"  {t}: " + ", ".join(f"{k}={v}" for k, v in sorted(d.items())))
    print("  ALL: " + ", ".join(f"{k}={v}" for k, v in sorted(total.items())))
    print("\n'interface' failures are NOT evidence of a misread requirement. If they are a large share,")
    print("the 'wrong code' and 'bad_merge' counts in the main results are inflated.")

    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["task_id", "repeat", "kind", "oracle_failure_message"])
        for (t, rp), (kind, msg) in sorted(seen.items()):
            w.writerow([t, rp, kind, msg])
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
