"""
Pilot task pool v2.0 (insurance-contextualized), for the CL10 experimental design.

Each Task carries:
  - unambiguous_spec: complete, precise requirement text
  - ambiguous_spec: a short issue/ticket-style history that under-constrains
    exactly one decision point (SpecPath-style contract-equivalent history)
  - clarifying_answer_fn(question: str) -> str | None:
      Simulates a requestor who KNOWS the true business intent (ground truth)
      but only answers what is actually asked, using judgment about what a
      real business stakeholder would volunteer. Returns None if the question
      doesn't touch the ambiguous point (requestor gives a generic/unhelpful
      answer in that case, modeling a requestor who also doesn't fully
      understand their own edge cases -- this matters for the
      clarification-allowed vs not-allowed fork, see run_pilot.py).
  - oracle(candidate_fn) -> OracleResult:
      Hidden ground-truth check. NEVER shown to implementer or validators.
  - divergence_note: what a wrong-but-plausible implementation looks like.

Domain: general insurance operations (claims, underwriting, policy
administration) -- chosen because insurance workflows are dense with
rules engines, effective/expiry date logic, and exactly the kind of
tacit business knowledge (what a "cash register" rounding convention
is, what "3 retries" means, what a stakeholder actually intends by an
offhand comment) that separates FDE-style requirement-gathering from a
literal reading of a ticket.
"""

from dataclasses import dataclass, field
from typing import Callable, List, Tuple, Any, Optional


@dataclass
class OracleResult:
    passed: bool
    total_cases: int
    failed_cases: List[str] = field(default_factory=list)


@dataclass
class Task:
    task_id: str
    domain: str
    unambiguous_spec: str
    ambiguous_spec: str
    divergence_note: str
    clarifying_answer_fn: Callable[[str], Optional[str]]
    oracle: Callable[[Any], OracleResult]
    function_name: str  # name the candidate implementation must define
    # Extra interface detail (e.g. return shape) shown with the ambiguous ticket so
    # the implementer is not forced to guess the call/return contract. It must NOT
    # reveal the planted behavioral ambiguity.
    interface_note: str = ""


# Marker prefix distinguishing a "hedge" answer (the question WAS on point,
# but the requestor genuinely has no resolved answer to give) from a plain
# `None` (the question did NOT touch the actual ambiguity at all). Used by
# run_pilot.py's four-way failure classifier -- see T13 below and the design
# doc sec. 9a.
HEDGE_PREFIX = "UNRESOLVED:"


# ---------------------------------------------------------------------------
# T01 -- Premium rounding
# ---------------------------------------------------------------------------

def _t01_clarify(question: str) -> Optional[str]:
    q = question.lower()
    if "round" in q or "cent" in q or "mode" in q:
        return ("Round-half-up (standard rounding), not banker's rounding. "
                "If a premium works out to exactly $x.xx5, it rounds up to "
                "the next cent, same as how a cashier would round a bill.")
    return None


def _t01_oracle(fn) -> OracleResult:
    cases = [
        ([12.345], 12.35),   # half-up -> 12.35; banker's (round-half-even) -> 12.34
        ([0.125], 0.13),     # half-up -> 0.13; banker's -> 0.12
        ([0.135], 0.14),     # half-up -> 0.14; banker's -> 0.14 (already even, not discriminating alone)
        ([2.675], 2.68),     # half-up -> 2.68; banker's -> 2.67 (float repr makes this a good divergent case)
    ]
    failed = []
    for premiums, expected in cases:
        try:
            got = fn(premiums)
        except Exception as e:
            failed.append(f"{premiums} raised {e!r}")
            continue
        if round(got, 2) != round(expected, 2):
            failed.append(f"{premiums}: expected {expected}, got {got}")
    return OracleResult(passed=not failed, total_cases=len(cases), failed_cases=failed)


