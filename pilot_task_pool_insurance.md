# Pilot Task Pool v2.0 (Insurance-Contextualized)

Human-readable companion to `tasks.py`, which contains the executable
version (specs, hidden oracles, simulated requestor answers). Domain:
general insurance operations -- claims, underwriting, policy
administration. Chosen because insurance workflows are dense with rules
engines, effective/expiry date logic, and exactly the kind of tacit
business knowledge (what "3 retries" means to a claims ops team, what a
underwriter actually intends by an offhand comment, what "shouldn't cause
problems" means for a quote) that separates genuine requirement
understanding from a literal reading of a ticket.

| ID | Domain | Function |
|---|---|---|
| T01 | Premium calculation | `total_premium` |
| T02 | Policy administration | `coverage_overlaps` |
| T03 | Policy administration | `dedupe_beneficiaries` |
| T04 | Claims submission | `submit_with_retry` |
| T05 | Claims triage | `rank_claims` |
| T06 | Claims dashboard | `paginate_claims` |
| T07 | Policy administration | `is_valid_policy_number` |
| T08 | Underwriting / quoting | `get_or_create_quote` |
| T09 | Claims submission | `is_past_deadline` |
| T10 | Underwriting | `is_risk_class_allowed` |
| T11 | Policy administration | `merge_coverage_periods` |
| T12 | Claims processing | `process_claims_batch` |
| T13 | Policy administration | `grace_period_days` |

---

### T01 — Premium rounding
**Unambiguous:** Sum coverage premiums, round to 2dp using round-half-up.
**Ambiguous (ticket):** "Total up the premium line items and round to the
nearest cent for the invoice." Comment: "Should round the way it would show
up on the customer's bill."
**Divergence:** Implementer reaches for the language default (often
banker's rounding) instead of round-half-up.
**If asked:** requestor clarifies round-half-up explicitly.

### T02 — Coverage period overlap
**Unambiguous:** Half-open interval overlap check `[start, end)`.
**Ambiguous (ticket):** "Flag if a customer has two policies with
overlapping coverage." Example given only shows interior overlap, not the
touching-boundary case.
**Divergence:** Touching coverage (one ends day 10, next starts day 10) is
wrongly flagged as overlapping.
**If asked:** requestor confirms half-open semantics generally.

### T03 — Dedupe beneficiary list
**Unambiguous:** Case-insensitive dedup, first-occurrence casing preserved.
**Ambiguous (ticket):** "Remove duplicate beneficiary names." One
case-variant example given as a side note, not stated as a general rule.
**Divergence:** Implementer treats the example as a one-off, ships
case-sensitive dedup by default.
**If asked:** requestor confirms case-insensitive matching generally.

### T04 — Retry policy for claims submission
**Unambiguous:** 3 retries = 4 total attempts (original + 3 retries).
**Ambiguous (ticket):** "The carrier submission API is flaky. Add retries --
up to 3 retries before we give up."
**Divergence:** Off-by-one -- implementer reads "3 retries" as 3 total
attempts.
**If asked:** requestor confirms 4 total attempts.

### T05 — Rank claims by severity
**Unambiguous:** Sort descending by severity, ties broken by input order
(stable sort).
**Ambiguous (ticket):** "Sort the adjuster queue by severity." Comment:
"For ties, alphabetical maybe? Not sure it matters, whatever's easiest is
fine."
**Divergence:** Implementer takes the alphabetical suggestion literally
instead of reading "whatever's easiest" as the actual instruction.
**If asked:** requestor confirms plain stable sort, no extra logic.

### T06 — Paginate claims dashboard
**Unambiguous:** Out-of-range page returns empty list, never raises.
**Ambiguous (ticket):** "Add pagination to the adjuster dashboard, pages
starting at 1." No mention of out-of-range behavior.
**Divergence:** Slicing logic raises an exception on an out-of-range page.
**If asked:** requestor confirms empty list, no error, for out-of-range
pages.

### T07 — Validate policy number
**Unambiguous:** `None`/empty string is invalid, returns `False`, never
raises.
**Ambiguous (ticket):** "Validate policy numbers from the batch upload --
format is 'POL-' plus 6 digits." No mention of missing/null handling.
**Divergence:** Format check works on strings but crashes on `None` from a
batch row with a missing field.
**If asked:** requestor confirms `None`/empty should return `False`, not
raise, since batch files have missing fields.

### T08 — Idempotent quote creation
**Unambiguous:** Re-running with the same application ID returns the
*existing* quote unchanged, never overwrites.
**Ambiguous (ticket):** "Need a function so re-running the quote
calculation for the same application doesn't cause problems."
**Divergence:** Implementer builds an upsert (always overwrites), which
"doesn't cause problems" at a shallow read but silently changes a quote
already shown to a customer.
**If asked:** requestor confirms true create-or-get semantics, existing
quote must never silently change.

### T09 — Claim submission deadline (timezone)
**Unambiguous:** Handles naive, aware, and mixed datetime comparisons;
assumes UTC for naive.
**Ambiguous (ticket):** "Check if a claim was submitted past its filing
deadline." Example given uses only naive datetimes.
**Divergence:** Direct comparison works on the naive example but raises
`TypeError` once a regional system sends a timezone-aware timestamp.
**If asked:** requestor confirms multiple regional systems send mixed
naive/aware timestamps, must assume UTC for naive.

### T10 — Underwriting rule evaluation order
**Unambiguous:** First-match-wins rule evaluation; unmatched risk class
defaults to deny (fail-closed).
**Ambiguous (ticket):** "Check whether an applicant's risk class is
allowed." Example given only shows a single matching rule -- no conflict,
no unmatched case.
**Divergence:** Two independent failure points: (a) any-match-wins instead
of first-match-wins when rules conflict; (b) fail-open default for an
unmatched risk class instead of fail-closed.
**If asked:** requestor confirms first-match-wins ordering and fail-closed
default separately (two distinct clarifying questions needed to fully
resolve this task).

### T11 — Merge overlapping coverage periods
**Unambiguous:** Merges overlapping *or touching* periods.
**Ambiguous (ticket):** "Merge overlapping endorsement periods." Example
given only shows interior overlap, not a touching-boundary case.
**Divergence:** Strict overlap check excludes the touching case.
**If asked:** requestor confirms touching periods should also merge (they
represent continuous coverage).

### T12 — Batch claims processing, partial failure
**Unambiguous:** Always returns the summary dict, even if every item fails
-- never raises.
**Ambiguous (ticket):** "Process this batch of claims overnight, don't let
one bad claim kill the whole run."
**Divergence:** Implementer catches per-item exceptions correctly but
raises (or returns `None`) in the all-failed case, since the ticket never
specifies that edge.
**If asked:** requestor confirms the summary dict must always come back,
even fully-failed, because the batch job framework treats any exception as
a crashed run.

### T13 — Grace period after premium lapse (the "unresolved intent" case)
**Unambiguous:** Grace period is 31 days — a conservative, consumer-protective
default used company-wide pending state-by-state legal review.
**Ambiguous (ticket):** "Add a grace-period check for lapsed policies."
Comment from legal/compliance: "How many days depends on the state and
policy type — we haven't finalized the matrix yet. Use your judgment for
now, we'll circle back once this is settled."
**Divergence:** Implementer defaults to a shorter, arbitrary number (e.g.
10–15 days) rather than the conservative maximum.
**If asked:** unlike every other task in this pool, the requestor does
**not** have a resolved answer to give — even to the exact right question,
the honest response is a hedge ("use your judgment for now"). This is
deliberate: it's the one task in the pool where clarification cannot fix
the ambiguity, because the business side hasn't actually resolved its own
intent yet. See the harness's four-way failure classifier
(`run_pilot.py::classify_failure_mode`), which tags this outcome
`unresolved_intent` — distinct from a wrong question being asked (T05) or a
clear answer not being applied (T08).

> **Callout — this task's ground truth is provisional.** The 31-day
> "conservative default" used as ground truth in `tasks.py` is a modeling
> assumption standing in for what an eventual compliance/legal review would
> decide — it is not something the simulated organization has actually
> confirmed anywhere in T13's own spec or clarifying exchange. Unlike
> T01–T12, where the hidden oracle reflects a fact the spec's history could
> in principle have stated, T13's oracle reflects a value judgment about
> where a real decision would land. **This is left open to be impacted by a
> decision on the hard gate** — if a real compliance/legal review step is
> ever added to the pipeline this task models, T13's ground truth should be
> revisited against that actual decision, and any writeup using T13's
> results should state this dependency explicitly rather than presenting 31
> as settled the way the other 12 tasks' ground truths are.

---

## Notes on the two-divergence-point task (T10)

T10 is deliberately the one task bundling two independent ambiguities
(evaluation order + default behavior). This is the closest analogue in the
pool to a real correlated-failure scenario in a security/access-control
context, but it also means a single clarifying question won't fully resolve
it -- the implementer needs to ask (or independently get right) both points.
If T10 underperforms in the pilot, that ambiguity is worth splitting into
two separate tasks to isolate which sub-ambiguity is driving the result.
