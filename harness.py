"""
Core experiment harness.

Pipeline per (task, ambiguity_condition, clarification_condition, independence_level, chain_length):

  1. IMPLEMENTATION PHASE
     - Present the implementer with the spec variant (unambiguous or ambiguous).
     - If clarification_condition == "allowed": implementer may ask up to
       MAX_CLARIFYING_TURNS questions before committing to code. Each question
       is answered by `task.clarifying_answer_fn`, which simulates a requestor
       who KNOWS the true business intent but only answers what's actually
       asked (and gives a non-answer if the question doesn't touch the real
       ambiguity -- modeling a requestor who also doesn't fully know their own
       edge cases, which matters: clarification only helps to the extent the
       requestor-side understanding is actually there to be elicited).
     - If clarification_condition == "not_allowed": implementer must commit to
       code in a single turn, no follow-up questions permitted.
     - Extract code, run task.oracle() -> ground truth correct/incorrect.

  2. VALIDATION PHASE
     - For each of `chain_length` validators at the specified independence
       level, present the code + spec (per that level's visibility rules) and
       get an accept/reject verdict.
     - Independence levels (see design doc sec. 4):
         L0: same client instance, same session, asked to "recheck its own work"
         L1: same model, fresh session, sees same spec + same code
         L2: different model family, sees same spec + same code
         L3: different model family, does NOT see the implementer's code framed
             as "review this" -- instead independently re-implements from the
             raw spec and is shown only a behavioral diff/description, forcing
             it to reconstruct its own understanding rather than rubber-stamp
             the implementer's.

  3. DECISION PHASE
     - Aggregate the validator verdicts via majority / unanimity / any-reject
       rules into a three-way outcome: ACCEPT / REJECT / HOLD. HOLD means
       "insufficient or conflicting evidence to decide confidently, flag for
       review" -- distinct from a confident REJECT, and a legitimate outcome
       in its own right rather than evidence being forced into a binary.
     - Compare the aggregated decision to ground truth (from the oracle) via
       the six-way `decision_quality` classification (correct_accept,
       bad_merge, correct_reject, false_reject, safe_hold, costly_hold), and
       via `decision_is_correct_default_policy` for a single collapsed
       correct/incorrect boolean -- see that function's docstring for why
       that collapse is a POLICY CHOICE that should be revisited explicitly,
       not treated as objectively settled.

Everything is logged to a flat record per run, suitable for the statistical
analysis in the design doc (sec. 6).
"""

from __future__ import annotations
import json
import re
import io
import contextlib
import traceback
import multiprocessing as mp
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any, Tuple

from tasks import Task, OracleResult
from llm_clients import LLMClient, ChatMessage, extract_code_block

MAX_CLARIFYING_TURNS = 2


class IndependenceLevel(str, Enum):
    L0 = "L0_fully_correlated"
    L1 = "L1_session_independent"
    L2 = "L2_substrate_independent"
    L3 = "L3_substrate_and_derivation_independent"
    # L3a (added v1.3): the validator writes its derivation from the raw ticket in a
    # SEPARATE call, before any code is shown, then judges the code against it. The
    # derivation instruction is worded exactly like L3's so that the only differences
    # from L3 are the order (derive first) and that the derivation is a separate turn.
    L3A = "L3a_derive_before_code"


class DecisionRule(str, Enum):
    MAJORITY = "majority"
    UNANIMITY = "unanimity"
    ANY_REJECT = "any_reject_triggers_hold"


class Verdict(str, Enum):
    """
    A validator's (or the chain's aggregated) verdict. Three-way, not binary --
    a validator can genuinely have insufficient or conflicting evidence to
    confidently accept OR reject, and forcing a binary choice in that case
    hides exactly the information the whole study cares about (how confident
    was this signal, really?). HOLD means "flag for human/deeper review,"
    distinct from REJECT ("I am confident this is wrong").
    """
    ACCEPT = "accept"
    REJECT = "reject"
    HOLD = "hold"


