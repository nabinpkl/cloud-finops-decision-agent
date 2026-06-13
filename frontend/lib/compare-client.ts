import type {
  CompareArgs,
  CompareResult,
} from "@/components/tools/compare-result-types";

// Deterministic comparison client for the MANUAL dashboard. It posts to the
// same-origin /compare proxy (next.config.js rewrites it to the backend), which
// returns the wire-stripped CompareResult (no store_path; ADR-0008). This path
// is fully decoupled from the agent transport: the manual table is driven by
// this result, never by agent view-state.
//
// Family option values are the backend's canonical FamilyName literals
// ("general-purpose", "compute-optimized", "memory-optimized", "any"), so the
// toolbar select values are posted verbatim with no remap and never 422.
export async function runCompare(args: CompareArgs): Promise<CompareResult> {
  const res = await fetch("/compare", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(args),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(
      `compare failed (${res.status})${detail ? `: ${detail.slice(0, 200)}` : ""}`,
    );
  }
  return (await res.json()) as CompareResult;
}