T01 = Task(
    task_id="T01",
    domain="Premium calculation",
    unambiguous_spec=(
        "Write a function `total_premium(premiums: List[float]) -> float` that sums a "
        "list of individual coverage premiums and rounds the result to 2 decimal places "
        "using round-half-up (standard rounding), NOT banker's rounding."
    ),
    ambiguous_spec=(
        "Ticket: \"Need a function to total up the premium line items on a policy and "
        "round to the nearest cent for the invoice.\"\n"
        "Comment from underwriting: \"Should round the way it would show up on the "
        "customer's bill.\""
    ),
    divergence_note=(
        "Implementer may use the language's default round() (often banker's rounding / "
        "round-half-to-even) instead of round-half-up, since 'round to nearest cent' "
        "doesn't specify a rounding *mode* and banker's rounding is a common library default."
    ),
    clarifying_answer_fn=_t01_clarify,
    oracle=_t01_oracle,
    function_name="total_premium",
)


# ---------------------------------------------------------------------------
# T02 -- Coverage period overlap check
# ---------------------------------------------------------------------------

def _t02_clarify(question: str) -> Optional[str]:
    q = question.lower()
    if "same day" in q or "boundary" in q or "inclusive" in q or "half" in q or "end date" in q:
        return ("Coverage periods are half-open: the end date is the first day NOT "
                "covered. So a policy ending 3/15 and a new policy starting 3/15 do NOT "
                "overlap -- coverage ends the morning the next one begins.")
    return None


def _t02_oracle(fn) -> OracleResult:
    # (a_start, a_end, b_start, b_end, expected_overlap)
    cases = [
        (1, 10, 10, 20, False),   # touching boundary -- NOT in the example given
        (1, 10, 5, 15, True),     # interior overlap
        (1, 10, 11, 20, False),   # clearly disjoint
        (1, 5, 5, 5, False),      # zero-length interval at boundary
    ]
    failed = []
    for a_s, a_e, b_s, b_e, expected in cases:
        try:
            got = fn(a_s, a_e, b_s, b_e)
        except Exception as e:
            failed.append(f"({a_s},{a_e},{b_s},{b_e}) raised {e!r}")
            continue
        if bool(got) != expected:
            failed.append(f"({a_s},{a_e},{b_s},{b_e}): expected {expected}, got {got}")
    return OracleResult(passed=not failed, total_cases=len(cases), failed_cases=failed)


T02 = Task(
    task_id="T02",
    domain="Policy administration",
    unambiguous_spec=(
        "Write `coverage_overlaps(a_start, a_end, b_start, b_end) -> bool` for two "
        "half-open coverage intervals `[start, end)` (integers = day numbers). Two "
        "policies' coverage overlaps iff they share at least one covered day."
    ),
    ambiguous_spec=(
        "Ticket: \"Flag if a customer has two policies with overlapping coverage.\"\n"
        "Example given: \"Policy A covers days 1-10, Policy B covers days 5-15 -- "
        "these overlap.\" No other example given."
    ),
    divergence_note=(
        "Implementer correctly handles the given interior-overlap example but treats "
        "endpoints as inclusive elsewhere, so a policy ending day 10 and one starting "
        "day 10 (touching, not overlapping under half-open semantics) is wrongly flagged."
    ),
    clarifying_answer_fn=_t02_clarify,
    oracle=_t02_oracle,
    function_name="coverage_overlaps",
)


# ---------------------------------------------------------------------------
# T03 -- Dedupe beneficiary list preserving order
# ---------------------------------------------------------------------------

def _t03_clarify(question: str) -> Optional[str]:
    q = question.lower()
    if "case" in q or "capital" in q:
        return ("Yes, match case-insensitively -- 'John Smith' and 'john smith' are the "
                "same beneficiary. Keep the casing of whichever entry appeared first "
                "on the form.")
    return None


def _t03_oracle(fn) -> OracleResult:
    cases = [
        (["John Smith", "Jane Doe", "john smith"], ["John Smith", "Jane Doe"]),
        (["A", "a", "A"], ["A"]),
        (["Bob", "Rob"], ["Bob", "Rob"]),
    ]
    failed = []
    for inp, expected in cases:
        try:
            got = fn(list(inp))
        except Exception as e:
            failed.append(f"{inp} raised {e!r}")
            continue
        if got != expected:
            failed.append(f"{inp}: expected {expected}, got {got}")
    return OracleResult(passed=not failed, total_cases=len(cases), failed_cases=failed)