class ClarificationCondition(str, Enum):
    ALLOWED = "allowed"
    NOT_ALLOWED = "not_allowed"


class AmbiguityCondition(str, Enum):
    UNAMBIGUOUS = "unambiguous"
    AMBIGUOUS = "ambiguous"


@dataclass
class ImplementationOutcome:
    code: str
    ground_truth_correct: bool
    oracle_result: OracleResult
    num_clarifying_questions_asked: int
    clarifying_transcript: List[Tuple[str, Optional[str]]] = field(default_factory=list)
    raw_response: str = ""


@dataclass
class ValidatorVerdict:
    validator_index: int
    independence_level: IndependenceLevel
    verdict: Verdict
    reasoning: str
    validator_model: str = ""
    # parse_ok is False when the reply could not be parsed into a recognized
    # verdict, in which case the verdict was defaulted to HOLD. Without this
    # flag, a parse failure is indistinguishable from a genuine "hold".
    parse_ok: bool = True
    raw_response: str = ""
    derivation: str = ""   # L3a only: the validator's derivation, written before it saw any code


@dataclass
class RunRecord:
    task_id: str
    ambiguity_condition: str
    clarification_condition: str
    independence_level: str
    chain_length: int
    decision_rule: str
    ground_truth_correct: bool
    validator_verdicts: List[str]
    validator_models: List[str]
    aggregated_decision: str
    decision_quality: str
    correct_decision: bool
    num_clarifying_questions_asked: int
    implementer_model: str
    validator_model: str
    notes: str = ""


IMPLEMENTER_SYSTEM_NO_CLARIFY = (
    "You are a software engineer. You will be given a ticket describing a "
    "function to implement. Write ONLY the Python function requested, in a "
    "single ```python fenced code block. Do not ask questions -- make your "
    "best judgment call on anything unstated and implement it now."
)

IMPLEMENTER_SYSTEM_CLARIFY = (
    "You are a software engineer. You will be given a ticket describing a "
    "function to implement. You may ask one clarifying question if something "
    "genuinely material to correctness is unstated -- respond with JSON "
    '{"action": "ask", "question": "..."} in that case. Otherwise, or once '
    'you have enough information, respond with the final implementation as a '
    "single ```python fenced code block. You may ask up to "
    f"{MAX_CLARIFYING_TURNS} clarifying questions total across the "
    "conversation before you must commit to an answer."
)

VALIDATOR_SYSTEM = (
    "You are a VALIDATOR reviewing a coding-agent's implementation before it "
    "is merged. Respond with JSON: "
    '{"verdict": "accept" or "reject" or "hold", "reasoning": "..."}. '
    "Accept only if you are genuinely confident the implementation correctly "
    "satisfies the actual business intent behind the ticket, not merely that "
    "it runs without errors. Reject only if you are genuinely confident it is "
    "wrong. If the ticket is ambiguous enough that you cannot confidently do "
    "either -- if a reasonable case exists for more than one interpretation "
    "and you cannot tell which one the implementation followed without more "
    "information -- respond \"hold\" and say what would resolve it. Do not "
    "guess accept or reject just to give a definite answer when you don't "
    "actually have one."
)

DERIVE_SYSTEM = (
    "You are a requirements analyst. You will be given a raw ticket for a "
    "function to be implemented. You will NOT be shown any implementation."
)

VALIDATOR_SYSTEM_L3A = (
    "You are a VALIDATOR (L3a - derive before seeing code). You first wrote "
    "your own derivation of the correct behavior from the raw ticket alone, "
    "before seeing any code. You are now shown a candidate implementation and "
    "must judge whether it matches YOUR derivation. Respond with JSON: "
    "{\"verdict\": \"accept\" or \"reject\" or \"hold\", \"reasoning\": "
    "\"...\"}. Use \"hold\" if your own independent derivation leaves genuine "
    "ambiguity about what the correct behavior should be -- don't force accept "
    "or reject past that point."
)

