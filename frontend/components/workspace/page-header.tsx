"use client";

import { Button } from "@/components/ui/button";
import { useWorkspace } from "@/lib/workspace-store";

// Contextual header beneath the navbar: reflects the committed comparison spec
// so the user always knows what the table below shows.
export function PageHeader() {
  const view = useWorkspace((s) => s.view);
  const reset = useWorkspace((s) => s.reset);

  const title = view
    ? `Cheapest ${view.vcpu} vCPU · ${view.ram_gb} GB`
    : "Compare cloud instances";
  const crumb = view
    ? `Compare · ${view.family} · ${view.region}`
    : "Compare · pick a spec";

  return (
    <div className="mx-auto flex w-full max-w-[1280px] items-end justify-between gap-4 px-5 pt-6 pb-4">
      <div>
        <div className="text-muted-foreground mb-0.5 text-[12.5px]">{crumb}</div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
      </div>
      <div className="flex gap-2">
        <Button variant="outline" size="sm">
          Export
        </Button>
        <Button size="sm" onClick={reset}>
          ＋ New comparison
        </Button>
      </div>
    </div>
  );
}