T03 = Task(
    task_id="T03",
    domain="Policy administration",
    unambiguous_spec=(
        "Write `dedupe_beneficiaries(names: List[str]) -> List[str]` returning names "
        "with duplicates removed, case-insensitive, keeping the FIRST occurrence's "
        "original casing and original relative order."
    ),
    ambiguous_spec=(
        "Ticket: \"Remove duplicate beneficiary names from this policy form data.\"\n"
        "Note from data entry: \"e.g. we had both 'John Smith' and 'john smith' on one "
        "form, those should count as duplicates.\""
    ),
    divergence_note=(
        "Implementer treats the case example as a one-off note rather than a general "
        "rule, and implements case-SENSITIVE dedup by default -- passing the literal "
        "example only if it happens to be tested, but failing on unlisted case variants."
    ),
    clarifying_answer_fn=_t03_clarify,
    oracle=_t03_oracle,
    function_name="dedupe_beneficiaries",
)


# ---------------------------------------------------------------------------
# T04 -- Retry policy for claims submission API
# ---------------------------------------------------------------------------

def _t04_clarify(question: str) -> Optional[str]:
    q = question.lower()
    if "retr" in q or "attempt" in q or "total" in q:
        return ("3 retries means the original submission attempt plus 3 more tries -- "
                "4 total attempts before giving up and surfacing the error to the adjuster.")
    return None


def _t04_oracle(fn) -> OracleResult:
    calls = {"n": 0}

    def flaky_submit():
        calls["n"] += 1
        raise ConnectionError("carrier system unavailable")

    calls["n"] = 0
    try:
        try:
            fn(flaky_submit, max_retries=3)
        except Exception:
            pass
    except Exception as e:
        return OracleResult(passed=False, total_cases=1, failed_cases=[f"raised unexpectedly: {e!r}"])
    expected_total_calls = 4
    if calls["n"] != expected_total_calls:
        return OracleResult(
            passed=False, total_cases=1,
            failed_cases=[f"expected {expected_total_calls} total attempts, got {calls['n']}"]
        )
    return OracleResult(passed=True, total_cases=1, failed_cases=[])


T04 = Task(
    task_id="T04",
    domain="Claims submission",
    unambiguous_spec=(
        "Write `submit_with_retry(submit_fn, max_retries=3)` that calls `submit_fn()` "
        "for the original attempt plus up to `max_retries` additional attempts (4 total "
        "calls when max_retries=3) on any exception, re-raising the last exception if "
        "all attempts fail. No delay between attempts."
    ),
    ambiguous_spec=(
        "Ticket: \"The carrier submission API is flaky. Add retries -- up to 3 retries "
        "before we give up and flag it for the adjuster.\""
    ),
    divergence_note=(
        "Implementer reads '3 retries' as max_attempts=3 (3 total calls) rather than "
        "3 retries on top of the original attempt (4 total calls) -- a classic "
        "off-by-one that a self-written test suite checking only 'it retries at all' "
        "won't catch."
    ),
    clarifying_answer_fn=_t04_clarify,
    oracle=_t04_oracle,
    function_name="submit_with_retry",
)


# ---------------------------------------------------------------------------
# T05 -- Rank claims by severity, tie-break
# ---------------------------------------------------------------------------

def _t05_clarify(question: str) -> Optional[str]:
    q = question.lower()
    if "tie" in q or "alphabet" in q:
        return ("Keep it simple: for tied severity scores, just preserve the order the "
                "claims came in (stable sort). Alphabetical tie-breaking isn't worth the "
                "extra logic for this queue.")
    return None


def _t05_oracle(fn) -> OracleResult:
    claims = [("CLM-003", 5), ("CLM-001", 8), ("CLM-002", 5), ("CLM-004", 9)]
    expected = ["CLM-004", "CLM-001", "CLM-003", "CLM-002"]  # CLM-003 before CLM-002: input order tie-break
    try:
        got = fn(list(claims))
    except Exception as e:
        return OracleResult(passed=False, total_cases=1, failed_cases=[f"raised {e!r}"])
    if got != expected:
        return OracleResult(passed=False, total_cases=1, failed_cases=[f"expected {expected}, got {got}"])
    return OracleResult(passed=True, total_cases=1, failed_cases=[])