VALIDATOR_SYSTEM_L3 = (
    "You are a VALIDATOR (L3 - independent derivation). You will be given "
    "ONLY the raw specification/ticket, NOT the implementer's code or "
    "reasoning. First derive your own understanding of the correct behavior, "
    "including edge cases. You will then be shown a behavioral description "
    "of what the submitted implementation actually does, and must judge "
    "whether it matches YOUR independently-derived understanding. Respond "
    "with JSON: {\"verdict\": \"accept\" or \"reject\" or \"hold\", "
    "\"reasoning\": \"...\"}. Use \"hold\" if your own independent derivation "
    "leaves genuine ambiguity about what the correct behavior should be -- "
    "don't force accept or reject past that point."
)


_SIGNATURE_RE = re.compile(r"`([A-Za-z_]\w*\([^`]*\)[^`]*)`")


def interface_text(task: Task) -> str:
    """
    The call/return contract that the ambiguous ticket must carry. A real
    ticket comes with an existing call site, so the signature is not part of
    the ambiguity under study. Without it the implementer must guess parameter
    names and return shapes, and the oracle then fails correct-in-spirit code
    on a mismatch that has nothing to do with the planted requirement.
    The signature is taken from the first backticked `name(...)` in the
    unambiguous spec, so the two arms cannot drift apart.
    """
    parts = []
    m = _SIGNATURE_RE.search(task.unambiguous_spec)
    if m:
        parts.append(f"Required signature (existing call sites depend on it): `{m.group(1)}`.")
    if task.interface_note:
        parts.append(task.interface_note)
    return " ".join(parts)


def _spec_text(task: Task, ambiguity: AmbiguityCondition) -> str:
    if ambiguity == AmbiguityCondition.AMBIGUOUS:
        extra = interface_text(task)
        return f"{task.ambiguous_spec}\n\n{extra}" if extra else task.ambiguous_spec
    return task.unambiguous_spec


_INTERFACE_FAILURE_RE = re.compile(
    r"unexpected keyword|positional argument|required positional|missing \d+ required|"
    r"takes \d+ positional|not callable|has no attribute|NameError|is not defined|"
    r"not subscriptable|cannot unpack|not iterable|object has no|unhashable|bad return shape",
    re.I)


def classify_oracle_failure(passed: bool, failed_cases) -> str:
    """
    "pass", "interface" (call or return-shape mismatch -- NOT evidence of a
    misread requirement), "crash" (oracle child died) or "behavior". A keyword
    screen on the oracle's own messages: read the messages before relying on it.
    """
    if passed:
        return "pass"
    msg = " | ".join(failed_cases or [])
    if "child process exited without a result" in msg:
        return "crash"
    # An attribute/type error on None is a MISHANDLED EDGE INPUT (behavior), not a
    # call-signature mismatch, so it must not be excluded as an interface failure.
    if "NoneType" in msg:
        return "behavior"
    if _INTERFACE_FAILURE_RE.search(msg):
        return "interface"
    return "behavior"


