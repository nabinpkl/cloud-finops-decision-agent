"use client";

import {
  ComparisonSkeleton,
  ComparisonTableView,
} from "@/components/tools/comparison-table-view";
import { FilterToolbar } from "@/components/workspace/filter-toolbar";
import { useWorkspace } from "@/lib/workspace-store";

// The deterministic manual dashboard. It is driven SOLELY by the workspace
// store's /compare result (status/result/view) — it never subscribes to agent
// view-state. This is the affirmative decoupling guarantee: the agent panel can
// read this view, but submitting in the panel cannot change these rows.
export function Dashboard() {
  const status = useWorkspace((s) => s.status);
  const result = useWorkspace((s) => s.result);
  const error = useWorkspace((s) => s.error);
  const view = useWorkspace((s) => s.view);

  const args = {
    vcpu: view?.vcpu,
    ram_gb: view?.ram_gb,
    region: view?.region,
    family: view?.family,
  };

  return (
    <div className="mx-auto w-full max-w-[1280px] px-5 pb-8">
      <FilterToolbar />

      {status === "error" ? (
        <div className="text-destructive bg-destructive/10 rounded-lg border border-transparent px-4 py-3 text-sm">
          {error ?? "Comparison failed."}
        </div>
      ) : status === "loading" ? (
        <ComparisonSkeleton args={args} />
      ) : result ? (
        <ComparisonTableView args={args} data={result} />
      ) : (
        <div className="text-muted-foreground bg-card rounded-lg border p-10 text-center text-sm">
          Pick a spec and press Compare to rank providers with cited prices.
        </div>
      )}
    </div>
  );
}