T05 = Task(
    task_id="T05",
    domain="Claims triage",
    unambiguous_spec=(
        "Write `rank_claims(claims: List[Tuple[str,int]]) -> List[str]` returning claim "
        "IDs sorted by severity score descending; ties broken by original input order "
        "(stable sort, no secondary key)."
    ),
    ambiguous_spec=(
        "Ticket: \"Sort the adjuster queue by severity, most severe first.\"\n"
        "Comment: \"For ties, alphabetical by claim ID maybe? Not sure it matters, "
        "whatever's easiest is fine.\""
    ),
    divergence_note=(
        "Implementer takes the alphabetical suggestion literally and adds extra "
        "tie-breaking logic, when the comment's actual instruction ('whatever's "
        "easiest') favors a plain stable sort with no added logic."
    ),
    clarifying_answer_fn=_t05_clarify,
    oracle=_t05_oracle,
    function_name="rank_claims",
)


# ---------------------------------------------------------------------------
# T06 -- Paginate claims list
# ---------------------------------------------------------------------------

def _t06_clarify(question: str) -> Optional[str]:
    q = question.lower()
    if "out of range" in q or "beyond" in q or "empty" in q or "error" in q:
        return ("If an adjuster requests a page beyond the available claims, just return "
                "an empty list -- don't throw an error, the dashboard should just show "
                "'no more claims' quietly.")
    return None


def _t06_oracle(fn) -> OracleResult:
    items = list(range(25))  # 25 claims
    failed = []
    try:
        got = fn(items, page=10, page_size=10)  # way beyond range
    except Exception as e:
        failed.append(f"out-of-range page raised {e!r} instead of returning []")
        got = None
    if got is not None and got != []:
        failed.append(f"out-of-range page: expected [], got {got}")
    return OracleResult(passed=not failed, total_cases=1, failed_cases=failed)


T06 = Task(
    task_id="T06",
    domain="Claims dashboard",
    unambiguous_spec=(
        "Write `paginate_claims(claims: List, page: int, page_size: int) -> List`, "
        "1-indexed pages. A page beyond available data returns an empty list, never "
        "raises."
    ),
    ambiguous_spec=(
        "Ticket: \"Add pagination to the adjuster claims dashboard, pages starting at 1.\""
    ),
    divergence_note=(
        "Implementer's slicing logic raises an IndexError (or equivalent) for an "
        "out-of-range page rather than returning an empty list, since the ticket never "
        "states what should happen beyond the last page."
    ),
    clarifying_answer_fn=_t06_clarify,
    oracle=_t06_oracle,
    function_name="paginate_claims",
)


# ---------------------------------------------------------------------------
# T07 -- Validate policy number format
# ---------------------------------------------------------------------------

def _t07_clarify(question: str) -> Optional[str]:
    q = question.lower()
    if "none" in q or "null" in q or "empty" in q or "missing" in q:
        return ("If the policy number field is missing entirely (None/null) or an empty "
                "string, treat it as invalid -- return False, don't let it throw an "
                "exception, since this runs on batch-uploaded files with missing fields.")
    return None


def _t07_oracle(fn) -> OracleResult:
    cases = [
        ("POL-123456", True),
        ("", False),
        (None, False),
        ("bad", False),
    ]
    failed = []
    for val, expected in cases:
        try:
            got = fn(val)
        except Exception as e:
            failed.append(f"{val!r} raised {e!r} instead of returning {expected}")
            continue
        if bool(got) != expected:
            failed.append(f"{val!r}: expected {expected}, got {got}")
    return OracleResult(passed=not failed, total_cases=len(cases), failed_cases=failed)


T07 = Task(
    task_id="T07",
    domain="Policy administration",
    unambiguous_spec=(
        "Write `is_valid_policy_number(value: str) -> bool`. Valid format is "
        "'POL-' followed by 6 digits. An empty string or `None` is invalid -- return "
        "`False`, never raise."
    ),
    ambiguous_spec=(
        "Ticket: \"Validate policy numbers coming in from the batch upload -- format is "
        "'POL-' plus 6 digits.\""
    ),
    divergence_note=(
        "Implementer's format check works on strings but doesn't guard against `None` "
        "(e.g. calls `value.startswith(...)` directly), raising an AttributeError on "
        "batch rows with a missing policy number field -- an input class a "
        "self-written test suite using only string examples wouldn't cover."
    ),
    clarifying_answer_fn=_t07_clarify,
    oracle=_t07_oracle,
    function_name="is_valid_policy_number",
)


