# FINDINGS

Running log of observations from real agent sessions in this repo. Each finding is an instance of "where did the agent earn its cost" or "where did it pay for plumbing it shouldn't have." Together these are the evidence the PRD §6 claim stands on or falls on.

Entries are dated, name the observation, and end with what the bench should do about it (capture, instrument, freeze, or note as open).

---

## 2026-05-25 — Agent knew AWS schema from training, no lookup needed

**What happened.** First session querying against an AWS price snapshot. The agent wrote a Python script that walked the JSON to the price field directly, without first reading one item to learn the schema. The keys it reached for were correct on first try. Result was the right price with no exploration cost.

**Why it matters.** The agent did not learn the AWS schema from the snapshot. It recalled it from training data. AWS Price List Bulk is well-documented enough that the schema lives inside most models. For this provider the probabilistic-recall step was effectively free.

**Relation to the bench thesis.** This is a layer where the agent looked like it earned its cost (it produced the answer) but actually the cost was paid at training time by the model maker, not at query time by the bench. Naively this looks like a win for the agent layer. The next two findings show the failure mode that completes the picture.

---

## 2026-05-25 — Agent guesses JSON field names instead of reading the schema

**What happened.** Mid-session, the agent reached for price fields inside a provider snapshot by recalling key names from training data rather than reading one item to learn the schema. When the guessed key did not exist, Python raised `KeyError` and the agent tried another guess. Several iterations passed before the agent landed on the right path.

**Why it matters.** The keys are deterministically present in the data the moment the agent opens one item. "What key holds the price" is not a judgment question, it is a one-read lookup. The agent paid for it as if it were judgment, repeatedly, with no persistence between attempts and no persistence between sessions. A future session in this repo will make the same guesses against the same files unless something changes.

**Relation to the bench thesis.** This is plumbing that looks like judgment. PRD §6 predicts agents earn their cost only at the irreducible semantic-judgment layer; schema-key discovery is the opposite of irreducible (the answer is in the file). PRD §7 invariant 3 ("memory freezes judgment, the probabilistic step happens once not per query") currently scopes only to instance-type equivalences. This finding argues the same invariant should cover schema maps too.

Together with the AWS-schema-from-training entry above, this brackets the real picture: training-data-as-cache works until it doesn't, and the project has no place for the lesson to land when it doesn't.

**Open question.** Whether to seed `schema/<provider>.json` by hand once (cheap, kills the failure mode immediately, but skips the self-improvement loop the project is supposed to be testing) or to instrument the agent's first successful traversal so the map populates from below (slower, tests the loop, but the friction remains until the loop closes for the first time). Not resolved.

---

## 2026-05-25 — Agent computes snapshot age in local time, gets a wrong answer

**What happened.** The agent reported snapshot ages off by exactly the local UTC offset (4 hours, on a US Eastern Daylight machine). A freshly-fetched snapshot appeared "from the future" in one direction or "4 hours stale" in the other, depending on which side of the subtraction the agent parsed naively.

**Root cause.** The stored `fetched_at` in every receipt is ISO 8601 UTC with a trailing `Z` (e.g. `"2026-05-25T07:32:08.467260Z"`). The `Z` is unambiguous. The gate's own freshness check parses it correctly. The agent, when composing the citation block's `age_hours` field, either stripped the `Z` and parsed naive, or compared against `datetime.now()` without a timezone, or both. Either error produces the same 4-hour delta on this machine.

**Why it matters.** The citation contract says every price carries an inline freshness signal ("snapshot Xh old") and a hard staleness threshold at 24h. If `age_hours` is wrong by the local UTC offset, every freshness claim the agent makes is wrong by that amount, and the staleness threshold trips at the wrong time. The citation contract fails silently because the deterministic plumbing under it was redone by the agent and redone badly.

**Relation to the bench thesis.** Same shape as the schema-key finding: deterministic plumbing the agent paid for as if it were judgment. The honest read is that "compute age in hours from an ISO 8601 UTC string" should never reach the agent at all; the gate or the receipt or a helper should hand the agent a ready-made `age_hours` field. The current design pushes it onto the agent and the agent gets it wrong.

**What changed.** AGENTS.md now contains explicit instructions on parsing `fetched_at` as UTC-aware and computing the delta in UTC. This is a freeze-the-judgment fix at the documentation layer. A code-level fix (compute `age_hours` inside the receipt or a shared helper) is more robust but not done yet.

**Open question.** Whether to keep the instruction-only fix and see if it holds across sessions, or to move the computation into the gates so the agent cannot get it wrong. The first preserves the bench's "agent does the work, receipt is the evidence" framing; the second admits that some things are too cheap to be worth letting the agent re-derive.