def run_implementation(
    task: Task,
    implementer: LLMClient,
    ambiguity: AmbiguityCondition,
    clarification: ClarificationCondition,
) -> ImplementationOutcome:
    spec = _spec_text(task, ambiguity)
    if hasattr(implementer, "set_context"):
        implementer.set_context(task.task_id)

    transcript: List[Tuple[str, Optional[str]]] = []
    num_questions = 0

    if clarification == ClarificationCondition.NOT_ALLOWED:
        prompt = (
            f"{spec}\n\nImplement the function `{task.function_name}` now. "
            f"Respond with only the code."
        )
        raw = implementer.chat([ChatMessage("user", prompt)], system=IMPLEMENTER_SYSTEM_NO_CLARIFY)
        code = extract_code_block(raw)
    else:
        history = [ChatMessage("user",
            f"{spec}\n\nImplement the function `{task.function_name}`.")]
        raw = ""
        for turn in range(MAX_CLARIFYING_TURNS + 1):
            raw = implementer.chat(history, system=IMPLEMENTER_SYSTEM_CLARIFY)
            parsed = _try_parse_json(raw)
            if parsed and parsed.get("action") == "ask" and turn < MAX_CLARIFYING_TURNS:
                question = parsed["question"]
                answer = task.clarifying_answer_fn(question)
                num_questions += 1
                transcript.append((question, answer))
                answer_text = answer if answer else (
                    "I'm not sure, honestly -- use your best judgment on that one."
                )
                history.append(ChatMessage("assistant", raw))
                history.append(ChatMessage("user", f"CLARIFICATION: {answer_text}\n\n"
                                                     f"Please continue -- implement now if you have "
                                                     f"enough information."))
            else:
                break
        code = extract_code_block(raw)

    oracle_result = _run_oracle_safely(task, code)
    return ImplementationOutcome(
        code=code,
        ground_truth_correct=oracle_result.passed,
        oracle_result=oracle_result,
        num_clarifying_questions_asked=num_questions,
        clarifying_transcript=transcript,
        raw_response=raw,
    )


def _try_parse_json(text: str) -> Optional[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    # strict=False: real models often put literal newlines inside the JSON string
    # ("reasoning": "line one\n\nline two" typed as real line breaks), which strict
    # JSON rejects. One such reply was previously defaulted to HOLD although the
    # model had clearly said "accept".
    try:
        return json.loads(text, strict=False)
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback: real models often put prose before or after the JSON object,
    # e.g. 'Before I start, one question: {"action": "ask", ...}'. Without
    # this, such a reply was NOT recognized as a question and was instead
    # treated as the final answer (its text then failed the oracle).
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start:end + 1], strict=False)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _oracle_worker(task_id: str, code: str, result_queue) -> None:
    """
    Runs inside a child process: exec the candidate code, call the task's
    oracle, put the OracleResult on the queue. Deliberately top-level (not
    nested inside _run_oracle_safely) so multiprocessing can pickle a
    reference to it on spawn-based platforms (macOS, Windows, and Python's
    increasingly-default spawn start method on Linux too).
    """
    from tasks import TASKS_BY_ID
    task = TASKS_BY_ID[task_id]
    namespace: Dict[str, Any] = {}
    try:
        exec(code, namespace)
        fn = namespace.get(task.function_name)
        if fn is None:
            result_queue.put(OracleResult(
                passed=False, total_cases=1,
                failed_cases=[f"expected function `{task.function_name}` not defined"]))
            return
        result_queue.put(task.oracle(fn))
    except Exception as e:
        result_queue.put(OracleResult(
            passed=False, total_cases=1,
            failed_cases=[f"code failed to execute/parse: {e!r}"]))


def _run_oracle_safely(task: Task, code: str, timeout_seconds: float = 5.0) -> OracleResult:
    """
    Executes untrusted LLM-generated code in a SEPARATE PROCESS with a hard
    timeout, and runs the task's hidden oracle against it there.

    This solves the most likely real risk for this harness -- a buggy
    implementation with an infinite loop (e.g. a retry function that never
    terminates) hanging the entire pilot run -- by killing the child process
    after `timeout_seconds` if it hasn't finished.

    ** This is process isolation with a timeout, NOT a security sandbox. **
    The child process still runs as the same OS user with the same
    filesystem and network access as the parent process -- it stops a hang,
    it does NOT stop code that deliberately tries to do something harmful.
    For genuine isolation (no filesystem/network access even if the
    generated code tries), run this harness inside a container or a
    locked-down VM. For a small, narrowly-scoped pilot on tasks like the 13
    this harness ships with, that residual risk is low enough to reasonably
    skip; for anything run at real scale, or with less predictable/more
    open-ended task prompts, it isn't, and a container is the right call.
    """
    ctx = mp.get_context("spawn")
    result_queue = ctx.Queue()
    process = ctx.Process(target=_oracle_worker, args=(task.task_id, code, result_queue))
    process.start()
    process.join(timeout=timeout_seconds)

    if process.is_alive():
        process.terminate()
        process.join(timeout=1.0)
        if process.is_alive():
            process.kill()
        return OracleResult(
            passed=False, total_cases=1,
            failed_cases=[f"timed out after {timeout_seconds}s -- likely an infinite loop or "
                          f"blocking call in the candidate implementation"])

    try:
        return result_queue.get_nowait()
    except Exception:
        return OracleResult(
            passed=False, total_cases=1,
            failed_cases=[f"child process exited without a result (exit code {process.exitcode})"])


