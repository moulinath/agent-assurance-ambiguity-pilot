# Experimental Design: Assurance-Decision Reliability Under Correlated Evidence

## A study of P(correct accept/reject decision | evidence chain) for autonomous coding changes

**v1.5 — adds Run D, the first different-family validator (Claude Haiku 5.5 judging the same gpt-5.1-written pool). Headline: a different model family did not separate wrong from correct code either; both families fail the same way on the same tasks (T02, T05, T09). v1.4 added the labeled pool judged within task (Run C), the L3a and control findings, the noise floor and the T08 oracle fix. See §3(b″), §6.1, §7, §8a and the changelog.**

---

## 1. Research question and hypotheses

**Primary question.** When an evidence chain (tests, verifiers, reviewer agents) is used to decide whether to accept an autonomous coding change, how does the probability of a *correct* accept/reject decision depend on (a) how independent the validators in that chain actually are, and (b) whether the underlying specification was ambiguous?

**H1 (independence effect).** For a fixed chain length *n*, decision accuracy is higher when validators are drawn from independent substrates than when they share substrate, holding ambiguity constant.

**H2 (ambiguity moderation).** The independence effect in H1 is larger when the specification is ambiguous than when it is unambiguous. Under an unambiguous spec, correlated and independent validators should converge (there's only one plausible reading to get right or wrong together); under an ambiguous spec, correlated validators share the same wrong reading while independent ones are more likely to diverge and expose it.

**H3 (diminishing returns / n_eff).** As chain length *n* increases, decision accuracy for correlated chains plateaus quickly (bounded by shared-substrate error), while accuracy for independent chains continues to improve toward the bound implied by the individual validators' true error rate — i.e., correlated chains have a low effective independent-vote count (n_eff) regardless of nominal *n*.

**H4 (requestor-implementer alignment, added v1.1).** Allowing the implementer to ask clarifying questions increases decision-relevant correctness only to the extent that (a) the implementer asks a question that actually targets the ambiguous decision point, and (b) the requestor side has a resolved, correct answer to give. Neither condition is guaranteed by the other — an implementer can ask the wrong question, or ask the right question of a requestor who hasn't actually resolved their own intent. This reframes independence-of-validators (H1–H3) as addressing only the *downstream* half of the reliability problem; H4 addresses the *upstream* half, where the evidence chain's ultimate ceiling is set by whether the specification's ambiguity was ever actually resolved by someone who understood the business intent, human or AI. See §9a for the four-outcome taxonomy this implies and how it's operationalized in the pilot harness.

**Primary outcome:** binary decision correctness (chain's accept/reject verdict matches hidden ground truth) per task per chain configuration. **Updated v1.2:** the chain's verdict is now three-way (accept/reject/hold, not forced binary), scored via the six-way `decision_quality` classification in §6.1 before any collapsing to a single correct/incorrect number — see that section for why the collapse itself is a policy choice worth stating explicitly, and for `bad_merge` (a confidently wrong accept), the specific outcome this whole study is trying to measure the rate of.

**Secondary outcome:** empirically estimated n_eff and correlation (ρ) per chain configuration, to be compared against the nominal chain length.

This design directly targets CL10 (decision-reliability metric), instantiated using the independence mechanism from CL11 (Kohli 2605.29800; Ghanem 2609.18272) and the ambiguity mechanism from CL12 (SpecPath 2608.09799; Building-to-the-Test 2606.28430; SPECA 2602.07513).

---

## 2. Design overview

A **2 (ambiguity: unambiguous / ambiguous) × 4 (independence level: L0–L3) × 3 (chain length: n = 1, 3, 5)** mixed factorial, crossed with a **decision-rule** manipulation (majority vote vs. unanimity) applied post hoc to the same validator outputs (no extra runs needed — see §5).

Each cell requires a set of coding tasks run through the full pipeline: implementation → chain of validators at the specified independence level → aggregated decision → comparison against hidden ground truth.

```
Task → Implementation (by Agent-I) → [Validator 1, Validator 2, ..., Validator n] → Aggregation rule → Decision
                                                                                            ↓
                                                                          Compare to hidden ground-truth oracle
```

### 2a. The clarification factor (added v1.1)

**Clarification (allowed / not_allowed)** is a fourth manipulated factor: under `not_allowed`, the implementer must commit to code in a single turn from the ambiguous spec, exactly as in the v1.0 design above. Under `allowed`, the implementer may ask up to a fixed number of clarifying questions before committing, each answered by a simulated requestor who knows the true business intent but only answers what's actually asked (see §9a).

**Scoping decision — not fully crossed in the pilot.** A full crossing of clarification × ambiguity × independence × chain length × decision rule multiplies the design's cells substantially for no immediate gain, since clarification's effect is on the *implementation* (does the ambiguity get resolved before code is written), while independence/chain-length/decision-rule act on the *validation* of a fixed implementation. The pilot harness therefore treats these as two linked but separately-run comparisons:

1. **Clarification-recovery comparison** (isolates H4): for each task, run three implementation conditions — unambiguous, ambiguous+no-clarification, ambiguous+with-clarification — and compare ground-truth correctness across the three. This directly estimates how often clarification recovers a would-be-wrong implementation, independent of any downstream validator.
2. **Independence/chain-length/decision-rule sweep** (isolates H1–H3): run against a *fixed* implementation (the ambiguous+no-clarification output, i.e. the harder case where the implementer didn't get to ask), varying only independence level, chain length, and decision rule.

This is a real scope-vs-cost tradeoff, not a methodological given — it means the pilot cannot yet answer "does an independent validator chain catch what a *clarification-allowed* implementer still got wrong," which is arguably the most decision-relevant combined scenario for organizational practice. If pilot results from both comparisons look strong enough to justify the added cost, the full 120-task study should consider fully crossing clarification with the independence/chain-length grid rather than keeping them separate — re-run the power analysis in §6.3 for that larger design before committing.

### 2b. Hold-to-requestor loop (proposed, added v1.3)

The first real pilot (§8a) found that validators at L0/L1 often return HOLD *with the right clarifying question in their reasoning* (e.g., T02: "How should a_end == b_start be treated?"), while the same model, as implementer, asked no question at all in ~100 clarification-allowed runs. A HOLD is therefore effectively a clarification request that currently has nowhere to go. This motivates a further condition, **hold → ask requestor → re-implement**: when the aggregated decision is HOLD, the validator's question is put to the simulated requestor (the same `clarifying_answer_fn`), the answer is returned to the implementer, and the revised implementation is re-scored by the oracle. It operationalizes the "hard gate" and tests H4 at the stage where the model demonstrably can detect the ambiguity. It is **not yet implemented**; it should be piloted on the tasks that reliably produce wrong code before any claim is made about it.

---

## 3. Task selection criteria

**Domain note (v1.1):** the pilot task pool is now insurance-contextualized (claims, underwriting, policy administration) rather than domain-generic — see `pilot_task_pool_insurance.md` and `tasks.py` for the current 13-task pilot pool (T01–T13; T13 is the unresolved-intent task). The generic version referenced earlier in this project (`pilot_task_pool_v1.md`) is superseded; the criteria and rationale below are domain-agnostic and apply unchanged to the insurance-contextualized pool, but any earlier references to the specific generic tasks (currency rounding, date ranges, etc. in the abstract) should now be read as their insurance equivalents (premium rounding, coverage-period overlap, etc.).

The validity of the whole study rests on the task pool, so this is the highest-risk part of the design. Three requirements must all be satisfied by every retained task:

**(a) A hidden ground-truth oracle that is independent of any validator in the chain.** This is what prevents circularity — if the "correct" answer is defined by the same test suite a validator checks against, you're measuring agreement with the oracle, not decision reliability. Follow Building-to-the-Test's design: the true oracle (e.g., a large held-out test suite, or expert adjudication) is never shown to the implementing agent or to any validator; validators only see what a real reviewer would see (diff, stated requirement, whatever test suite they're told to run).

**(b) A specification that admits a controlled ambiguity manipulation.** Following SpecPath's "contract-equivalent specification histories," each task needs two specification variants that describe the *same final requirement* but differ in how they got there (revision history, phrasing, examples given) such that a plausible-but-wrong implementation exists under the ambiguous variant. Concretely:
- *Unambiguous variant:* a single, complete, unambiguous statement of the requirement.
- *Ambiguous variant:* a specification history (e.g., an issue thread with an initial vague ask, a clarifying comment that was ambiguous or later contradicted, or an example that under-constrains an edge case) that a competent implementer could resolve two or more incompatible ways.

Both variants must resolve to the *same* ground-truth-correct behavior when checked against the hidden oracle — the ambiguity is in what the implementer/validator can infer from what's in front of them, not in what's actually required.

**(b′) The call-and-return contract is pinned in BOTH variants (added v1.3).** The ambiguity under study must be a behavioral one. A real ticket arrives with an existing call site, so function name, parameter names/order and return shape are *not* part of the ambiguity. In the first pilot run the ambiguous tickets omitted the signature, and 18 of the 28 "wrong" implementations failed the oracle only because the implementer chose different parameter names, input shapes or return shapes (e.g., `per_page` vs `page_size`; a list of policies instead of four integers; a differently-keyed result dict). Those are interface mismatches, not misread requirements, and treating them as wrong code made validators who accepted them look like they had committed a `bad_merge`. The harness now appends the signature (taken from the unambiguous spec so the arms cannot drift) and, where needed, a return-shape note to every ambiguous ticket, and labels each oracle failure `interface` / `behavior` / `crash` so interface and crash runs can be excluded as invalid. **Caveat:** pinning a signature can leak the answer. T04's signature names the parameter `max_retries`, which may nudge the implementer toward the intended reading, and T13's no-argument signature implies a single constant. Re-check each task's wrong-code rate after pinning.

**(b″) The oracle must check only the planted behavior, not incidental representation (added v1.4).** T08's oracle required quotes to be stored at `store["APP-001"]`; the ticket never says how `store` is organised. Of the six ambiguous-arm samples in the labeled pool, five were idempotent (they returned the existing quote, `second=500.0`) but kept quotes under `store["quotes"]`, and were scored wrong. That is the same class of error as the missing signature in (b′), and the keyword classifier labeled it `behavior`. The oracle now checks only that an existing quote is returned and not overwritten. Every T08 "wrong" count produced before this fix (Run B, the L3a test) was re-labelled; the analysis scripts now re-run the current oracle on saved code (`analyze_results.py`, `analyze_discrimination.py`). **General rule:** read the oracle's failing message for each task's wrong samples before trusting a task, and make oracles representation-agnostic wherever the ticket is silent.

**(c) Room for wrong-but-plausible implementations, verified empirically, not assumed.** Before a task enters the main study, pilot it: generate implementations from several different models under the ambiguous variant, and confirm that at least a meaningful fraction (recommend ≥20%) produce an implementation that is fluent, passes a naive self-written test suite, but fails the hidden oracle. Tasks where every pilot implementation converges on the correct behavior anyway are useless for this design — discard them. **Updated v1.3:** count a pilot implementation as wrong only if the oracle failure is a *behavior* failure (see (b′) and `audit_oracle.py`). In the fixed pilot run, 6 of the 11 retained tasks never produced wrong code on the ambiguous ticket (T01, T03, T06, T08, T11, T12); only 5 (T02, T04, T05, T09, T13) did, and each did so on all three repeats. T07 and T10 had already been dropped from that run for never being wrong. Wrong-code production is a property of the *task*, so the ≥20% screen must be applied per task before a task enters the study.

**Task pool composition.** Recommend a mixed pool:
- **Synthetic/formal-spec tasks** (~40% of pool): adapted from tightly-specified problems in the style of the Knight–Leveson Launch Interceptor Program used by the N-Version-with-Coding-Agents paper (2606.20158). These give maximum control over where ambiguity is introduced and make the hidden oracle cheap to build (property-based or exhaustive testing).
- **Real repository tasks** (~60% of pool): adapted from SWE-bench-style issues (verified subset, to reduce leakage risk per SWE-Bench+'s findings on data contamination and weak tests) where the original issue thread can be split into an unambiguous and an ambiguous specification variant, with the repository's actual merged fix (or a superset of its tests, extended by the research team) serving as the hidden oracle.

**Stratification.** Stratify the final pool by task difficulty (e.g., using an independent difficulty proxy such as baseline single-agent pass rate under the unambiguous spec) to avoid floor effects (task so hard nobody gets it right regardless of chain) or ceiling effects (task so easy every chain gets it right). Target a baseline single-implementer pass rate under the unambiguous spec in the 40–80% band.

**Target pool size.** For adequate power on the interaction (H2) and on n_eff estimation (§6), plan for **at least 120 tasks** surviving pilot filtering, split roughly evenly across the two ambiguity conditions and across difficulty strata. This is the main cost driver in the study — see §7 for the power rationale.

---

## 4. Independence manipulation (the "substrate axis")

Following Ghanem's (2609.18272) three-axis independence grade (principal, substrate, evidence) and Proof-or-Stop's operational host/session/key separation, define four levels. Each validator in a chain is assigned a level; a chain's nominal independence is the level shared (or not) across its validators.

| Level | Label | Definition | Precedent |
|---|---|---|---|
| **L0** | Fully correlated | Same model, same session/context, re-invoked to "check its own work" (e.g., asked to re-verify after generating) | Naive self-check baseline |
| **L1** | Session-independent, substrate-shared | Same model family/version, fresh session, no shared context window, but sees the same spec and the same test suite the implementer used | Isolates session correlation only |
| **L2** | Substrate-independent | Different model family (e.g., implementer uses Model A, validator uses Model B from a different lab/training lineage), sees the same spec and test suite | Ghanem's substrate axis; approximates typical "Agent-2 reviews Agent-1" setups in current practice |
| **L3** | Substrate- and derivation-independent | Different model family **and** the validator does not see the implementer's test suite or reasoning trace — it independently re-derives its own understanding of the requirement and its own checks from the raw specification only | RETRACE's reconstruct-and-verify pattern; the strongest independence condition available without a human in the loop |

**L3 implementation caveat (added v1.3).** The reference L3 prompt shows the validator the raw specification, asks it to "decide what the correct behavior should be", and then — in the same message — shows the candidate code. The validator's "independent derivation" is therefore produced after it has seen the implementation. In the first real pilot, L3 reasoning often reproduced the implementer's choices as its "independently derived" expectation (T09, T13), which is what anchoring would look like. A stronger **L3a** should obtain the validator's derivation in a separate call, *before* any code is shown, and then present the code for comparison (or compare behaviors on shared inputs). Until L3a is run, L3 results must be described as "derive-then-judge prompting," not as demonstrated derivation independence.

**Chain composition.** For chain length *n* > 1, construct chains as either **homogeneous** (all *n* validators at the same level — used for the main independence contrast) or **mixed** (e.g., one L0 + one L3 — a secondary condition testing whether one strongly independent validator can compensate for correlated ones, directly testing Ghanem's Proposition 4.2 in the coding-decision setting). Recommend the main pre-registered analysis use homogeneous chains only, with mixed chains as an exploratory secondary analysis.

**Do not assume the independence level equals the empirical correlation.** Nominal level (L0–L3) is the manipulated variable; the actually realized correlation between validator errors must be *estimated from the data* (§6), because model families can share pretraining data or convergent failure modes even at L2/L3. Report both.

**Controlling for raw competence.** Independence level and validator competence are potentially confounded (e.g., L2/L3 might use a stronger model than L0/L1). Where possible, hold the validator model constant across independence levels and vary only session/substrate/derivation-sharing; where a different model family is required (L2/L3), include validator standalone accuracy (on unambiguous, non-chained tasks) as a covariate, and consider a supplementary condition swapping which model plays "implementer" vs. "validator" to check the effect isn't an artifact of one model being generally better.

---

## 5. Chains and decision rules

Reuse each task's validator outputs across decision rules rather than re-running the pipeline, to hold the evidence constant and isolate the effect of aggregation:

- **Chain configurations:** n ∈ {1, 3, 5} validators at a given independence level (homogeneous), plus the mixed L0+L3 secondary condition at n = 2.
- **Verdicts are three-way, not binary (updated v1.2).** Each validator returns ACCEPT, REJECT, or HOLD — not a forced accept/reject choice. HOLD means "the evidence doesn't support confidently doing either; flag for review." This matters for construct validity: forcing a binary choice would hide exactly the information the study cares about (how confident was this signal, really?), and a real validator facing genuine ambiguity that gives a confident-sounding accept or reject anyway is itself a finding, not noise to be collapsed away before it's even measured.
- **Aggregation rules, applied post hoc to the same n validator verdicts, now genuinely distinct outcomes (not three labels for one computation):**
  - *Unanimity:* ACCEPT only if every validator says accept; REJECT only if every validator says reject; any disagreement → HOLD. Can produce a confident REJECT when all validators independently agree it's wrong.
  - *Majority:* whichever verdict has a plain plurality wins; a tie (including an even split across all three verdicts) defaults to HOLD rather than being broken arbitrarily.
  - *Any-reject-triggers-hold:* the conservative rule — ACCEPT only if every validator says accept; a single REJECT or HOLD from anyone pulls the whole chain to HOLD. Never resolves to a confident REJECT on its own; closest to how a cautious real review process behaves (one dissenting voice means "review this," not "definitely wrong").

This lets you report, for the same underlying evidence, how much the *aggregation rule* itself changes decision reliability — a variable the current literature (Ghanem, Assurance Envelopes) treats structurally but doesn't test empirically against a decision-correctness ground truth.

---

## 6. Statistical plan

### 6.1 Primary analysis: decision accuracy

**Updated v1.2 — the outcome variable is now six-way, not binary, before any collapsing happens.** Cross the aggregated decision (ACCEPT/REJECT/HOLD) against ground truth (correct/incorrect) to get `decision_quality`:

| Decision | Ground truth correct | Ground truth wrong |
|---|---|---|
| ACCEPT | `correct_accept` | **`bad_merge`** |
| REJECT | `false_reject` | `correct_reject` |
| HOLD | `costly_hold` | `safe_hold` |

**`bad_merge` — a confidently wrong accept — is the single most important cell in this table.** It is the literal failure mode motivating the entire project (correlated evidence producing false confidence in a wrong change), and should be reported as its own rate, not only folded into an aggregate accuracy number.

Fit a mixed-effects logistic regression on a *collapsed* correct/incorrect outcome for the primary hypothesis test, but only after deciding — and stating — how HOLD is scored:

```
correct_decision ~ independence_level * ambiguity * chain_length + decision_rule
                   + (1 | task) + (1 | implementer_model)
```

- `correct_decision`: **this collapse is a policy choice, not a fact** (see the reference harness's `decision_is_correct_default_policy`, flagged there the same way T13's ground truth is flagged). The default policy counts `correct_accept`, `correct_reject`, and `safe_hold` as correct, and `bad_merge`, `false_reject`, `costly_hold` as incorrect — i.e., it rewards avoiding a bad merge above all else and treats an unnecessary hold on a fine change as a real cost. An equally defensible alternative policy excludes HOLD entirely from the correct/incorrect calculation and reports hold rate as a separate descriptive statistic rather than folding it into either side. **Whichever policy is used, report the full six-way table above alongside the collapsed number**, so the policy choice is auditable rather than hidden inside an aggregate — this is exactly the kind of collapsing-away-real-uncertainty move the project's own thesis (evidence chains hiding correlated failure) should make you suspicious of in your own analysis, not just in the systems you're studying.
- Random intercept for `task` accounts for task-level difficulty variance (some tasks are just harder to get right or to validate correctly, independent of chain design).
- Random intercept for `implementer_model` accounts for baseline implementer competence.
- Report the independence × ambiguity interaction (H2) as the key test; a significant positive interaction (independence helps more under ambiguity) is the paper's central empirical claim. **Also report the bad_merge rate by independence level as a separate, arguably more intuitive headline result** — the reference harness's summary output (`run_pilot.py::_print_decision_quality_summary`) already computes this breakdown directly.

**Discrimination as a secondary outcome (added v1.4).** `bad_merge` alone is gameable (a validator that rejects everything never commits one) and is computed only on wrong code. Report also, *within task*: P(accept | wrong code) against P(accept | correct code), their difference (discrimination), and `false_reject` / `costly_hold` on correct code. The comparison must be within task, because wrongness is task-determined; comparing wrong code from some tasks with correct code from others confounds validator behavior with task. The unit is the task. `generate_pool.py` and `analyze_discrimination.py` implement this.

**Unit of analysis and exclusions (added v1.3).** Rates must be computed over *wrong-code* runs only for `bad_merge` (it can occur only when the code is wrong), after excluding runs whose oracle failure is `interface` or `crash`. The three decision rules re-score the *same* validator verdicts, so counting all three rows per case triple-counts it; report one row per (task, repeat, level, chain length) using the primary rule. Repeats of one task are not independent. Wilson intervals computed over task-runs (as the pilot's summary printer does) overstate precision; inference must treat task as the cluster (the `(1 | task)` term above, or a task-level resampling). The pilot printers are descriptive aids, not the analysis.

### 6.2 Estimating n_eff and ρ for a binary decision outcome

Kohli's Kish design-effect approach was built for continuous/multi-class judge agreement; it needs adaptation for binary accept/reject verdicts. Ghanem's beta-factor common-cause model is the better starting point here since it's already framed in terms of fault/error probabilities. Concretely:

**Step 1 — Estimate the marginal error rate.** For each independence level, pool across validators (at chain length n=1, i.e., single-validator verdicts) to estimate p = P(an individual validator gives the wrong verdict). **Updated v1.2 — "wrong" here should use the same collapsed correct/incorrect definition as §6.1's `decision_is_correct_default_policy`** (i.e., a lone HOLD counts as correct only when ground truth is wrong), applied to each individual validator's own verdict as if it alone had decided. This keeps the n_eff/ρ analysis consistent with the primary analysis's policy choice, rather than silently using a different definition of "error" in the two places. As a secondary, policy-independent check, also report the raw three-way verdict agreement rate between validators on the same task directly (do two validators at a given independence level give the *same* verdict — accept/reject/hold — as each other, regardless of ground truth?) since that statistic doesn't depend on the correct/incorrect collapsing choice at all and is worth reporting alongside the ρ estimate for that reason.

**Step 2 — Estimate the shared/common-cause component.** Fit a random-intercept probit (or logit) model on individual validator error indicators, grouped by task:

```
validator_error ~ 1 + (1 | task)
```

within each independence level and ambiguity condition. The intraclass correlation (ICC) from this model — the proportion of total error variance attributable to the task-level random effect — is your empirical estimate of ρ (the correlation between validators' errors on the same task), directly analogous to Ghanem's ρ in the beta-factor model and to an ICC-based reformulation of Kohli's design effect for binary outcomes.

**Step 3 — Compute n_eff.** Apply the standard design-effect formula to the estimated ρ:

```
n_eff = n / [1 + (n − 1)·ρ]
```

Report n_eff alongside nominal n for each independence level and chain length. The headline comparison: does n_eff for L0/L1 chains stay near 1 regardless of nominal n (as Kohli found for correlated judges, n_eff ≈ 2.18 at n = 9), while n_eff for L3 chains tracks closer to nominal n?

**Step 4 — Beta factor.** Optionally report β = ρ·p / [ρ·p + (1−ρ)·p²] (Ghanem's formulation) as a second, more interpretable statistic for a practitioner audience: "what fraction of this validator's failures are shared-cause failures that a second, correlated validator would also miss."

**Important distinction from Kohli/Ghanem:** neither of those papers ties ρ to a *manipulated, planted* common cause (specification ambiguity); they measure ρ as it exists in the wild. This design's contribution is showing that ρ is not a fixed property of a validator pair — it is driven up by shared specification ambiguity and driven down by independent derivation (L3), which is a mechanistic claim neither existing paper makes.

### 6.3 Power and sample size

- For the primary interaction (H2), assuming a baseline decision accuracy around 70% and targeting detection of a 15-percentage-point interaction effect at α = 0.05, power = 0.80, a conventional logistic-regression power calculation suggests roughly **300–400 chain-level decision observations per ambiguity condition** (summed across independence levels), which the proposed 120-task pool × multiple independence levels × repeated chain draws comfortably exceeds — but confirm with a simulation-based power analysis once pilot data on baseline accuracy and expected ρ are available, since mixed-effects models with crossed random effects don't have simple closed-form power formulas.
- For ρ/n_eff estimation specifically, precision depends on the number of *tasks* (the clustering unit for the ICC), not the number of validators per task. With ~60 tasks per ambiguity condition and ≥3 validators per task at each independence level, expect a 95% CI on ρ on the order of ±0.15–0.20 — wide, but sufficient to distinguish L0/L1 (expected ρ high) from L3 (expected ρ near zero) as Kohli's comparably-sized study did.
- **Empirical correction from the pilot (added v1.3).** In the fixed pilot run, 5 of 11 tasks produced wrong code and each did so on 3 of 3 repeats, so 15 "wrong-code runs" carried roughly 5 independent cases. Adding repeats of the same tasks therefore buys almost no precision on between-level contrasts; only adding *tasks that produce wrong code* does. Plan the pool so that **at least ~30–40 tasks reliably produce behavior-wrong code** under the ambiguous ticket (about half of the pilot's tasks did), which implies authoring roughly 60–80 candidate tasks before filtering.
- **Recommendation:** run a small pilot (10–15 tasks, all independence levels, n=3 chains) before committing to the full pool, to get empirical estimates of baseline p and ρ and re-run the power calculation with real numbers rather than assumed ones.

### 6.4 Clarification-recovery analysis (added v1.1)

Separate from the n_eff/ρ analysis above (which uses the ambiguous+no-clarification implementation as its fixed evidence base — §2a), report a simple recovery-rate table from the clarification-recovery comparison:

| Metric | Definition |
|---|---|
| Recovery rate | fraction of tasks where ambiguous+no-clarification was wrong but ambiguous+with-clarification was correct |
| Non-recovery, wrong question | fraction where a question was asked but didn't target the actual ambiguous point |
| Non-recovery, answer not applied | fraction where the right question was asked, a correct answer was given, but the implementation still didn't reflect it |
| Non-recovery, unresolved intent | fraction where the right question was asked but the requestor-side answer function itself failed to resolve the ambiguity (see §9a's fourth outcome) |

This table is the direct empirical test of H4, and should be reported as its own result rather than folded into the independence/chain-length analysis, since it answers a categorically different question: not "how much does an additional validator help," but "how much does resolving the ambiguity before implementation help, and what breaks even when the opportunity to ask is available."

### 6.5 Pre-registration

Given how contested this space now is (per the Phase 2 gap review), pre-register before running the full study:
- The primary hypothesis (H2, the interaction), the decision rule used for the primary analysis (recommend majority vote as primary, others as secondary), the exact ρ/n_eff estimation procedure, the task-pool inclusion criteria from §3, and, as of v1.1, the recovery-rate taxonomy in §6.4 and §9a — decide the coding rules for the four outcome categories in advance, since "wrong question" vs. "answer not applied" vs. "unresolved intent" require a judgment call on real transcripts that's easy to fit post hoc otherwise.
- (v1.3) The rule that `interface`/`crash` oracle failures are excluded as invalid runs, the rule for scoring a HOLD (§6.1), and the rule that `ambiguity_flagged` is a keyword screen that must be hand-coded before any number derived from it is reported.
- This mirrors Proof-or-Stop's pre-registered ablation design and directly preempts the most likely reviewer objection in this literature — post-hoc fitting to whichever aggregation rule or independence contrast happens to look best.

---

## 7. Threats to validity

- **Independence is graded, not binary, and residual correlation is unavoidable.** Different model families may still share pretraining corpora or convergent architectural biases (Ghanem's own point in the audit setting). Frame L3 as "the strongest independence achievable without a human validator," not as a true independence baseline — and consider a small human-validator arm as an upper-independence reference point if resourcing allows.
- **Model nondeterminism vs. genuine independence.** Repeated calls to "the same model" (L0) are not perfectly identical validators due to sampling; separate within-model sampling variance from between-model correlation in the ICC model (Step 2 above) by including a same-model repeated-sampling condition as a check — if ρ is high even here, part of what you're calling "correlation" is just correlated competence on a hard task, not shared substrate per se, and the paper needs to say so explicitly.
- **Construct validity of the ambiguity manipulation.** An ambiguity manipulation that's too subtle produces no effect; one that's too obvious (e.g., a literally contradictory spec) is unrealistic and easy for any validator to flag regardless of independence. The pilot step in §3(c) is the safeguard — tune ambiguity strength empirically rather than by intuition.
- **Task pool leakage.** Given SWE-Bench+'s finding of substantial data leakage and weak tests in existing benchmarks, any real-repository tasks must be checked against training-data cutoffs of the models used, and hidden-oracle tests must be newly written or substantially extended, not reused verbatim from the original PR.
- **Ecological validity of the decision rules.** Majority vote and unanimity are clean for analysis but may not match how organizations actually aggregate signals in practice (which is often more like a risk-weighted or gated process, per Proof-or-Stop and Assurance Envelopes). Treat the "any-reject-triggers-hold" rule as the bridge to practical relevance, and discuss the majority/unanimity results as the cleaner scientific baseline.
- **Requestor-answer availability is currently assumed, not tested (added v1.1).** In the reference pilot harness, `clarifying_answer_fn` always has a correct, resolved answer available if the implementer asks the right question. This is optimistic relative to real organizations, where the business side frequently has NOT resolved its own edge cases either. Before the full study, add 1–2 tasks where the answer function itself returns a hedge/non-answer even to the right question, to observe what the evidence chain does when clarification is sought but genuinely unavailable — this is the scenario where reliance on the AI's own guess is least constrained by anything upstream, and arguably the more consequential real-world failure mode than either "wrong question" or "answer not applied."

- **Interface mismatch masquerading as wrong code (added v1.3; mitigated).** See §3(b′). Residual risk: the keyword screen that labels failures `interface` can mislabel (it treats errors on `None` input as behavior). Read the oracle failure messages (`audit_oracle.py` prints them) before relying on the label.
- **Oracle strictness and provisional ground truth.** Oracles encode one acceptable behavior. For T13 the oracle expects 31 days, a modeling assumption standing in for an undecided compliance matrix; an implementation returning 30 is scored as wrong although both are defensible placeholders. Any T13 `bad_merge` is conditional on that assumption and waits on the real decision at the hard gate.
- **Run-to-run variability.** Real models are not deterministic: T13's L3 verdict flipped between two identical runs, and 6 of 18 wrong-without-clarification runs came out correct in the clarification arm without any question being asked. Single runs are single draws.
- **Single implementer family; one small cross-family validator (updated v1.5).** Every implementation was written by gpt-5.1. Runs A–C also used gpt-5.1 as every validator, so only session- and prompt-level contrasts were tested. Run D adds one different-family validator (Claude Haiku 5.5, a small model). A stronger different-family validator, or a different implementer family, is untested, and L2 has not been run as a separate level.
- **Keyword screen for ambiguity.** `ambiguity_flagged` / `accepted_despite_flag` come from a keyword heuristic and are a screen, not a measurement.
- **Default-prompt effects on asking.** The implementer prompt says it may ask "if something genuinely material to correctness is unstated". The ~100 zero-question runs show that gpt-5.1 does not ask under *this* wording; they do not show that models cannot ask. A prompt that requires listing unstated assumptions first is a separate condition.

- **Ground truth is one hidden reading, and validators see only the ambiguous ticket (added v1.4).** The oracle encodes the interpretation the clarified ticket states. A validator reviewing the ambiguous ticket cannot know it, so a "correct" implementation can look like a departure from the natural reading and be held or rejected, while a wrong implementation that follows the natural reading looks fine. Low or negative discrimination in Run C is therefore partly *by construction*, and that is the study's point (the ambiguity is a common cause the validator cannot see past), but it means discrimination measures "can a validator recover an intent that is not in the ticket", not "can it find bugs". State this in any write-up.
- **Style confound between arms (added v1.4).** Correct samples come from the *unambiguous* ticket and often document the non-default reading in their docstrings (half-open intervals, UTC normalization, stable order). A validator may react to that visible departure from the ambiguous ticket. A correct sample drawn from the ambiguous ticket (T08, T13) avoids this; for the tasks that rarely produce one, the confound cannot be removed with this design.
- **Validator noise (added v1.4).** Repeating the same validator on the same code flips about 1 verdict in 5 (14/18 agreement at L1, L3 and L3a in the replay test). Single-verdict differences below that scale are not evidence. Run C used one validator run per sample.
- **Level confounds in L3a (added v1.4).** L3a never held and rejected 60% of correct code in the control run, then 100% of correct code in Run C. It is a stricter validator, not a clearly better one, and its reject rate reflects disagreement with its own pre-code derivation.

---

## 8. Phased execution plan

| Phase | Deliverable | Gate to proceed |
|---|---|---|
| 1 | Pilot task pool (13 tasks, insurance-contextualized — see `tasks.py`), all independence levels at n=3, both clarification conditions | Confirm ≥20% wrong-but-plausible rate under ambiguous spec; confirm measurable ρ separation between L0 and L3; confirm the four-outcome clarification taxonomy (§9a) is actually distinguishable in real transcripts, not just in the scripted mock |
| 2 | Finalize task pool (~120 tasks), re-run power analysis with pilot p/ρ estimates, decide whether to fully cross clarification with independence/chain-length (§2a) | Power ≥0.80 for H2 at planned pool size |
| 3 | Pre-register hypotheses, decision rule, clarification-taxonomy coding rules, and analysis plan | — |
| 4 | Full data collection | — |
| 5 | Primary analysis (§6.1–6.5) + write-up | — |

A working, tested reference harness for Phase 1 (task definitions, LLM client abstraction, implementation/validation/decision pipeline, mock dry-run mode) already exists — see the accompanying `cl10_harness/` package. It has been verified end-to-end with scripted mock models and, as of v1.3, run against a live model (gpt-5.1 in every role; see §8a). Genuine L2/L3 independence via a second model provider has **not** yet been tested, and the Phase 1 gate has not yet been met (see §8a).

### 8a. Pilot status and findings to date (added v1.3)

All figures below come from gpt-5.1 in every role (implementer and validators), one validator per chain, majority rule. Treat them as pilot observations, not results.

**Run A (13 tasks × 3 repeats; ambiguous tickets without signatures) — headline rates withdrawn.** The summary reported `bad_merge` of 23% / 28% / 41% at L0 / L1 / L3. A re-score of the saved code (`audit_oracle.py`) showed that of 39 distinct code samples, 11 passed, 10 failed on behavior and **18 failed on interface**. T02, T08, T12 and T13 were 100% interface failures; T07 and T10 passed every time. These rates should not be quoted.

**Run B (11 tasks × 3 repeats; signatures pinned; T07 and T10 dropped) — usable.** All 33 task-runs valid (no interface/crash exclusions); the implementer passed the clear spec on every task.
- Wrong code on the ambiguous ticket: **15 of 33 task-runs, from 5 tasks (T02, T04, T05, T09, T13)**, each wrong on all 3 repeats. (Originally scored 18 from 6 tasks; T08 was removed after the oracle fix in §3(b″).)
- `bad_merge` as a share of wrong-code runs, re-labelled with the fixed oracle: **L0 6/15 (40%), L1 5/15 (33%), L3 10/15 (67%)**. Paired exact sign tests: L0 vs L3 p = 0.22, L1 vs L3 p = 0.125, L0 vs L1 p = 1.0. These ignore task clustering and are optimistic; the effective n is 5 tasks. **No between-level difference is established**, but L3 is the highest in every comparison made so far (see "Direction that has repeated" below).
- T05 is a blind spot at every level (wrong code accepted 3/3 at L0, L1 and L3). T09 is close behind. The L3 excess comes from T02 (2 `bad_merge` at L3 vs 0 at L0/L1), T09 and T13.
- On correct code, validators accepted 11/18 at each of L0, L1 and L3 and held on 6/18.
- **Clarification:** the implementer asked 0 questions in every clarification-allowed run across all pilot runs (about 98). Under this prompt, "recovered" has not occurred; the harness now labels a correct-with-no-question outcome `correct_without_asking` (it had been mislabeled `recovered` for T11). 6 of the 18 wrong-without-clarification runs were correct in the clarification arm with no question asked, and none went the other way, suggesting the permission-to-ask wording itself may change behavior. Hypothesis only.

**Qualitative findings from reading validator reasoning (T02, T09, T13; one repeat each).**
- L0/L1 holds contain the right question: T02 asks how `a_end == b_start` should be treated; T13 asks whether a 30-day interim default is acceptable until the matrix is final.
- L3 accepts frequently restate the implementation's choices as the "independently derived" expectation (T09, T13), consistent with anchoring (§4, L3 caveat).
- T09: all levels read the naive-datetime example as the full requirement ("no timezone handling is expected") and accept code that fails on aware datetimes — a shared blind spot, the correlated-failure case the study targets.
- One verdict/reasoning mismatch in 99 verdicts (T02, L3: verdict `reject`, reasoning concludes hold).

**Retraction affecting Run B — resolved (v1.4).** T08 failed on the storage-layout oracle flaw (§3(b″)). Re-labelling the saved Run B and L3a-test code with the fixed oracle changed 3 task-run labels (the three T08 repeats, wrong → correct) and produced the corrected Run B numbers above. The earlier "T05 and T08 are blind spots" statement now reads T05 only.

**L3a test and control (replay of saved code, no new implementer calls).** L3a (derive in a separate call before seeing the code) is compared on 30 wrong-code runs (5 tasks × 3 repeats × 2 validator runs), re-labelled with the fixed oracle. `bad_merge` on wrong code: **L0 15/30 (50%), L1 15/30 (50%), L3 20/30 (67%), L3a 16/30 (53%)**. The 44% vs 67% advantage first reported for L3a was driven mostly by T08, which was not wrong code; with it removed, L3a is level with L0 and L1 (L3 vs L3a paired p = 0.29, L1 vs L3a p = 1.0). L3a also never held and rejected correct code (60% in the earlier control run on other tasks; all 6 correct-code runs in this file; 100% in Run C). Treat L3a as a stricter validator, not a better one. Running the control on correct code was necessary to see this. Three verdicts in this file were parse failures defaulted to hold (this predates the parser fix).

**Validator noise floor.** gpt-5.1: re-running the same validator on the same code changed about 1 verdict in 5 at L1, L3 and L3a (14/18 agreement, small sample). Claude Haiku 5.5 on the full pool (50 samples, two runs each, majority rule): the same decision on both runs in **170/200 (85%)** cases: L0 80%, L1 84%, L3 98%, L3a 78%. L3 is the most stable (consistent with a validator that follows the code it is shown), L3a the least. Differences between conditions smaller than this are not evidence. On the two-run Haiku data, `bad_merge` as a share of wrong-code decisions was L0 36/54 (67%), L1 37/54 (69%), L3 42/54 (78%), L3a 24/54 (44%), the same ordering as the single run.

**Run C: labeled implementation pool, judged within task (v1.4).** 60 gpt-5.1 implementations (6 tasks × 6 ambiguous-ticket + 4 unambiguous-ticket draws), each labeled by the oracle, then judged at L0, L1, L3 and L3a, chain length 1, majority rule, one validator run, all against the *ambiguous* ticket. T08 dropped (all 10 samples pass the fixed oracle). Five tasks have both behavior-wrong and correct samples: T02, T04, T05, T09 (6 wrong / 4 correct each) and T13 (3 wrong / 7 correct; T13's ground truth is still provisional).

| Level | P(accept \| wrong) | P(accept \| correct) | Mean within-task discrimination | P(reject \| correct) |
|---|---|---|---|---|
| L0 | 11/27 (41%) | 3/23 (13%) | −22% | 0% |
| L1 | 12/27 (44%) | 3/23 (13%) | −27% | about 1/23 |
| L3 | 16/27 (59%) | 9/23 (39%) | −25% | mixed by task (0–75%) |
| L3a | 19/27 (70%) | 0/23 (0%) | −73% | 100% |

(Pooled counts are derived from the per-task table; the mean discrimination is the unweighted mean over the 5 tasks. Per-task cells are 4–7 samples, so each rate moves in steps of 14–25 points.)

- **No level separated wrong from correct code.** The mean within-task discrimination is negative at every level. Correct code was accepted more often than wrong code in only 1–2 of 5 tasks per level (T04 at L0 and L3 by +8%; T13 at L3 by +52%, n = 3 wrong and provisional ground truth; T13 at L1 by +14%).
- **Most correct code was held, not accepted.** At L0 and L1 the typical response to correct code was `hold`, which is defensible given that a validator cannot see the hidden reading, but it makes "accept" uninformative about correctness.
- **L3a converged on the natural (wrong) reading.** It accepted 70% of the wrong code and rejected 100% of the correct code, i.e. its independently derived expectation matched the implementer's misreading. This is the shared-misreading pattern the study is about (an ambiguity acting as a common cause even for an independent derivation), but in this pilot both roles are the same model, so shared priors are an alternative explanation; only a second provider can separate them.
- **Interpretation, hypothesis only.** Consistent with the ambiguity being a common cause that independence of *process* does not remove. Not tested: independence of *substrate* (L2), validators that can query the requestor (§2b), and tasks outside this pool.

**Run D: a different-family validator on the same pool (v1.5).** The same 50 samples as Run C (code written by gpt-5.1, same 5 tasks, ambiguous ticket shown to the validator, chain length 1, majority rule, one run) judged by `anthropic/claude-haiku-5.5` through OpenRouter at L0, L1, L3 and L3a. This is the first substrate-different validator in the pilot. Haiku is a small model, so a null result here does not rule out a stronger validator discriminating.

| Level | P(accept \| wrong) | P(accept \| correct) | Mean within-task discrimination | P(reject \| correct) |
|---|---|---|---|---|
| L0 | 20/27 (74%) | 12/23 (52%) | −30% | 0% |
| L1 | 19/27 (70%) | 9/23 (39%) | −37% | 0% |
| L3 | 20/27 (74%) | 15/23 (65%) | −17% | 0% |
| L3a | 12/27 (44%) | 2/23 (9%) | −39% | 20/23 (87%) |

- **No discrimination, and the same direction as gpt-5.1** (Run C mean discrimination −22% / −27% / −25% / −73%).
- **The two families fail on the same tasks.** T02 and T09: wrong code (the natural reading) is accepted 67–100% while correct code (the clarified reading) is held 100% of the time at L0, L1 and L3. T05: both families accept the wrong code 100% of the time at L0–L3 (Haiku also accepts the correct code 100%). T13: wrong code accepted 100%, correct code 57–100%. The shared pattern is accept the natural reading, hold on the clarified one.
- **T04 is the exception.** Haiku rejected all wrong T04 code and accepted the correct code 50% / 25% / 100% at L0 / L1 / L3 (discrimination +50 / +25 / +100), but not at L3a. gpt-5.1 did not do this (≤ +8%). Possible reading, hypothesis only: "max_retries" has a conventional meaning (extra attempts) that Haiku applies, so for T04 the intended reading is recoverable from convention, unlike T02, T05 and T09. One task of five; the T04 ticket also names `max_retries` in its pinned signature, which §3(b′) flags as possibly leaking the answer.
- **Strictness differs, discrimination does not.** Haiku is much more lenient than gpt-5.1 (accepts 74% of wrong code at L0 against 41%) and also more lenient on correct code (52% against 13%). That is a difference in threshold, not in ability to separate.
- **Reading for the study.** Replacing the validator's model family did not remove the failure. That fits the hypothesis that the ambiguity, not the validator's substrate, is the common cause, but it is not a test of it: one weak cross-family validator and one implementer family.

**Run E: a larger different-family validator on a subset (v1.5).** `anthropic/claude-sonnet-5.5` (the same tier as gpt-5.1; same family as Haiku, so not independent of Run D) judged the pool for T02, T04 and T09 only, at L0 and L3, one run each (60 calls). It tests whether Run D's null was just a small model.

| Task | L0: accept wrong / accept correct | L3: accept wrong / accept correct |
|---|---|---|
| T02 | 0% / 0% (holds on all correct code) | 50% / 0% (holds on all correct code) |
| T04 | 0% / 0% (holds on all correct code) | 0% / 100% |
| T09 | 100% / 0% (rejects all correct code) | 100% / 0% (rejects all correct code) |
| T05 | 100% / 100% | 100% / 100% |
| T13 | 100% / 100% | 100% / 100% (ground truth provisional) |

Mean within-task discrimination on T02, T04, T09: L0 −33%, L3 −17% (3 tasks). A second Sonnet run on T05 and T13 (L0 and L3, 20 samples) found that Sonnet **accepted every sample, wrong and correct** (bad_merge 9/9 wrong-code runs at both levels; 11/11 correct code accepted; zero holds or rejects), i.e. zero discrimination and a complete blind spot on both tasks.
- **T09 is a shared blind spot across three models and two families.** gpt-5.1, Haiku and Sonnet all accept the naive-datetime implementation and Sonnet rejects 100% of the correct (timezone-normalizing) code. The ticket's naive-datetime example reads as the full requirement; no validator tried here recovers the missing intent.
- **Model size matters on T02.** Haiku accepted the wrong T02 code 83% at L0; Sonnet accepted 0% (it held on everything). So "the validator was too small" is partly right for T02 and wrong for T09.
- **T04 discriminates again at L3** (Haiku and Sonnet), at L0 Sonnet holds on everything. Hold on both wrong and correct code is safe in `bad_merge` terms but gives no accept signal, so it shows as zero discrimination.
- **Limits.** Two levels (L0, L3) only, one run per sample, same family as Run D, so not independent of Run D.

**Direction that has repeated.** L3 (derive-then-judge with the code visible) has accepted the most wrong code in every comparison: Run B (67% vs 40% L0, 33% L1), the L3a test (67% vs 50%, 50%) and Run C on fresh code draws (59% vs 41%, 44%). Run C also shows L3 accepting more *correct* code (39% vs 13%), so L3 looks more lenient overall, not more discriminating. Two of those comparisons share the same task set, and none is significant, so this is a hypothesis for the full study (anchoring on the visible code, §4 L3 caveat), not a result.

**Not claimed.** Five tasks, one model, one validator run, a provisional T13 truth and a style confound mean none of this supports a significance claim or any statement about validators in general. It supports: the harness runs end to end, the measurement is well-posed, and in this configuration accepting did not carry information about correctness.

**Stopping rule (v1.4, extended v1.5).** The pilot is closed at Run E. No further prompt tuning or re-running to improve these numbers. Two follow-ups are separate decisions, not pilot work: expanding to ~30–40 reliably-wrong tasks, and adding a second provider for a real L2.

**Gate status (updated v1.4).** The ≥20% wrong-but-plausible gate is met for 5 tasks that have behavior-wrong code under the fixed oracle (T02, T04, T05, T09, T13; T08 no longer counts); the measurable-ρ-separation gate and the real-transcript taxonomy gate are **not** met (no clarifying questions to classify; no L2/L3 substrate contrast).

---

## 9. What this design gets you that the current literature doesn't

- An empirical **P(correct decision)**, not a pass rate or a fault-escape probability, as the dependent variable — closing the specific hole in CL10.
- **n_eff and ρ estimated for a coding-change accept/reject decision**, extending Kohli's (NLI judges) and Ghanem's (abstract auditors) results into the domain the researcher's original literature review was built around.
- A **manipulated, not observed**, common cause (specification ambiguity) driving validator correlation — a mechanistic claim that SPECA and the N-Version paper argue qualitatively/observationally but that this design tests causally.
- **(Added v1.1) A test of where the reliability ceiling actually comes from** — not just downstream validator independence (H1–H3), but upstream requestor-implementer alignment (H4). This is the more fundamental framing: no amount of independent downstream validation compensates for an evidence chain built on a specification nobody with real business understanding ever actually resolved.

### 9a. The four-outcome clarification taxonomy (added v1.1)

When clarification is allowed but the implementation is still wrong, that single fact ("still wrong after clarification") collapses four distinct causes that must be told apart in analysis, since they imply different fixes:

| Outcome | What happened | Implication if this dominates |
|---|---|---|
| **Recovery** | Right question asked, correct answer given, correctly applied | Clarification works when the opportunity is used well — the mechanism is sound |
| **Wrong question asked** | Implementer never targets the actual ambiguous point | A requirements-elicitation-skill problem — the implementer doesn't know what it doesn't know |
| **Right answer, not applied** | Correct, clear answer given, but not correctly incorporated into code | An instruction-following problem, downstream of understanding — distinct from not understanding the requirement at all |
| **Unresolved intent** | Right question asked, but the requestor side itself has no resolved answer | The evidence chain's ceiling is set upstream of any coding agent or validator — no amount of downstream independence fixes an ambiguity nobody has actually resolved |

The pilot harness's mock script demonstrates the first three (recovery: T01, T04; wrong question: T05; answer not applied: T08), and the fourth is modeled by T13, whose requestor answer is a deliberate hedge. **Real-model status (v1.3):** none of the four outcomes has yet been observed in real transcripts, because gpt-5.1 asked no clarifying question in any run under the default prompt; the classifier now reports `correct_without_asking` and `no_clarification_sought` separately so that sampling variation is not counted as recovery. The more informative real-data observation so far is on the validator side (§8a): the model detects the ambiguity at review time and writes the right question, but at the wrong stage and with no route to the requestor — the motivation for §2b.

---

## Changelog

**v1.5 (current):**
- Added Runs D and E (§8a): Run E repeats T02, T04, T09 with Claude Sonnet 5.5 (T09 fails across all three models; T02 depends on model size; T04 discriminates at L3). Run D: the same pool judged by a different-family validator (Claude Haiku 5.5). No discrimination at any level, the same task-level pattern as gpt-5.1; T04 is the single exception. Updated the single-provider threat (§7).
- Harness: `GeminiClient` (native Gemini API) and `OpenRouterClient`; `run_pilot.py --validator-provider openrouter --validator-model <id>` with `--replay-code` judges saved code with a different-family validator without any implementer calls. A Google `AQ.`-prefix key was rejected by Google's endpoints in this project (401 on every route), so OpenRouter was used.

**v1.4 (superseded):**
- Pilot frozen. Run B and the L3a test corrected for T08 (15 of 33 wrong-code runs, 5 tasks; L3a is level with L0/L1, not better). Added Run C (labeled pool judged within task), the L3a test and control, and the validator noise floor to §8a, with an explicit stopping rule.
- Added §3(b″): the T08 oracle checked storage layout the ticket never specified, so idempotent code was scored wrong. Oracle fixed; Run B and the L3a test were re-labelled (3 task-runs changed); corrected numbers are in §8a. The analysis scripts now re-label saved code with the current oracle.
- §6.1: added within-task discrimination (P(accept | wrong) vs P(accept | correct)) and `false_reject` / `costly_hold` as secondary outcomes.
- §7: added threats (ground truth is a hidden reading; arm style confound; validator noise; L3a is stricter, not better).
- New scripts: `generate_pool.py`, `analyze_discrimination.py`; `analyze_results.py` now re-labels with the current oracle.

**v1.3 (superseded):**
- First real-model pilot (gpt-5.1, all roles). Added §8a (pilot status and findings) and recorded that Run A's headline rates are withdrawn.
- Added §3(b′): the call-and-return contract must be pinned in both spec variants; the harness now appends the signature (and T12's return shape) to the ambiguous ticket and labels oracle failures `interface` / `behavior` / `crash`, excluding interface and crash runs as invalid. Noted that pinning can leak the answer (T04, T13).
- Tightened §3(c): a "wrong" pilot implementation counts only if it is a behavior failure; the ≥20% screen applies per task. Dropped T07 and T10 from the pilot (never wrong on the ambiguous ticket).
- §4: documented that the reference L3 prompt is derive-then-judge with the code visible (anchoring risk) and defined a stronger L3a (derive before seeing code).
- §6.1 / §6.3: unit of analysis is one row per (task, repeat, level, chain length) on wrong-code runs; wrongness is task-determined so repeats do not substitute for tasks; plan for ~30–40 reliably-wrong tasks. §6.5: pre-register the exclusion, HOLD-scoring and keyword-screen rules.
- Added §2b: proposed hold → ask requestor → re-implement condition (not yet implemented).
- §7: added threats (interface mismatch, oracle strictness / T13 provisional truth, run-to-run variability, single provider, keyword screen, default-prompt effects on asking).
- Fixed the clarification classifier: a correct-with-clarification outcome with zero questions asked is now `correct_without_asking`, not `recovered`. §9a updated (T13 now models the unresolved-intent case; no real-transcript outcomes yet).

**v1.2 (superseded):**
- Replaced the forced binary accept/reject validator verdict with a three-way verdict (accept/reject/hold) — HOLD meaning "the evidence doesn't support confidently doing either" (§5). This was a real gap in v1.1: a binary choice hid exactly the confidence information the study cares about.
- The three decision rules (unanimity, majority, any-reject) are now genuinely distinct computations rather than two of them collapsing to the same result (§5) — confirmed directly in the reference harness.
- Replaced the flat `correct_decision` boolean with a six-way `decision_quality` classification crossing {accept, reject, hold} × {ground truth correct, wrong}: `correct_accept`, `bad_merge`, `correct_reject`, `false_reject`, `safe_hold`, `costly_hold` (§6.1). **`bad_merge`** — a confidently wrong accept — is now named explicitly as the single most important outcome, since it's the literal phenomenon (correlated evidence producing false confidence) motivating the whole project.
- Flagged that collapsing the six-way outcome back into one correct/incorrect number for the primary regression is a **policy choice, not a fact**, mirroring how T13's ground truth is flagged (§6.1) — the reference harness implements a default policy and documents it as revisable, not settled.
- Updated the n_eff/ρ estimation procedure (§6.2) to use the same collapsing policy consistently, and added a policy-independent secondary check (raw three-way verdict agreement between validators) that doesn't depend on how HOLD is scored.

**v1.1:**
- Added the clarification factor (§2a) and H4 (§1), operationalized as a separate clarification-recovery comparison rather than a full crossing with the independence/chain-length grid, for cost reasons — see §2a for the explicit scope tradeoff this implies.
- Added the four-outcome clarification taxonomy (§9a) and its statistical treatment (§6.4), distinguishing "recovery" from three distinct non-recovery causes (wrong question, answer not applied, unresolved intent).
- Flagged that "unresolved intent" (requestor side has no resolved answer even when asked correctly) is not yet modeled in the reference pilot harness and should be added before the full study (§7, §9a).
- Updated task pool references: the pilot pool is now insurance-contextualized (`pilot_task_pool_insurance.md` / `tasks.py`, v2.0), superseding the earlier domain-generic pool (`pilot_task_pool_v1.md`, v1.0). Task selection criteria (§3) are unchanged and apply to both; only the specific tasks changed domain.
- Noted that a working, tested reference implementation of the Phase 1 harness now exists (§8), with the specific bugs found and fixed during testing left in the harness's own README/commit history rather than duplicated here.

**v1.0:** Initial design — H1–H3, the independence/ambiguity/chain-length factorial, task selection criteria, n_eff/ρ statistical plan, threats to validity, phased execution plan.