# ---------------------------------------------------------------------------
# T08 -- Idempotent quote creation
# ---------------------------------------------------------------------------

def _t08_clarify(question: str) -> Optional[str]:
    q = question.lower()
    if "overwrite" in q or "update" in q or "existing" in q or "already" in q:
        return ("No -- if a quote already exists for that application ID, return the "
                "existing quote unchanged. Don't overwrite it, even if the new premium "
                "calculation differs slightly. We don't want quotes silently changing "
                "underneath an agent who already showed a number to the customer.")
    return None


def _t08_oracle(fn) -> OracleResult:
    store = {}
    try:
        first = fn(store, "APP-001", 500.00)
        second = fn(store, "APP-001", 650.00)  # different premium, same application
    except Exception as e:
        return OracleResult(passed=False, total_cases=1, failed_cases=[f"raised {e!r}"])
    # Layout-agnostic on purpose: the ticket never says how `store` is organised, so we
    # check only the planted behavior (an existing quote is returned, not overwritten).
    # (An earlier version also required store["APP-001"] == 500, which failed code that was
    # idempotent but kept quotes under store["quotes"] -- an interface mismatch, not a bug.)
    if second != 500.00:
        return OracleResult(
            passed=False, total_cases=1,
            failed_cases=[f"expected existing quote 500.0 preserved, got second={second}"]
        )
    return OracleResult(passed=True, total_cases=1, failed_cases=[])


T08 = Task(
    task_id="T08",
    domain="Underwriting / quoting",
    unambiguous_spec=(
        "Write `get_or_create_quote(store: dict, application_id: str, premium: float) -> float`. "
        "If a quote already exists for `application_id`, return the EXISTING premium "
        "unchanged (do not overwrite). Otherwise store and return the new premium."
    ),
    ambiguous_spec=(
        "Ticket: \"Need a function so re-running the quote calculation for the same "
        "application doesn't cause problems -- should just work if it's called twice.\""
    ),
    divergence_note=(
        "Implementer builds an upsert (`store[application_id] = premium` "
        "unconditionally) since 'shouldn't cause problems' is satisfied at a shallow "
        "read either way -- but only true create-or-get semantics prevent a quote from "
        "silently changing on a second call with a different computed premium."
    ),
    clarifying_answer_fn=_t08_clarify,
    oracle=_t08_oracle,
    function_name="get_or_create_quote",
)


# ---------------------------------------------------------------------------
# T09 -- Claim submission deadline check (timezone)
# ---------------------------------------------------------------------------

def _t09_clarify(question: str) -> Optional[str]:
    q = question.lower()
    if "timezone" in q or "naive" in q or "aware" in q or "utc" in q:
        return ("Assume UTC if no timezone is attached to a timestamp. Claims come in "
                "from multiple regional systems, some of which don't attach timezone "
                "info, so the check needs to handle both aware and naive timestamps "
                "without crashing.")
    return None


def _t09_oracle(fn) -> OracleResult:
    import datetime as dt
    naive_expiry = dt.datetime(2026, 1, 1)
    naive_now = dt.datetime(2026, 1, 2)
    aware_expiry = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    aware_now = dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc)
    mixed_expiry = naive_expiry
    mixed_now = aware_now

    failed = []
    for label, expiry, now, expected in [
        ("naive/naive", naive_expiry, naive_now, True),
        ("aware/aware", aware_expiry, aware_now, True),
        ("mixed naive-expiry/aware-now", mixed_expiry, mixed_now, True),
    ]:
        try:
            got = fn(expiry, now)
        except Exception as e:
            failed.append(f"{label} raised {e!r}")
            continue
        if bool(got) != expected:
            failed.append(f"{label}: expected {expected}, got {got}")
    return OracleResult(passed=not failed, total_cases=3, failed_cases=failed)