def run_validator(
    task: Task,
    validator: LLMClient,
    code: str,
    ambiguity: AmbiguityCondition,
    independence_level: IndependenceLevel,
    validator_index: int,
) -> ValidatorVerdict:
    if hasattr(validator, "set_context"):
        validator.set_context(task.task_id)

    spec = _spec_text(task, ambiguity)
    derivation = ""

    if independence_level == IndependenceLevel.L3:
        # L3: validator never sees the implementer's code as "review this."
        # It only sees a behavioral description, forcing independent derivation.
        # (Reference implementation shows the code but under a reframed prompt
        # that instructs independent-derivation-first reasoning; a stronger L3
        # would have the validator produce its OWN implementation first, then
        # diff behaviors programmatically -- left as an extension point.)
        prompt = (
            f"SPECIFICATION (raw ticket, this is all the context you get):\n{spec}\n\n"
            f"First, in your own reasoning, decide what the correct behavior should be, "
            f"including edge cases the ticket doesn't spell out.\n\n"
            f"Now here is a candidate implementation submitted by someone else:\n"
            f"```python\n{code}\n```\n\n"
            f"Judge it against YOUR OWN independently-derived understanding, not against "
            f"whatever reasoning the implementer might have used."
        )
        system = VALIDATOR_SYSTEM_L3
    elif independence_level == IndependenceLevel.L3A:
        # Call 1: derive the behavior from the raw ticket only. The wording mirrors
        # L3's derivation instruction exactly (no extra nudge toward noticing
        # ambiguity), so L3a differs from L3 only in order and in being a separate turn.
        derive_prompt = (
            f"SPECIFICATION (raw ticket, this is all the context you get):\n{spec}\n\n"
            f"In your own reasoning, decide what the correct behavior should be, "
            f"including edge cases the ticket doesn't spell out. Do not write code."
        )
        derivation = validator.chat([ChatMessage("user", derive_prompt)], system=DERIVE_SYSTEM)
        prompt = (
            f"SPECIFICATION (raw ticket):\n{spec}\n\n"
            f"YOUR DERIVATION, written before you saw any code:\n{derivation}\n\n"
            f"Now here is a candidate implementation submitted by someone else:\n"
            f"```python\n{code}\n```\n\n"
            f"Judge it against YOUR OWN independently-derived understanding above, not "
            f"against whatever reasoning the implementer might have used."
        )
        system = VALIDATOR_SYSTEM_L3A
    else:
        prompt = (
            f"SPECIFICATION:\n{spec}\n\n"
            f"CANDIDATE IMPLEMENTATION:\n```python\n{code}\n```\n\n"
            f"Review this implementation."
        )
        system = VALIDATOR_SYSTEM
        if independence_level == IndependenceLevel.L0:
            prompt += ("\n\n(Note: you wrote this implementation yourself in this same "
                       "session -- re-check your own work.)")

    raw = validator.chat([ChatMessage("user", prompt)], system=system)
    parsed = _try_parse_json(raw)
    if not isinstance(parsed, dict):
        # Fallback: real models often wrap the JSON in prose. Take the
        # outermost {...} span and try again before giving up.
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(raw[start:end + 1])
            except (json.JSONDecodeError, ValueError):
                parsed = None
    if not isinstance(parsed, dict):
        parsed = {}
    if "verdict" not in parsed:
        # Last resort: the reply is not valid JSON at all, but it states the verdict
        # in the expected key. Take only that field; the reasoning stays the raw text.
        m = re.search(r'"verdict"\s*:\s*"(accept|reject|hold)"', raw, re.I)
        if m:
            parsed = {"verdict": m.group(1)}
    verdict_str = str(parsed.get("verdict", "")).strip().lower()
    verdict_map = {"accept": Verdict.ACCEPT, "reject": Verdict.REJECT, "hold": Verdict.HOLD}
    parse_ok = verdict_str in verdict_map
    # Unparseable or unrecognized responses default to HOLD, not accept and
    # not reject -- if we can't tell what the validator meant, the safe
    # default is "needs review," not silently picking a side. parse_ok records
    # that this happened, so it is never mistaken for a genuine hold.
    verdict = verdict_map.get(verdict_str, Verdict.HOLD)
    reasoning = parsed.get("reasoning", raw)
    return ValidatorVerdict(
        validator_index=validator_index,
        independence_level=independence_level,
        verdict=verdict,
        reasoning=reasoning,
        validator_model=getattr(validator, "name", validator.__class__.__name__),
        parse_ok=parse_ok,
        raw_response=raw,
        derivation=derivation,
    )


