# ADR 0005: `data_quality` envelope on normalize responses

- **Status:** Accepted
- **Date:** 2026-05-26
- **Supersedes:** N/A

## Context

ADR 0004 establishes how the indexer detects schema drift and emits drift flags. The next question is how that information crosses the normalize-to-agent boundary so the agent can give honest answers and the frontend (assistant-ui custom components) can render warnings appropriately.

Three constraints:

- **Specific.** "Data quality may be degraded" is wallpaper. The user has to know which provider, what shifted, by how much.
- **Actionable.** A warning that does not lead to a fix is noise. The response has to point at the report file the operator can drill into.
- **Stable.** Eval needs a fixed shape to assert against. Free-text warnings would not be machine-checkable.

## Decision

Every `compare` and `lookup` response carries a top-level `data_quality` block, always present, structured as follows:

```json
{
  "request":  {...},
  "results":  [...],
  "data_quality": {
    "overall_status": "ok | warn | stale | broken",
    "per_provider": {
      "aws":   {"status": "ok",   "snapshot_age_hours": 6.2, "flags": []},
      "ibm":   {
        "status": "warn",
        "snapshot_age_hours": 4.1,
        "flags": ["new_unclassified_shapes"],
        "evidence": {
          "unclassified_count": 12,
          "unclassified_sample": ["bx5-2x8", "cx5-4x8"],
          "rows_in_index": 237,
          "rows_in_previous_index": 225
        },
        "report_path": "store/ibm/<ISO>/index_report.json",
        "human_summary": "12 new IBM shapes in this snapshot are not yet in our taxonomy; the comparison excludes them."
      },
      "azure": {
        "status": "broken",
        "flags": ["index_rebuild_failed_fell_back"],
        "fallback_snapshot_age_hours": 49.6,
        "human_summary": "Today's Azure snapshot failed to parse; serving from the previous one."
      }
    }
  },
  "unmet_requirements": []
}
```

### Status values

| Status | Meaning |
|---|---|
| `ok` | Snapshot is fresh and clean. No warnings. |
| `warn` | Index built. Drift flags fired. Answer is usable but incomplete. |
| `stale` | `age_hours > 24`. Answer is usable but old. |
| `broken` | Today's index failed or this provider has no usable index. The provider may be excluded from results or served from a fallback snapshot. |

`overall_status` equals the worst of the `per_provider` statuses, on the ordering `ok < warn < stale < broken`.

### Field roles

- **`flags`** carry the enum identifiers from ADR 0004. The eval judge keys off flags.
- **`evidence`** carries the structured numbers (counts, deltas, samples). Inspectable, machine-checkable.
- **`human_summary`** carries one to two sentences of prose pre-composed at index build time. The agent paraphrases this in its response so the wording is pinned for eval but does not sound canned.
- **`report_path`** points at `store/<provider>/<ISO>/index_report.json`. The assistant-ui Source primitive renders this as a clickable artifact alongside `store_path`.

### Agent prose treatment

| `overall_status` | Agent's prose |
|---|---|
| `ok` | Answer normally. No warning text. |
| `warn` | Lead with one sentence paraphrasing the worst provider's `human_summary`, then the answer. |
| `stale` | Mark age inline ("snapshot 28h old, past the 24h freshness threshold") and offer refetch. |
| `broken` | Surface which providers were excluded by name, answer with the survivors, refuse to extrapolate to excluded providers. |

The agent must NOT average prices across providers when any one is `broken` or `warn`. The `considered_count` field in the SPEC.md response shape lets the agent say "I compared 6 of 7 providers; IBM excluded due to drift."

### Frontend rendering

- `ComparisonTable` shows a per-row status dot (green/yellow/red), with the `human_summary` on hover. When `overall_status != "ok"`, a top-of-table banner shows the worst provider's `human_summary` with a link to `report_path`.
- `PriceCard` shows a warning banner above the citation block when `status != "ok"`.
- Both components: assistant-ui's `Source` primitive renders `report_path` as a clickable artifact, sibling to `store_path`.

## Consequences

### Positive

- The reliability story crosses the normalize-to-agent boundary in a structured way. No prose-only "things look weird" channel.
- Eval lane 3 (drift detection) becomes mechanical: a doctored snapshot fires a specific flag, the response carries the flag, the agent paraphrases the `human_summary`. Pass/fail is binary.
- The frontend has predictable structure to render against. UX patterns are reusable across query types.
- Operators have one artifact to drill into (`report_path`), one prose summary to paraphrase (`human_summary`), one enum to grep for (`flags`).

### Negative

- Every response carries the envelope even when everything is fine. Tiny payload cost (one nested object with empty arrays). Acceptable.
- `human_summary` is pre-composed at index build time, which means schema drift in a way we did not anticipate has no pre-baked sentence. The indexer falls back to a generic "schema drift detected; see report" sentence in that case. The agent's prose then references the report directly.

### Neutral

- A separate `/index_health` endpoint (same envelope, no `results` field) is a natural future addition for a self-check pattern where the agent probes health before answering. Not in v0.

## Alternatives considered

- **Free-text warnings only.** Rejected: not machine-checkable, encourages prose drift across responses, eval cannot assert on them.
- **Per-row warnings instead of per-provider.** Rejected: the warning usually attaches to the snapshot, not the row. Cleaner top-level. Per-row warnings remain available as an optional `result_quality` block on a result entry for the rare case (e.g. Oracle composite citations).
- **Block responses when `overall_status` is `warn` or worse.** Rejected: degraded service with an honest warning is more useful than refusal. The agent's prose treatment carries the contract.
- **Embed `data_quality` inside each result.** Rejected: redundant when the warning applies to the whole comparison.