T09 = Task(
    task_id="T09",
    domain="Claims submission",
    unambiguous_spec=(
        "Write `is_past_deadline(deadline: datetime, now: datetime) -> bool` returning "
        "True if `now >= deadline`. Assume UTC for any naive (timezone-less) datetime "
        "before comparing; must work whether inputs are naive, aware, or one of each."
    ),
    ambiguous_spec=(
        "Ticket: \"Check if a claim was submitted past its filing deadline.\"\n"
        "Example given uses plain datetimes with no timezone info at all."
    ),
    divergence_note=(
        "Implementer writes a direct comparison that works on the naive example given, "
        "but raises `TypeError: can't compare offset-naive and offset-aware datetimes` "
        "once a regional system sends a timezone-aware timestamp -- an input class the "
        "example never exercises."
    ),
    clarifying_answer_fn=_t09_clarify,
    oracle=_t09_oracle,
    function_name="is_past_deadline",
)


# ---------------------------------------------------------------------------
# T10 -- Underwriting rule evaluation order
# ---------------------------------------------------------------------------

def _t10_clarify(question: str) -> Optional[str]:
    q = question.lower()
    if "order" in q or "first" in q or "priority" in q or "conflict" in q:
        return ("Evaluate rules in the order they're listed and stop at the FIRST "
                "matching rule for the applicant's risk class -- underwriters deliberately "
                "order exclusion rules before broader allow rules, so order matters.")
    if "default" in q or "no rule" in q or "unmatched" in q or "no match" in q:
        return ("If no rule matches the applicant's risk class, default to DENY "
                "(fail-closed). We never want to auto-approve a risk class nobody "
                "explicitly reviewed.")
    return None


def _t10_oracle(fn) -> OracleResult:
    # rules: ordered list of (risk_class, allow)
    rules_conflict = [("smoker", False), ("standard", True)]
    rules_unmatched = [("standard", True)]

    failed = []
    try:
        got1 = fn(rules_conflict, "smoker")
    except Exception as e:
        failed.append(f"conflict case raised {e!r}")
        got1 = None
    if got1 is not None and got1 != False:
        failed.append(f"first-match-wins case: expected False (smoker excluded first), got {got1}")

    try:
        got2 = fn(rules_unmatched, "high_risk_hobby")
    except Exception as e:
        failed.append(f"unmatched case raised {e!r}")
        got2 = None
    if got2 is not None and got2 != False:
        failed.append(f"unmatched-role default: expected False (fail-closed), got {got2}")

    return OracleResult(passed=not failed, total_cases=2, failed_cases=failed)


T10 = Task(
    task_id="T10",
    domain="Underwriting",
    unambiguous_spec=(
        "Write `is_risk_class_allowed(rules: List[Tuple[str,bool]], risk_class: str) -> bool` "
        "where `rules` is an ordered list of `(risk_class, allow)` pairs. Evaluate in order; "
        "the FIRST matching rule wins. If no rule matches, default to `False` (deny)."
    ),
    ambiguous_spec=(
        "Ticket: \"Check whether an applicant's risk class is allowed, based on the "
        "underwriting rules list.\"\n"
        "Example given: `rules = [(\"standard\", True)]`, risk_class=\"standard\" -> "
        "True. No example with conflicting or unmatched rules."
    ),
    divergence_note=(
        "Two independent divergence points the single example doesn't disambiguate: "
        "(1) implementer scans for ANY matching allow rule rather than stopping at the "
        "first matching rule, which only shows up when an earlier exclusion rule "
        "conflicts with a later allow rule; (2) implementer defaults an unmatched risk "
        "class to True (fail-open) rather than False (fail-closed) -- the more "
        "consequential failure mode for an underwriting decision."
    ),
    clarifying_answer_fn=_t10_clarify,
    oracle=_t10_oracle,
    function_name="is_risk_class_allowed",
)


# ---------------------------------------------------------------------------
# T11 -- Merge overlapping endorsement/coverage periods
# ---------------------------------------------------------------------------

def _t11_clarify(question: str) -> Optional[str]:
    q = question.lower()
    if "touch" in q or "adjacent" in q or "boundary" in q or "consecutive" in q:
        return ("Yes, merge touching periods too -- if one endorsement ends on day 30 "
                "and the next starts on day 30, that's continuous coverage and should "
                "collapse into one period on the policy summary.")
    return None