def aggregate_decision(verdicts: List[ValidatorVerdict], rule: DecisionRule) -> Verdict:
    """
    Three-way aggregation. The three rules are now genuinely distinct, not
    two labels for the same computation:

      MAJORITY:   whichever verdict has a plain plurality wins; a tie
                  (including an even 3-way split) defaults to HOLD, since
                  "no clear majority" is itself informative and shouldn't be
                  silently broken in either direction.
      UNANIMITY:  ACCEPT only if every validator says accept; REJECT only if
                  every validator says reject; ANY disagreement -> HOLD.
                  This rule can produce a confident REJECT (when everyone
                  independently agrees it's wrong), which MAJORITY can too,
                  but UNANIMITY requires full agreement either way.
      ANY_REJECT: the conservative rule. ACCEPT only if every validator
                  says accept; a single REJECT or HOLD from anyone pulls
                  the whole chain to HOLD (deliberately never resolves to a
                  confident REJECT on its own -- one dissenting voice means
                  "review this," not "definitely wrong"). This is the rule
                  that most resembles how a cautious real review process
                  would behave in practice.
    """
    votes = [v.verdict for v in verdicts]
    if not votes:
        return Verdict.HOLD

    if rule == DecisionRule.MAJORITY:
        counts = {v: votes.count(v) for v in set(votes)}
        top_count = max(counts.values())
        winners = [v for v, c in counts.items() if c == top_count]
        if len(winners) == 1 and top_count > len(votes) / 2:
            return winners[0]
        return Verdict.HOLD  # no clear majority

    if rule == DecisionRule.UNANIMITY:
        if all(v == Verdict.ACCEPT for v in votes):
            return Verdict.ACCEPT
        if all(v == Verdict.REJECT for v in votes):
            return Verdict.REJECT
        return Verdict.HOLD

    if rule == DecisionRule.ANY_REJECT:
        if all(v == Verdict.ACCEPT for v in votes):
            return Verdict.ACCEPT
        return Verdict.HOLD  # any reject OR any hold pulls the chain to hold

    raise ValueError(f"unknown decision rule {rule}")


