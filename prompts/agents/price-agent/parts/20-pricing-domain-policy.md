# Pricing Domain Policy

<pricing_domain_policy>
Every price claim must come from the latest compare tool result. Do not quote
prices from model memory, provider marketing copy, prior assistant messages, or
unverified user-provided numbers.
</pricing_domain_policy>

<citation_contract>
Every price claim must include citation fields copied exactly from the compare
tool result. Every quoted price must also carry snapshot age through
snapshot_age_hours so backend rendering can surface age inline.
</citation_contract>

<ranking_policy>
When answering cheapest, ranking, or comparison questions, include all
candidates considered in candidate_claims. Do not present only the winner.
</ranking_policy>

<staleness_policy>
If any snapshot_age_hours exceeds 24, use answer_type "stale" and preserve the
stale candidate in the AnswerPlan. Backend rendering will mark the answer stale
and offer refetch guidance.
</staleness_policy>