def _t11_oracle(fn) -> OracleResult:
    cases = [
        ([(1, 10), (10, 20)], [(1, 20)]),   # touching -- not in the given example
        ([(1, 4), (2, 6)], [(1, 6)]),        # interior overlap -- matches given example
        ([(1, 5), (10, 15)], [(1, 5), (10, 15)]),  # disjoint
    ]
    failed = []
    for periods, expected in cases:
        try:
            got = fn(list(periods))
        except Exception as e:
            failed.append(f"{periods} raised {e!r}")
            continue
        if sorted(got) != sorted(expected):
            failed.append(f"{periods}: expected {expected}, got {got}")
    return OracleResult(passed=not failed, total_cases=len(cases), failed_cases=failed)


T11 = Task(
    task_id="T11",
    domain="Policy administration",
    unambiguous_spec=(
        "Write `merge_coverage_periods(periods: List[Tuple[int,int]]) -> List[Tuple[int,int]]` "
        "merging overlapping OR touching periods (e.g. (1,10) and (10,20) merge into "
        "(1,20)), sorted by start."
    ),
    ambiguous_spec=(
        "Ticket: \"Merge overlapping endorsement periods on the policy summary.\"\n"
        "Example given: (1,4) and (2,6) -> (1,6). No touching-boundary example given."
    ),
    divergence_note=(
        "Implementer writes a strict overlap check (`next_start < current_end`) that "
        "correctly handles the given interior-overlap example but excludes the "
        "touching case (`next_start == current_end`), since the example never "
        "exercises that boundary."
    ),
    clarifying_answer_fn=_t11_clarify,
    oracle=_t11_oracle,
    function_name="merge_coverage_periods",
)


# ---------------------------------------------------------------------------
# T12 -- Batch claims processing, partial failure reporting
# ---------------------------------------------------------------------------

def _t12_clarify(question: str) -> Optional[str]:
    q = question.lower()
    if "all fail" in q or "every" in q or "raise" in q or "exception" in q:
        return ("No -- even if every single claim in the batch fails, still return the "
                "summary dict with an empty succeeded list. Don't raise. The nightly job "
                "framework expects a summary object back every time, never an exception, "
                "or it treats the whole run as a crashed job and pages someone at 3am.")
    return None


def _t12_oracle(fn) -> OracleResult:
    def always_fails(item):
        raise ValueError(f"bad claim: {item}")

    items = ["CLM-A", "CLM-B", "CLM-C"]
    try:
        result = fn(items, always_fails)
    except Exception as e:
        return OracleResult(passed=False, total_cases=1, failed_cases=[f"raised {e!r} on all-fail batch"])
    if not isinstance(result, dict) or "succeeded" not in result or "failed" not in result:
        return OracleResult(passed=False, total_cases=1, failed_cases=[f"bad return shape: {result!r}"])
    if result["succeeded"] != []:
        return OracleResult(passed=False, total_cases=1, failed_cases=[f"expected empty succeeded, got {result['succeeded']}"])
    return OracleResult(passed=True, total_cases=1, failed_cases=[])


T12 = Task(
    task_id="T12",
    domain="Claims processing",
    unambiguous_spec=(
        "Write `process_claims_batch(items: List, process_fn) -> Dict` that attempts "
        "`process_fn(item)` for each item, continues past individual failures, and "
        "returns `{'succeeded': [...], 'failed': [(item, error_message), ...]}`. Never "
        "raises, even if EVERY item fails -- always returns the summary dict."
    ),
    ambiguous_spec=(
        "Ticket: \"Process this batch of claims overnight, don't let one bad claim "
        "kill the whole run.\""
    ),
    interface_note=(
        "Return a dict of the form {'succeeded': [...], 'failed': [(item, error_message), ...]}."
    ),
    divergence_note=(
        "Implementer correctly catches per-item exceptions (satisfying the literal ask) "
        "but raises an aggregate exception -- or returns None -- in the all-failed case, "
        "reasoning that a fully-failed batch is itself worth surfacing as an error; the "
        "ticket never specifies what should happen if every item fails."
    ),
    clarifying_answer_fn=_t12_clarify,
    oracle=_t12_oracle,
    function_name="process_claims_batch",
)