def decision_quality(decision: Verdict, ground_truth_correct: bool) -> str:
    """
    Six-way classification crossing {ACCEPT, REJECT, HOLD} x
    {ground truth correct, ground truth wrong}. This replaces a flat
    accept/reject "correct_decision" boolean, because a HOLD is neither
    simply right nor simply wrong -- it's a distinct, generally safer
    outcome that deserves its own bucket rather than being forced into one
    side of a binary.

        decision=ACCEPT, truth=True  -> "correct_accept"   (best case)
        decision=ACCEPT, truth=False -> "bad_merge"         (worst case --
                                          the exact failure this whole study
                                          is about: confident wrong merge)
        decision=REJECT, truth=False -> "correct_reject"    (best defensive case)
        decision=REJECT, truth=True  -> "false_reject"      (wasted rework)
        decision=HOLD,   truth=False -> "safe_hold"          (caught the
                                          problem without confidently
                                          asserting either way)
        decision=HOLD,   truth=True  -> "costly_hold"        (unnecessary
                                          delay, but not harmful)
    """
    if decision == Verdict.ACCEPT:
        return "correct_accept" if ground_truth_correct else "bad_merge"
    if decision == Verdict.REJECT:
        return "false_reject" if ground_truth_correct else "correct_reject"
    if decision == Verdict.HOLD:
        return "costly_hold" if ground_truth_correct else "safe_hold"
    raise ValueError(f"unknown verdict {decision}")


def decision_is_correct_default_policy(decision: Verdict, ground_truth_correct: bool) -> bool:
    """
    ** POLICY CHOICE -- flag this the same way T13's ground truth is
    flagged, and revisit it explicitly before reporting a headline
    P(correct decision) number. **

    Collapsing the six-way `decision_quality` outcome into a single
    correct/incorrect boolean (the primary outcome variable in the design
    doc, sec. 1) requires deciding how to score a HOLD, and there is no
    single objectively right answer -- it depends on what the eventual
    paper wants "correct" to mean (avoiding harm vs. resolving the
    decision). The default implemented here:

      - correct_accept, correct_reject, safe_hold -> counted CORRECT
        (a safe_hold avoided a bad merge, which is the outcome that
        matters most for this study's motivating concern)
      - bad_merge, false_reject, costly_hold -> counted INCORRECT

    This default rewards avoiding a bad merge above all else, and treats
    an unnecessary hold on a fine change (costly_hold) as a real cost, not
    a free pass. A different, equally defensible policy would count BOTH
    hold outcomes as a distinct third category excluded from the P(correct)
    calculation entirely, reporting hold rate separately rather than
    folding it into either side. Do not treat this function's current
    behavior as settled -- report the full six-way `decision_quality`
    breakdown alongside whatever single number this produces, so the
    policy choice is auditable rather than hidden inside an aggregate.
    """
    q = decision_quality(decision, ground_truth_correct)
    return q in ("correct_accept", "correct_reject", "safe_hold")


def run_full_pipeline(
    task: Task,
    implementer: LLMClient,
    validators: List[LLMClient],
    ambiguity: AmbiguityCondition,
    clarification: ClarificationCondition,
    independence_level: IndependenceLevel,
    decision_rules: List[DecisionRule],
    implementer_model_name: str,
    validator_model_name: str,
) -> Tuple[ImplementationOutcome, List[ValidatorVerdict], List[RunRecord]]:
    impl_outcome = run_implementation(task, implementer, ambiguity, clarification)

    verdicts = [
        run_validator(task, v, impl_outcome.code, ambiguity, independence_level, i)
        for i, v in enumerate(validators)
    ]

    records = []
    for rule in decision_rules:
        decision = aggregate_decision(verdicts, rule)
        quality = decision_quality(decision, impl_outcome.ground_truth_correct)
        correct = decision_is_correct_default_policy(decision, impl_outcome.ground_truth_correct)
        records.append(RunRecord(
            task_id=task.task_id,
            ambiguity_condition=ambiguity.value,
            clarification_condition=clarification.value,
            independence_level=independence_level.value,
            chain_length=len(validators),
            decision_rule=rule.value,
            ground_truth_correct=impl_outcome.ground_truth_correct,
            validator_verdicts=[v.verdict.value for v in verdicts],
            validator_models=[v.validator_model for v in verdicts],
            aggregated_decision=decision.value,
            decision_quality=quality,
            correct_decision=correct,
            num_clarifying_questions_asked=impl_outcome.num_clarifying_questions_asked,
            implementer_model=implementer_model_name,
            validator_model=validator_model_name,
        ))
    return impl_outcome, verdicts, records