# ---------------------------------------------------------------------------
# T13 -- Grace period after premium lapse (UNRESOLVED INTENT case)
# ---------------------------------------------------------------------------
#
# Unlike T01-T12, the requestor here does NOT have a resolved answer even to
# the on-point clarifying question -- modeling a real organizational failure
# mode: the business side hasn't actually decided its own intent yet. This
# is the fourth outcome in the clarification taxonomy (design doc sec. 9a),
# distinct from "wrong question asked" (the question never touches the
# ambiguity) and "answer not applied" (a clear answer exists but isn't used).
#
# ** CALLOUT: this task's ground truth is a modeling assumption, not a **
# ** settled fact, and should be reported as such. ** The oracle below
# treats "31 days, the conservative/consumer-protective default" as ground
# truth for scoring purposes. That number is the researcher's stand-in for
# what a real compliance/legal review would eventually decide -- it is NOT
# something the simulated organization has actually confirmed anywhere in
# this task's own spec or clarifying exchange. This is intentional (it lets
# T13 still produce a scoreable pass/fail), but it means T13's result is
# conditional on that assumed default being the one an actual hard gate
# would land on. If the eventual project involves a real compliance/legal
# review step (a "hard gate") that fixes this number, T13's ground truth
# should be revisited against that decision before drawing conclusions from
# it, and any writeup should state this dependency explicitly rather than
# presenting 31 as an uncontested fact the way T01-T12's ground truths are.

def _t13_clarify(question: str) -> Optional[str]:
    q = question.lower()
    if "grace" in q or "day" in q or "lapse" in q:
        return (
            f"{HEDGE_PREFIX} Honestly we don't have a final number yet -- legal is "
            "still finalizing the state-by-state grace period matrix. Use your best "
            "judgment for now; we'll need to revisit this once compliance signs off."
        )
    return None


def _t13_oracle(fn) -> OracleResult:
    # Ground truth: the most conservative (longest, most consumer-protective)
    # grace period among the jurisdictions this product is sold in -- the
    # answer a compliance review would eventually land on, per standard
    # insurance regulatory practice of defaulting to the policyholder-favorable
    # interpretation when state requirements vary and a firm decision hasn't
    # been made.
    try:
        got = fn()
    except Exception as e:
        return OracleResult(passed=False, total_cases=1, failed_cases=[f"raised {e!r}"])
    expected = 31  # PROVISIONAL -- see the CALLOUT above; gated on a real hard-gate decision, not a confirmed org fact
    if got != expected:
        return OracleResult(passed=False, total_cases=1,
                             failed_cases=[f"expected {expected} (conservative default), got {got}"])
    return OracleResult(passed=True, total_cases=1, failed_cases=[])


T13 = Task(
    task_id="T13",
    domain="Policy administration",
    unambiguous_spec=(
        "Write `grace_period_days() -> int` returning 31 -- the conservative, "
        "consumer-protective default grace period (in days) after a missed premium "
        "payment before a policy lapses, used company-wide pending state-by-state "
        "legal review."
    ),
    ambiguous_spec=(
        "Ticket: \"Add a grace-period check for lapsed policies.\"\n"
        "Comment from legal/compliance: \"How many days depends on the state and "
        "policy type -- we haven't finalized the matrix yet. Use your judgment for "
        "now, we'll circle back once this is settled.\""
    ),
    divergence_note=(
        "Implementer defaults to a shorter, arbitrary grace period (e.g. 10 or 15 "
        "days, common in other contexts) rather than the conservative/"
        "consumer-protective maximum -- and clarification CANNOT fix this, because "
        "the requestor genuinely has no resolved answer to give, even to the exact "
        "right question. This is the 'unresolved intent' failure mode: the evidence "
        "chain's ceiling is set upstream of any coding agent or validator."
    ),
    clarifying_answer_fn=_t13_clarify,
    oracle=_t13_oracle,
    function_name="grace_period_days",
)


ALL_TASKS: List[Task] = [T01, T02, T03, T04, T05, T06, T07, T08, T09, T10, T11, T12, T13]
TASKS_BY_ID = {t.task_id: t for t in ALL_TASKS}